"""Vocabulary, normalisation and value objects for long-term memory.

Everything persisted by the memory system passes through this module: fact
keys are normalised here, search text is tokenised here, and timestamps are
formatted here so that lexical ordering matches chronological ordering.
"""

# pyright: reportUnnecessaryIsInstance=false
from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any, cast

# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class MemoryError(RuntimeError):
    """Base class for memory failures that callers must handle explicitly."""


class MemoryStoreError(MemoryError):
    """The fact store could not complete a transaction."""


class MemorySchemaError(MemoryError):
    """A stored row or a model-produced payload violates the fact schema."""


class MemoryExtractionError(MemoryError):
    """Fact extraction could not produce a trustworthy candidate set."""


class MemoryRetrievalError(MemoryError):
    """The configured retrieval indexes could not serve a query."""


class MemoryMigrationError(MemoryError):
    """Legacy data could not be imported safely, so startup must stop."""


class DreamError(MemoryError):
    """A consolidation pass could not be completed or recovered."""


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------


class FactKind(StrEnum):
    """The kind of long-term knowledge a fact represents."""

    USER_FACT = "user_fact"
    USER_PREFERENCE = "user_preference"
    PROJECT_KNOWLEDGE = "project_knowledge"
    DECISION = "decision"
    GOAL = "goal"
    EVENT_EXPERIENCE = "event_experience"
    PROCEDURE = "procedure"


class FactStatus(StrEnum):
    """Lifecycle of one fact version."""

    ACTIVE = "active"
    DORMANT = "dormant"
    SUPERSEDED = "superseded"
    DISPUTED = "disputed"
    DELETED = "deleted"


class SourceType(StrEnum):
    """Where a claim came from, which determines how much it may be trusted."""

    USER_STATEMENT = "user_statement"
    USER_CORRECTION = "user_correction"
    TOOL_RESULT = "tool_result"
    EXTERNAL_DOCUMENT = "external_document"
    AGENT_INFERENCE = "agent_inference"
    LEGACY_IMPORT = "legacy_import"
    UNVERIFIED = "unverified"


class EvidenceKind(StrEnum):
    """The shape of one durable observation."""

    USER_MESSAGE = "user_message"
    ASSISTANT_MESSAGE = "assistant_message"
    TOOL_CALL = "tool_call"
    TOOL_RESULT = "tool_result"
    OBSERVATION = "observation"
    LEGACY_ENTRY = "legacy_entry"


class TrustLevel(StrEnum):
    """How the evidence may be used when it reaches the model again."""

    VERIFIED = "verified"
    ASSERTED = "asserted"
    INFERRED = "inferred"
    UNTRUSTED = "untrusted"


class JobStatus(StrEnum):
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


SINGLE_VALUED_KINDS: frozenset[FactKind] = frozenset({
    FactKind.USER_FACT,
    FactKind.USER_PREFERENCE,
    FactKind.PROJECT_KNOWLEDGE,
    FactKind.DECISION,
    FactKind.GOAL,
})

_ALL_STATUSES = {status.value for status in FactStatus}
_ALL_KINDS = {kind.value for kind in FactKind}
_ALL_SOURCE_TYPES = {source.value for source in SourceType}

_USER_KINDS = frozenset({
    FactKind.USER_FACT,
    FactKind.USER_PREFERENCE,
    FactKind.GOAL,
    FactKind.DECISION,
})

# A lower rank yields to a higher rank for the same dedupe key. Confidence is
# deliberately absent: a model's self-reported certainty never outranks the
# provenance of the claim.
_AUTHORITY = {
    "user": {
        SourceType.USER_CORRECTION: 4,
        SourceType.USER_STATEMENT: 3,
        SourceType.TOOL_RESULT: 2,
        SourceType.EXTERNAL_DOCUMENT: 2,
        SourceType.LEGACY_IMPORT: 2,
        SourceType.AGENT_INFERENCE: 1,
        SourceType.UNVERIFIED: 0,
    },
    "world": {
        SourceType.TOOL_RESULT: 4,
        SourceType.USER_CORRECTION: 3,
        SourceType.USER_STATEMENT: 3,
        SourceType.EXTERNAL_DOCUMENT: 2,
        SourceType.LEGACY_IMPORT: 2,
        SourceType.AGENT_INFERENCE: 1,
        SourceType.UNVERIFIED: 0,
    },
}


def authority(source_type: SourceType, kind: FactKind) -> int:
    """Rank the provenance of one claim within its subject domain."""
    domain = "user" if kind in _USER_KINDS else "world"
    return _AUTHORITY[domain][source_type]


