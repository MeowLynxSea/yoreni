"""Server-owned first-run setup: onboarding progress and the user profile."""

from __future__ import annotations

import hmac
import threading
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from pydantic import ValidationError

from yoreni.agent.state import PERSONAL_CHAT_ID, AgentInput, AgentStore
from yoreni.config.loader import ConfigLoadError, resolve_config_env_vars
from yoreni.profile.schema import UserProfile
from yoreni.profile.store import (
    ONBOARDING_STEPS,
    OnboardingStore,
    UserProfileStore,
)
from yoreni.profile.welcome import welcome_messages
from yoreni.providers.routing import validate_provider_setup
from yoreni.webui.settings_services import WebUISettingsConfig

if TYPE_CHECKING:
    from yoreni.channels.websocket.runtime import WebSocketConfig

_TOKEN_MIN = 8
_TOKEN_MAX = 256
_WELCOME_EVENT_ID = "onboarding.welcome"
_WELCOME_SOURCE = "onboarding"


class OnboardingError(ValueError):
    """A setup request the owner must correct before continuing."""

    def __init__(self, message: str, *, status: int = 400) -> None:
        super().__init__(message)
        self.message = message
        self.status = status


def _websocket_config(config: Any) -> "WebSocketConfig":
    from yoreni.channels.websocket.runtime import WebSocketConfig

    current: object = getattr(config.channels, "websocket", None) or {}
    return WebSocketConfig.model_validate(current)


def _token_is_set(config: Any) -> bool:
    return bool(_configured_secret(config))


def _configured_secret(config: Any) -> str:
    websocket = _websocket_config(config)
    return websocket.token_issue_secret.strip() or websocket.token.strip()


def profile_payload(profile: UserProfile) -> dict[str, Any]:
    return profile.model_dump(mode="json")


