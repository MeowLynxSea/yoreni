"""Host-side preparation of the BrowserSkill CLI and its VM extension.

The VM never downloads anything: the host fetches a pinned ``bsk`` release,
verifies it against a recorded checksum, and writes the binary into the VM.
The Chromium extension is shipped with Yoreni as an unpacked build.
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import os
import tarfile
import tempfile
import urllib.request
from collections.abc import Callable
from pathlib import Path

from filelock import FileLock
from loguru import logger

from yoreni.sandbox.backend import SandboxBackend
from yoreni.sandbox.errors import SandboxUnavailableError

# (downloaded bytes, total bytes when the server reports it)
ProgressCallback = Callable[[int, int | None], None]

BSK_VERSION = "0.4.0"
_RELEASE_BASE = (
    "https://github.com/Tencent/BrowserSkill/releases/download/"
    f"cli-v{BSK_VERSION}"
)
# Filename and SHA-256 of the published release archives, per guest architecture.
_ARCHIVES = {
    "x64": (
        f"bsk-v{BSK_VERSION}-x86_64-unknown-linux-musl.tar.gz",
        "d8a37ec49505e8bbac15cf3335c832d12c77118de276f8164dcad9323cafabc5",
    ),
    "arm64": (
        f"bsk-v{BSK_VERSION}-aarch64-unknown-linux-musl.tar.gz",
        "dcee46ef93959ed9656689803e6c013488d38c174ba201f674ca574e1f1ed99d",
    ),
}
# Brave is the browser the VM presents to the agent: a normal release build,
# unlike Playwright's Chrome for Testing binary, and it publishes both Linux
# architectures. Chrome and Edge have no arm64 Linux build, and Ubuntu ships
# Chromium only as a snap, which a container cannot run.
BRAVE_VERSION = "1.98.63"
_BRAVE = {
    "x64": ("amd64", "24227cd5a9259ebdc5108db394cc5998df5ac3635f84f8838a7464a7b5cddd55"),
    "arm64": ("arm64", "8d0b4a0155c2810ec9c2e2b755159b9a278bd51fb9d0d776ca7ac4f42e0cc2f3"),
}
_BRAVE_URL = (
    "https://github.com/brave/brave-browser/releases/download/"
    "v{version}/brave-browser_{version}_{arch}.deb"
)

_MACHINES = {
    "x86_64": "x64",
    "amd64": "x64",
    "aarch64": "arm64",
    "arm64": "arm64",
}

ASSETS_DIR = Path(__file__).parent / "assets" / "browser-skill"
EXTENSION_DIR = ASSETS_DIR / "extension"
# Both live on the VM root filesystem, not in the shared workspace: a
# workspace mount hides files copied in from outside the VM's mount namespace
# and does not reliably support the daemon's Unix socket and lock file.
VM_ASSET_ROOT = "/opt/yoreni/browser-skill"
VM_EXTENSION_DIR = f"{VM_ASSET_ROOT}/extension"
VM_BRAVE_DEB = f"{VM_ASSET_ROOT}/brave-browser.deb"
BSK_HOME = "/var/lib/yoreni/bsk"
VM_BINARY = "/usr/local/bin/bsk"
_MAX_ARCHIVE_BYTES = 64 * 1024 * 1024


def extension_version() -> str:
    """Return the release tag the bundled extension was built from."""
    return (ASSETS_DIR / "VERSION").read_text(encoding="utf-8").strip()


def guest_arch(machine: str) -> str:
    """Map a VM ``uname -m`` value onto a release architecture."""
    arch = _MACHINES.get(machine.strip().lower())
    if arch is None:
        raise SandboxUnavailableError(
            f"BrowserSkill has no build for the VM architecture {machine!r}"
        )
    return arch


def _cache_is_valid(executable: Path, digest: Path) -> bool:
    """A cache hit must match the digest recorded when it was extracted.

    The binary is a Linux executable, so the host cannot run it to verify.
    """
    try:
        return digest.read_text(encoding="utf-8").strip() == hashlib.sha256(
            executable.read_bytes()
        ).hexdigest()
    except OSError:
        return False


def ensure_bsk_binary(arch: str, *, cache_dir: Path | None = None) -> Path:
    """Return the pinned CLI binary for ``arch``, downloading it once."""
    try:
        filename, checksum = _ARCHIVES[arch]
    except KeyError as exc:
        raise SandboxUnavailableError(
            f"BrowserSkill has no build for the VM architecture {arch!r}"
        ) from exc
    root = cache_dir or Path.home() / ".yoreni" / "tools" / "bsk"
    directory = root / BSK_VERSION / arch
    directory.mkdir(parents=True, exist_ok=True)
    executable = directory / "bsk"
    digest = directory / "bsk.sha256"
    with FileLock(str(directory / "install.lock"), timeout=180):
        if executable.is_file() and _cache_is_valid(executable, digest):
            return executable
        url = f"{_RELEASE_BASE}/{filename}"
        logger.info("Downloading BrowserSkill CLI {} ({})", BSK_VERSION, arch)
        try:
            with urllib.request.urlopen(url, timeout=60) as response:
                raw = response.read(_MAX_ARCHIVE_BYTES + 1)
            if len(raw) > _MAX_ARCHIVE_BYTES:
                raise SandboxUnavailableError("BrowserSkill archive is unexpectedly large")
            if hashlib.sha256(raw).hexdigest() != checksum:
                raise SandboxUnavailableError(
                    "BrowserSkill download failed checksum verification. Retry the download."
                )
            with tarfile.open(fileobj=io.BytesIO(raw), mode="r:gz") as archive:
                member = archive.getmember("bsk")
                if member.size > _MAX_ARCHIVE_BYTES:
                    raise SandboxUnavailableError("BrowserSkill archive holds an oversized binary")
                content = archive.extractfile(member)
                if content is None:
                    raise SandboxUnavailableError("BrowserSkill archive has no CLI binary")
                data = content.read()
            with tempfile.TemporaryDirectory(prefix=".install-", dir=directory) as temporary:
                candidate = Path(temporary) / "bsk"
                candidate.write_bytes(data)
                candidate.chmod(0o755)
                os.replace(candidate, executable)
            digest.write_text(hashlib.sha256(data).hexdigest() + "\n", encoding="utf-8")
        except (OSError, ValueError, KeyError, tarfile.TarError) as exc:
            raise SandboxUnavailableError(
                f"Could not prepare the BrowserSkill CLI {BSK_VERSION}: {exc}"
            ) from exc
    return executable


def ensure_brave_package(
    arch: str,
    *,
    cache_dir: Path | None = None,
    on_progress: ProgressCallback | None = None,
) -> Path:
    """Return the pinned Brave package for ``arch``, downloading it once."""
    try:
        deb_arch, checksum = _BRAVE[arch]
    except KeyError as exc:
        raise SandboxUnavailableError(
            f"No browser build is available for the VM architecture {arch!r}"
        ) from exc
    root = cache_dir or Path.home() / ".yoreni" / "tools" / "brave"
    directory = root / BRAVE_VERSION / arch
    directory.mkdir(parents=True, exist_ok=True)
    package = directory / "brave-browser.deb"
    with FileLock(str(directory / "install.lock"), timeout=180):
        if package.is_file() and hashlib.sha256(package.read_bytes()).hexdigest() == checksum:
            return package
        logger.info("Downloading Brave {} ({})", BRAVE_VERSION, arch)
        url = _BRAVE_URL.format(version=BRAVE_VERSION, arch=deb_arch)
        try:
            with urllib.request.urlopen(url, timeout=300) as response:
                length = response.headers.get("Content-Length")
                expected = int(length) if length and length.isdigit() else None
                chunks: list[bytes] = []
                received = 0
                while chunk := response.read(256 * 1024):
                    chunks.append(chunk)
                    received += len(chunk)
                    if on_progress is not None:
                        on_progress(received, expected)
                    if received > 512 * 1024 * 1024:
                        break
                raw = b"".join(chunks)
            if len(raw) > 512 * 1024 * 1024:
                raise SandboxUnavailableError("The Brave package is unexpectedly large")
            if hashlib.sha256(raw).hexdigest() != checksum:
                raise SandboxUnavailableError(
                    "The Brave download failed checksum verification. Retry the download."
                )
            with tempfile.TemporaryDirectory(prefix=".install-", dir=directory) as temporary:
                candidate = Path(temporary) / package.name
                candidate.write_bytes(raw)
                os.replace(candidate, package)
        except (OSError, ValueError) as exc:
            raise SandboxUnavailableError(
                f"Could not prepare Brave {BRAVE_VERSION}: {exc}"
            ) from exc
    return package


async def install(backend: SandboxBackend, *, on_progress: ProgressCallback | None = None) -> None:
    """Place the CLI and the extension inside the VM."""
    machine = await backend.exec("uname -m", cwd="/", timeout_s=10)
    if machine.exit_code != 0:
        raise SandboxUnavailableError(
            "Could not read the VM architecture: " + (machine.stderr or machine.stdout).strip()[:200]
        )
    arch = guest_arch(machine.stdout)
    binary = await asyncio.to_thread(ensure_bsk_binary, arch)
    previous = await backend.exec("bsk --version", cwd="/", timeout_s=10)
    # Streamed base64 through an exec stdin cannot carry this binary, so the
    # host copy moves the file itself and the VM only adjusts its mode.
    await backend.copy_in(str(binary), VM_BINARY)
    await backend.exec(("chmod", "0755", VM_BINARY), cwd="/", timeout_s=10)
    current = await backend.exec("bsk --version", cwd="/", timeout_s=20)
    if current.exit_code != 0 or current.stdout.strip() != f"bsk {BSK_VERSION}":
        raise SandboxUnavailableError(
            f"{VM_BINARY} is not the pinned BrowserSkill CLI {BSK_VERSION}: "
            + (current.stderr or current.stdout).strip()[:200]
        )
    if previous.exit_code == 0 and previous.stdout.strip() != f"bsk {BSK_VERSION}":
        # A running daemon from the previous release cannot serve the new CLI.
        await backend.exec("bsk daemon stop", cwd="/", timeout_s=30)

    await backend.exec(("mkdir", "-p", VM_ASSET_ROOT), cwd="/", timeout_s=10, user="root")
    await _install_brave(backend, arch, on_progress)

    marker = f"{VM_ASSET_ROOT}/VERSION"
    installed = await backend.exec(f"cat {marker} 2>/dev/null", cwd="/", timeout_s=10)
    current = installed.exit_code == 0 and installed.stdout.strip() == extension_version()
    if current and await _extension_is_present(backend):
        return
    await _copy_extension(backend)
    await backend.write_bytes(
        marker, (extension_version() + "\n").encode("utf-8"), mode=0o644
    )


# Brave's .deb depends on `brave-keyring`, which only exists in their apt
# repository. Yoreni installs a pinned package instead of using that repository,
# so a local placeholder satisfies the dependency and keeps dpkg consistent
# when the repository host is unreachable.
_BRAVE_KEYRING_STUB = (
    "set -eu; "
    "rm -rf /tmp/brave-keyring; mkdir -p /tmp/brave-keyring/DEBIAN; "
    "printf 'Package: brave-keyring\\nVersion: 1.20\\nArchitecture: all\\n"
    "Maintainer: Yoreni <yoreni@localhost>\\n"
    "Description: local placeholder, the Brave apt repository is unused\\n' "
    "> /tmp/brave-keyring/DEBIAN/control; "
    "dpkg-deb -b /tmp/brave-keyring /tmp/brave-keyring.deb >/dev/null; "
    "dpkg -i /tmp/brave-keyring.deb >/dev/null"
)


async def _install_brave(
    backend: SandboxBackend,
    arch: str,
    on_progress: ProgressCallback | None = None,
) -> None:
    """Install the presented browser; the bundled Chromium stays as a fallback."""
    installed = await backend.exec(
        ("dpkg-query", "-W", "-f=${Version}", "brave-browser"),
        cwd="/",
        timeout_s=20,
    )
    if installed.exit_code == 0 and installed.stdout.strip() == BRAVE_VERSION:
        return
    try:
        package = await asyncio.to_thread(
            lambda: ensure_brave_package(arch, on_progress=on_progress)
        )
        await backend.copy_in(str(package), VM_BRAVE_DEB)
        keyring = await backend.exec(
            ("sh", "-c", _BRAVE_KEYRING_STUB), cwd="/", timeout_s=120, user="root"
        )
        if keyring.exit_code != 0:
            raise SandboxUnavailableError(
                (keyring.stderr or keyring.stdout).strip()[-400:]
                or "could not prepare the Brave dependency"
            )
        result = await backend.exec(
            ("apt-get", "install", "-y", "--no-install-recommends", VM_BRAVE_DEB),
            cwd="/",
            timeout_s=900,
            user="root",
        )
        await backend.exec(("rm", "-f", VM_BRAVE_DEB), cwd="/", timeout_s=10, user="root")
        if result.exit_code != 0:
            raise SandboxUnavailableError(
                (result.stderr or result.stdout).strip()[-400:] or "apt-get failed"
            )
    except Exception as exc:
        logger.warning(
            "Brave could not be installed; the VM keeps the bundled Chromium: {}", exc
        )


async def _extension_is_present(backend: SandboxBackend) -> bool:
    result = await backend.exec(
        ("test", "-f", f"{VM_EXTENSION_DIR}/manifest.json"), cwd="/", timeout_s=10
    )
    return result.exit_code == 0


async def _copy_extension(backend: SandboxBackend) -> None:
    """Copy the extension and normalize however the backend laid the tree down.

    ``copy_in`` of a directory may place its contents at the target or nest
    them under the source name, so the staged result is found by its manifest
    and moved into place instead of guessing.
    """
    staging = f"{VM_ASSET_ROOT}/extension.staging"
    await backend.remove(staging, recursive=True)
    await backend.copy_in(str(EXTENSION_DIR), staging)
    copied = await backend.exec(
        (
            "sh",
            "-c",
            'set -eu; '
            'source=$(dirname "$(find "$1" -maxdepth 3 -name manifest.json | head -n 1)"); '
            'test -f "$source/manifest.json"; '
            'rm -rf "$2"; mv "$source" "$2"; rm -rf "$1"',
            "sh",
            staging,
            VM_EXTENSION_DIR,
        ),
        cwd="/",
        timeout_s=120,
    )
    if copied.exit_code != 0 or not await _extension_is_present(backend):
        detail = (copied.stderr or copied.stdout).strip()[-400:]
        raise SandboxUnavailableError(
            "The BrowserSkill extension could not be installed from "
            f"{staging}: {detail or 'manifest.json is missing'}"
        )