def single_valued(kind: FactKind) -> bool:
    """Whether a newer value replaces the previous one for the same key."""
    return kind in SINGLE_VALUED_KINDS


# ---------------------------------------------------------------------------
# Time
# ---------------------------------------------------------------------------


def utc_now() -> str:
    """Current instant in the one format the store compares lexically.

    Microsecond precision matters: forget tombstones compare the instant of
    deletion with the creation time of cited evidence, and two user actions in
    the same second must not collapse into one.
    """
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def normalize_timestamp(value: object) -> str | None:
    """Return an ISO-8601 UTC timestamp, or None when the input has no time.

    Unknown times stay unknown: a caller must not substitute the current time
    for a historical record whose date was lost.
    """
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        for pattern in ("%Y-%m-%d %H:%M", "%Y-%m-%d", "%Y/%m/%d %H:%M"):
            try:
                parsed = datetime.strptime(text, pattern)
            except ValueError:
                continue
            break
        else:
            return None
    if parsed.tzinfo is None:
        # Legacy records carry no zone. Keeping the wall clock is honest;
        # inventing an offset would fabricate a historical instant.
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).isoformat(timespec="seconds")


# ---------------------------------------------------------------------------
# Text
# ---------------------------------------------------------------------------

_CJK_RE = re.compile(r"[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uac00-\ud7af\uf900-\ufaff]")
_WORD_RE = re.compile(r"\w+", re.UNICODE)
_ASCII_WORD_RE = re.compile(r"[a-z0-9_]+")
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_MAX_TOKEN_CHARS = 64
_MAX_FACT_TEXT_CHARS = 2_000


def normalize_text(value: str) -> str:
    """Case/width/whitespace-insensitive text used for comparison."""
    normalized = unicodedata.normalize("NFKC", value).casefold()
    return " ".join(normalized.split())


def normalize_key(value: str) -> str:
    """Normalise a subject or predicate into a stable snake_case key."""
    normalized = unicodedata.normalize("NFKC", value).strip().casefold()
    key = re.sub(r"[^\w]+", "_", normalized).strip("_")
    return key[:120]


def normalize_scope(value: str) -> str:
    """Normalise a memory scope such as ``global`` or ``project:yoreni``."""
    scope = " ".join(unicodedata.normalize("NFKC", value).split()).casefold()
    return scope or "global"


def _collapse(value: str) -> str:
    return " ".join(_CONTROL_RE.sub("", value).split())


def clean_value(value: str) -> str:
    """Remove control characters and bound one fact's rendered text."""
    return _collapse(value)[:_MAX_FACT_TEXT_CHARS]


def content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def fts_tokens(text: str) -> list[str]:
    """Split text into FTS5 tokens, one token per CJK character.

    SQLite's ``unicode61`` tokenizer treats a run of CJK as a single token,
    which makes substring queries unusable. Indexing and querying both use this
    function, so a Chinese query matches at character granularity without a
    second tokenizer or an extension.
    """
    tokens: list[str] = []
    normalized = unicodedata.normalize("NFKC", text).casefold()
    for match in _WORD_RE.finditer(normalized):
        word = match.group(0)
        if not _CJK_RE.search(word):
            if len(word) <= _MAX_TOKEN_CHARS:
                tokens.append(word)
            continue
        pending: list[str] = []
        for char in word:
            if _CJK_RE.match(char):
                if pending:
                    tokens.append("".join(pending))
                    pending = []
                tokens.append(char)
            else:
                pending.append(char)
        if pending:
            tokens.append("".join(pending))
    return [token for token in tokens if token]


def fts_index_text(*parts: str) -> str:
    """Build the stored token stream for FTS5 (space separated tokens)."""
    tokens: list[str] = []
    for part in parts:
        tokens.extend(fts_tokens(part))
    return " ".join(tokens)


def fts_match_query(query: str) -> str | None:
    """Build a safe FTS5 MATCH expression, or None when nothing is searchable."""
    seen: list[str] = []
    for token in fts_tokens(query):
        if token not in seen:
            seen.append(token)
    if not seen:
        return None
    terms: list[str] = []
    for token in seen[:32]:
        escaped = token.replace('"', '""')
        if _ASCII_WORD_RE.fullmatch(token) and len(token) > 1:
            terms.append(f'"{escaped}"*')
        else:
            terms.append(f'"{escaped}"')
    return " OR ".join(terms)


