"""Agent-authored Home widgets: definitions, storage, execution, scheduling."""

from yoreni.widgets.runner import WidgetRunner
from yoreni.widgets.service import WidgetService
from yoreni.widgets.store import WidgetStore
from yoreni.widgets.types import (
    WidgetChange,
    WidgetDefinition,
    WidgetError,
    WidgetNotFoundError,
    WidgetPatch,
    WidgetRunResult,
    WidgetSnapshot,
    WidgetSpec,
    WidgetSummary,
)

__all__ = [
    "WidgetChange",
    "WidgetDefinition",
    "WidgetError",
    "WidgetNotFoundError",
    "WidgetPatch",
    "WidgetRunResult",
    "WidgetRunner",
    "WidgetService",
    "WidgetSnapshot",
    "WidgetSpec",
    "WidgetStore",
    "WidgetSummary",
]
