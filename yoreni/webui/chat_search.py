"""Search the conversation for the WebUI search overlay.

The gateway owns the durable store, so both local tiers run here instead of in
the browser: a client that just opened does not need to load history before it
can search. Exact matches rank first, fuzzy matches follow, and the current
model ranks the remaining excerpts by meaning.
"""

from __future__ import annotations

import asyncio
import re
import unicodedata
from dataclasses import dataclass
from typing import Any, Literal, Sequence, TypedDict, cast

import json_repair
from loguru import logger

from yoreni.agent.state import ConversationRow
from yoreni.providers.base import LLMProvider

MAX_QUERY_CHARS = 200
MAX_CANDIDATES = 60
MAX_EXCERPT_CHARS = 400
MAX_RESULTS = 6
MAX_HITS_PER_TIER = 8
MIN_FUZZY_QUERY_LENGTH = 2
SEMANTIC_CANDIDATE_LIMIT = 40
SEMANTIC_SEARCH_TIMEOUT_S = 20.0
SNIPPET_CHARS = 150

_WORD_PATTERN = re.compile(r"\w+", re.UNICODE)
_CJK_PATTERN = re.compile(r"[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uac00-\ud7af\uf900-\ufaff]")
_CODE_FENCE = re.compile(r"^```(?:json)?\s*(.*?)\s*```$", re.DOTALL | re.IGNORECASE)
_QUOTED_CONTEXT_MARKER = "> [!QUOTE]"
_QUOTED_MESSAGE_ID = re.compile(r"[A-Za-z0-9._:-]{1,128}")

_SYSTEM_PROMPT = (
    "You are the semantic layer of a chat search box. Given a query and numbered "
    "excerpts from the user's conversation, choose the excerpts whose meaning matches "
    "the query, including synonyms, paraphrases, and other languages. Skip excerpts "
    "that only share words without matching meaning. Return JSON only, shaped like "
    '{"ids":[1,4]}, with an empty list when nothing matches. Never explain.'
)


class ChatSearchError(ValueError):
    """Invalid search input; the route answers with 400."""


class ChatSearchHit(TypedDict):
    """One result row, already trimmed to the excerpt the overlay renders."""

    id: str
    role: Literal["user", "assistant"]
    excerpt: str
    spans: list[list[int]]
    created_at_ms: int


@dataclass(frozen=True)
class ChatSearchCandidate:
    id: str
    role: str
    text: str


def normalize_search_query(raw: object) -> str:
    if not isinstance(raw, str):
        raise ChatSearchError("query must be a string")
    query = " ".join(raw.split())[:MAX_QUERY_CHARS]
    if not query:
        raise ChatSearchError("query is required")
    return query


def normalize_search_text(value: str) -> str:
    """Case, width, and whitespace differences should not decide a match."""
    return " ".join(unicodedata.normalize("NFKC", value).lower().split())


def visible_user_text(content: str) -> str:
    """Drop the quoted block a client inlined, so search sees the typed message.

    The WebUI sends the model a quote prefixed with ``> [!QUOTE] <id>`` and the
    reader sees only the body below it. Searching the whole payload would match
    text that is not on screen and produce unhighlightable excerpts.
    """
    normalized = content.replace("\r\n", "\n").replace("\r", "\n")
    marker_end = normalized.find("\n")
    if not normalized.startswith(_QUOTED_CONTEXT_MARKER) or marker_end == -1:
        return content
    marker_id = normalized[len(_QUOTED_CONTEXT_MARKER):marker_end].strip()
    if marker_id and _QUOTED_MESSAGE_ID.fullmatch(marker_id) is None:
        return content
    quote_start = marker_end + 1
    separator = normalized.find("\n\n", quote_start)
    quote_block = normalized[quote_start:] if separator == -1 else normalized[quote_start:separator]
    quote_lines = quote_block.split("\n")
    if any(line != ">" and not line.startswith("> ") for line in quote_lines):
        return content
    quoted = "\n".join("" if line == ">" else line[2:] for line in quote_lines).strip()
    if not quoted:
        return content
    return "" if separator == -1 else normalized[separator + 2:]


def search_text(row: ConversationRow) -> str:
    """One-line text for matching and excerpts, so offsets stay valid."""
    text = visible_user_text(row["text"]) if row["role"] == "user" else row["text"]
    return " ".join(text.split())


def query_tokens(query: str) -> list[str]:
    """Split a query into Latin words and CJK bigrams, so Chinese matches by phrase."""
    tokens: set[str] = set()
    for run in _WORD_PATTERN.findall(query):
        if _CJK_PATTERN.search(run) is None:
            tokens.add(run)
            continue
        characters = list(run)
        if len(characters) == 1:
            tokens.add(run)
            continue
        tokens.update(characters[index] + characters[index + 1]
                      for index in range(len(characters) - 1))
    return list(tokens)


