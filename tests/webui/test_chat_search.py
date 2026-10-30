"""Server-side chat search: ranking, excerpts, pagination, and its routes."""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any, Literal
from urllib.parse import urlencode

import pytest
from websockets.datastructures import Headers
from websockets.http11 import Request

from yoreni.agent.state import AgentInput, AgentStore, ConversationRow
from yoreni.providers.base import LLMProvider, LLMResponse
from yoreni.webui import chat_search
from yoreni.webui.chat_search import (
    ChatSearchError,
    build_search_prompt,
    parse_search_candidates,
    parse_search_response,
    search_conversation,
    semantic_candidates,
    semantic_chat_search,
    semantic_hit_payloads,
    visible_user_text,
)
from yoreni.webui.gateway_tokens import GatewayTokenStore
from yoreni.webui.ws_http import GatewayHTTPHandler


def _row(
    message_id: str,
    role: Literal["user", "assistant"],
    text: str,
    created_at_ms: int = 1,
    event_seq: int = 1,
) -> ConversationRow:
    return ConversationRow(
        id=message_id, role=role, text=text,
        created_at_ms=created_at_ms, event_seq=event_seq,
    )


def _rows() -> list[ConversationRow]:
    return [
        _row("m1", "user", "How do I deploy the gateway?", 1, 1),
        _row("m2", "assistant", "Run the install script, then restart.", 2, 1),
        _row("m3", "user", "The gawtey failed with a port error", 3, 2),
        _row("m4", "user", "报错说端口被占用了", 4, 3),
    ]


def test_exact_matches_rank_before_fuzzy_matches() -> None:
    rows = [
        _row("old", "assistant", "The gateway restarts in ten seconds.", 1),
        _row("typo", "user", "The gatewy restarts too.", 2),
        _row("new", "user", "Gateway logs look clean.", 3),
    ]

    exact, fuzzy = search_conversation(rows, "gateway")

    assert [hit["id"] for hit in exact] == ["new", "old"]
    assert [hit["id"] for hit in fuzzy] == ["typo"]


def test_search_tolerates_a_typo_and_matches_chinese_phrases() -> None:
    typo_exact, typo_fuzzy = search_conversation(
        [_row("d", "assistant", "Follow the deployment steps.", 1)], "deploymnt",
    )
    chinese_exact, chinese_fuzzy = search_conversation(
        [_row("c", "user", "报错说端口被占用了", 1)], "端口被占用",
    )

    assert typo_exact == []
    assert [hit["id"] for hit in typo_fuzzy] == ["d"]
    assert [hit["id"] for hit in chinese_exact] == ["c"]
    assert chinese_fuzzy == []
    excerpt = chinese_exact[0]["excerpt"]
    start, end = chinese_exact[0]["spans"][0]
    assert excerpt[start:end] == "端口被占用"


def test_excerpts_keep_the_match_visible_and_hide_the_quoted_block() -> None:
    text = f"{'a' * 200} deployment {'b' * 200}"
    quoted = "> [!QUOTE] m2\n> Run the installer.\n\nwhere is the config?"

    exact, _ = search_conversation([_row("d", "assistant", text, 1)], "deployment")
    excerpt = exact[0]["excerpt"]
    start, end = exact[0]["spans"][0]

    assert excerpt.startswith("…")
    assert len(excerpt) <= 152
    assert excerpt[start:end] == "deployment"
    assert visible_user_text(quoted) == "where is the config?"
    quoted_exact, quoted_fuzzy = search_conversation([_row("u", "user", quoted, 1)], "installer")
    assert quoted_exact == [] and quoted_fuzzy == []


def test_semantic_candidates_skip_local_matches_and_use_newest_first() -> None:
    candidates = semantic_candidates(_rows(), {"m1"}, limit=3)

    assert [candidate["id"] for candidate in candidates] == ["m4", "m3", "m2"]


def test_semantic_hits_resolve_rows_without_repeating_local_matches() -> None:
    hits = semantic_hit_payloads(_rows(), ["m4", "m1", "missing", "m4"], {"m1"})

    assert [hit["id"] for hit in hits] == ["m4"]
    assert hits[0]["spans"] == []
    assert hits[0]["role"] == "user"


def test_search_rejects_empty_queries() -> None:
    with pytest.raises(ChatSearchError):
        search_conversation(_rows(), "   ")


