"""MCP stdio transport bridged into the Yoreni VM.

The agent never runs an MCP server process on the host: stdio servers start
inside the VM and speak newline-delimited JSON-RPC through the gateway's raw
process streams.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncGenerator, Sequence
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from yoreni.sandbox.gateway import SandboxGateway

logger = logging.getLogger(__name__)

# Time the VM process gets to exit after its stdin closes, matching the MCP
# SDK's own stdio shutdown budget.
_SHUTDOWN_GRACE_S = 2.0


def _text(chunk: str | bytes) -> str:
    return chunk.decode("utf-8", errors="replace") if isinstance(chunk, bytes) else chunk


@asynccontextmanager
async def vm_stdio_client(
    gateway: "SandboxGateway",
    *,
    command: str,
    args: Sequence[str] = (),
    env: dict[str, str] | None = None,
    cwd: str | None = None,
) -> AsyncGenerator[tuple[Any, Any]]:
    """Run one MCP stdio server in the VM and yield its session streams."""
    import anyio
    import mcp.types as types
    from mcp.shared.message import SessionMessage

    read_stream_writer, read_stream = anyio.create_memory_object_stream[
        SessionMessage | Exception
    ](0)
    write_stream, write_stream_reader = anyio.create_memory_object_stream[SessionMessage](0)
    process = await gateway.start_stdio_process((command, *args), cwd=cwd, env=env)

    async def emit(line: str) -> None:
        line = line.strip()
        if not line:
            return
        try:
            message = types.JSONRPCMessage.model_validate_json(line)
        except Exception as exc:
            logger.exception("MCP VM server %r sent an unparsable message", command)
            await read_stream_writer.send(exc)
            return
        await read_stream_writer.send(SessionMessage(message))

    async def stdout_reader() -> None:
        stream = process.stdout()
        if stream is None:
            return
        try:
            async with read_stream_writer:
                buffer = ""
                async for chunk in stream:
                    lines = (buffer + _text(chunk)).split("\n")
                    buffer = lines.pop()
                    for line in lines:
                        await emit(line)
                if buffer.strip():
                    await emit(buffer)
        except anyio.ClosedResourceError:
            await anyio.lowlevel.checkpoint()

    async def stdin_writer() -> None:
        try:
            async with write_stream_reader:
                async for session_message in write_stream_reader:
                    payload = session_message.message.model_dump_json(
                        by_alias=True, exclude_none=True
                    )
                    await process.write(payload + "\n")
        except anyio.ClosedResourceError:
            await anyio.lowlevel.checkpoint()

    async def stderr_reader() -> None:
        stream = process.stderr()
        if stream is None:
            return
        try:
            async for chunk in stream:
                text = _text(chunk).rstrip()
                if text:
                    logger.debug("MCP VM server %r stderr: %s", command, text)
        except anyio.ClosedResourceError:
            await anyio.lowlevel.checkpoint()

    try:
        async with anyio.create_task_group() as tg:
            tg.start_soon(stdout_reader)
            tg.start_soon(stdin_writer)
            tg.start_soon(stderr_reader)
            try:
                yield read_stream, write_stream
            finally:
                await process.close_stdin()
                with anyio.move_on_after(_SHUTDOWN_GRACE_S):
                    await process.wait()
                await gateway.close_stdio_process(process)
                tg.cancel_scope.cancel()
    finally:
        await read_stream.aclose()
        await write_stream.aclose()
