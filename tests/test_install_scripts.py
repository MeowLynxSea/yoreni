"""Exercise the public installers against real, offline package managers.

The tiny wheel reports its runtime identity; it is not the yoreni application.
No gateway, model, user configuration or public package index is used by these tests.
"""

import base64
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from zipfile import ZipFile

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
WINDOWS = os.name == "nt"
BASE_PYTHON = sys._base_executable


def _wheel(directory, version, *, platform="any", package="yoreni", extras=(), installer_receipt=False):
    module = package.replace("-", "_") if package != "yoreni" else "yoreni"
    wheel_name = package.replace("-", "_")
    metadata = f"{wheel_name}-{version}.dist-info"
    files = {
        "yoreni/__init__.py": f'__version__ = "{version}"\n',
        "yoreni/__main__.py": """
import json
import sys
import yoreni

def main():
    print("INSTALLER_PROBE " + json.dumps({
        "version": yoreni.__version__, "package": yoreni.__file__,
        "python": sys.executable, "prefix": sys.prefix,
        "isolated": sys.flags.isolated, "args": sys.argv[1:],
    }))

if __name__ == "__main__":
    main()
""",
        f"{metadata}/METADATA": f"Metadata-Version: 2.1\nName: yoreni\nVersion: {version}\nRequires-Python: >=3.11\n",
        f"{metadata}/WHEEL": "Wheel-Version: 1.0\nGenerator: installer-test\nRoot-Is-Purelib: true\nTag: py3-none-any\n",
        f"{metadata}/entry_points.txt": "[console_scripts]\nyoreni = yoreni.__main__:main\n",
    }
    files = {name.replace("yoreni/", f"{module}/"): content for name, content in files.items()}
    files[f"{module}/__main__.py"] = files[f"{module}/__main__.py"].replace("yoreni", module)
    files[f"{metadata}/METADATA"] = files[f"{metadata}/METADATA"].replace("Name: yoreni", f"Name: {package}")
    files[f"{metadata}/METADATA"] += "".join(f"Provides-Extra: {extra}\n" for extra in extras)
    files[f"{metadata}/WHEEL"] = files[f"{metadata}/WHEEL"].replace("py3-none-any", f"py3-none-{platform}")
    files[f"{metadata}/entry_points.txt"] = f"[console_scripts]\n{module} = {module}.__main__:main\n"
    if installer_receipt:
        files["yoreni/installation_receipt.py"] = (SCRIPTS.parent / "yoreni/installation_receipt.py").read_text()
        files["yoreni/utils/__init__.py"] = ""
        files["yoreni/utils/atomic_file.py"] = (SCRIPTS.parent / "yoreni/utils/atomic_file.py").read_text()
    record = []
    path = directory / f"{wheel_name}-{version}-py3-none-{platform}.whl"
    with ZipFile(path, "w") as wheel:
        for name, content in files.items():
            data = content.encode()
            wheel.writestr(name, data)
            digest = base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=").decode()
            record.append(f"{name},sha256={digest},{len(data)}\n")
        wheel.writestr(f"{metadata}/RECORD", "".join(record) + f"{metadata}/RECORD,,\n")
    return path


def _python(environment):
    return environment / ("Scripts/python.exe" if WINDOWS else "bin/python")


def _run(command, sandbox, *, check=True, timeout=90):
    # Installer subprocesses may inherit output handles. Bound the command's
    # lifetime, not pipe EOF, and retain partial output when the command times out.
    with tempfile.TemporaryFile() as stdout, tempfile.TemporaryFile() as stderr:
        try:
            process = subprocess.run(command, cwd=sandbox["cwd"], env=sandbox["env"],
                                     stdout=stdout, stderr=stderr, timeout=timeout)
        except subprocess.TimeoutExpired:
            stdout.seek(0)
            stderr.seek(0)
            output = stdout.read().decode("utf-8", errors="replace")
            error = stderr.read().decode("utf-8", errors="replace")
            pytest.fail(f"Installer command exceeded {timeout}s: {command!r}\n"
                        f"stdout:\n{output}\nstderr:\n{error}", pytrace=False)
        stdout.seek(0)
        stderr.seek(0)
        result = subprocess.CompletedProcess(command, process.returncode,
                                             stdout.read().decode("utf-8", errors="replace"),
                                             stderr.read().decode("utf-8", errors="replace"))
    if check:
        assert result.returncode == 0, result.stdout + result.stderr
    return result