def dedupe_key(scope: str, subject: str, predicate: str) -> str:
    return f"{normalize_scope(scope)}|{normalize_key(subject)}|{normalize_key(predicate)}"


_STOPWORDS = frozenset({
    "a", "about", "also", "an", "and", "are", "as", "at", "be", "been", "but",
    "by", "can", "could", "did", "do", "does", "for", "from", "had", "has",
    "have", "he", "her", "his", "i", "if", "in", "is", "it", "its", "just",
    "like", "my", "no", "not", "of", "on", "or", "our", "said", "says", "she",
    "should", "that", "the", "their", "them", "then", "these", "they", "this",
    "those", "to", "use", "used", "user", "users", "uses", "very", "was", "we",
    "were", "will", "with", "would", "yes", "you", "your",
})
_CJK_STOPWORDS = frozenset(
    "的了是在我你他她它们和与也就都而及或对从到把被让给为以于中上下这那有个"
    "不很会要可能着过吧呢啊"
)


_IDENTIFIER_RE = re.compile(r"\b\d+(?:\.\d+)+[a-z0-9_-]*\b", re.IGNORECASE)


def content_tokens(text: str) -> set[str]:
    """Tokens that can establish correspondence between a claim and evidence."""
    tokens: set[str] = set()
    for token in fts_tokens(text):
        if token in _STOPWORDS or token in _CJK_STOPWORDS:
            continue
        if len(token) == 1 and not token.isdigit() and not _CJK_RE.match(token):
            continue
        tokens.add(token)
    tokens.update(match.group(0).casefold() for match in _IDENTIFIER_RE.finditer(text))
    return tokens


_USER_DOMAIN_KINDS = frozenset({
    FactKind.USER_FACT,
    FactKind.USER_PREFERENCE,
    FactKind.GOAL,
    FactKind.DECISION,
})
_LEGACY_SOURCE_TYPES = frozenset({
    SourceType.EXTERNAL_DOCUMENT,
    SourceType.LEGACY_IMPORT,
})


def validate_claim_support(draft: FactDraft, cited: Sequence["Evidence"]) -> None:
    """Reject citations that cannot plausibly support the claim.

    A user request is authority for what the user wants, not proof of an
    external state. World facts therefore need a verified tool result, an
    imported document, or a user statement that actually mentions the claim.
    """
    if not cited:
        raise MemorySchemaError("a fact must cite at least one evidence item")

    def mentions(item: "Evidence") -> bool:
        return bool(content_tokens(draft.text) & content_tokens(item.content))

    def user_mentions(item: "Evidence") -> bool:
        """A user statement needs more than a shared topic word for a world fact."""
        shared = content_tokens(draft.text) & content_tokens(item.content)
        if not shared:
            return False
        if any(any(char.isdigit() for char in token) for token in shared):
            return True
        return len(shared) >= 2

    user_items = [
        item for item in cited
        if item.kind in {EvidenceKind.USER_MESSAGE, EvidenceKind.LEGACY_ENTRY}
        and item.source_type in {SourceType.USER_STATEMENT, SourceType.USER_CORRECTION}
    ]
    if draft.kind in _USER_DOMAIN_KINDS:
        if user_items:
            return
        if any(mentions(item) for item in cited):
            return
        raise MemorySchemaError(
            "the cited evidence does not mention the user fact it is meant to support"
        )

    # An inference or a failed tool call carries no proof of external state,
    # but the cited text still has to be about the claimed subject.
    if draft.source_type in {SourceType.AGENT_INFERENCE, SourceType.UNVERIFIED}:
        if any(mentions(item) for item in cited):
            return
        raise MemorySchemaError(
            "an inferred fact must be supported by the text of the evidence it cites"
        )

    verified = [
        item for item in cited
        if item.kind is EvidenceKind.TOOL_RESULT
        and item.source_type is SourceType.TOOL_RESULT
    ]
    if verified:
        if any(mentions(item) for item in verified):
            return
        raise MemorySchemaError(
            "the cited tool result does not mention the fact it is meant to support"
        )
    imported = [item for item in cited if item.source_type in _LEGACY_SOURCE_TYPES]
    if any(mentions(item) for item in imported) or any(
        user_mentions(item) for item in user_items
    ):
        return
    raise MemorySchemaError(
        "a world fact must cite a verified tool result, an imported source, "
        "or a user statement that mentions it"
    )


# ---------------------------------------------------------------------------
# Value objects
# ---------------------------------------------------------------------------


