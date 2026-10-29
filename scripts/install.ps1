param(
    [switch]$Dev,
    [switch]$DryRun,
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$RemainingArgs
)

$ErrorActionPreference = "Stop"

$Package = "yoreni"
$InstallTarget = $Package
$InstallSource = "PyPI"
$script:YoreniPython = $null

function Write-Info {
    param([string]$Message)
    Write-Host $Message
}

function Fail {
    param([string]$Message)
    throw "Error: $Message"
}

function Show-InstallFailureHint {
    [Console]::Error.WriteLine("Error: could not install yoreni.")
    [Console]::Error.WriteLine("If pip mentioned externally-managed-environment, use uv, pipx, or a virtual environment instead of system pip.")
    [Console]::Error.WriteLine("You can also run manually:")
    [Console]::Error.WriteLine("  uv tool install --force --upgrade $InstallTarget")
    [Console]::Error.WriteLine("  $Python -m venv `$HOME\.yoreni\venv")
    [Console]::Error.WriteLine("  `$HOME\.yoreni\venv\Scripts\python.exe -m pip install --upgrade $InstallTarget")
    [Console]::Error.WriteLine("Then start setup with:")
    [Console]::Error.WriteLine("  yoreni onboard --wizard")
    throw "could not install yoreni from $InstallSource"
}

function Show-Usage {
    Write-Host "Usage: install.ps1 [-DryRun|--dry-run]"
    Write-Host ""
    Write-Host "This installs or upgrades yoreni from PyPI with an existing manager or a"
    Write-Host "dedicated virtual environment. Use --dry-run to print what would happen"
    Write-Host "without installing or starting setup."
    Write-Host "Stop affected gateways and back up their data before upgrading an existing install."
    Write-Host ""
    Write-Host "For current main, clone the repository and run 'python -m pip install -e .'."
}

function Test-AvailableCommand {
    param([string]$Name)
    # Get-Command can import modules for missing tools, even with an application filter.
    # Enumerate session/PATH commands instead, retaining existing functions and aliases.
    foreach ($Command in $ExecutionContext.InvokeCommand.GetCommands(
        $Name, [System.Management.Automation.CommandTypes]::All, $false)) {
        return $true
    }
    return $false
}

function Test-Python {
    param([string]$Command)
    try {
        & $Command -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)" *> $null
        return $LASTEXITCODE -eq 0
    } catch {
        return $false
    }
}

function Find-Python {
    if ($env:PYTHON) {
        if (Test-AvailableCommand $env:PYTHON) {
            if (Test-Python $env:PYTHON) {
                return $env:PYTHON
            }
            Fail "PYTHON=$env:PYTHON is not Python 3.11 or newer."
        }
        Fail "PYTHON=$env:PYTHON was not found."
    }

    foreach ($Candidate in @("python", "py")) {
        if (Test-AvailableCommand $Candidate) {
            if (Test-Python $Candidate) {
                return $Candidate
            }
        }
    }

    Fail "Python 3.11 or newer was not found. Install Python first, then rerun this command."
}

function Test-VirtualEnv {
    param([string]$Command)
    try {
        & $Command -c "import sys; raise SystemExit(0 if sys.prefix != sys.base_prefix else 1)" *> $null
        return $LASTEXITCODE -eq 0
    } catch {
        return $false
    }
}

function Get-InstallMethod {
    if (Test-VirtualEnv $Python) {
        $ActivePrefix = & $Python -I -c "import json, sys; print(json.dumps(sys.prefix))" | ConvertFrom-Json
        if ((Test-Path -LiteralPath (Join-Path $ActivePrefix "uv-receipt.toml")) -or
            (Test-Path -LiteralPath (Join-Path $ActivePrefix "pipx_metadata.json"))) {
            Fail "The active Python belongs to uv or pipx. Deactivate it and rerun this installer so its manager owns the update."
        }
        return "python"
    }

    $Existing = @()
    if (Test-AvailableCommand uv) {
        $ToolDir = & uv tool dir
        if ($LASTEXITCODE -ne 0 -or -not $ToolDir) { Fail "Could not inspect the uv tools directory." }
        if (Test-Path -LiteralPath (Join-Path $ToolDir $Package)) { $Existing += "uv" }
    }
    if (Test-AvailableCommand pipx) {
        $ToolDir = & pipx environment --value PIPX_LOCAL_VENVS
        if ($LASTEXITCODE -ne 0 -or -not $ToolDir) { Fail "Could not inspect the pipx environments directory." }
        if (Test-Path -LiteralPath (Join-Path $ToolDir $Package)) { $Existing += "pipx" }
    }
    $ProfileDir = if ($env:HOME) { $env:HOME } else { $env:USERPROFILE }
    if ($env:YORENI_VENV -or $ProfileDir) {
        $Environment = if ($env:YORENI_VENV) { $env:YORENI_VENV } else { Join-Path $ProfileDir ".yoreni\venv" }
        if (Test-Path -LiteralPath (Join-Path $Environment "pyvenv.cfg")) { $Existing += "managed" }
    }
    if ($Existing.Count -gt 1) {
        Fail "Multiple yoreni installations were found. Use the intended environment's package manager; this installer changed none of them."
    }
    if ($Existing.Count -eq 1) { return $Existing[0] }
    return "managed"
}

