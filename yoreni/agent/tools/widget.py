"""Agent tool for creating and maintaining Home widgets."""

# pyright: reportIncompatibleMethodOverride=false

from __future__ import annotations

import shlex
from typing import Any, cast

from yoreni.agent.tools.base import Tool, ToolResult, tool_parameters
from yoreni.agent.tools.context import ToolContext, current_request_context
from yoreni.agent.tools.schema import (
    ArraySchema,
    IntegerSchema,
    ObjectSchema,
    StringSchema,
)
from yoreni.widgets.service import WidgetService
from yoreni.widgets.types import (
    DEFAULT_TIMEOUT_SECONDS,
    ViewKind,
    WidgetDefinition,
    WidgetError,
    WidgetNotFoundError,
    WidgetPatch,
    WidgetRunResult,
    WidgetSpec,
    dump_json,
)

_PREVIEW_CHARS = 4000

_WIDGET_PARAMETERS = ObjectSchema(
    {
        "action": StringSchema(
            "Action to perform",
            enum=["list", "get", "create", "update", "delete", "set_data", "refresh"],
        ),
        "widget_id": StringSchema(
            "Widget id: 1-64 letters, digits, '_' or '-'. Required for every action except list."
        ),
        "title": StringSchema("REQUIRED for create: short widget title (max 60 characters)."),
        "description": StringSchema(
            "Optional one-line description shown in Settings (max 120 characters)."
        ),
        "view": StringSchema(
            "REQUIRED for create: how the widget renders. Template views render stored data: "
            "stat, list, table, text, metrics, progress, facts, chart, timeline, badges, links. "
            "'panel' composes several of them into one card. 'html' needs html and is only for "
            "layouts no template can express.",
            enum=[
                "stat",
                "list",
                "table",
                "text",
                "metrics",
                "progress",
                "facts",
                "chart",
                "timeline",
                "badges",
                "links",
                "panel",
                "html",
            ],
        ),
        "html": StringSchema(
            "HTML fragment for view='html'. It runs in a sandboxed iframe with scripts but no "
            "network access, and receives the stored data as window.yoreniWidget.data."
        ),
        "script": StringSchema(
            "Backend command source written into the widget directory, for example a Python "
            "script that prints one JSON object on stdout."
        ),
        "script_filename": StringSchema(
            "Filename for script; defaults to 'data.py'. Use 'data.js' with argv ['node','data.js'] "
            "for another language."
        ),
        "argv": ArraySchema(
            items=StringSchema(""),
            description=(
                "Command to run without a shell, for example ['python3','data.py']. It must print "
                "one JSON object on stdout. When script is given and argv is omitted, argv defaults "
                "to ['python3','data.py']. Pass [] to remove the command."
            ),
        ),
        "timeout_seconds": IntegerSchema(
            description="Command timeout in seconds (default 30).",
            minimum=1,
            maximum=120,
        ),
        "every_seconds": IntegerSchema(
            description=(
                "Automatic refresh interval in seconds. Omit for no automatic refresh; "
                "pass 0 to clear an existing interval."
            ),
            minimum=0,
            maximum=86400,
        ),
        "data": ObjectSchema(
            additional_properties=True,
            description=(
                "REQUIRED for set_data. Shape depends on the view: stat {value, label?, detail?}, "
                "list {items: [{title, detail?, meta?}]}, table {columns: [string], rows: [[value]]}, "
                "text {text}, metrics {items: [{label, value, detail?, tone?}], columns?: 1|2|3}, "
                "progress {items: [{label, value, max?, detail?, tone?}]}, "
                "facts {items: [{label, value, tone?}]}, "
                "chart {type?: 'bar'|'line', max?, items: [{label, value}]}, "
                "timeline {items: [{title, detail?, meta?, tone?}]}, "
                "badges {items: [{label, detail?, tone?}]}, "
                "links {items: [{label, href, detail?, meta?}]}, "
                "panel {lead?, blocks: [{kind, title?, ...}], note?}, html any JSON object. "
                "tone is neutral, positive, warning, or critical."
            ),
        ),
    },
    required=["action"],
    additional_properties=False,
    description=(
        "create needs title and view; prefer a template view and use view='html' only when no "
        "template can express the layout, which also needs html. panel composes text, list, "
        "table, metrics, progress, facts, chart, timeline, badges, and links blocks into one "
        "card. Add script or argv when the widget should fetch its own data. update changes "
        "only the fields you pass. Widgets show on the user's Home panel."
    ),
).to_json_schema()