JsonValue = dict[str, Any] | list[Any] | str | int | float | bool | None


@dataclass(frozen=True, slots=True)
class FactDraft:
    """One candidate fact before it is reconciled with stored knowledge."""

    subject: str
    predicate: str
    text: str
    kind: FactKind
    scope: str = "global"
    sources: tuple[str, ...] = ()
    source_type: SourceType = SourceType.UNVERIFIED
    observed_at: str | None = None
    valid_from: str | None = None
    valid_to: str | None = None
    supersedes: str | None = None
    attributes: dict[str, JsonValue] = field(default_factory=dict)
    confidence: float | None = None

    def normalized(self) -> "FactDraft":
        text = _collapse(self.text)
        if not text:
            raise MemorySchemaError("fact text is empty after normalisation")
        if len(text) > _MAX_FACT_TEXT_CHARS:
            raise MemorySchemaError(
                f"fact text exceeds {_MAX_FACT_TEXT_CHARS} characters"
            )
        if not normalize_key(self.subject):
            raise MemorySchemaError("fact subject is empty")
        if not normalize_key(self.predicate):
            raise MemorySchemaError("fact predicate is empty")
        observed = normalize_timestamp(self.observed_at) or utc_now()
        valid_from = normalize_timestamp(self.valid_from) or observed
        valid_to = normalize_timestamp(self.valid_to)
        confidence = self.confidence
        if confidence is not None and not 0.0 <= confidence <= 1.0:
            confidence = None
        kind = self.kind if isinstance(self.kind, FactKind) else FactKind(str(self.kind))
        source_type = (
            self.source_type
            if isinstance(self.source_type, SourceType)
            else SourceType(str(self.source_type))
        )
        return FactDraft(
            subject=normalize_key(self.subject),
            predicate=normalize_key(self.predicate),
            text=text,
            kind=kind,
            scope=normalize_scope(self.scope),
            sources=tuple(dict.fromkeys(source for source in self.sources if source)),
            source_type=source_type,
            observed_at=observed,
            valid_from=valid_from,
            valid_to=valid_to,
            supersedes=self.supersedes,
            attributes=dict(self.attributes),
            confidence=confidence,
        )


@dataclass(frozen=True, slots=True)
class SourceRef:
    """One piece of evidence supporting a fact."""

    evidence_id: str
    source_type: SourceType
    quote: str
    run_id: str | None
    event_seq: int | None
    observed_at: str | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "evidence_id": self.evidence_id,
            "source_type": self.source_type.value,
            "quote": self.quote,
            "run_id": self.run_id,
            "event_seq": self.event_seq,
            "observed_at": self.observed_at,
        }


@dataclass(frozen=True, slots=True)
class Fact:
    """One stored version of a fact."""

    id: str
    subject: str
    predicate: str
    text: str
    kind: FactKind
    scope: str
    status: FactStatus
    source_type: SourceType
    observed_at: str | None
    valid_from: str
    valid_to: str | None
    supersedes: str | None
    superseded_by: str | None
    created_at: str
    updated_at: str
    confidence: float | None = None
    attributes: dict[str, JsonValue] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "subject": self.subject,
            "predicate": self.predicate,
            "value": {"text": self.text, "attributes": self.attributes},
            "kind": self.kind.value,
            "scope": self.scope,
            "status": self.status.value,
            "source_type": self.source_type.value,
            "observed_at": self.observed_at,
            "valid_from": self.valid_from,
            "valid_to": self.valid_to,
            "supersedes": self.supersedes,
            "superseded_by": self.superseded_by,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "confidence": self.confidence,
        }


@dataclass(frozen=True, slots=True)
class MemoryHit:
    """One ranked retrieval result with provenance attached."""

    fact: Fact
    score: float
    signals: tuple[str, ...]
    sources: tuple[SourceRef, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "fact_id": self.fact.id,
            "subject": self.fact.subject,
            "predicate": self.fact.predicate,
            "text": self.fact.text,
            "kind": self.fact.kind.value,
            "scope": self.fact.scope,
            "status": self.fact.status.value,
            "source_type": self.fact.source_type.value,
            "observed_at": self.fact.observed_at,
            "valid_from": self.fact.valid_from,
            "valid_to": self.fact.valid_to,
            "score": round(self.score, 6),
            "signals": list(self.signals),
            "sources": [source.to_dict() for source in self.sources],
        }