function Ensure-Pip {
    param([string]$Command)

    try {
        & $Command -m pip --version *> $null
    } catch {}

    if ($LASTEXITCODE -eq 0) {
        return
    }

    Write-Info "pip was not found for $Command. Trying ensurepip..."
    & $Command -m ensurepip --upgrade *> $null
    if ($LASTEXITCODE -ne 0) {
        Fail "pip is not available. Install pip for $Command, then rerun this command."
    }
}

function Select-InstalledPython {
    param([string]$Command, [string]$ExpectedEnvironment = "")

    $Probe = @'
import json
import sys
from importlib.metadata import distribution
from pathlib import Path

import yoreni

prefix = Path(sys.prefix).resolve()
package = Path(yoreni.__file__).resolve()
expected = sys.argv[1] if len(sys.argv) > 1 else ""
if sys.prefix == sys.base_prefix or (expected not in ("", "-") and prefix != Path(expected).resolve()):
    raise SystemExit("The installed interpreter does not belong to the expected virtual environment")
if not package.is_relative_to(prefix) or package != Path(distribution("yoreni").locate_file("yoreni/__init__.py")).resolve():
    raise SystemExit("yoreni imports from outside the installed package")
print(json.dumps(str(Path(sys.executable).absolute())))
'@
    # Do not resolve the interpreter symlink or launch a new pipx/uv run environment.
    # Send code on stdin so Windows PowerShell preserves its embedded quotes.
    $ExpectedRoot = if ($ExpectedEnvironment) { $ExpectedEnvironment } else { "-" }
    $Result = $Probe | & $Command -I - $ExpectedRoot
    if ($LASTEXITCODE -ne 0 -or -not $Result) {
        Fail "The installed yoreni environment could not be verified. No setup or other installation was attempted."
    }
    $script:YoreniPython = [string]($Result | ConvertFrom-Json)
}

function Invoke-Yoreni {
    param([string[]]$YoreniArgs)
    & $script:YoreniPython -I -m yoreni @YoreniArgs
}

function Get-YoreniCommand {
    return "& '" + $script:YoreniPython.Replace("'", "''") + "' -I -m yoreni"
}

function Test-FreshYoreniInstall {
    $HomeDir = [Environment]::GetFolderPath([Environment+SpecialFolder]::UserProfile)
    if (-not $HomeDir) {
        return $false
    }
    return -not (Test-Path -LiteralPath (Join-Path $HomeDir ".yoreni\config.json"))
}

function Test-BrowserSession {
    if ($env:SSH_CONNECTION -or $env:SSH_TTY -or -not [Environment]::UserInteractive) {
        return $false
    }

    $CurrentSessionId = (Get-Process -Id $PID).SessionId
    return @(
        Get-Process -Name explorer -ErrorAction SilentlyContinue |
            Where-Object { $_.SessionId -eq $CurrentSessionId }
    ).Count -gt 0
}

function Install-WithActivePython {
    Write-Info "Detected an active virtual environment. Installing into it..."
    Ensure-Pip $Python
    & $Python -m pip install --upgrade $InstallTarget
    if ($LASTEXITCODE -ne 0) {
        Show-InstallFailureHint
    }
    Select-InstalledPython $Python
}

function Install-WithUv {
    Write-Info "Installing or upgrading yoreni from $InstallSource with uv tool..."
    $ToolDir = & uv tool dir
    if ($LASTEXITCODE -ne 0 -or -not $ToolDir) {
        Fail "Could not locate the uv tools directory."
    }
    $Environment = Join-Path $ToolDir $Package
    if (Test-Path -LiteralPath (Join-Path $Environment "uv-receipt.toml")) {
        & uv tool upgrade $InstallTarget
    } elseif (Test-Path -LiteralPath $Environment) {
        Fail "The uv environment exists without its installation record. Repair it with uv before rerunning this installer."
    } else {
        & uv tool install --python $Python $InstallTarget
    }
    if ($LASTEXITCODE -ne 0) {
        Fail "uv could not install or upgrade yoreni. Resolve the error above and retry; no other installation was attempted."
    }
    Select-InstalledPython (Join-Path $Environment "Scripts\python.exe") $Environment
}

