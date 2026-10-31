"""Turn a completed Run's evidence into validated candidate facts.

Extraction is the only path that creates facts from conversation. It never
writes during the model call: candidates are produced, validated against the
evidence they cite, and then committed in one store transaction.
"""

# pyright: reportUnknownArgumentType=false, reportUnknownMemberType=false, reportUnknownVariableType=false
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, cast

from loguru import logger

from yoreni.memory.models import (
    Evidence,
    EvidenceKind,
    FactDraft,
    FactKind,
    MemoryExtractionError,
    MemorySchemaError,
    SourceType,
    authority,
    normalize_key,
    normalize_scope,
    optional_str,
    parse_json_object,
    parse_kind,
    require_list,
    require_object,
    require_str,
    validate_claim_support,
)
from yoreni.memory.store import FactStore
from yoreni.utils.llm_runtime import LLMRuntime

# One evidence item longer than this is clamped for the prompt; the full text
# stays in the store and can be read through memory_get.
MAX_EVIDENCE_CHARS = 4_000
MAX_EVIDENCE_ITEMS = 60
MAX_EXISTING_FACTS = 20

_EXTRACTION_PROMPT = """You extract durable facts for a personal agent's long-term memory.

Return JSON only, shaped like:
{{"facts": [
  {{"subject": "user", "predicate": "preferred_language", "text": "The user prefers Chinese.",
    "kind": "user_preference", "scope": "global", "sources": ["ev_id"], "confidence": 0.9,
    "supersedes": null}}
]}}

Rules:
- Keep only information with lasting value: identity, stable preferences, project knowledge,
  decisions, goals, reusable procedures, and lessons from concrete events.
- Skip small talk, transient state, restatements of the current task, and generic world knowledge.
- The assistant's own words never prove that an external action happened. Only cite a tool result
  for facts about executed effects.
- Every fact must cite one or more evidence IDs from the input. Never invent an ID.
- When the new evidence clearly corrects an existing fact, set "supersedes" to that fact's id.
  Otherwise use null and let the store reconcile it.
- subject: a stable lowercase identifier ("user", "project:yoreni", a tool or system name).
- predicate: lowercase snake_case property ("preferred_language", "default_branch").
- text: one self-contained sentence, in the language of the evidence, with no pronoun that
  depends on the conversation.
- kind: one of user_fact, user_preference, project_knowledge, decision, goal,
  event_experience, procedure.
- scope: "global", or "project:<name>" for project-local knowledge.
- confidence: 0..1, informational only; it never grants authority.

Existing facts (for corrections only):
{existing}

Evidence:
{evidence}
"""


@dataclass(frozen=True, slots=True)
class ExtractionResult:
    """Facts committed by one extraction pass."""

    fact_ids: tuple[str, ...]
    candidates: int


class FactExtractor:
    """Produce and validate candidate facts from one Run's evidence."""

    def __init__(self, store: FactStore, *, max_candidates: int = 20):
        self.store = store
        self.max_candidates = max(1, max_candidates)

    async def extract(
        self,
        evidence: list[Evidence],
        *,
        runtime: LLMRuntime,
        existing: list[dict[str, Any]] | None = None,
    ) -> ExtractionResult:
        usable = [item for item in evidence if item.content.strip() and not item.suppressed]
        if not usable:
            raise MemoryExtractionError("run has no usable evidence")
        fact_ids: list[str] = []
        candidates = 0
        # Long Runs are processed in chunks so no evidence is silently dropped.
        for start in range(0, len(usable), MAX_EVIDENCE_ITEMS):
            chunk = usable[start:start + MAX_EVIDENCE_ITEMS]
            prompt = _EXTRACTION_PROMPT.format(
                existing=_render_existing(existing or []),
                evidence=_render_evidence(chunk),
            )
            from yoreni.credentials.manager import get_credential_manager

            response = await runtime.provider.chat(
                messages=get_credential_manager().redact_messages(
                    [{"role": "user", "content": prompt}]
                ),
                model=runtime.model,
                temperature=0.0,
                max_tokens=runtime.generation.max_tokens,
                reasoning_effort=runtime.generation.reasoning_effort,
            )
            if response.finish_reason == "error":
                raise MemoryExtractionError(
                    f"extraction model failed: {response.error_kind or 'provider error'}"
                )
            if not response.content:
                raise MemoryExtractionError("extraction model returned no content")
            drafts = parse_candidates(response.content, chunk, self.max_candidates)
            if not drafts:
                continue
            candidates += len(drafts)
            fact_ids.extend(self.store.commit_candidates(drafts))
        logger.info(
            "memory extraction committed {} fact(s) from {} evidence item(s)",
            len(fact_ids), len(usable),
        )
        return ExtractionResult(fact_ids=tuple(fact_ids), candidates=candidates)