@dataclass(frozen=True, slots=True)
class Evidence:
    """One durable observation from a Run, independent of transcript shape."""

    id: str
    run_id: str
    event_seq: int | None
    kind: EvidenceKind
    role: str
    content: str
    content_hash: str
    source_type: SourceType
    trust: TrustLevel
    observed_at: str | None
    created_at: str = ""
    tool_name: str | None = None
    suppressed: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "run_id": self.run_id,
            "event_seq": self.event_seq,
            "kind": self.kind.value,
            "role": self.role,
            "content": self.content,
            "content_hash": self.content_hash,
            "source_type": self.source_type.value,
            "trust": self.trust.value,
            "observed_at": self.observed_at,
            "tool_name": self.tool_name,
            "suppressed": self.suppressed,
        }

@dataclass(frozen=True, slots=True)
class MemoryStats:
    """Observable backlog and consolidation state."""

    active_facts: int
    disputed_facts: int
    dormant_facts: int
    evidence_items: int
    pending_jobs: int
    failed_jobs: int
    unconsolidated_facts: int
    last_dream_at: str | None
    last_dream_status: str | None
    last_dream_error: str | None
    last_dream_changes: int
    last_dream_tokens: int


@dataclass(frozen=True, slots=True)
class SearchFilter:
    """Scope, status and temporal constraints for one retrieval request."""

    query: str
    scope: str | None = None
    subjects: tuple[str, ...] = ()
    kinds: tuple[FactKind, ...] = ()
    statuses: tuple[FactStatus, ...] = (FactStatus.ACTIVE,)
    valid_at: str | None = None
    limit: int = 8

    def normalized(self) -> "SearchFilter":
        try:
            kinds = tuple(
                kind if isinstance(kind, FactKind) else FactKind(str(kind))
                for kind in self.kinds
            )
            statuses = tuple(
                status if isinstance(status, FactStatus) else FactStatus(str(status))
                for status in self.statuses
            )
        except ValueError as exc:
            raise MemorySchemaError(f"unknown retrieval filter value: {exc}") from exc
        return SearchFilter(
            query=" ".join(self.query.split())[:400],
            scope=normalize_scope(self.scope) if self.scope else None,
            subjects=tuple(normalize_key(subject) for subject in self.subjects),
            kinds=kinds,
            statuses=statuses,
            valid_at=normalize_timestamp(self.valid_at),
            limit=max(1, min(self.limit, 50)),
        )


def parse_json_object(raw: str) -> dict[str, Any]:
    """Parse a strict JSON object from a model response.

    A single surrounding code fence is formatting, not content; anything else
    that fails to parse is an error rather than a guess.
    """
    text = raw.strip()
    if text.startswith("```") and text.endswith("```"):
        inner = text[3:-3]
        first_newline = inner.find("\n")
        if first_newline != -1:
            text = inner[first_newline + 1:]
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        raise MemorySchemaError(f"model output is not valid JSON: {exc.msg}") from exc
    if not isinstance(parsed, dict):
        raise MemorySchemaError("model output must be a JSON object")
    return cast(dict[str, Any], parsed)


def require_object(value: object, *, field_name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise MemorySchemaError(f"{field_name} must be an object")
    return cast(dict[str, Any], value)


def require_list(value: object, *, field_name: str) -> list[Any]:
    if not isinstance(value, list):
        raise MemorySchemaError(f"{field_name} must be a list")
    return cast(list[Any], value)


def require_str(value: object, *, field_name: str, allow_empty: bool = False) -> str:
    if not isinstance(value, str):
        raise MemorySchemaError(f"{field_name} must be a string")
    if not allow_empty and not value.strip():
        raise MemorySchemaError(f"{field_name} must not be empty")
    return value


def optional_str(value: object, *, field_name: str) -> str | None:
    if value is None:
        return None
    return require_str(value, field_name=field_name, allow_empty=False)


def parse_kind(value: object) -> FactKind:
    text = require_str(value, field_name="kind")
    if text not in _ALL_KINDS:
        raise MemorySchemaError(f"unknown fact kind {text!r}")
    return FactKind(text)


def parse_status(value: object) -> FactStatus:
    text = require_str(value, field_name="status")
    if text not in _ALL_STATUSES:
        raise MemorySchemaError(f"unknown fact status {text!r}")
    return FactStatus(text)


def parse_source_type(value: object) -> SourceType:
    text = require_str(value, field_name="source_type")
    if text not in _ALL_SOURCE_TYPES:
        raise MemorySchemaError(f"unknown source type {text!r}")
    return SourceType(text)
