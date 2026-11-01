"""Cross-suite test infrastructure."""

from __future__ import annotations

import os
import ssl
import sys
from collections.abc import Callable, Iterator
from pathlib import Path
from threading import Thread
from uuid import uuid4

import certifi
import pytest
from loguru import logger


@pytest.fixture(autouse=True)
def _isolate_tokenizer_warmup(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep tests deterministic and out of the user's tokenizer cache and network."""
    monkeypatch.setattr("yoreni.utils.token_encoding._encoding", None)
    monkeypatch.setattr("yoreni.utils.token_encoding._warmup_thread", Thread())


def _local_gateway(workspace: str, media_dir: str):
    """Build a gateway backed by the host for tests only."""
    from yoreni.sandbox.config import SandboxConfig
    from yoreni.sandbox.gateway import SandboxGateway

    tests_dir = str(Path(__file__).parent / "tests")
    if tests_dir not in sys.path:
        sys.path.insert(0, tests_dir)
    from support.local_backend import LocalBackend

    gateway = SandboxGateway(
        SandboxConfig(
            enabled=True,
            backend="test-local",
            workspace=workspace,
            media_dir=media_dir,
            desktop_enabled=False,
            browser_enabled=False,
        ),
        backend=LocalBackend(),
        host_media_dir=media_dir,
    )
    gateway._started = True
    return gateway


@pytest.fixture(autouse=True)
def _sandbox_test_gateway() -> Iterator[None]:
    """Bind the execution plane to a host-backed backend for tests only.

    Production never constructs this backend. It keeps tool contracts testable
    without a hypervisor while still routing every call through the gateway.
    """
    from yoreni.sandbox.gateway import set_sandbox_gateway

    gateway = _local_gateway("/", "/tmp/yoreni-test-media")
    set_sandbox_gateway(gateway)
    try:
        yield
    finally:
        set_sandbox_gateway(None)


@pytest.fixture
def vm_workspace(tmp_path: Path) -> Iterator[None]:
    """Bind the execution plane to a VM whose workspace is the test directory.

    The default test gateway covers the whole host; commands and file
    references that must stay inside one workspace need this narrower one.
    """
    from yoreni.sandbox.gateway import get_sandbox_gateway, set_sandbox_gateway

    previous = get_sandbox_gateway()
    gateway = _local_gateway(str(tmp_path), str(tmp_path / ".yoreni" / "media"))
    set_sandbox_gateway(gateway)
    try:
        yield
    finally:
        set_sandbox_gateway(previous)


@pytest.fixture(autouse=True)
def _isolate_tool_entry_points(monkeypatch: pytest.MonkeyPatch) -> None:
    """Only load external tool plugins explicitly provided by the test."""
    monkeypatch.setattr("yoreni.agent.tools.loader.entry_points", lambda **_kwargs: ())


@pytest.fixture(autouse=True)
def _isolate_yoreni_log_activation() -> Iterator[None]:
    """Keep CLI log settings from leaking into later tests in the same process."""
    logger.enable("yoreni")
    try:
        yield
    finally:
        logger.enable("yoreni")


@pytest.fixture(autouse=True)
def _isolate_runtime_stores(
    tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch,
) -> Iterator[None]:
    """Isolate writable stores without making a directory for pure tests."""
    import yoreni.llm_usage.service as llm_usage
    import yoreni.pairing.store as pairing_store
    from yoreni.credentials import crypto as credential_crypto
    from yoreni.credentials import store as credential_store
    from yoreni.credentials.manager import reset_credential_manager
    from yoreni.credentials.vault import reset_vault
    from yoreni.webui import star_prompt

    # A sibling of tmp_path stays outside any test's agent workspace. mkdir is
    # deferred until a store is used and remains safe for concurrent callers.
    data_root = tmp_path_factory.getbasetemp() / "runtime-data" / uuid4().hex

    def data_dir() -> Path:
        data_root.mkdir(parents=True, exist_ok=True)
        return data_root

    def runtime_subdir(name: str) -> Path:
        path = data_root / name
        path.mkdir(parents=True, exist_ok=True)
        return path

    monkeypatch.setattr(star_prompt, "get_webui_dir", lambda: runtime_subdir("webui"))
    monkeypatch.setattr(pairing_store, "_store_path", lambda: data_dir() / "pairing.json")
    monkeypatch.setattr(llm_usage, "get_data_dir", data_dir)
    monkeypatch.setattr(credential_crypto, "get_data_dir", data_dir)
    monkeypatch.setattr(credential_store, "get_data_dir", data_dir)
    reset_credential_manager(None)
    reset_vault(None)
    stores: dict[Path, llm_usage.LLMUsageStore] = {}
    monkeypatch.setattr(llm_usage, "_STORES", stores)
    try:
        yield
    finally:
        for store in stores.values():
            store.close()
        reset_credential_manager(None)
        reset_vault(None)


@pytest.fixture(scope="session", autouse=True)
def _use_windows_system_ca_for_default_http_clients() -> Iterator[None]:
    """Avoid reparsing certifi's CA bundle for every offline HTTP client.

    Loading certifi takes roughly 0.7 seconds per client on Windows. The test
    suite constructs hundreds of clients while mocking their I/O. System roots
    preserve certificate verification for accidental local requests; explicit
    ``cafile``, ``capath``, and ``cadata`` arguments still use the real loader.
    """
    if sys.platform != "win32":
        yield
        return

    original = ssl.create_default_context
    certifi_path = os.path.normcase(os.path.abspath(certifi.where()))

    def create_default_context(
        purpose: ssl.Purpose = ssl.Purpose.SERVER_AUTH,
        *,
        cafile: str | None = None,
        capath: str | None = None,
        cadata: str | bytes | None = None,
    ) -> ssl.SSLContext:
        requested_path = os.path.normcase(os.path.abspath(cafile)) if cafile else None
        if requested_path == certifi_path and capath is None and cadata is None:
            return original(purpose)
        return original(
            purpose,
            cafile=cafile,
            capath=capath,
            cadata=cadata,
        )

    ssl.create_default_context = create_default_context
    try:
        yield
    finally:
        ssl.create_default_context = original


@pytest.fixture(scope="session")
def create_integration_env(tmp_path_factory: pytest.TempPathFactory) -> Callable[[Path], Path]:
    """Seed each stdlib-created prefix from private, offline pip images."""
    import ensurepip
    import venv
    from importlib.resources import files

    from virtualenv import session_via_cli

    seed = tmp_path_factory.mktemp("integration-env-seed")
    wheels = seed / "wheels"
    wheels.mkdir()
    versions = {}
    # Keep the stdlib seed versions, including setuptools on Python 3.11,
    # rather than silently selecting virtualenv's newer bundled pip.
    for wheel in files("ensurepip").joinpath("_bundled").iterdir():
        if wheel.name.endswith(".whl"):
            name, version, _ = wheel.name.split("-", 2)
            versions[name] = version
            (wheels / wheel.name).write_bytes(wheel.read_bytes())
    assert versions["pip"] == ensurepip.version()

    env = {key: value for key, value in os.environ.items() if not key.startswith(("VIRTUALENV_", "PIP_"))}
    env.update({
        "VIRTUALENV_CONFIG_FILE": os.devnull,
        "VIRTUALENV_OVERRIDE_APP_DATA": str(seed / "app-data"),
        "PIP_CONFIG_FILE": os.devnull,
        "PIP_NO_INDEX": "1",
    })
    options = [
        "--python", sys.executable,
        "--creator", "venv",
        "--seeder", "app-data",
        "--no-download",
        "--no-periodic-update",
        "--pip", versions["pip"],
        "--setuptools", versions.get("setuptools", "none"),
        "--extra-search-dir", str(wheels),
    ]

    def create(environment: Path) -> Path:
        # Keep stdlib creation semantics; use virtualenv only for copied seed
        # packages and prefix-specific launchers, not its runtime import hooks.
        # Do not request creator --copies: Homebrew disallows copying its Python,
        # and this creator is never run. The app-data seeder copies by default.
        venv.EnvBuilder(with_pip=False).create(environment)
        with session_via_cli([str(environment), *options], setup_logging=False, env=env) as session:
            session.seeder.run(session.creator)
        return environment / ("Scripts/python.exe" if os.name == "nt" else "bin/python")

    return create


@pytest.fixture
def create_symlink():
    """Create real symlinks, skipping only Windows' missing-privilege error."""
    def create(link: Path, target: Path, *, target_is_directory: bool = False) -> None:
        try:
            link.symlink_to(target, target_is_directory=target_is_directory)
        except OSError as exc:
            if sys.platform == "win32" and getattr(exc, "winerror", None) == 1314:
                pytest.skip("Windows symlink creation requires Developer Mode or privilege")
            raise

    return create


class DisplayHistoryFixture:
    """Explicit offline setup plus snapshot reads for history projection tests."""

    def __init__(self, workspace: Path) -> None:
        from yoreni.session.manager import SessionManager

        self.sessions = SessionManager(workspace)

    async def metadata(self, chat_id, metadata=None):
        from yoreni.session.display_history import HISTORY_GENERATION_METADATA_KEY

        session = await self.sessions.state.get_header(f"websocket:{chat_id}")
        return {**(metadata or {}), HISTORY_GENERATION_METADATA_KEY: session.generation}

    async def message(self, **kwargs):
        from yoreni.bus.events import OutboundMessage

        kwargs["metadata"] = await self.metadata(kwargs["chat_id"], kwargs.get("metadata"))
        return OutboundMessage(**kwargs)

    async def stream(self, channel, method, chat_id, *args, **kwargs):
        kwargs["metadata"] = await self.metadata(chat_id, kwargs.get("metadata"))
        await getattr(channel, method)(chat_id, *args, **kwargs)

    def append(self, key, record):
        from yoreni.webui.transcript import append_transcript_object

        append_transcript_object(key, record, sessions=self.sessions)

    def _project(self, name, key, *args, **kwargs):
        from yoreni.webui import transcript

        return self.sessions.project_history(key, lambda snapshot: getattr(transcript, name)(
            key, *args, history=snapshot, **kwargs,
        ))

    def read(self, key):
        return self._project("read_transcript_lines", key)

    def build(self, key, **kwargs):
        return self._project("build_webui_thread_response", key, **kwargs)

    def trace(self, key, detail_ref):
        return self._project("build_webui_trace_detail_response", key, detail_ref)

    def revision(self, key, **kwargs):
        return self._project("webui_transcript_revision", key, **kwargs)

    def prompts(self, key, **kwargs):
        return self._project("build_prompt_index", key, **kwargs)

    def prompt_response(self, key, previous_revision, **kwargs):
        return self._project("prompt_index_response", key, previous_revision, **kwargs)


@pytest.fixture
async def history(tmp_path: Path):
    fixture = DisplayHistoryFixture(tmp_path)
    yield fixture
    if fixture.sessions._state is not None:
        await fixture.sessions.state.aclose()
