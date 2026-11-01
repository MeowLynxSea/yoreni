from __future__ import annotations

from yoreni.sandbox.progress import SandboxProgress


def test_progress_is_monotonic_across_stages() -> None:
    progress = SandboxProgress()
    progress.start()
    assert progress.snapshot()["state"] == "running"

    progress.set_bytes(50, 100)
    first = progress.snapshot()["percent"]
    progress.set_stage("packages")
    second = progress.snapshot()["percent"]

    assert first is not None and second is not None
    assert second >= first
    assert progress.snapshot()["stage"] == "packages"


def test_byte_progress_reports_rate_and_eta() -> None:
    progress = SandboxProgress()
    progress.start()
    progress.set_stage("image")
    progress.set_bytes(1_000_000, 4_000_000)
    progress.set_bytes(3_000_000, 4_000_000)
    snapshot = progress.snapshot()

    assert snapshot["downloaded_bytes"] == 3_000_000
    assert snapshot["total_bytes"] == 4_000_000
    assert snapshot["percent"] is not None and snapshot["percent"] > 3.0


def test_an_unknown_total_stays_indeterminate() -> None:
    progress = SandboxProgress()
    progress.start()
    progress.set_stage("image")
    progress.set_bytes(1024, None)

    snapshot = progress.snapshot()
    assert snapshot["downloaded_bytes"] == 1024
    assert snapshot["total_bytes"] is None
    # The bar must not park on the stage floor while bytes are moving.
    assert snapshot["percent"] is None
    assert snapshot["eta_seconds"] is None


def test_a_total_arriving_late_restores_the_position() -> None:
    progress = SandboxProgress()
    progress.start()
    progress.set_stage("image")
    progress.set_bytes(500, None)
    progress.set_bytes(500, 1000)

    snapshot = progress.snapshot()
    assert snapshot["percent"] is not None and snapshot["percent"] > 3.0
    assert snapshot["total_bytes"] == 1000


def test_a_failed_size_lookup_is_not_cached(monkeypatch) -> None:
    from yoreni.sandbox import image_manifest

    answers = [None, 4096]
    monkeypatch.setattr(image_manifest, "_CACHE", {})
    monkeypatch.setattr(image_manifest, "_fetch", lambda image: answers.pop(0))

    assert image_manifest.image_pull_bytes("example/yoreni:1") is None
    assert image_manifest.image_pull_bytes("example/yoreni:1") == 4096
    assert image_manifest.image_pull_bytes("example/yoreni:1") == 4096


def test_failure_keeps_the_reason_for_the_user() -> None:
    progress = SandboxProgress()
    progress.start()
    progress.fail("KVM is not available")

    snapshot = progress.snapshot()
    assert snapshot["state"] == "failed"
    assert "KVM" in snapshot["error"]
    assert snapshot["eta_seconds"] is None


def test_a_finished_setup_reports_full_marks() -> None:
    progress = SandboxProgress()
    progress.start()
    progress.set_stage("services")
    progress.finish()

    snapshot = progress.snapshot()
    assert snapshot["state"] == "ready"
    assert snapshot["percent"] == 100.0
    assert snapshot["eta_seconds"] == 0


async def test_image_monitor_reports_bytes_from_the_runtime_home(monkeypatch, tmp_path) -> None:
    import asyncio

    from yoreni.sandbox.config import SandboxConfig
    from yoreni.sandbox.gateway import SandboxGateway

    class _Backend:
        def data_dir(self):
            return tmp_path

    gateway = SandboxGateway(SandboxConfig(), backend=_Backend())  # type: ignore[arg-type]
    gateway.progress.start()
    gateway.progress.set_stage("image")
    monkeypatch.setattr("yoreni.sandbox.image_manifest.image_pull_bytes", lambda image: 1000)
    monkeypatch.setattr("yoreni.sandbox.gateway._directory_bytes", lambda root: 500)

    task = asyncio.create_task(gateway._monitor_image_pull())
    try:
        for _ in range(60):
            await asyncio.sleep(0.05)
            if gateway.progress.snapshot()["total_bytes"] == 1000:
                break
    finally:
        task.cancel()

    snapshot = gateway.progress.snapshot()
    assert snapshot["downloaded_bytes"] == 500
    assert snapshot["total_bytes"] == 1000
    assert snapshot["percent"] > 3.0


