"""Tests for replaying durable conversation rows into the WebUI history."""

from __future__ import annotations

from yoreni.agent.state import AgentInput, AgentStore
from yoreni.webui.outbound_wire import encode_history_entries


def _sign(path: str) -> dict[str, str] | None:
    return None if path.endswith("missing.png") else {"url": f"/api/media/sig/{path}", "name": path}


def test_message_rows_keep_the_live_frame_shape() -> None:
    frames = encode_history_entries(
        [
            {
                "seq": 1, "kind": "message",
                "payload": {
                    "id": "m-1", "content": "look at this", "media": ["chart.png", "missing.png"],
                    "reply_to": "msg-1", "quote": "look", "created_at_ms": 1_700_000_000_000,
                },
            },
            {
                "seq": 2, "kind": "reaction",
                "payload": {
                    "id": "r-1", "target": "msg-1", "emoji": "👀",
                    "created_at_ms": 1_700_000_000_500,
                },
            },
            {
                "seq": 3, "kind": "user",
                "payload": {"id": "msg-2", "content": "wait", "created_at_ms": 1_700_000_001_000},
            },
        ],
        sign_media=_sign,
    )

    assert frames == [
        {
            "event": "message", "id": "m-1", "text": "look at this",
            "created_at_ms": 1_700_000_000_000,
            "media_urls": [{"url": "/api/media/sig/chart.png", "name": "chart.png"}],
            "reply_to": "msg-1", "quote": "look",
        },
        {
            "event": "reaction", "id": "r-1", "target": "msg-1", "emoji": "👀",
            "created_at_ms": 1_700_000_000_500,
        },
        {
            "event": "user_message", "message_id": "msg-2", "text": "wait",
            "created_at_ms": 1_700_000_001_000,
        },
    ]


def test_unknown_rows_do_not_reach_the_client() -> None:
    assert encode_history_entries(
        [{"seq": 1, "kind": "trace", "payload": {"id": "x"}}],
        sign_media=_sign,
    ) == []


def test_history_carries_the_projection_version_and_ordered_rows(tmp_path) -> None:
    store = AgentStore(tmp_path)
    run_id = store.admit(AgentInput(
        source="websocket", content="hello", principal="owner", event_id="msg-1",
        channel="websocket", chat_id="yoreni",
    ))
    store.start(run_id, [])
    store.append_entry(run_id, "message", {"id": "m-1", "content": "hi", "created_at_ms": 1})
    store.append_entry(run_id, "user", {"id": "msg-2", "content": "again", "created_at_ms": 2})
    store.append_entry(run_id, "reaction", {"id": "r-1", "target": "msg-1", "emoji": "🙂", "created_at_ms": 3})

    run = store.webui_history()[0]

    assert run["projection"] == 1
    assert run["created_at_ms"] > 0
    assert [(entry["kind"], entry["payload"]["id"]) for entry in run["entries"]] == [
        ("message", "m-1"),
        ("user", "msg-2"),
        ("reaction", "r-1"),
    ]


def test_unstarted_run_has_no_projection(tmp_path) -> None:
    store = AgentStore(tmp_path)
    store.admit(AgentInput(
        source="websocket", content="queued", principal="owner", event_id="msg-1",
        channel="websocket", chat_id="yoreni",
    ))

    run = store.webui_history()[0]

    assert run["projection"] == 0
    assert run["entries"] == []