function Install-WithPipx {
    Write-Info "Installing or upgrading yoreni from $InstallSource with pipx..."
    $ToolDir = & pipx environment --value PIPX_LOCAL_VENVS
    if ($LASTEXITCODE -ne 0 -or -not $ToolDir) {
        Fail "Could not locate the pipx environments directory."
    }
    $Environment = Join-Path $ToolDir $Package
    if (Test-Path -LiteralPath (Join-Path $Environment "pipx_metadata.json")) {
        # Keep this stdlib bootstrap check in sync with install.sh.
        $Inspect = @'
import json
import re
import sys
from pathlib import Path

try:
    path = Path(sys.argv[1])
    if path.is_symlink() or path.stat().st_size > 1024 * 1024:
        raise ValueError
    record = json.loads(path.read_text(encoding="utf-8"))
    main = record["main_package"]
    name = r"(?i)yoreni(?:\[[a-z0-9_., -]+\])?"
    spec = main["package_or_url"]
    # Never discard registry constraints, lock workflows or recorded options.
    allowed = re.fullmatch(name, spec)
    if record["pipx_metadata_version"] not in {"0.5", "0.12"} or not allowed or main.get("pip_args") or main.get("lock_file"):
        raise ValueError
except (OSError, ValueError, KeyError, TypeError):
    raise SystemExit("Cannot safely reuse pipx installation settings; inspect the original requirement and options.") from None
'@
        $Inspect | & $Python -I - (Join-Path $Environment "pipx_metadata.json")
        if ($LASTEXITCODE -ne 0) { Fail "Review the original pipx installation settings; no installation was attempted." }
        & pipx upgrade $Package
    } elseif (Test-Path -LiteralPath $Environment) {
        Fail "The pipx environment exists without its installation record. Repair it with pipx before rerunning this installer."
    } else {
        & pipx install --python $Python $InstallTarget
    }
    if ($LASTEXITCODE -ne 0) {
        Fail "pipx could not install or upgrade yoreni. Resolve the error above and retry; no other installation was attempted."
    }
    Select-InstalledPython (Join-Path $Environment "Scripts\python.exe") $Environment
}

function Install-WithManagedVenv {
    $HomeDir = if ($env:HOME) { $env:HOME } elseif ($env:USERPROFILE) { $env:USERPROFILE } else { $null }
    if (-not $HomeDir) {
        Fail "HOME is not set; cannot create a managed virtual environment."
    }

    $VenvDir = if ($env:YORENI_VENV) { $env:YORENI_VENV } else { Join-Path $HomeDir ".yoreni\venv" }
    $VenvPython = Join-Path $VenvDir "Scripts\python.exe"
    if (-not (Test-Path $VenvPython)) {
        Write-Info "Creating a dedicated virtual environment at $VenvDir..."
        $Parent = Split-Path -Parent $VenvDir
        if ($Parent) {
            New-Item -ItemType Directory -Force -Path $Parent *> $null
        }
        # Selection survives a failed first venv/pip operation. It is not an
        # installed/readiness claim, nor adoption of an existing environment.
        $RecordSelection = @'
import json, os, sys
from pathlib import Path
prefix = Path(sys.argv[1]).resolve()
if any((prefix / name).exists() for name in ('uv-receipt.toml', 'pipx_metadata.json')):
    raise SystemExit('This environment belongs to another package manager.')
prefix.mkdir(parents=True, exist_ok=True)
path = prefix / 'yoreni-install.json'
record = {'schema_version': 1, 'owner': 'yoreni-installer', 'environment': str(prefix), 'extras': []}
if path.is_symlink():
    raise SystemExit('The installation selection is invalid; no receipt was replaced.')
if path.exists():
    if path.stat().st_size > 4096 or json.loads(path.read_bytes()) != record:
        raise SystemExit('The installation selection changed; no receipt was replaced.')
else:
    with path.open('x', encoding='utf-8') as stream:
        json.dump(record, stream, sort_keys=True)
        stream.write('\n')
        stream.flush()
        os.fsync(stream.fileno())
    path.chmod(0o600)
'@
        & $Python -I -c $RecordSelection $VenvDir
        if ($LASTEXITCODE -ne 0) { Fail "Could not record the new installation's update settings." }
        & $Python -m venv $VenvDir
        if ($LASTEXITCODE -ne 0) {
            Show-InstallFailureHint
        }
    }

    if (-not (Test-Python $VenvPython)) {
        Fail "The managed venv uses Python older than 3.11. Remove it or set YORENI_VENV to a new path."
    }

    Write-Info "Installing or upgrading yoreni from $InstallSource in $VenvDir..."
    Ensure-Pip $VenvPython
    & $VenvPython -m pip install --upgrade $InstallTarget
    if ($LASTEXITCODE -ne 0) {
        Show-InstallFailureHint
    }

    Select-InstalledPython $VenvPython $VenvDir
}

