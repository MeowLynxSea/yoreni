"""Runtime context for tool construction."""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Protocol, runtime_checkable

from yoreni.config.network import NetworkConfig

if TYPE_CHECKING:
    from yoreni.agent.memory import MemoryStore
    from yoreni.agent.state import AgentStore
    from yoreni.agent.tools.exec_session import ExecSessionManager
    from yoreni.agent.tools.file_state import FileStates
    from yoreni.agent.tools.runtime_control import RuntimeControl
    from yoreni.bus.queue import MessageBus
    from yoreni.config.schema import ProvidersConfig, ToolsConfig
    from yoreni.cron.service import CronService
    from yoreni.providers.factory import ProviderSnapshot
    from yoreni.security.workspace_access import WorkspaceSandboxStatus, WorkspaceScope
    from yoreni.utils.llm_runtime import LLMRuntime

_CURRENT_REQUEST_CONTEXT: ContextVar["RequestContext | None"] = ContextVar(
    "yoreni_tool_request_context",
    default=None,
)


@dataclass(frozen=True)
class RequestContext:
    """Per-request context injected into tools at message-processing time."""
    channel: str
    chat_id: str
    message_id: str | None = None
    original_user_text: str | None = None
    runtime: LLMRuntime | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    sender_id: str | None = None
    turn_id: str | None = None
    workspace: Path | None = None
    attributes: dict[str, Any] = field(default_factory=dict)
    log_content: bool = True
    # The host can consume completion messages after this request returns.
    can_receive_background_results: bool = True
    # Effective tool scope; injected inputs retain the active run's scope.
    workspace_scope: WorkspaceScope | None = None
    agent_id: str | None = None


@runtime_checkable
class ContextAware(Protocol):
    def set_context(self, ctx: RequestContext) -> None:
        ...


def bind_request_context(ctx: RequestContext) -> Token[RequestContext | None]:
    return _CURRENT_REQUEST_CONTEXT.set(ctx)


def reset_request_context(token: Token[RequestContext | None]) -> None:
    _CURRENT_REQUEST_CONTEXT.reset(token)


@contextmanager
def request_context(ctx: RequestContext):
    """Bind one immutable request snapshot and restore the previous value."""
    token = bind_request_context(ctx)
    try:
        yield ctx
    finally:
        reset_request_context(token)


def current_request_context() -> RequestContext | None:
    return _CURRENT_REQUEST_CONTEXT.get()


def tool_log_content_allowed() -> bool:
    """Whether diagnostics may include content from the current tool request."""
    ctx = current_request_context()
    return ctx is None or ctx.log_content




def current_execution_owner() -> str | None:
    """Scope process execution to a run while retaining agent-wide access."""
    ctx = current_request_context()
    if ctx is None:
        return None
    if ctx.agent_id and ctx.turn_id:
        return f"agent:{ctx.agent_id}:run:{ctx.turn_id}"
    return ctx.turn_id


@dataclass
class ToolContext:
    config: ToolsConfig
    workspace: str
    network: NetworkConfig = field(default_factory=NetworkConfig)
    bus: MessageBus | None = None
    cron_service: CronService | None = None
    exec_session_manager: ExecSessionManager | None = None
    agent_store: AgentStore | None = None
    memory: MemoryStore | None = None
    file_state_store: FileStates | None = None
    provider_snapshot_loader: Callable[..., ProviderSnapshot] | None = None
    providers_config: ProvidersConfig | None = None
    timezone: str = "UTC"
    workspace_sandbox: WorkspaceSandboxStatus | None = None
    runtime_control: RuntimeControl | None = None