def _installer(sandbox, *args, check=True):
    if WINDOWS:
        shell = shutil.which("powershell")
        if not shell:
            pytest.skip("Windows PowerShell is required")
        command = [shell, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                   "-File", str(SCRIPTS / "install.ps1")]
    else:
        command = ["/bin/sh", str(SCRIPTS / "install.sh")]
    return _run([*command, *args], sandbox, check=check)


def _identity(result):
    reports = [json.loads(line.removeprefix("INSTALLER_PROBE "))
               for line in result.stdout.splitlines() if line.startswith("INSTALLER_PROBE ")]
    assert len(reports) == 1, result.stdout
    return reports[0]


def _expose(sandbox, name):
    # Explicit overrides let local verification use disposable manager installations.
    source = os.environ.get(f"YORENI_TEST_{name.upper()}") or shutil.which(name)
    if not source:
        pytest.skip(f"{name} is required for this real installer check")
    destination = sandbox["bin"] / (f"{name}.exe" if WINDOWS else name)
    if WINDOWS:
        shutil.copy2(source, destination)
    else:
        destination.symlink_to(source)
    return str(destination)


@pytest.fixture
def sandbox(tmp_path):
    root = tmp_path.resolve() / "user's 数据 $safe"
    root.mkdir()
    command_bin = root / "commands"
    command_bin.mkdir()
    wheels = root / "wheels"
    wheels.mkdir()
    # A checkout and PYTHONPATH must not replace the package just installed.
    checkout = root / "checkout"
    (checkout / "yoreni").mkdir(parents=True)
    (checkout / "yoreni/__init__.py").write_text('raise RuntimeError("wrong checkout imported")\n')
    env = {key: value for key, value in os.environ.items()
           if key.upper() in {"SYSTEMROOT", "WINDIR", "COMSPEC", "PATHEXT"}}
    system_path = (str(Path(env["SYSTEMROOT"]) / "System32") if WINDOWS else "/usr/bin:/bin")
    env.update({
        "HOME": str(root), "USERPROFILE": str(root),
        "PATH": str(command_bin) + os.pathsep + system_path,
        "PYTHON": BASE_PYTHON, "PYTHONPATH": str(checkout), "PYTHONUTF8": "1",
        "YORENI_SKIP_WIZARD": "1", "YORENI_VENV": str(root / "managed"),
        "YORENI_BIN_DIR": str(root / "launchers"),
        "XDG_CONFIG_HOME": str(root / "config"), "XDG_CACHE_HOME": str(root / "cache"),
        "XDG_DATA_HOME": str(root / "data"), "APPDATA": str(root / "config"),
        "LOCALAPPDATA": str(root / "data"), "TMPDIR": str(root), "TMP": str(root), "TEMP": str(root),
        "UV_TOOL_DIR": str(root / "uv-tools"), "UV_TOOL_BIN_DIR": str(root / "uv-bin"),
        "UV_CACHE_DIR": str(root / "uv-cache"), "UV_NO_CONFIG": "1", "UV_NO_INDEX": "1",
        "UV_OFFLINE": "1", "UV_PYTHON_DOWNLOADS": "never", "UV_FIND_LINKS": wheels.as_uri(),
        "PIPX_HOME": str(root / "pipx"), "PIPX_BIN_DIR": str(root / "pipx-bin"),
        "PIPX_MAN_DIR": str(root / "man"), "PIPX_DEFAULT_BACKEND": "pip",
        "PIPX_DISABLE_SHARED_LIBS_AUTO_UPGRADE": "1", "PIPX_DEFAULT_PYTHON": BASE_PYTHON,
        "PIP_CONFIG_FILE": os.devnull, "PIP_NO_INDEX": "1", "PIP_FIND_LINKS": wheels.as_uri(),
        "PIP_DISABLE_PIP_VERSION_CHECK": "1", "PIP_NO_INPUT": "1",
    })
    return {"root": root, "bin": command_bin, "wheels": wheels, "cwd": checkout, "env": env}


