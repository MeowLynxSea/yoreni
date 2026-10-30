"""Tests for CommandRouter.is_dispatchable_command and mid-turn command interception."""

from __future__ import annotations

from inspect import Parameter, signature
from unittest.mock import MagicMock

import pytest

from yoreni.command.builtin import (
    builtin_command_starts_agent_turn,
    register_builtin_commands,
)
from yoreni.command.router import CommandContext, CommandRouter


def test_command_context_requires_loop_as_keyword_dependency() -> None:
    loop_parameter = signature(CommandContext).parameters["loop"]

    assert loop_parameter.kind is Parameter.KEYWORD_ONLY
    assert loop_parameter.default is Parameter.empty


class TestIsDispatchableCommand:
    """Unit tests for the is_dispatchable_command() predicate."""

    @pytest.fixture()
    def router(self) -> CommandRouter:
        r = CommandRouter()
        register_builtin_commands(r)
        return r

    def test_exact_commands_match(self, router: CommandRouter) -> None:
        assert router.is_dispatchable_command("/help")
        assert router.is_dispatchable_command("/model")
        assert router.is_dispatchable_command("/dream")
        assert router.is_dispatchable_command("/dream-log")
        assert router.is_dispatchable_command("/dream-restore")
        assert router.is_dispatchable_command("/dream-prompt")
        assert router.is_dispatchable_command("/goal")
        assert router.is_dispatchable_command("/pairing")

    def test_prefix_commands_match(self, router: CommandRouter) -> None:
        assert router.is_dispatchable_command("/dream-log abc123")
        assert router.is_dispatchable_command("/dream-restore def456")
        assert router.is_dispatchable_command("/dream-prompt init")
        assert router.is_dispatchable_command("/model fast")
        assert router.is_dispatchable_command("/goal migrate the database")
        assert router.is_dispatchable_command("/pairing list")
        assert router.is_dispatchable_command("/pairing approve CODE")
        assert router.is_dispatchable_command("/__shell pwd")

    def test_priority_commands_not_matched(self, router: CommandRouter) -> None:
        # Priority commands are NOT in the dispatchable tiers — they are
        # handled by is_priority() separately.
        assert not router.is_dispatchable_command("/stop")
        assert not router.is_dispatchable_command("/restart")

    def test_regular_text_not_matched(self, router: CommandRouter) -> None:
        assert not router.is_dispatchable_command("hello")
        assert not router.is_dispatchable_command("what is 2+2?")
        assert not router.is_dispatchable_command("!important is still ordinary text")
        assert not router.is_dispatchable_command("")

    def test_case_insensitive(self, router: CommandRouter) -> None:
        assert router.is_dispatchable_command("/HELP")
        assert router.is_dispatchable_command("/Help")
        assert router.is_dispatchable_command("/PAIRING")

    def test_strips_whitespace(self, router: CommandRouter) -> None:
        assert router.is_dispatchable_command("  /help  ")
        assert router.is_dispatchable_command("  /pairing list  ")

    def test_only_registered_commands_match_for_argument_validation(
        self, router: CommandRouter,
    ) -> None:
        assert not router.is_dispatchable_command("/unknown")
        assert not router.is_dispatchable_command("/foo bar")
        assert router.is_dispatchable_command("/status now")
        assert router.is_dispatchable_command("/STOP extra")


@pytest.mark.parametrize(
    ("content", "expected"),
    [
        ("/status", False),
        ("/history 5", False),
        ("/goal", False),
        ("/goal migrate the database", True),
        ("regular prompt", True),
        ("!pwd", True),
    ],
)
def test_builtin_command_agent_turn_lifecycle(content: str, expected: bool) -> None:
    assert builtin_command_starts_agent_turn(content) is expected


class TestPairingCommandDispatch:
    """Verify /pairing works via CommandRouter."""

    @pytest.fixture()
    def router(self) -> CommandRouter:
        r = CommandRouter()
        register_builtin_commands(r)
        return r

    @pytest.fixture()
    def fake_msg(self) -> MagicMock:
        msg = MagicMock()
        msg.channel = "telegram"
        msg.chat_id = "chat1"
        msg.content = "/pairing list"
        msg.metadata = {}
        return msg

    @pytest.mark.asyncio
    async def test_pairing_list_dispatched(
        self, router: CommandRouter, fake_msg: MagicMock, monkeypatch,
    ) -> None:
        monkeypatch.setattr(
            "yoreni.pairing.store.list_pending",
            lambda: [
                {
                    "code": "ABCD-EFGH",
                    "channel": "telegram",
                    "sender_id": "123",
                    "expires_at": 9999999999,
                }
            ],
        )
        ctx = CommandContext(
            msg=fake_msg, raw="/pairing list", args="list", loop=MagicMock(),
        )
        result = await router.dispatch(ctx)
        assert result is not None
        assert "ABCD-EFGH" in result.content
        assert result.metadata.get("_pairing_command") is True

    @pytest.mark.asyncio
    async def test_pairing_approve_dispatched(
        self, router: CommandRouter, fake_msg: MagicMock, monkeypatch,
    ) -> None:
        monkeypatch.setattr(
            "yoreni.pairing.store.approve_code",
            lambda code, *, on_change=None: ("telegram", "123") if code == "ABCD-EFGH" else None,
        )
        fake_msg.content = "/pairing approve ABCD-EFGH"
        ctx = CommandContext(
            msg=fake_msg, raw="/pairing approve ABCD-EFGH",
            args="approve ABCD-EFGH", loop=MagicMock(),
        )
        result = await router.dispatch(ctx)
        assert result is not None
        assert "Approved" in result.content
        assert result.content == (
            "Approved pairing code `ABCD-EFGH` — 123 can now access telegram"
        )
        assert result.metadata.get("_pairing_command") is True

    @pytest.mark.asyncio
    async def test_pairing_revoke_dispatched(
        self, router: CommandRouter, fake_msg: MagicMock, monkeypatch,
    ) -> None:
        monkeypatch.setattr(
            "yoreni.pairing.store.revoke",
            lambda ch, sid, *, on_change=None: sid == "123",
        )
        fake_msg.content = "/pairing revoke 123"
        ctx = CommandContext(
            msg=fake_msg, raw="/pairing revoke 123",
            args="revoke 123", loop=MagicMock(),
        )
        result = await router.dispatch(ctx)
        assert result is not None
        assert "Revoked" in result.content
        assert result.content == "Revoked 123 from telegram"
        assert result.metadata.get("_pairing_command") is True