def bounded_distance(left: str, right: str, limit: int) -> int | None:
    """Levenshtein distance, abandoned once it can no longer fit the limit."""
    if abs(len(left) - len(right)) > limit:
        return None
    previous = list(range(len(right) + 1))
    for left_index in range(1, len(left) + 1):
        current = [left_index]
        row_minimum = left_index
        for right_index in range(1, len(right) + 1):
            substitution = previous[right_index - 1] + (
                left[left_index - 1] != right[right_index - 1]
            )
            value = min(previous[right_index] + 1, current[right_index - 1] + 1, substitution)
            current.append(value)
            row_minimum = min(row_minimum, value)
        if row_minimum > limit:
            return None
        previous = current
    distance = previous[len(right)]
    return distance if distance <= limit else None


def token_similarity(token: str, normalized: str, words: Sequence[str]) -> float:
    if token in normalized:
        return 1.0
    if len(token) < 2:
        return 0.0
    # Typo tolerance should not glue unrelated long words together.
    limit = 1 if len(token) <= 4 else 2
    best = 0.0
    for word in words:
        if abs(len(word) - len(token)) > limit:
            continue
        if word.startswith(token) or token.startswith(word):
            best = max(best, 0.92)
            continue
        distance = bounded_distance(token, word, limit)
        if distance is not None:
            best = max(best, 1 - distance / max(len(token), len(word)))
    return best


def find_spans(text: str, needles: Sequence[str]) -> list[tuple[int, int]]:
    lower = text.lower()
    spans: list[tuple[int, int]] = []
    for needle in needles:
        normalized = needle.lower()
        if not normalized:
            continue
        start = 0
        while len(spans) < 12:
            index = lower.find(normalized, start)
            if index < 0:
                break
            spans.append((index, index + len(normalized)))
            start = index + len(normalized)
    return sorted(spans, key=lambda span: span[0])