async def test_the_monitor_retries_a_failed_size_lookup(monkeypatch, tmp_path) -> None:
    import asyncio

    from yoreni.sandbox.config import SandboxConfig
    from yoreni.sandbox.gateway import SandboxGateway

    class _Backend:
        def data_dir(self):
            return tmp_path

    answers = [None, 1000]
    gateway = SandboxGateway(SandboxConfig(), backend=_Backend())  # type: ignore[arg-type]
    gateway.progress.start()
    gateway.progress.set_stage("image")
    monkeypatch.setattr("yoreni.sandbox.gateway._IMAGE_TOTAL_RETRY_S", 0.0)
    monkeypatch.setattr(
        "yoreni.sandbox.image_manifest.image_pull_bytes",
        lambda image: answers.pop(0) if answers else 1000,
    )
    monkeypatch.setattr("yoreni.sandbox.gateway._directory_bytes", lambda root: 500)

    task = asyncio.create_task(gateway._monitor_image_pull())
    try:
        for _ in range(80):
            await asyncio.sleep(0.05)
            if gateway.progress.snapshot()["total_bytes"] == 1000:
                break
    finally:
        task.cancel()

    snapshot = gateway.progress.snapshot()
    assert snapshot["total_bytes"] == 1000
    assert snapshot["percent"] is not None and snapshot["percent"] > 3.0


def test_the_runtime_home_lives_in_the_instance_directory(tmp_path) -> None:
    from yoreni.sandbox.boxlite_backend import BoxLiteBackend
    from yoreni.sandbox.config import SandboxConfig

    explicit = BoxLiteBackend(SandboxConfig(home_dir=str(tmp_path / "custom")))
    assert explicit.data_dir() == tmp_path / "custom"

    from yoreni.config.paths import get_data_dir

    default = BoxLiteBackend(SandboxConfig())
    assert default.data_dir() == get_data_dir() / "sandbox"


def test_byte_progress_never_exceeds_the_image_size() -> None:
    progress = SandboxProgress()
    progress.start()
    progress.set_stage("image")
    progress.set_bytes(2 * 1024**3, 845 * 1000**2)

    snapshot = progress.snapshot()
    assert snapshot["downloaded_bytes"] == snapshot["total_bytes"]
    assert snapshot["percent"] == 58.0
    assert snapshot["rate_bytes_per_s"] is None
    assert snapshot["eta_seconds"] is None


async def test_the_monitor_switches_to_configuring_once_layers_are_downloaded(
    monkeypatch, tmp_path
) -> None:
    import asyncio

    from yoreni.sandbox.config import SandboxConfig
    from yoreni.sandbox.gateway import SandboxGateway

    class _Backend:
        def data_dir(self):
            return tmp_path

    gateway = SandboxGateway(SandboxConfig(), backend=_Backend())  # type: ignore[arg-type]
    gateway.progress.start()
    gateway.progress.set_stage("image")
    monkeypatch.setattr("yoreni.sandbox.image_manifest.image_pull_bytes", lambda image: 1000)
    monkeypatch.setattr("yoreni.sandbox.gateway._directory_bytes", lambda root: 1000)

    task = asyncio.create_task(gateway._monitor_image_pull())
    try:
        for _ in range(60):
            await asyncio.sleep(0.05)
            if gateway.progress.snapshot()["stage"] == "configuring":
                break
    finally:
        task.cancel()

    snapshot = gateway.progress.snapshot()
    assert snapshot["stage"] == "configuring"
    assert snapshot["percent"] == 58.0
    assert snapshot["eta_seconds"] is None
