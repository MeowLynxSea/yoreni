"""Server-owned first-run setup: onboarding progress and the user profile."""

from __future__ import annotations

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
    websocket = _websocket_config(config)
    return bool(websocket.token_issue_secret.strip() or websocket.token.strip())


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

    def snapshot(self) -> dict[str, Any]:
        status = self._status()
        record = self._state.read()
        config = self._config.load()
        profile = self._profile.read()
        token_set = _token_is_set(config)
        step = record.step if record is not None else (
            "done" if status == "completed" else "token"
        )
        if status != "completed" and not token_set:
            step = "token"
        return {
            "status": status,
            "step": step,
            "token_set": token_set,
            "profile": profile_payload(profile),
            "completed_at": record.completed_at if record else None,
        }

    def read_profile(self) -> dict[str, Any]:
        return profile_payload(self._profile.read())

    # -- writes --------------------------------------------------------------

    def set_token(self, raw: Any) -> dict[str, Any]:
        token = raw.strip() if isinstance(raw, str) else ""
        if not token:
            raise OnboardingError("Enter a WebUI password")
        if not _TOKEN_MIN <= len(token) <= _TOKEN_MAX:
            raise OnboardingError(
                f"Use between {_TOKEN_MIN} and {_TOKEN_MAX} characters"
            )
        if any(ord(character) < 32 or ord(character) == 127 for character in token):
            raise OnboardingError("The password cannot contain control characters")

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
        record = self._state.read()
        if record is None or record.step == "token":
            self._state.mark_step("intro")
        return {"token_set": True}

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
            created = self._ensure_welcome(profile, locale if isinstance(locale, str) else None)
            record = self._state.mark_completed()
            return {
                "status": record.status,
                "completed_at": record.completed_at,
                "created_welcome": created,
            }

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
