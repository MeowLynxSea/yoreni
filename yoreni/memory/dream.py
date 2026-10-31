"""Background consolidation ("Dream") as a validated change-set pipeline.

Dream is a single bounded model call followed by deterministic validation and
application: it never holds file tools and never edits memory while reasoning.
The operation record is durable, so a crash at any point resumes without
applying a change twice.
"""

# pyright: reportUnknownArgumentType=false, reportUnknownMemberType=false, reportUnknownVariableType=false
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, cast

from loguru import logger

from yoreni.memory.core import CORE_WRITABLE, validate_core_content
from yoreni.memory.models import (
    Evidence,
    Fact,
    FactDraft,
    FactKind,
    FactStatus,
    MemorySchemaError,
    SourceType,
    parse_json_object,
    parse_kind,
    require_list,
    require_object,
    require_str,
    validate_claim_support,
)
from yoreni.memory.store import FactStore
from yoreni.utils.helpers import (
    build_assistant_message,
    estimate_prompt_tokens_chain,
    truncate_text_to_tokens,
)
from yoreni.utils.prompt_templates import render_template

if TYPE_CHECKING:
    from yoreni.memory.service import MemoryService
    from yoreni.utils.llm_runtime import LLMRuntime

DEFAULT_BATCH_FACTS = 60
MAX_FACT_CHARS = 800
MAX_CORE_CHARS = 60_000
MAX_TOOL_ROUNDS = 6
_MAX_TOOL_RESULT_TOKENS = 4_000
_ALLOWED_STATUS = {FactStatus.ACTIVE, FactStatus.DORMANT}
_DREAM_TOOLS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "memory_search",
            "description": "Search stored facts and their evidence by meaning or keyword.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 20},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "memory_get",
            "description": "Read one fact with its sources, relations and version history.",
            "parameters": {
                "type": "object",
                "properties": {"fact_id": {"type": "string"}},
                "required": ["fact_id"],
            },
        },
    },
]


@dataclass(frozen=True, slots=True)
class DreamResult:
    """Outcome of one consolidation attempt."""

    status: str
    run_id: str | None
    changes: int
    detail: str


@dataclass(frozen=True, slots=True)
class DreamBatch:
    """Facts and evidence shown to the model for one pass."""

    cursor_from: int
    cursor_to: int
    facts: tuple[Fact, ...]
    disputed: tuple[Fact, ...]