$PreviousOutputEncoding = [Console]::OutputEncoding
try {
# uv/pipx emit UTF-8 paths; Windows PowerShell otherwise uses the console code page.
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)

foreach ($Arg in $RemainingArgs) {
    switch ($Arg) {
        "--dev" {
            $Dev = $true
        }
        "--dry-run" {
            $DryRun = $true
        }
        "-h" {
            Show-Usage
            return
        }
        "--help" {
            Show-Usage
            return
        }
        default {
            Fail "Unknown option: $Arg"
        }
    }
}

if ($Dev) {
    Fail "--dev installed an untracked main snapshot and is no longer supported; clone the repository and run 'python -m pip install -e .' instead."
}

$Python = Find-Python
$Version = & $Python --version
Write-Info "Using Python: $Version"
$InstallMethod = Get-InstallMethod

if ($DryRun) {
    Write-Info "Dry run: would install or upgrade yoreni from $InstallSource."
    if ($InstallMethod -eq "python") {
        Write-Info "Dry run: active virtual environment detected; would run: $Python -m pip install --upgrade $InstallTarget"
        Write-Info "Dry run: would start yoreni with this environment's Python in isolated mode."
    } elseif ($InstallMethod -eq "uv") {
        Write-Info "Dry run: would install with uv tool install, or use uv tool upgrade for an existing installation."
        Write-Info "Dry run: would start yoreni from its persistent uv tool environment."
    } elseif ($InstallMethod -eq "pipx") {
        Write-Info "Dry run: would review pipx installation settings before upgrading an existing environment."
        Write-Info "Dry run: would start yoreni from its persistent pipx environment."
    } else {
        $HomeDir = if ($env:HOME) { $env:HOME } elseif ($env:USERPROFILE) { $env:USERPROFILE } else { "~" }
        $VenvDir = if ($env:YORENI_VENV) { $env:YORENI_VENV } else { Join-Path $HomeDir ".yoreni\venv" }
        Write-Info "Dry run: would create or reuse a dedicated virtual environment: $VenvDir"
        Write-Info "Dry run: would run: $VenvDir\Scripts\python.exe -m pip install --upgrade $InstallTarget"
        Write-Info "Dry run: would start yoreni with $VenvDir\Scripts\python.exe in isolated mode."
    }
    if ($env:YORENI_SKIP_WIZARD -eq "1") {
        Write-Info "Dry run: would skip automatic setup because YORENI_SKIP_WIZARD=1."
    } elseif ((Test-FreshYoreniInstall) -and (Test-BrowserSession)) {
        Write-Info "Dry run: would start the WebUI for this fresh desktop install."
        Write-Info "Dry run: would fall back to the setup wizard if this build lacks a WebUI."
    } else {
        Write-Info "Dry run: would run the setup wizard."
    }
    Write-Info "Dry run: no changes made."
    return
}

if ($InstallMethod -eq "python") {
    Install-WithActivePython
} elseif ($InstallMethod -eq "uv") {
    Install-WithUv
} elseif ($InstallMethod -eq "pipx") {
    Install-WithPipx
} else {
    Write-Info "Using a dedicated virtual environment to avoid system pip."
    Install-WithManagedVenv
}

Write-Info "Installed yoreni:"
Invoke-Yoreni @("--version")
if ($LASTEXITCODE -ne 0) {
    Fail "yoreni was installed, but the command could not be started."
}

if ($env:YORENI_SKIP_WIZARD -eq "1") {
    Write-Info "Skipping automatic setup because YORENI_SKIP_WIZARD=1."
    Write-Info "Run this later: $(Get-YoreniCommand) webui"
    return
}

if ((Test-FreshYoreniInstall) -and (Test-BrowserSession)) {
    Invoke-Yoreni @("webui", "--help") *> $null
    if ($LASTEXITCODE -eq 0) {
        Write-Info "Starting yoreni WebUI..."
        Write-Info "Configure your first provider and model in Settings > Models."
        Write-Info "Run this later: $(Get-YoreniCommand) webui"
        Invoke-Yoreni @("webui", "--yes")
        if ($LASTEXITCODE -ne 0) {
            Fail "WebUI did not start."
        }
        return
    }
    Write-Info "This yoreni build does not support the WebUI command yet."
    Write-Info "Falling back to the setup wizard..."
}

Write-Info "Starting setup wizard..."
Invoke-Yoreni @("onboard", "--wizard")
if ($LASTEXITCODE -ne 0) {
    Fail "Setup wizard did not complete."
}

Write-Info "Done. Try: $(Get-YoreniCommand) agent -m `"Hello!`""
} finally {
    [Console]::OutputEncoding = $PreviousOutputEncoding
}