@tool_parameters(_WIDGET_PARAMETERS)
class WidgetTool(Tool):
    """Create, edit, inspect, and refresh Home widgets."""

    def __init__(self, widget_service: WidgetService, workspace: str) -> None:
        self._service = widget_service
        self._workspace = workspace

    @classmethod
    def enabled(cls, ctx: ToolContext) -> bool:
        return ctx.widget_service is not None

    @classmethod
    def create(cls, ctx: ToolContext) -> Tool:
        widget_service = ctx.widget_service
        if widget_service is None:
            raise RuntimeError("WidgetTool requires an initialized widget service")
        return cls(widget_service=widget_service, workspace=ctx.workspace)

    @property
    def name(self) -> str:
        return "widget"

    @property
    def description(self) -> str:
        return (
            "Create and maintain Home widgets: list, get, create, update, delete, set_data, refresh. "
            "A widget renders stored data with a template (stat, list, table, text, metrics, "
            "progress, facts, chart, timeline, badges, links, panel) or an HTML fragment in a "
            "sandboxed iframe. A backend command can produce the data as JSON on stdout and "
            "refresh it every_seconds. Use this for values the user wants at a glance."
        )

    def validate_params(self, params: dict[str, Any]) -> list[str]:
        errors = super().validate_params(params)
        action = params.get("action")
        if action != "list" and not str(params.get("widget_id") or "").strip():
            errors.append(f"widget_id is required when action='{action}'")
        if action == "create":
            if not str(params.get("title") or "").strip():
                errors.append("title is required when action='create'")
            view = params.get("view")
            if not view:
                errors.append("view is required when action='create'")
            elif view == "html" and not str(params.get("html") or "").strip():
                errors.append("html is required when view='html'")
            elif view != "html" and params.get("html") is not None:
                errors.append("html is only accepted when view='html'")
        if action == "update" and not any(
            params.get(field) is not None
            for field in (
                "title",
                "description",
                "view",
                "html",
                "script",
                "argv",
                "timeout_seconds",
                "every_seconds",
            )
        ):
            errors.append("update requires at least one changed field")
        if params.get("data") is not None and action != "set_data":
            errors.append("data is only accepted when action='set_data'")
        if action == "set_data" and params.get("data") is None:
            errors.append("data is required when action='set_data'")
        if params.get("script_filename") is not None and params.get("script") is None:
            errors.append("script_filename requires script")
        return errors

    async def execute(
        self,
        action: str,
        widget_id: str | None = None,
        title: str | None = None,
        description: str | None = None,
        view: str | None = None,
        html: str | None = None,
        script: str | None = None,
        script_filename: str | None = None,
        argv: list[str] | None = None,
        timeout_seconds: int | None = None,
        every_seconds: int | None = None,
        data: dict[str, Any] | None = None,
    ) -> str:
        try:
            if action == "list":
                return self._list()
            if action == "get":
                return self._get(widget_id or "")
            if action == "create":
                return await self._create(
                    widget_id or "",
                    title or "",
                    description or "",
                    view or "",
                    html,
                    script,
                    script_filename,
                    argv,
                    timeout_seconds,
                    every_seconds,
                )
            if action == "update":
                return await self._update(
                    widget_id or "",
                    title,
                    description,
                    view,
                    html,
                    script,
                    script_filename,
                    argv,
                    timeout_seconds,
                    every_seconds,
                )
            if action == "delete":
                return await self._delete(widget_id or "")
            if action == "set_data":
                return await self._set_data(widget_id or "", data or {})
            if action == "refresh":
                return await self._refresh(widget_id or "")
        except WidgetNotFoundError:
            return ToolResult.error(
                f"Error: widget '{widget_id}' not found. Use action='list' to see widget ids."
            )
        except WidgetError as exc:
            return ToolResult.error(f"Error: {exc}")
        return ToolResult.error(f"Error: unknown action '{action}'")

    def _list(self) -> str:
        summaries = self._service.summaries()
        if not summaries:
            return "No Home widgets yet. Use action='create' to add one."
        lines = ["Home widgets:"]
        for summary in summaries:
            definition = summary.definition
            details = [definition.view]
            if definition.command:
                details.append("command")
            if definition.every_seconds:
                details.append(f"every {definition.every_seconds}s")
            line = f"- {definition.id} — {definition.title} ({', '.join(details)})"
            if summary.error:
                line += f" — last run failed: {summary.error}"
            elif not summary.has_data and definition.view != "html":
                line += " — no data yet"
            lines.append(line)
        return "\n".join(lines)

    def _get(self, widget_id: str) -> str:
        snapshot = self._service.snapshot(widget_id)
        definition = snapshot.summary.definition
        lines = [f"Widget '{definition.title}' (id: {definition.id})"]
        if definition.description:
            lines.append(f"Description: {definition.description}")
        lines.extend(_describe(definition, None))
        if snapshot.data is not None:
            lines.append("Data:\n" + _preview(dump_json(snapshot.data)))
        if snapshot.summary.error:
            lines.append(f"Last run failed: {snapshot.summary.error}")
        if snapshot.html is not None:
            lines.append("HTML:\n" + _preview(snapshot.html))
        source = self._service.script_source(definition.id)
        if source is not None:
            filename, content = source
            lines.append(f"Script ({filename}):\n" + _preview(content))
        return "\n".join(lines)

    async def _create(
        self,
        widget_id: str,
        title: str,
        description: str,
        view: str,
        html: str | None,
        script: str | None,
        script_filename: str | None,
        argv: list[str] | None,
        timeout_seconds: int | None,
        every_seconds: int | None,
    ) -> str:
        spec = WidgetSpec(
            id=widget_id,
            title=title,
            description=description,
            view=cast(ViewKind, view),
            html=html,
            script=script,
            script_filename=script_filename or "data.py",
            command=tuple(argv) if argv else None,
            timeout_seconds=(
                DEFAULT_TIMEOUT_SECONDS if timeout_seconds is None else timeout_seconds
            ),
            every_seconds=every_seconds,
            workspace=self._turn_workspace(),
        )
        definition, result = await self._service.create(spec)
        lines = [f"Created widget '{definition.title}' (id: {definition.id})."]
        lines.extend(_describe(definition, result))
        return "\n".join(lines)

    async def _update(
        self,
        widget_id: str,
        title: str | None,
        description: str | None,
        view: str | None,
        html: str | None,
        script: str | None,
        script_filename: str | None,
        argv: list[str] | None,
        timeout_seconds: int | None,
        every_seconds: int | None,
    ) -> str:
        patch = WidgetPatch(
            title=title,
            description=description,
            view=cast(ViewKind, view) if view is not None else None,
            html=html,
            script=script,
            script_filename=script_filename,
            command=tuple(argv) if argv is not None else None,
            timeout_seconds=timeout_seconds,
            every_seconds=every_seconds,
        )
        definition, result = await self._service.update(widget_id, patch)
        lines = [f"Updated widget '{definition.title}' (id: {definition.id})."]
        lines.extend(_describe(definition, result))
        return "\n".join(lines)

    async def _delete(self, widget_id: str) -> str:
        await self._service.delete(widget_id)
        return f"Deleted widget '{widget_id}'."

    async def _set_data(self, widget_id: str, data: dict[str, Any]) -> str:
        definition = await self._service.set_data(widget_id, data)
        return f"Saved data for widget '{definition.title}' (id: {definition.id})."

    async def _refresh(self, widget_id: str) -> str:
        definition = self._service.snapshot(widget_id).summary.definition
        result = await self._service.refresh(widget_id)
        if result.error:
            return ToolResult.error(
                f"Error: refresh failed for widget '{widget_id}': {result.error}"
            )
        return (
            f"Refreshed widget '{definition.title}' (id: {widget_id}).\n"
            "Data:\n" + _preview(dump_json(result.data))
        )

    def _turn_workspace(self) -> str:
        request = current_request_context()
        if request is not None and request.workspace is not None:
            return str(request.workspace)
        return self._workspace


def _describe(definition: WidgetDefinition, result: WidgetRunResult | None) -> list[str]:
    lines = [f"View: {definition.view}"]
    if definition.command:
        lines.append(f"Command: {shlex.join(definition.command)}")
    if definition.every_seconds:
        lines.append(f"Refresh: every {definition.every_seconds}s")
    if result is not None:
        if result.error:
            lines.append(f"Last run failed: {result.error}")
        else:
            lines.append("Data:\n" + _preview(dump_json(result.data)))
    return lines


def _preview(text: str) -> str:
    if len(text) <= _PREVIEW_CHARS:
        return text
    return text[:_PREVIEW_CHARS] + f"\n… ({len(text) - _PREVIEW_CHARS} more characters)"