class _Provider(LLMProvider):
    def __init__(
        self,
        response: LLMResponse | None = None,
        *,
        delay: float = 0.0,
        error: Exception | None = None,
    ) -> None:
        super().__init__(provider_name="test")
        self.response = response
        self.delay = delay
        self.error = error
        self.calls: list[dict[str, Any]] = []

    async def chat(self, messages, **kwargs) -> LLMResponse:
        self.calls.append({"messages": messages, **kwargs})
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.error is not None:
            raise self.error
        return self.response or LLMResponse(content="")

    def get_default_model(self) -> str:
        return "test-model"


def _candidates() -> list[dict[str, str]]:
    return [
        {"id": "m1", "role": "user", "text": "How do I deploy the gateway?"},
        {"id": "m2", "role": "assistant", "text": "Run the install script, then restart."},
        {"id": "m3", "role": "user", "text": "报错说端口被占用了"},
    ]


def test_candidates_drop_malformed_rows_and_cap_excerpts() -> None:
    candidates = parse_search_candidates([
        "not a row",
        {"id": "m1", "role": "assistant", "text": "  spaced\n\ntext  "},
        {"id": "m1", "role": "user", "text": "duplicate"},
        {"id": "", "role": "user", "text": "empty id"},
        {"id": "m2", "role": "user", "text": ""},
        {"id": "m3", "role": "system", "text": "x" * 500},
    ])

    assert [candidate.id for candidate in candidates] == ["m1", "m3"]
    assert candidates[0].text == "spaced text"
    assert candidates[0].role == "assistant"
    assert candidates[1].role == "user"
    assert len(candidates[1].text) == 400


def test_prompt_numbers_excerpts_and_keeps_the_query() -> None:
    candidates = parse_search_candidates(_candidates())
    messages = build_search_prompt("deploy", candidates)

    assert messages[0]["role"] == "system"
    assert "Query: deploy" in messages[1]["content"]
    assert "[1] user: How do I deploy the gateway?" in messages[1]["content"]
    assert "[3] user: 报错说端口被占用了" in messages[1]["content"]


def test_response_accepts_fenced_numbers_raw_ids_and_ignores_invalid_entries() -> None:
    candidates = parse_search_candidates(_candidates())

    assert parse_search_response('```json\n{"ids":[1,3]}\n```', candidates) == ["m1", "m3"]
    assert parse_search_response('["m2", 9, 2, 2]', candidates) == ["m2"]
    assert parse_search_response('{"ids":[true,3,0,-1,1]}', candidates) == ["m3", "m1"]
    assert parse_search_response("not json", candidates) == []


async def test_semantic_search_returns_model_ranked_ids() -> None:
    provider = _Provider(LLMResponse(content='{"ids":[2,3]}'))

    ids = await semantic_chat_search(
        "  部署报错  ",
        _candidates(),
        provider=provider,
        model="test-model",
    )

    assert ids == ["m2", "m3"]
    call = provider.calls[0]
    assert call["model"] == "test-model"
    assert call["temperature"] == 0.0
    assert "Query: 部署报错" in call["messages"][1]["content"]


async def test_semantic_search_rejects_empty_queries() -> None:
    with pytest.raises(ChatSearchError):
        await semantic_chat_search("   ", _candidates(), provider=_Provider(), model="test-model")


async def test_semantic_search_times_out(monkeypatch) -> None:
    monkeypatch.setattr(chat_search, "SEMANTIC_SEARCH_TIMEOUT_S", 0.01)
    provider = _Provider(LLMResponse(content="{}"), delay=0.05)

    with pytest.raises(TimeoutError):
        await semantic_chat_search("deploy", _candidates(), provider=provider, model="test-model")


def _seed(tmp_path) -> AgentStore:
    store = AgentStore(tmp_path)
    first = store.admit(AgentInput(
        source="webui", content="How do I deploy the gateway?",
        principal="owner", event_id="u1",
    ))
    store.start(first, [])
    store.append_entry(first, "message", {
        "id": "a1", "content": "Run the install script, then restart.", "created_at_ms": 2,
    })
    store.finish(first, [], {"content": "", "stop_reason": "stop"})
    second = store.admit(AgentInput(
        source="webui", content="报错说端口被占用了",
        principal="owner", event_id="u2",
    ))
    store.finish(second, [], {"content": "", "stop_reason": "stop"})
    return store