class DreamConsolidator:
    """Run, validate, apply and recover Dream consolidation."""

    def __init__(
        self,
        service: "MemoryService",
        *,
        batch_facts: int = DEFAULT_BATCH_FACTS,
    ) -> None:
        self.service = service
        self.store = service.store
        self.core = service.core
        self.batch_facts = max(1, batch_facts)

    # -- public entry point --------------------------------------------------

    async def consolidate(self, runtime: "LLMRuntime") -> DreamResult:
        # One consolidation per process at a time. The durable claim below
        # still protects against another process holding an unfinished pass.
        async with self.service.dream_lock:
            return await self._consolidate(runtime)

    async def _consolidate(self, runtime: "LLMRuntime") -> DreamResult:
        # A "planned" run without a proposal may be a live model call in
        # another process; finishing half-applied runs is always safe because
        # those already passed validation and cannot be re-proposed.
        recovered = self.recover(include_planned=False)
        if recovered:
            logger.info("Dream recovered {} unfinished operation(s)", recovered)
        batch = self._collect_batch()
        if batch is None:
            return DreamResult(status="idle", run_id=None, changes=0, detail="nothing to consolidate")
        run_id = self.store.claim_dream_run(
            cursor_from=batch.cursor_from, cursor_to=batch.cursor_to,
        )
        if run_id is None:
            return DreamResult(status="busy", run_id=None, changes=0, detail="another consolidation is active")
        snapshot = self.core.snapshot()
        try:
            proposal, tokens = await self._propose(runtime, batch)
        except Exception as exc:
            self.store.update_dream_run(run_id, status="failed", error=f"{type(exc).__name__}: {exc}")
            logger.warning("Dream proposal failed: {}", exc)
            return DreamResult(status="failed", run_id=run_id, changes=0, detail=str(exc))
        self.store.update_dream_run(
            run_id, proposal=proposal, tokens_in=tokens[0], tokens_out=tokens[1],
        )
        return self._apply(run_id, proposal, batch, snapshot)

    def recover(self, *, include_planned: bool = True) -> int:
        """Finish or fail operations interrupted by a previous process.

        ``include_planned=False`` leaves pre-proposal runs alone: they may be
        alive in another process. Startup recovery uses the default.
        """
        recovered = 0
        for record in self.store.unfinished_dream_runs():
            run_id = record["id"]
            status = record["status"]
            if not include_planned and status in {"planned", "validated"}:
                continue
            try:
                if status in {"planned", "validated"}:
                    if record["proposal"] is None:
                        self.store.update_dream_run(
                            run_id, status="failed",
                            error="interrupted before a proposal was stored",
                        )
                    else:
                        batch = DreamBatch(
                            cursor_from=record["cursor_from"],
                            cursor_to=record["cursor_to"],
                            facts=(), disputed=(),
                        )
                        self._apply(run_id, record["proposal"], batch, self.core.snapshot())
                elif status in {"applying", "files_written", "git_committed"}:
                    self._finish(run_id, record)
                else:  # unreachable for the closed status set
                    self.store.update_dream_run(
                        run_id, status="failed", error=f"unknown status {status}",
                    )
            except Exception as exc:
                logger.opt(exception=exc).error("Dream recovery failed for {}", run_id)
                self.store.update_dream_run(
                    run_id, error=f"recovery: {type(exc).__name__}: {exc}",
                )
                continue
            recovered += 1
        return recovered

    # -- input ---------------------------------------------------------------

    def _collect_batch(self) -> DreamBatch | None:
        cursor = int(self.store.meta_get("dream_fact_cursor") or 0)
        rows = self.store.facts_since(cursor, self.batch_facts)
        disputed = [
            fact for fact in self.store.recent_by_status(FactStatus.DISPUTED, 10)
        ]
        if not rows and not disputed:
            return None
        return DreamBatch(
            cursor_from=cursor,
            cursor_to=rows[-1][0] if rows else cursor,
            facts=tuple(fact for _rowid, fact in rows),
            disputed=tuple(disputed),
        )

    # -- proposal ------------------------------------------------------------

    async def _propose(
        self, runtime: "LLMRuntime", batch: DreamBatch,
    ) -> tuple[dict[str, Any], tuple[int, int]]:
        prompt = render_template("agent/dream.md", strip=True)
        sections = [prompt, self._render_core()]
        if batch.facts:
            sections.append("## New facts\n" + self._render_facts(batch.facts))
        if batch.disputed:
            sections.append("## Disputed facts\n" + self._render_facts(batch.disputed))
        messages: list[dict[str, Any]] = [
            {"role": "user", "content": "\n\n".join(sections)},
        ]
        input_budget = runtime.context_window_tokens - 4_000
        estimated, source = estimate_prompt_tokens_chain(
            runtime.provider, runtime.model, messages, _DREAM_TOOLS,
        )
        if input_budget > 0 and estimated > input_budget:
            # Dropping facts would advance the cursor past unseen evidence;
            # fail loudly so the operator can lower dream_batch_facts.
            raise MemorySchemaError(
                f"dream input does not fit the model context: {estimated}/"
                f"{input_budget} via {source}; lower memory.dream_batch_facts"
            )
        tokens_in = 0
        tokens_out = 0
        for _round in range(MAX_TOOL_ROUNDS):
            response = await runtime.provider.chat(
                messages=messages,
                model=runtime.model,
                temperature=0.0,
                max_tokens=runtime.generation.max_tokens,
                reasoning_effort=runtime.generation.reasoning_effort,
                tools=_DREAM_TOOLS,
            )
            if response.usage is not None:
                tokens_in += response.usage.input_tokens
                tokens_out += response.usage.output_tokens
            if response.finish_reason == "error":
                raise MemorySchemaError(
                    f"dream model failed: {response.error_kind or 'provider error'}"
                )
            if response.should_execute_tools:
                messages.append(build_assistant_message(
                    response.content,
                    tool_calls=[call.to_openai_tool_call() for call in response.tool_calls],
                    reasoning_content=response.reasoning_content,
                    thinking_blocks=response.thinking_blocks,
                ))
                for call in response.tool_calls:
                    messages.append({
                        "role": "tool",
                        "tool_call_id": call.id,
                        "name": call.name,
                        "content": await self._run_tool(call.name, call.arguments),
                    })
                continue
            if not response.content:
                raise MemorySchemaError("dream model returned no content")
            return parse_json_object(response.content), (tokens_in, tokens_out)
        raise MemorySchemaError(
            f"dream did not return a change set within {MAX_TOOL_ROUNDS} tool rounds"
        )

    async def _run_tool(self, name: str, arguments: dict[str, Any]) -> str:
        try:
            if name == "memory_search":
                query = str(arguments.get("query") or "").strip()
                if not query:
                    return "Error: query is required"
                limit = int(arguments.get("limit") or 8)
                hits = await self.service.search(
                    query,
                    statuses=(
                        FactStatus.ACTIVE,
                        FactStatus.DORMANT,
                        FactStatus.SUPERSEDED,
                        FactStatus.DISPUTED,
                    ),
                    limit=max(1, min(limit, 20)),
                )
                payload: Any = [hit.to_dict() for hit in hits]
            elif name == "memory_get":
                fact_id = str(arguments.get("fact_id") or "").strip()
                payload = self.service.fact_detail(fact_id) if fact_id else None
                if payload is None:
                    return f"Error: unknown fact {fact_id!r}"
            else:
                return f"Error: unknown tool {name!r}"
        except MemorySchemaError as exc:
            return f"Error: {exc}"
        rendered = json.dumps(payload, ensure_ascii=False)
        return truncate_text_to_tokens(rendered, _MAX_TOOL_RESULT_TOKENS)

    def _render_core(self) -> str:
        lines = ["## Current core memory"]
        for name in CORE_WRITABLE:
            text = self.core.read(name).strip() or "(empty)"
            lines.append(f"### {name}\n{text}")
        return "\n\n".join(lines)

    def _render_facts(self, facts: tuple[Fact, ...]) -> str:
        sources = self.store.sources_for(fact.id for fact in facts)
        lines: list[str] = []
        for fact in facts:
            text = fact.text
            if len(text) > MAX_FACT_CHARS:
                text = f"{text[:MAX_FACT_CHARS]}…[truncated]"
            lines.append(
                f"- [{fact.id}] {fact.kind.value}/{fact.status.value}/scope={fact.scope}"
                f"/observed={fact.observed_at or 'unknown'}/valid_from={fact.valid_from}"
                f"\n  {fact.subject}.{fact.predicate} = {text}"
            )
            for source in sources.get(fact.id, [])[:3]:
                quote = " ".join(source.quote.split())[:200]
                lines.append(f"  evidence {source.evidence_id}: {quote}")
        return "\n".join(lines)

    # -- validation + application -------------------------------------------

    def _apply(
        self,
        run_id: str,
        proposal: dict[str, Any],
        batch: DreamBatch,
        snapshot: Any,
    ) -> DreamResult:
        try:
            operations = validate_operations(proposal, self.store, self.core)
        except Exception as exc:
            message = f"{type(exc).__name__}: {exc}"
            self.store.update_dream_run(run_id, status="failed", error=message)
            return DreamResult(status="failed", run_id=run_id, changes=0, detail=message)
        self.store.update_dream_run(run_id, status="validated")
        if not operations:
            self.store.complete_dream_cursor(run_id, batch.cursor_to)
            return DreamResult(status="completed", run_id=run_id, changes=0, detail="no changes")

        if not snapshot.matches(self.core.snapshot()):
            message = "core memory changed while Dream was running; the change set was rejected"
            self.store.update_dream_run(run_id, status="failed", error=message)
            return DreamResult(status="failed", run_id=run_id, changes=0, detail=message)

        updates = {
            str(operation["file"]): str(operation["content"])
            for operation in operations if operation["op"] == "update_core"
        }
        head_before = (
            self.service.git.head_commit_sha()
            if self.service.git.is_initialized() else None
        )
        self.store.set_dream_head(run_id, head_before)
        try:
            self.store.apply_dream_operations(
                run_id,
                [operation for operation in operations if operation["op"] not in {"update_core", "propose_skill"}],
                changes=len(operations),
            )
        except Exception as exc:
            self.store.update_dream_run(run_id, status="failed", error=f"{type(exc).__name__}: {exc}")
            return DreamResult(status="failed", run_id=run_id, changes=0, detail=str(exc))

        for operation in operations:
            if operation["op"] == "propose_skill":
                self.store.add_skill_proposal(
                    run_id,
                    str(operation["name"]),
                    str(operation["description"]),
                    str(operation["body"]),
                )

        try:
            if updates:
                snapshot = self.service.apply_core_updates(updates, expected=snapshot)
                self.store.set_dream_files(run_id, {
                    path: snapshot.hashes[path] for path in updates
                })
        except Exception as exc:
            # Database changes are durable; files are not. Recovery retries the
            # file write before the operation can complete.
            self.store.update_dream_run(run_id, error=f"file write: {exc}")
            return DreamResult(status="failed", run_id=run_id, changes=len(operations), detail=str(exc))

        self.store.update_dream_run(run_id, status="files_written")
        return self._finish(run_id, self.store.dream_run(run_id) or {})

    def _finish(self, run_id: str, record: dict[str, Any]) -> DreamResult:
        """Commit files and advance the cursor for an applied operation."""
        applied = record.get("applied") or {}
        changes = int(applied.get("changes", 0)) if isinstance(applied, dict) else 0
        files = applied.get("files") if isinstance(applied, dict) else None
        commit = record.get("git_commit")
        if not commit and isinstance(files, dict) and files:
            try:
                diff_body = self.service.git.summarize_working_tree(
                    list(CORE_WRITABLE),
                ) if self.service.git.is_initialized() else ""
                if diff_body:
                    commit = self.service.git.auto_commit(
                        f"dream: consolidate memory ({changes} change(s))\n\n{diff_body}",
                    )
                elif self.service.git.is_initialized():
                    # A crash after the commit but before the status write: only
                    # adopt HEAD when it moved since this operation started.
                    head = self.service.git.head_commit_sha("dream:")
                    if head and head != applied.get("head_before"):
                        commit = head
            except Exception as exc:
                self.store.update_dream_run(run_id, error=f"git commit: {exc}")
                return DreamResult(status="failed", run_id=run_id, changes=changes, detail=str(exc))
            if commit:
                self.store.update_dream_run(run_id, status="git_committed", git_commit=commit)
        self.store.complete_dream_cursor(run_id, int(record["cursor_to"]))
        return DreamResult(status="completed", run_id=run_id, changes=changes, detail="applied")