@pytest.mark.parametrize("manager", ["uv", "pipx"])
def test_installer_starts_and_upgrades_same_persistent_manager_environment(sandbox, manager):
    executable = _expose(sandbox, manager)
    _wheel(sandbox["wheels"], "0.0.1")
    _run([executable, *(["tool"] if manager == "uv" else []), "install", "--python", BASE_PYTHON, "yoreni"], sandbox)
    prefix = (sandbox["root"] / "uv-tools/yoreni" if manager == "uv"
              else sandbox["root"] / "pipx/venvs/yoreni")
    for version in ("0.0.1", "0.0.2"):
        if version == "0.0.2":
            _wheel(sandbox["wheels"], version)
        identity = _identity(_installer(sandbox))
        assert identity["version"] == version
        assert Path(identity["prefix"]).resolve() == prefix
        assert Path(identity["package"]).resolve().is_relative_to(prefix)
        assert Path(identity["python"]).absolute() == _python(prefix)
        assert identity["isolated"] == 1
        assert identity["args"] == ["--version"]
    assert not (sandbox["root"] / "managed").exists()


@pytest.mark.skipif(not WINDOWS, reason="Windows PowerShell command discovery")
def test_powershell_manager_discovery_keeps_session_functions_without_importing_modules(sandbox):
    shell = shutil.which("powershell")
    if not shell:
        pytest.skip("Windows PowerShell is required")
    uv = Path(_expose(sandbox, "uv"))
    _wheel(sandbox["wheels"], "0.0.1")
    _run([str(uv), "tool", "install", "--python", BASE_PYTHON, "yoreni"], sandbox)
    prefix = sandbox["root"] / "uv-tools/yoreni"
    # The session function is the only uv command; its real executable is not on PATH.
    native = sandbox["root"] / "native"
    native.mkdir()
    uv = uv.rename(native / "uv.exe")
    _wheel(sandbox["wheels"], "0.0.2")

    def ps_literal(value):
        return "'" + str(value).replace("'", "''") + "'"

    modules = sandbox["root"] / "modules"
    decoy = modules / "InstallerDecoy"
    decoy.mkdir(parents=True)
    marker = sandbox["root"] / "module-imported"
    (decoy / "InstallerDecoy.psd1").write_text(
        "@{ RootModule = 'InstallerDecoy.psm1'; ModuleVersion = '1.0'; FunctionsToExport = @('pipx') }\n",
        encoding="utf-8-sig",
    )
    (decoy / "InstallerDecoy.psm1").write_text(
        f"[IO.File]::WriteAllText({ps_literal(marker)}, 'imported')\n"
        "function pipx { throw 'Manager discovery imported an unrelated module' }\n",
        encoding="utf-8-sig",
    )
    sandbox["env"]["PSModuleAnalysisCachePath"] = str(sandbox["root"] / "module-analysis-cache")
    command = (
        f"$env:PSModulePath = {ps_literal(modules)} + ';' + $PSHOME + '\\Modules'\n"
        f"function uv {{ & {ps_literal(uv)} @args }}\n"
        f"& {ps_literal(SCRIPTS / 'install.ps1')}"
    )
    result = _run([shell, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                   "-Command", command], sandbox, check=False)
    assert not marker.exists(), result.stdout + result.stderr
    assert result.returncode == 0, result.stdout + result.stderr
    identity = _identity(result)
    assert identity["version"] == "0.0.2"
    assert Path(identity["prefix"]).resolve() == prefix
    assert identity["isolated"] == 1
    assert not (sandbox["root"] / "managed").exists()