class OnboardingService:
    """Own the durable onboarding state and the owner profile beside config."""

    def __init__(
        self,
        config: WebUISettingsConfig,
        *,
        workspace: Path,
        live_websocket: "WebSocketConfig | None" = None,
    ) -> None:
        base = config.path.parent
        self._config = config
        self._workspace = workspace
        self._live_websocket = live_websocket
        self._state = OnboardingStore(base / "onboarding.json")
        self._profile = UserProfileStore(base / "profile.json")
        self._lock = threading.Lock()

    # -- reads ---------------------------------------------------------------

    def is_complete(self) -> bool:
        return self._status() == "completed"

    def _status(self) -> str:
        record = self._state.read()
        if record is not None:
            return record.status
        if self._legacy_complete(self._config.load()):
            # Persist the one-time migration so later launches and chat turns
            # do not re-validate the whole provider route on every message.
            self._state.mark_completed()
            return "completed"
        return "uninitialized"

    def _legacy_complete(self, config: Any) -> bool:
        """Treat an installed instance that already has a token and model as initialized."""
        if not _token_is_set(config):
            return False
        try:
            validate_provider_setup(resolve_config_env_vars(config))
        except (ValueError, ConfigLoadError):
            return False
        return True

    def sandbox_snapshot(self) -> dict[str, Any]:
        from yoreni.sandbox.setup import get_sandbox_setup

        return get_sandbox_setup().snapshot()

    def sandbox_host_mode(self) -> str:
        from yoreni.sandbox.setup import get_sandbox_setup

        return get_sandbox_setup().host_mode()

    async def sandbox_host(self) -> dict[str, Any]:
        """The environment the agent runs in, for the first-run screen."""
        return await self._host_request()

    async def install_sandbox_host(self) -> dict[str, Any]:
        """Open the Podroid download; Android asks the owner to confirm it."""
        return await self._host_request("install")

    async def start_sandbox_host(self) -> dict[str, Any]:
        """Ask the host app to boot the VM that Podroid owns."""
        return await self._host_request("start")

    async def _host_request(self, action: str | None = None) -> dict[str, Any]:
        from yoreni.sandbox.errors import SandboxUnavailableError
        from yoreni.sandbox.setup import get_sandbox_setup

        setup = get_sandbox_setup()
        try:
            if action == "install":
                await setup.install_host()
            elif action == "start":
                await setup.start_host()
            host = await setup.host_status()
        except SandboxUnavailableError as exc:
            raise OnboardingError(str(exc), status=503) from None
        return host or {"mode": setup.host_mode()}

    def sandbox_resources(
        self, config: Any, sandbox: dict[str, Any] | None = None
    ) -> dict[str, Any] | None:
        """Return the VM size and this host's limits, or None without a VM."""
        from yoreni.sandbox.resources import read_resources

        state = (sandbox or self.sandbox_snapshot()).get("state")
        if state == "unsupported":
            return None
        current = config if config is not None else self._config.load()
        return read_resources(current.tools.sandbox)

    def set_sandbox_resources(self, payload: Any) -> dict[str, Any]:
        """Persist the VM size chosen before the first VM is created."""
        if not isinstance(payload, dict):
            raise OnboardingError("Sandbox resources must be an object")
        from yoreni.sandbox.resources import write_resources

        data = cast(dict[str, Any], payload)
        try:
            return write_resources(
                self._config.update, data.get("cpus"), data.get("memory_mib")
            )
        except ValueError as exc:
            raise OnboardingError(str(exc)) from None

    async def prepare_sandbox(self, *, retry: bool = False) -> dict[str, Any]:
        """Start (or restart) the sandbox VM that onboarding waits for."""
        from yoreni.sandbox.setup import get_sandbox_setup

        setup = get_sandbox_setup()
        return await (setup.retry() if retry else setup.ensure_started())

    def snapshot(self) -> dict[str, Any]:
        status = self._status()
        record = self._state.read()
        config = self._config.load()
        profile = self._profile.read()
        token_set = _token_is_set(config)
        sandbox = self.sandbox_snapshot()
        resources = self.sandbox_resources(config, sandbox)
        if record is not None:
            step = record.step
        elif status == "completed":
            step = "done"
        else:
            # The VM's size is fixed when it is created, so it is the one
            # question that cannot wait for the rest of the setup.
            step = "resources" if resources is not None else "token"
        if status != "completed" and not token_set and step not in {"resources", "token"}:
            step = "token"
        return {
            "status": status,
            "step": step,
            "token_set": token_set,
            "profile": profile_payload(profile),
            "completed_at": record.completed_at if record else None,
            "sandbox": sandbox,
            "sandbox_resources": resources,
            "sandbox_host": self.sandbox_host_mode(),
        }

    def read_profile(self) -> dict[str, Any]:
        return profile_payload(self._profile.read())

    # -- writes --------------------------------------------------------------

    def set_token(self, raw: Any) -> dict[str, Any]:
        token = self._validated_token(raw)
        self._write_token(token)
        record = self._state.read()
        if record is None or record.step == "token":
            self._state.mark_step("language")
        return {"token_set": True}

    def change_password(self, current: Any, new: Any) -> dict[str, Any]:
        """Replace the WebUI password after the owner proves the current one."""
        config = self._config.load()
        secret = _configured_secret(config)
        supplied = current.strip() if isinstance(current, str) else ""
        if not secret or not supplied or not hmac.compare_digest(supplied, secret):
            raise OnboardingError("The current password is incorrect", status=403)
        token = self._validated_token(new)
        if hmac.compare_digest(token, secret):
            raise OnboardingError("The new password is the same as the current one")
        self._write_token(token)
        return {"token_set": True}

    def password_is_set(self) -> bool:
        return _token_is_set(self._config.load())

    def _validated_token(self, raw: Any) -> str:
        token = raw.strip() if isinstance(raw, str) else ""
        if not token:
            raise OnboardingError("Enter a WebUI password")
        if not _TOKEN_MIN <= len(token) <= _TOKEN_MAX:
            raise OnboardingError(
                f"Use between {_TOKEN_MIN} and {_TOKEN_MAX} characters"
            )
        if any(ord(character) < 32 or ord(character) == 127 for character in token):
            raise OnboardingError("The password cannot contain control characters")
        return token

    def _write_token(self, token: str) -> None:
        def mutate(config: Any) -> None:
            websocket = _websocket_config(config)
            websocket.token_issue_secret = token
            websocket.websocket_requires_token = True
            setattr(
                config.channels,
                "websocket",
                websocket.model_dump(by_alias=True, exclude_none=True),
            )

        self._config.update(mutate)
        if self._live_websocket is not None:
            # The running listener checks this live object, not the file.
            self._live_websocket.token_issue_secret = token
            self._live_websocket.websocket_requires_token = True

    def save_profile(self, payload: Any) -> dict[str, Any]:
        profile = self._validate_profile(payload)
        self._profile.write(profile)
        return profile_payload(profile)

    def save_step(self, step: Any) -> dict[str, Any]:
        if step not in ONBOARDING_STEPS:
            raise OnboardingError("unknown onboarding step")
        record = self._state.mark_step(step)
        return {"step": record.step, "status": record.status}

    def update_profile(self, payload: Any) -> dict[str, Any]:
        """Persist a complete profile edited from WebUI settings."""
        profile = self._validate_profile(payload)
        try:
            profile.require_complete()
        except ValueError as exc:
            raise OnboardingError(str(exc)) from None
        self._profile.write(profile)
        return profile_payload(profile)

    def _validate_profile(self, payload: Any) -> UserProfile:
        if not isinstance(payload, dict):
            raise OnboardingError("Profile must be an object")
        try:
            return UserProfile.model_validate(cast(dict[str, Any], payload))
        except ValidationError as exc:
            issue = exc.errors()[0]
            message = str(issue.get("msg") or "Invalid profile")
            raise OnboardingError(message) from None

    async def test_embedding(self, payload: Any) -> dict[str, Any]:
        """Call the embedding endpoint the owner is configuring, without saving it."""
        if not isinstance(payload, dict):
            raise OnboardingError("Embedding settings must be an object")
        data = cast(dict[str, Any], payload)
        model = str(data.get("model") or "").strip()
        if not model:
            raise OnboardingError("Embedding model is required")
        provider = str(data.get("provider") or "").strip() or None
        api_base = str(data.get("api_base") or "").strip() or None
        if not api_base and not provider:
            raise OnboardingError("Enter an API base or choose a provider")
        dimensions = data.get("dimensions")
        if dimensions in ("", None):
            dimensions = None
        else:
            try:
                dimensions = int(dimensions)
            except (TypeError, ValueError):
                raise OnboardingError("Dimensions must be a number") from None

        from yoreni.config.schema import EmbeddingConfig
        from yoreni.memory.embeddings import (
            EmbeddingClient,
            EmbeddingError,
            resolve_embedding_settings,
        )

        api_key = str(data.get("api_key") or "").strip() or None
        try:
            candidate = EmbeddingConfig(
                provider=provider,
                model=model,
                api_base=api_base,
                api_key=api_key,
                dimensions=dimensions,
                multimodal=bool(data.get("multimodal")),
            )
            settings = resolve_embedding_settings(
                candidate,
                resolve_config_env_vars(self._config.load()).providers,
            )
            if settings is None:
                raise OnboardingError("Embedding endpoint is not configured")
            vector = await EmbeddingClient(settings).embed_query("ping")
        except EmbeddingError as exc:
            raise OnboardingError(str(exc)) from None
        except (ValidationError, ValueError) as exc:
            raise OnboardingError(str(exc)) from None
        return {"ok": True, "dimensions": len(vector)}

    def complete(self, locale: Any = None) -> dict[str, Any]:
        """Validate required setup, persist completion, and create the welcome run once."""
        with self._lock:
            config = self._config.load()
            if not _token_is_set(config):
                raise OnboardingError("Set a WebUI password first")
            profile = self._profile.read()
            try:
                profile.require_complete()
            except ValueError as exc:
                raise OnboardingError(str(exc)) from None
            try:
                validate_provider_setup(resolve_config_env_vars(config))
            except (ValueError, ConfigLoadError) as exc:
                raise OnboardingError(f"Model setup is incomplete: {exc}") from None
            if not self.sandbox_ready():
                raise OnboardingError(
                    "The sandbox is still being prepared", status=409
                )
            created = self._ensure_welcome(profile, locale if isinstance(locale, str) else None)
            record = self._state.mark_completed()
            return {
                "status": record.status,
                "completed_at": record.completed_at,
                "created_welcome": created,
            }

    def sandbox_ready(self) -> bool:
        from yoreni.sandbox.setup import get_sandbox_setup

        return get_sandbox_setup().is_ready()

    def _ensure_welcome(self, profile: UserProfile, locale: str | None) -> bool:
        """Create the opening messages exactly once, resuming a partial write."""
        store = AgentStore(self._workspace)
        event = AgentInput(
            source=_WELCOME_SOURCE,
            content="",
            principal="owner",
            kind="internal",
            event_id=_WELCOME_EVENT_ID,
            channel="websocket",
            chat_id=PERSONAL_CHAT_ID,
        )
        existing = store.find_event(_WELCOME_SOURCE, _WELCOME_EVENT_ID)
        if existing is None:
            run_id = store.admit(event)
            store.start(run_id, [])
        else:
            run_id = existing["id"]
            if existing["result"] is not None:
                return False
            store.start(run_id, [])

        rows = store.entries(run_id)
        seen = {
            str(entry["payload"].get("id") or "")
            for entry in rows
        }
        base = int(time.time() * 1000)
        texts = welcome_messages(locale, profile.name)
        for index, text in enumerate(texts):
            message_id = f"welcome-{index + 1}"
            if message_id in seen:
                continue
            store.append_entry(
                run_id,
                "message",
                {
                    "id": message_id,
                    "content": text,
                    "created_at_ms": base + index * 800,
                },
            )
        store.finish(
            run_id,
            [],
            {"content": "\n\n".join(texts), "stop_reason": "stop"},
        )
        return existing is None