def validate_operations(
    proposal: dict[str, Any],
    store: FactStore,
    core: Any,
) -> list[dict[str, Any]]:
    """Validate the entire change set before anything is applied."""
    raw = proposal.get("operations", [])
    if raw is None:
        return []
    items = require_list(raw, field_name="operations")
    cited = [
        evidence_id
        for item in items
        if isinstance(item, dict)
        for evidence_id in cast(list[Any], item.get("evidence", []) or [])
        if isinstance(evidence_id, str)
    ]
    evidence_rows = store.evidence(cited)
    operations: list[dict[str, Any]] = []
    for item in items:
        operation = require_object(item, field_name="operations[]")
        op = require_str(operation.get("op"), field_name="op")
        if op == "add_fact":
            operations.append(_validate_add_fact(operation, evidence_rows))
        elif op == "merge_facts":
            operations.append(_validate_merge(operation, store))
        elif op == "set_status":
            operations.append(_validate_status(operation, store))
        elif op == "update_core":
            operations.append(_validate_core(operation, core))
        elif op == "propose_skill":
            operations.append(_validate_skill(operation))
        else:
            raise MemorySchemaError(f"unsupported dream operation {op!r}")
    return operations


def _validate_add_fact(
    operation: dict[str, Any],
    evidence_rows: dict[str, Evidence],
) -> dict[str, Any]:
    subject = require_str(operation.get("subject"), field_name="subject")
    predicate = require_str(operation.get("predicate"), field_name="predicate")
    text = require_str(operation.get("text"), field_name="text")
    kind: FactKind = parse_kind(operation.get("kind"))
    scope = str(operation.get("scope") or "global")
    evidence = [
        require_str(value, field_name="evidence[]")
        for value in require_list(operation.get("evidence", []), field_name="evidence")
    ]
    if not evidence:
        raise MemorySchemaError("add_fact requires evidence ids")
    for evidence_id in evidence:
        if evidence_id not in evidence_rows or evidence_rows[evidence_id].suppressed:
            raise MemorySchemaError(f"add_fact cites unknown or deleted evidence {evidence_id!r}")
    supersedes = operation.get("supersedes")
    if supersedes is not None and not isinstance(supersedes, str):
        raise MemorySchemaError("supersedes must be a fact id or null")
    source_type = _derive_source_type(evidence_rows, evidence)
    observed_at = max(
        (evidence_rows[item].observed_at or evidence_rows[item].created_at for item in evidence),
        default=None,
    )
    draft = FactDraft(
        subject=subject,
        predicate=predicate,
        text=text,
        kind=kind,
        scope=scope,
        sources=tuple(evidence),
        source_type=source_type,
        observed_at=observed_at,
        supersedes=supersedes,
    ).normalized()
    validate_claim_support(draft, [evidence_rows[item] for item in evidence])
    return {
        "op": "add_fact",
        "subject": subject,
        "predicate": predicate,
        "text": text,
        "kind": kind.value,
        "scope": scope,
        "evidence": evidence,
        "source_type": source_type,
        "observed_at": observed_at,
        "supersedes": supersedes,
    }