def test_rerunning_installer_preserves_uv_version_constraint(sandbox):
    uv = _expose(sandbox, "uv")
    _wheel(sandbox["wheels"], "0.0.1")
    _wheel(sandbox["wheels"], "0.0.2")
    _run([uv, "tool", "install", "--python", BASE_PYTHON, "yoreni==0.0.1"], sandbox)
    assert _identity(_installer(sandbox))["version"] == "0.0.1"
    receipt = sandbox["root"] / "uv-tools/yoreni/uv-receipt.toml"
    assert "==0.0.1" in receipt.read_text(encoding="utf-8")


@pytest.mark.parametrize("custom", ["constraint", "pip-options"])
def test_installer_never_discards_pipx_install_policy(sandbox, custom):
    pipx = _expose(sandbox, "pipx")
    _wheel(sandbox["wheels"], "0.0.1")
    requirement = "yoreni>=0.0.1,<0.1" if custom == "constraint" else "yoreni"
    options = ["--pip-args=--no-deps"] if custom == "pip-options" else []
    _run([pipx, "install", "--python", BASE_PYTHON, *options, requirement], sandbox)
    prefix = sandbox["root"] / "pipx/venvs/yoreni"
    receipt = prefix / "pipx_metadata.json"
    original = receipt.read_bytes()
    _wheel(sandbox["wheels"], "0.0.2")
    result = _installer(sandbox, check=False)
    assert result.returncode != 0
    assert "pipx installation settings" in result.stdout + result.stderr
    assert receipt.read_bytes() == original
    assert _identity(_run([str(_python(prefix)), "-I", "-m", "yoreni", "--version"], sandbox))["version"] == "0.0.1"
    assert not (sandbox["root"] / "managed").exists()


def test_fresh_default_records_dedicated_environment_even_with_uv_available(sandbox):
    _expose(sandbox, "uv")
    _wheel(sandbox["wheels"], "0.0.1", installer_receipt=True)
    identity = _identity(_installer(sandbox))
    prefix = sandbox["root"] / "managed"
    assert Path(identity["prefix"]).resolve() == prefix
    receipt = prefix / "yoreni-install.json"
    original = receipt.read_bytes()
    assert json.loads(original) == {"schema_version": 1, "owner": "yoreni-installer",
                                    "environment": str(prefix), "extras": []}
    _wheel(sandbox["wheels"], "0.0.2", installer_receipt=True)
    assert _identity(_installer(sandbox))["version"] == "0.0.2"
    assert receipt.read_bytes() == original
    assert not (sandbox["root"] / "uv-tools/yoreni").exists()


@pytest.mark.parametrize("original", ["pipx", "managed"])
def test_existing_owner_wins_when_uv_is_installed_later(sandbox, original):
    if original == "pipx":
        executable = _expose(sandbox, "pipx")
    _wheel(sandbox["wheels"], "0.0.1")
    if original == "pipx":
        _run([executable, "install", "--python", BASE_PYTHON, "yoreni"], sandbox)
    before = _identity(_installer(sandbox))
    _expose(sandbox, "uv")
    _wheel(sandbox["wheels"], "0.0.2")
    after = _identity(_installer(sandbox))
    assert after["version"] == "0.0.2"
    assert after["prefix"] == before["prefix"]
    assert after["python"] == before["python"]
    assert not (sandbox["root"] / "uv-tools/yoreni").exists()