def parse_candidates(
    raw: str,
    evidence: list[Evidence],
    limit: int,
) -> list[FactDraft]:
    """Validate a model response into drafts; raise rather than guess."""
    payload = parse_json_object(raw)
    items = require_list(payload.get("facts", []), field_name="facts")
    by_id = {item.id: item for item in evidence}
    drafts: list[FactDraft] = []
    for item in items:
        if len(drafts) >= limit:
            break
        draft = _parse_candidate(require_object(item, field_name="facts[]"), by_id)
        if draft is not None:
            drafts.append(draft)
    return drafts


def _parse_candidate(
    item: dict[str, Any],
    by_id: dict[str, Evidence],
) -> FactDraft | None:
    subject = normalize_key(require_str(item.get("subject"), field_name="subject"))
    predicate = normalize_key(require_str(item.get("predicate"), field_name="predicate"))
    text = require_str(item.get("text"), field_name="text")
    if not subject or not predicate or not text.strip():
        return None
    kind: FactKind = parse_kind(item.get("kind"))
    scope = normalize_scope(str(item.get("scope") or "global"))
    raw_sources = require_list(item.get("sources"), field_name="sources")
    sources = tuple(
        source for source in (
            require_str(entry, field_name="sources[]") for entry in raw_sources
        )
        if source
    )
    cited: list[Evidence] = []
    for source_id in sources:
        found = by_id.get(source_id)
        if found is None:
            raise MemorySchemaError(
                f"candidate cites evidence {source_id!r} that was not provided"
            )
        if found.suppressed or not found.content.strip():
            raise MemorySchemaError(f"candidate cites deleted evidence {source_id!r}")
        cited.append(found)
    if not cited:
        raise MemorySchemaError("candidate has no evidence")
    source_type = derive_source_type(cited)
    observed_at = max(
        (item.observed_at or item.created_at for item in cited),
        default=None,
    )
    confidence = item.get("confidence")
    supersedes = optional_str(item.get("supersedes"), field_name="supersedes")
    attributes = item.get("attributes")
    draft = FactDraft(
        subject=subject,
        predicate=predicate,
        text=text,
        kind=kind,
        scope=scope,
        sources=tuple(cited_ids(cited)),
        source_type=source_type,
        observed_at=observed_at,
        supersedes=supersedes,
        attributes=cast(dict[str, Any], attributes) if isinstance(attributes, dict) else {},
        confidence=float(confidence) if isinstance(confidence, (int, float)) and not isinstance(confidence, bool) else None,
    )
    # Citing an id is not enough: the cited text has to be about the claim.
    validate_claim_support(draft, cited)
    return draft


def cited_ids(cited: list[Evidence]) -> list[str]:
    return [item.id for item in cited]


def derive_source_type(cited: list[Evidence]) -> SourceType:
    """Provenance comes from the cited evidence, never from the model."""
    candidates: list[SourceType] = []
    for item in cited:
        if item.kind in {EvidenceKind.USER_MESSAGE, EvidenceKind.LEGACY_ENTRY}:
            candidates.append(item.source_type)
        elif item.kind is EvidenceKind.TOOL_RESULT:
            # A failed tool call is recorded as UNVERIFIED by the Run import;
            # its provenance must not be upgraded merely because it came from
            # the tool channel.
            candidates.append(item.source_type)
        else:
            candidates.append(SourceType.AGENT_INFERENCE)
    return max(candidates, key=lambda source: authority(source, FactKind.PROJECT_KNOWLEDGE))


def _render_evidence(evidence: list[Evidence]) -> str:
    lines: list[str] = []
    for item in evidence:
        content = item.content.strip()
        if len(content) > MAX_EVIDENCE_CHARS:
            content = f"{content[:MAX_EVIDENCE_CHARS]}…[truncated]"
        lines.append(
            f"[{item.id}] ({item.role}/{item.kind.value}/{item.tool_name or '-'}"
            f"/{item.observed_at or 'unknown-time'}) {content}"
        )
    return "\n".join(lines)


def _render_existing(existing: list[dict[str, Any]]) -> str:
    if not existing:
        return "(none)"
    rows = []
    for fact in existing[:MAX_EXISTING_FACTS]:
        rows.append(json.dumps({
            "id": fact.get("id"),
            "subject": fact.get("subject"),
            "predicate": fact.get("predicate"),
            "text": fact.get("text"),
            "status": fact.get("status"),
        }, ensure_ascii=False))
    return "\n".join(rows)


__all__ = ["ExtractionResult", "FactExtractor", "parse_candidates"]