def _validate_merge(operation: dict[str, Any], store: FactStore) -> dict[str, Any]:
    keep = require_str(operation.get("keep"), field_name="keep")
    merge = [
        require_str(value, field_name="merge[]")
        for value in require_list(operation.get("merge", []), field_name="merge")
    ]
    if not merge:
        raise MemorySchemaError("merge_facts requires at least one fact to merge")
    for fact_id in (keep, *merge):
        fact = store.fact(fact_id)
        if fact is None or fact.status is FactStatus.DELETED:
            raise MemorySchemaError(f"merge_facts references unknown fact {fact_id!r}")
    return {"op": "merge_facts", "keep": keep, "merge": merge}


def _validate_status(operation: dict[str, Any], store: FactStore) -> dict[str, Any]:
    fact_id = require_str(operation.get("fact_id"), field_name="fact_id")
    status = FactStatus(require_str(operation.get("status"), field_name="status"))
    if status not in _ALLOWED_STATUS:
        raise MemorySchemaError("Dream may only set a fact active or dormant")
    fact = store.fact(fact_id)
    if fact is None or fact.status is FactStatus.DELETED:
        raise MemorySchemaError(f"set_status references unknown fact {fact_id!r}")
    if fact.status not in {FactStatus.ACTIVE, FactStatus.DORMANT}:
        # A disputed or superseded fact is a real conflict; only new evidence
        # or a user correction may resolve it, never Dream's preference.
        raise MemorySchemaError(
            f"Dream cannot change the status of a {fact.status.value} fact"
        )
    return {"op": "set_status", "fact_id": fact_id, "status": status.value}


