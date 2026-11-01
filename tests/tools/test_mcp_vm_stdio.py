"""The VM stdio bridge carries JSON-RPC both ways for MCP servers."""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest

from yoreni.agent.tools.mcp_vm_stdio import vm_stdio_client


class _FakeProcess:
    def __init__(self, lines: list[str]) -> None:
        self._lines = lines
        self.written: list[str] = []
        self.stdout_closed = False
        self.stderr_closed = False
        self.stdin_closed = False
        self.killed = False

    def stdout(self) -> AsyncIterator[str]:
        async def _lines() -> AsyncIterator[str]:
            for line in self._lines:
                yield line
            self.stdout_closed = True

        return _lines()

    def stderr(self) -> AsyncIterator[str]:
        async def _lines() -> AsyncIterator[str]:
            self.stderr_closed = True
            yield ""

        return _lines()

    async def write(self, text: str) -> None:
        self.written.append(text)

    async def close_stdin(self) -> None:
        self.stdin_closed = True

    async def wait(self) -> int:
        return 0

    async def kill(self) -> None:
        self.killed = True


class _FakeGateway:
    def __init__(self, process: _FakeProcess) -> None:
        self.process = process
        self.started: list[tuple[tuple[str, ...], str | None]] = []
        self.closed: list[_FakeProcess] = []

    async def start_stdio_process(self, command, *, cwd=None, env=None):
        self.started.append((tuple(command), cwd))
        return self.process

    async def close_stdio_process(self, process: _FakeProcess) -> None:
        self.closed.append(process)
        await process.kill()


@pytest.mark.asyncio
async def test_vm_stdio_client_frames_messages_both_ways() -> None:
    import json

    import mcp.types as types
    from mcp.shared.message import SessionMessage

    process = _FakeProcess([
        '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}\n',
    ])
    gateway = _FakeGateway(process)

    async with vm_stdio_client(
        gateway, command="mcp-server", args=["--stdio"], cwd="/workspace/mcp"
    ) as (read_stream, write_stream):
        message = await read_stream.receive()
        assert isinstance(message, SessionMessage)
        assert message.message.root.method == "initialize"

        await write_stream.send(SessionMessage(types.JSONRPCMessage.model_validate({
            "jsonrpc": "2.0",
            "id": 2,
            "method": "initialize",
            "params": {},
        })))

    assert gateway.started == [(("mcp-server", "--stdio"), "/workspace/mcp")]
    assert len(process.written) == 1
    assert process.written[0].endswith("\n")
    assert json.loads(process.written[0]) == {
        "jsonrpc": "2.0",
        "id": 2,
        "method": "initialize",
        "params": {},
    }
    assert process.stdin_closed is True
    assert gateway.closed == [process]
