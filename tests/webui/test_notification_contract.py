import json
from pathlib import Path

from nanobot.events import ContextCompactionEvent, RecoveryStateEvent, RetryStatusEvent
from nanobot.webui.outbound_wire import project_notification

ROOT = Path(__file__).resolve().parents[2]


def test_wire_fixtures_are_real_python_projections(monkeypatch):
    monkeypatch.setattr("nanobot.webui.outbound_wire.time.time", lambda: 100.0)
    fixtures = json.loads((ROOT / "packages/client-events/fixtures.json").read_text())
    for expected in fixtures:
        fields = {key: value for key, value in expected.items() if key not in {"event", "chat_id"}}
        event_type = {
            "context_compaction": ContextCompactionEvent,
            "recovery_state": RecoveryStateEvent,
            "retry_status": RetryStatusEvent,
        }[expected["event"]]
        if "retry_after_s" in fields:
            fields["next_retry_at"] = 100.0 + fields.pop("retry_after_s")
        projection = project_notification(expected["chat_id"], event_type(**fields))
        assert projection is not None
        assert projection.payload == expected