def excerpt_with_spans(
    text: str,
    spans: Sequence[tuple[int, int]],
    max_length: int = SNIPPET_CHARS,
) -> tuple[str, list[tuple[int, int]]]:
    """Clip a single-line excerpt around the first match and shift its spans."""
    if len(text) <= max_length:
        return text, list(spans)
    anchor = spans[0][0] if spans else 0
    start = max(0, min(anchor - max_length // 3, len(text) - max_length))
    end = min(len(text), start + max_length)
    prefix = "…" if start > 0 else ""
    suffix = "…" if end < len(text) else ""
    shifted = [
        (max(0, span_start - start) + len(prefix), min(end, span_end) - start + len(prefix))
        for span_start, span_end in spans
        if span_end > start and span_start < end
    ]
    return f"{prefix}{text[start:end]}{suffix}", shifted


def hit_payload(row: ConversationRow, spans: Sequence[tuple[int, int]]) -> ChatSearchHit:
    excerpt, shifted = excerpt_with_spans(search_text(row), spans)
    return ChatSearchHit(
        id=row["id"],
        role=row["role"],
        excerpt=excerpt,
        spans=[[start, end] for start, end in shifted],
        created_at_ms=row["created_at_ms"] or row["event_seq"] * 2,
    )


def search_conversation(
    rows: Sequence[ConversationRow],
    raw_query: object,
) -> tuple[list[ChatSearchHit], list[ChatSearchHit]]:
    """Rank transcript rows for a query: exact phrase matches, then typo-tolerant ones.

    Semantic results are requested separately, so this function only owns the
    two tiers a local index can answer.
    """
    query = normalize_search_query(raw_query)
    normalized_query = normalize_search_text(query)
    exact: list[tuple[float, ChatSearchHit]] = []
    fuzzy: list[tuple[float, ChatSearchHit]] = []
    if not normalized_query:
        return [], []
    indexed: list[tuple[ConversationRow, str, list[str]]] = []
    for row in rows:
        normalized = normalize_search_text(search_text(row))
        if normalized:
            indexed.append((row, normalized, _WORD_PATTERN.findall(normalized)))
    if len(normalized_query) >= MIN_FUZZY_QUERY_LENGTH:
        tokens = query_tokens(normalized_query)
        needles = [query, *tokens]
        for row, normalized, _ in indexed:
            if normalized_query not in normalized:
                continue
            text = search_text(row)
            exact.append((1.0, hit_payload(row, find_spans(text, needles))))
        for row, normalized, words in indexed:
            if normalized_query in normalized:
                continue
            similarities = [token_similarity(token, normalized, words) for token in tokens]
            if any(similarity < 0.55 for similarity in similarities):
                continue
            score = sum(similarities) / max(1, len(similarities))
            if score < 0.62:
                continue
            fuzzy.append((score, hit_payload(row, find_spans(search_text(row), tokens))))

    def ranked(hits: list[tuple[float, ChatSearchHit]]) -> list[ChatSearchHit]:
        hits.sort(key=lambda item: (-item[0], -item[1]["created_at_ms"]))
        return [hit for _, hit in hits[:MAX_HITS_PER_TIER]]

    return ranked(exact), ranked(fuzzy)


def semantic_candidates(
    rows: Sequence[ConversationRow],
    excluded: set[str],
    limit: int = SEMANTIC_CANDIDATE_LIMIT,
) -> list[dict[str, str]]:
    """Pick the newest rows the local tiers missed, newest first for the model."""
    selected: list[ChatSearchCandidate] = []
    for row in reversed(rows):
        if row["id"] in excluded:
            continue
        excerpt = " ".join(search_text(row).split())[:MAX_EXCERPT_CHARS]
        if not excerpt:
            continue
        selected.append(ChatSearchCandidate(id=row["id"], role=row["role"], text=excerpt))
        if len(selected) >= limit:
            break
    return [{"id": item.id, "role": item.role, "text": item.text} for item in selected]


def semantic_hit_payloads(
    rows: Sequence[ConversationRow],
    ids: Sequence[str],
    excluded: set[str],
) -> list[ChatSearchHit]:
    """Map the model's IDs back to rows without repeating a local match."""
    by_id = {row["id"]: row for row in rows}
    hits: list[ChatSearchHit] = []
    seen: set[str] = set()
    for message_id in ids:
        if message_id in excluded or message_id in seen:
            continue
        row = by_id.get(message_id)
        if row is None:
            continue
        seen.add(message_id)
        hits.append(hit_payload(row, ()))
        if len(hits) >= MAX_RESULTS:
            break
    return hits


def parse_search_candidates(raw: object) -> list[ChatSearchCandidate]:
    """Keep well-formed rows in the caller's order, capped for one model call."""
    if not isinstance(raw, list):
        raise ChatSearchError("messages must be a list")
    candidates: list[ChatSearchCandidate] = []
    seen: set[str] = set()
    for item in cast(list[object], raw):
        if not isinstance(item, dict):
            continue
        entry = cast(dict[str, Any], item)
        message_id = entry.get("id")
        text = entry.get("text")
        if not isinstance(message_id, str) or not message_id or len(message_id) > 128:
            continue
        if message_id in seen or not isinstance(text, str):
            continue
        excerpt = " ".join(text.split())[:MAX_EXCERPT_CHARS]
        if not excerpt:
            continue
        seen.add(message_id)
        candidates.append(ChatSearchCandidate(
            id=message_id,
            role="assistant" if entry.get("role") == "assistant" else "user",
            text=excerpt,
        ))
        if len(candidates) >= MAX_CANDIDATES:
            break
    return candidates


def build_search_prompt(query: str, candidates: list[ChatSearchCandidate]) -> list[dict[str, str]]:
    excerpts = "\n".join(
        f"[{index}] {candidate.role}: {candidate.text}"
        for index, candidate in enumerate(candidates, start=1)
    )
    return [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": f"Query: {query}\n\nExcerpts:\n{excerpts}"},
    ]


def parse_search_response(content: str | None, candidates: list[ChatSearchCandidate]) -> list[str]:
    """Accept numbered or raw message IDs in the model's JSON answer."""
    if not content:
        return []
    text = content.strip()
    fenced = _CODE_FENCE.match(text)
    if fenced:
        text = fenced.group(1).strip()
    try:
        parsed = json_repair.loads(text)
    except Exception:
        return []
    raw_ids = parsed.get("ids") if isinstance(parsed, dict) else parsed
    if not isinstance(raw_ids, list):
        return []
    by_id = {candidate.id: candidate for candidate in candidates}
    results: list[str] = []
    for value in cast(list[object], raw_ids):
        candidate: ChatSearchCandidate | None = None
        if isinstance(value, bool):
            continue
        if isinstance(value, int):
            index = value
            if 1 <= index <= len(candidates):
                candidate = candidates[index - 1]
        elif isinstance(value, str):
            stripped = value.strip()
            if stripped.isdigit():
                index = int(stripped)
                if 1 <= index <= len(candidates):
                    candidate = candidates[index - 1]
            elif stripped in by_id:
                candidate = by_id[stripped]
        if candidate is None or candidate.id in results:
            continue
        results.append(candidate.id)
        if len(results) >= MAX_RESULTS:
            break
    return results


async def semantic_chat_search(
    raw_query: object,
    raw_candidates: object,
    *,
    provider: LLMProvider,
    model: str,
) -> list[str]:
    """Ask the current model which unmatched rows mean the same as the query."""
    query = normalize_search_query(raw_query)
    candidates = parse_search_candidates(raw_candidates)
    if not candidates:
        return []
    try:
        from yoreni.credentials.manager import get_credential_manager

        response = await asyncio.wait_for(
            provider.chat(
                messages=get_credential_manager().redact_messages(build_search_prompt(query, candidates)),
                model=model,
                max_tokens=160,
                temperature=0.0,
            ),
            timeout=SEMANTIC_SEARCH_TIMEOUT_S,
        )
    except TimeoutError as exc:
        raise TimeoutError("semantic search timed out") from exc
    except Exception as exc:
        logger.warning("semantic chat search request failed: {}", type(exc).__name__)
        raise RuntimeError("semantic search failed") from exc
    if response.finish_reason == "error":
        logger.warning(
            "semantic chat search returned an error: {}/{}",
            response.error_kind,
            response.error_status_code,
        )
        raise RuntimeError("semantic search failed")
    return parse_search_response(response.content, candidates)