def test_multiple_existing_installations_need_an_explicit_owner(sandbox):
    uv, pipx = _expose(sandbox, "uv"), _expose(sandbox, "pipx")
    _wheel(sandbox["wheels"], "0.0.1")
    _run([uv, "tool", "install", "--python", BASE_PYTHON, "yoreni"], sandbox)
    _run([pipx, "install", "--python", BASE_PYTHON, "yoreni"], sandbox)
    _wheel(sandbox["wheels"], "0.0.2")
    result = _installer(sandbox, check=False)
    assert result.returncode != 0
    assert "Multiple yoreni installations" in result.stdout + result.stderr
    for prefix in (sandbox["root"] / "uv-tools/yoreni", sandbox["root"] / "pipx/venvs/yoreni"):
        identity = _identity(_run([str(_python(prefix)), "-I", "-m", "yoreni", "--version"], sandbox))
        assert identity["version"] == "0.0.1"


def test_activated_manager_environment_is_not_updated_with_pip(sandbox):
    executable = _expose(sandbox, "uv")
    _wheel(sandbox["wheels"], "0.0.1")
    _run([executable, "tool", "install", "--python", BASE_PYTHON, "yoreni"], sandbox)
    before = _identity(_installer(sandbox))
    sandbox["env"]["PYTHON"] = before["python"]
    _wheel(sandbox["wheels"], "0.0.2")
    result = _installer(sandbox, check=False)
    assert result.returncode != 0
    assert "active Python belongs to uv or pipx" in result.stdout + result.stderr
    assert _identity(_run([before["python"], "-I", "-m", "yoreni", "--version"], sandbox))["version"] == "0.0.1"


@pytest.mark.parametrize("active", [True, False], ids=["active-venv", "managed-venv"])
def test_python_install_uses_same_environment_and_quoted_launcher(sandbox, active):
    _wheel(sandbox["wheels"], "0.0.1")
    prefix = sandbox["root"] / ("active" if active else "managed")
    if active:
        _run([BASE_PYTHON, "-I", "-m", "venv", str(prefix)], sandbox)
        sandbox["env"]["PYTHON"] = str(_python(prefix))
    identity = _identity(_installer(sandbox))
    assert Path(identity["prefix"]).resolve() == prefix
    assert identity["isolated"] == 1
    if not WINDOWS and not active:
        wrapper = sandbox["root"] / "launchers/yoreni"
        launched = _identity(_run([str(wrapper), "--version"], sandbox))
        assert launched == identity


@pytest.mark.parametrize("manager", ["uv", "pipx"])
def test_failed_default_install_does_not_fall_through_to_available_manager(sandbox, manager):
    _expose(sandbox, manager)
    # Offline and no candidate: only the default dedicated environment is tried.
    result = _installer(sandbox, check=False)
    assert result.returncode != 0
    assert (sandbox["root"] / "managed").exists()
    assert not (sandbox["root"] / "uv-tools/yoreni").exists()
    assert not (sandbox["root"] / "pipx/venvs/yoreni").exists()
    assert "INSTALLER_PROBE" not in result.stdout
    receipt = sandbox["root"] / "managed/yoreni-install.json"
    selection = receipt.read_bytes()
    _wheel(sandbox["wheels"], "0.0.1", installer_receipt=True)
    retried = _identity(_installer(sandbox))
    assert Path(retried["prefix"]).resolve() == sandbox["root"] / "managed"
    assert receipt.read_bytes() == selection
    assert json.loads(selection)["extras"] == []
    assert not (sandbox["root"] / "uv-tools/yoreni").exists()
    assert not (sandbox["root"] / "pipx/venvs/yoreni").exists()


def test_dry_run_does_not_create_tool_environment(sandbox):
    _expose(sandbox, "uv")
    result = _installer(sandbox, "--dry-run")
    assert "dedicated virtual environment" in result.stdout
    assert not (sandbox["root"] / "uv-tools").exists()
    assert "INSTALLER_PROBE" not in result.stdout


def test_installer_command_timeout_preserves_partial_diagnostics(sandbox):
    command = [BASE_PYTHON, "-I", "-c",
               "import sys, time; print('installer-stage', flush=True); "
               "print('manager-stage', file=sys.stderr, flush=True); time.sleep(10)"]
    with pytest.raises(pytest.fail.Exception, match="(?s)installer-stage.*manager-stage"):
        _run(command, sandbox, timeout=1)