def test_conversation_rows_cover_prompts_entries_and_legacy_answers(tmp_path) -> None:
    store = _seed(tmp_path)
    legacy = store.admit(AgentInput(
        source="webui", content="Old turn", principal="owner", event_id="u3",
    ))
    store.finish(legacy, [], {"content": "The old answer", "stop_reason": "stop"})

    rows = store.conversation_rows()
    by_id = {row["id"]: row for row in rows}

    assert set(by_id) == {"u1", "a1", "u2", "u3", f"{legacy}:answer"}
    assert by_id["u1"]["role"] == "user"
    assert by_id["a1"]["role"] == "assistant"
    assert by_id["a1"]["created_at_ms"] == 2
    assert by_id[f"{legacy}:answer"]["text"] == "The old answer"


def test_webui_history_pages_backwards_before_a_sequence(tmp_path) -> None:
    store = _seed(tmp_path)
    legacy = store.admit(AgentInput(
        source="webui", content="Old turn", principal="owner", event_id="u3",
    ))
    store.finish(legacy, [], {"content": "The old answer", "stop_reason": "stop"})

    latest = store.webui_history(limit=2)
    older = store.webui_history(limit=2, before_seq=latest[0]["event_seq"])

    assert [run["id"] for run in latest] == [_seed_ids(store)[1], legacy]
    assert [run["id"] for run in older] == [_seed_ids(store)[0]]
    assert store.webui_history(limit=2, before_seq=0) == []


def _seed_ids(store: AgentStore) -> list[str]:
    with store.connection() as db:
        rows = db.execute("SELECT id FROM runs ORDER BY event_seq").fetchall()
    return [str(row["id"]) for row in rows]


def _handler(tmp_path, action=None) -> GatewayHTTPHandler:
    handler = object.__new__(GatewayHTTPHandler)
    handler.skills_workspace_path = tmp_path
    tokens = GatewayTokenStore(max_tokens=4)
    tokens.api_tokens["fixture-token"] = time.monotonic() + 60
    handler.tokens = tokens
    handler.chat_search_action = action
    return handler


def _request(query: str, path: str = "/api/webui/chat-search") -> Request:
    return Request(
        f"{path}?{urlencode({'q': query})}",
        Headers({"Authorization": "Bearer fixture-token"}),
    )


async def test_local_route_returns_exact_and_fuzzy_hits(tmp_path) -> None:
    _seed(tmp_path)

    response = await _handler(tmp_path)._handle_chat_search(_request("deploy"))

    assert response.status_code == 200
    payload = json.loads(response.body)
    assert [hit["id"] for hit in payload["exact"]] == ["u1"]
    assert payload["fuzzy"] == []


async def test_semantic_route_builds_candidates_on_the_server(tmp_path) -> None:
    _seed(tmp_path)
    seen: dict[str, Any] = {}

    async def action(payload: dict[str, Any]) -> dict[str, Any]:
        seen.update(payload)
        return {"ids": ["u2"]}

    response = await _handler(tmp_path, action)._handle_chat_search(
        _request("deploy", "/api/webui/chat-search/semantic"), semantic=True,
    )

    assert response.status_code == 200
    assert [hit["id"] for hit in json.loads(response.body)["hits"]] == ["u2"]
    assert seen["query"] == "deploy"
    assert [message["id"] for message in seen["messages"]] == ["u2", "a1"]


async def test_routes_report_invalid_input_and_failures(tmp_path) -> None:
    _seed(tmp_path)

    async def unavailable(payload: dict[str, Any]) -> dict[str, Any]:
        raise RuntimeError("semantic search failed")

    async def slow(payload: dict[str, Any]) -> dict[str, Any]:
        raise TimeoutError

    async def broken(payload: dict[str, Any]) -> dict[str, Any]:
        raise ValueError("query is required")

    handler = _handler(tmp_path, unavailable)
    assert (await handler._handle_chat_search(_request(""))).status_code == 400
    assert (await handler._handle_chat_search(_request("x"))).status_code == 200
    assert (await handler._handle_chat_search(_request("x"), semantic=True)).status_code == 502
    assert (await _handler(tmp_path, slow)._handle_chat_search(
        _request("x"), semantic=True,
    )).status_code == 504
    assert (await _handler(tmp_path, broken)._handle_chat_search(
        _request("x"), semantic=True,
    )).status_code == 400
    assert (await _handler(tmp_path)._handle_chat_search(
        _request("x"), semantic=True,
    )).status_code == 503


async def test_routes_require_a_token(tmp_path) -> None:
    _seed(tmp_path)
    request = Request("/api/webui/chat-search?q=x", Headers())

    assert (await _handler(tmp_path)._handle_chat_search(request)).status_code == 401
