import json
from typing import Any

import pytest

from yoreni.sandbox import browser_assets, provision
from yoreni.sandbox.models import ExecResult


def test_guest_architecture_is_normalized() -> None:
    assert browser_assets.guest_arch("x86_64") == "x64"
    assert browser_assets.guest_arch("aarch64\n") == "arm64"
    with pytest.raises(Exception, match="no build for the VM architecture"):
        browser_assets.guest_arch("riscv64")


def test_a_cached_binary_is_reused_without_downloading(tmp_path, monkeypatch) -> None:
    import hashlib

    binary = tmp_path / "0.4.0" / "x64" / "bsk"
    binary.parent.mkdir(parents=True)
    binary.write_bytes(b"cached-binary")
    binary.chmod(0o755)
    (binary.parent / "bsk.sha256").write_text(
        hashlib.sha256(b"cached-binary").hexdigest() + "\n", encoding="utf-8"
    )

    def fail(*args, **kwargs):
        raise AssertionError("a cached binary must not be downloaded again")

    monkeypatch.setattr("urllib.request.urlopen", fail)

    assert browser_assets.ensure_bsk_binary("x64", cache_dir=tmp_path) == binary


def test_a_checksum_mismatch_is_refused(tmp_path, monkeypatch) -> None:
    class _Response:
        def __enter__(self):
            return self

        def __exit__(self, *exc) -> None:
            return None

        def read(self, limit: int) -> bytes:
            return b"not the published archive"

    monkeypatch.setattr("urllib.request.urlopen", lambda *args, **kwargs: _Response())

    with pytest.raises(Exception, match="checksum verification"):
        browser_assets.ensure_bsk_binary("x64", cache_dir=tmp_path)


def test_the_bundled_extension_matches_the_recorded_version() -> None:
    manifest = json.loads((browser_assets.EXTENSION_DIR / "manifest.json").read_text(encoding="utf-8"))

    assert manifest["manifest_version"] == 3
    assert manifest["name"] == "BrowserSkill"
    assert browser_assets.extension_version().endswith(manifest["version"])


def test_the_browser_service_starts_the_daemon_with_the_extension() -> None:
    script = provision._START_BROWSER.format(
        browser_path="/browsers",
        profile="/workspace/.yoreni/chromium",
        extension=browser_assets.VM_EXTENSION_DIR,
        bsk_home=browser_assets.BSK_HOME,
    )

    assert f"--load-extension={browser_assets.VM_EXTENSION_DIR}" in script
    assert "daemon start" in script
    assert "su -s /bin/sh" in script
    assert "remote-debugging-port" not in script


class _InstallBackend:
    """Records the VM calls of one install pass."""

    def __init__(self, *, manifest_present: bool) -> None:
        self.manifest_present = manifest_present
        self.binary_installed = False
        self.calls: list[tuple[str, Any]] = []
        self.files: dict[str, bytes] = {}

    async def exec(self, command: Any, *, cwd: str, timeout_s: float | None = None, **kwargs: Any):
        self.calls.append(("exec", command))
        if isinstance(command, tuple) and command[:2] == ("test", "-f"):
            code = 0 if self.manifest_present else 1
            return ExecResult(command=str(command), exit_code=code, stdout="", stderr="")
        if command == "uname -m":
            return ExecResult(command="uname -m", exit_code=0, stdout="aarch64\n", stderr="")
        if command == "bsk --version":
            code = 0 if self.binary_installed else 1
            return ExecResult(
                command="bsk --version", exit_code=code, stdout="bsk 0.4.0\n", stderr=""
            )
        return ExecResult(command=str(command), exit_code=0, stdout="", stderr="")

    async def remove(self, path: str, *, recursive: bool = False) -> None:
        self.calls.append(("remove", path))

    async def copy_in(self, host_path: str, vm_path: str) -> None:
        self.calls.append(("copy_in", (host_path, vm_path)))
        if vm_path == browser_assets.VM_BINARY:
            self.binary_installed = True

    async def write_bytes(self, path: str, data: bytes, **kwargs: Any) -> None:
        self.calls.append(("write_bytes", path))
        self.files[path] = data


async def test_install_stages_the_extension_and_records_its_version(tmp_path, monkeypatch) -> None:
    binary = tmp_path / "bsk"
    binary.write_bytes(b"binary")
    monkeypatch.setattr(browser_assets, "ensure_bsk_binary", lambda arch: binary)
    monkeypatch.setattr(browser_assets, "ensure_brave_package", lambda arch: binary)
    backend = _InstallBackend(manifest_present=True)

    await browser_assets.install(backend)

    copies = [value for kind, value in backend.calls if kind == "copy_in"]
    assert copies == [
        (str(binary), browser_assets.VM_BINARY),
        (str(binary), browser_assets.VM_BRAVE_DEB),
        (str(browser_assets.EXTENSION_DIR), f"{browser_assets.VM_ASSET_ROOT}/extension.staging"),
    ]
    assert f"{browser_assets.VM_ASSET_ROOT}/VERSION" in backend.files


async def test_install_refuses_an_extension_without_a_manifest(tmp_path, monkeypatch) -> None:
    binary = tmp_path / "bsk"
    binary.write_bytes(b"binary")
    monkeypatch.setattr(browser_assets, "ensure_bsk_binary", lambda arch: binary)
    monkeypatch.setattr(browser_assets, "ensure_brave_package", lambda arch: binary)
    backend = _InstallBackend(manifest_present=False)

    with pytest.raises(Exception, match="manifest.json is missing"):
        await browser_assets.install(backend)
    assert f"{browser_assets.VM_ASSET_ROOT}/VERSION" not in backend.files


def test_runtime_assets_live_outside_the_shared_workspace() -> None:
    """A workspace mount hides host copies and may reject the daemon's socket."""

    assert not browser_assets.BSK_HOME.startswith("/workspace")
    assert not browser_assets.VM_EXTENSION_DIR.startswith("/workspace")


def test_a_cached_brave_package_is_reused(tmp_path, monkeypatch) -> None:
    import hashlib

    package = tmp_path / browser_assets.BRAVE_VERSION / "arm64" / "brave-browser.deb"
    package.parent.mkdir(parents=True)
    package.write_bytes(b"deb")
    monkeypatch.setitem(
        browser_assets._BRAVE, "arm64", ("arm64", hashlib.sha256(b"deb").hexdigest())
    )

    def fail(*args, **kwargs):
        raise AssertionError("a cached package must not be downloaded again")

    monkeypatch.setattr("urllib.request.urlopen", fail)

    assert browser_assets.ensure_brave_package("arm64", cache_dir=tmp_path) == package
