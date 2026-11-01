"""Installed ripgrep with native arguments and shared process controls."""

# pyright: reportIncompatibleMethodOverride=false

from __future__ import annotations

from typing import Any

from yoreni.agent.tools.base import ToolResult
from yoreni.agent.tools.context import ToolContext
from yoreni.agent.tools.shell import ExecTool


class RgTool(ExecTool):
    @classmethod
    def enabled(cls, ctx: ToolContext) -> bool:
        # ripgrep is provisioned on the agent's machine, so availability is the
        # machine's business; probing the host here would answer for the wrong
        # environment.
        return ctx.config.file.enable and super().enabled(ctx)

    @property
    def name(self) -> str:
        return "rg"

    @property
    def description(self) -> str:
        return (
            "Search file contents and discover files with ripgrep. "
            "Accepts native rg arguments as an array of strings."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        schema = super().parameters
        properties = schema["properties"]
        for name in ("command", "cmd", "shell", "login"):
            properties.pop(name, None)
        properties["args"] = {
            "type": "array", "items": {"type": "string"}, "minItems": 1,
            "description": "Native rg arguments, with each argument as one array element",
        }
        schema["required"] = ["args"]
        return schema

    async def execute(self, args: list[str], **kwargs: Any) -> str:
        if not args:
            return ToolResult.error("Error: Provide rg arguments, such as --help or --files.")
        for name in ("command", "cmd", "shell", "login"):
            kwargs.pop(name, None)
        return await super().execute(command=["rg", *args], **kwargs)