def _validate_core(operation: dict[str, Any], core: Any) -> dict[str, Any]:
    file_name = require_str(operation.get("file"), field_name="file")
    if file_name not in CORE_WRITABLE:
        raise PermissionError(
            f"Dream cannot modify {file_name}; only {', '.join(CORE_WRITABLE)} are maintained"
        )
    content = require_str(operation.get("content"), field_name="content", allow_empty=True)
    validate_core_content(content)
    if len(content) > MAX_CORE_CHARS:
        raise MemorySchemaError("core memory update exceeds the maximum size")
    budget = int(core.token_budget * 0.35)
    if truncate_text_to_tokens(content, budget) != content:
        raise MemorySchemaError(
            f"{file_name} update exceeds its share of the core token budget"
        )
    return {"op": "update_core", "file": file_name, "content": content}


def _validate_skill(operation: dict[str, Any]) -> dict[str, Any]:
    name = require_str(operation.get("name"), field_name="name")
    description = require_str(operation.get("description"), field_name="description")
    body = require_str(operation.get("body"), field_name="body")
    if not name.replace("-", "").replace("_", "").isalnum():
        raise MemorySchemaError("skill proposal name must be alphanumeric with dashes or underscores")
    if len(name) > 80 or len(description) > 400 or len(body) > 20_000:
        raise MemorySchemaError("skill proposal exceeds its size limits")
    return {
        "op": "propose_skill",
        "name": name,
        "description": description,
        "body": body,
    }


def _derive_source_type(evidence_rows: dict[str, Evidence], ids: list[str]) -> SourceType:
    from yoreni.memory.extraction import derive_source_type

    return derive_source_type([evidence_rows[item] for item in ids])


def serialize_proposal(operations: list[dict[str, Any]]) -> str:
    return json.dumps({"operations": operations}, ensure_ascii=False)


__all__ = ["DreamBatch", "DreamConsolidator", "DreamResult", "validate_operations"]
