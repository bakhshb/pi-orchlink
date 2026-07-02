#requires -Version 5.1
[CmdletBinding()]
param(
    [string]$Repo = $(if ($env:ORCHLINK_REPO_URL) { $env:ORCHLINK_REPO_URL } else { "https://github.com/bakhshb/pi-orchlink.git" }),
    [string]$Ref = $(if ($env:ORCHLINK_REF) { $env:ORCHLINK_REF } else { "main" }),
    [string]$Dir = $(if ($env:ORCHLINK_INSTALL_DIR) { $env:ORCHLINK_INSTALL_DIR } elseif ($env:LOCALAPPDATA) { Join-Path $env:LOCALAPPDATA "orchlink" } else { "" }),
    [string]$BinDir = $(if ($env:ORCHLINK_BIN_DIR) { $env:ORCHLINK_BIN_DIR } elseif ($env:LOCALAPPDATA) { Join-Path (Join-Path $env:LOCALAPPDATA "orchlink") "bin" } else { "" }),
    [string]$Python = $(if ($env:ORCHLINK_PYTHON) { $env:ORCHLINK_PYTHON } else { "python" }),
    [switch]$Force,
    [switch]$Uninstall
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$AppName = "orchlink"
if (-not $Dir -or -not $BinDir) {
    Write-Host "[orchlink error] LOCALAPPDATA is not set. Pass -Dir and -BinDir, or set ORCHLINK_INSTALL_DIR and ORCHLINK_BIN_DIR." -ForegroundColor Red -ErrorAction Continue
    throw "LOCALAPPDATA is not set."
}
$InstallDir = [System.IO.Path]::GetFullPath($ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($Dir))
$CommandDir = [System.IO.Path]::GetFullPath($ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($BinDir))
$VenvDir = Join-Path $InstallDir ".venv"
$MarkerFile = Join-Path $InstallDir ".orchlink-install"

function Write-OrchLog([string]$Message) {
    Write-Host "[orchlink] $Message" -ForegroundColor Green
}

function Write-OrchWarn([string]$Message) {
    Write-Host "[orchlink warn] $Message" -ForegroundColor Yellow
}

function Fail([string]$Message) {
    Write-Host "[orchlink error] $Message" -ForegroundColor Red -ErrorAction Continue
    throw $Message
}

function Require-Command([string]$Name) {
    if (-not (Get-Command $Name -ErrorAction SilentlyContinue)) {
        Fail "Required command not found: $Name"
    }
}

function Test-Interactive {
    return [Environment]::UserInteractive
}

function Confirm-OrchAction([string]$Prompt) {
    if (-not (Test-Interactive)) {
        return $false
    }
    $reply = Read-Host $Prompt
    return ($reply -match '(?i)^(y|yes)$')
}

function Update-CurrentProcessPath {
    $machinePath = [Environment]::GetEnvironmentVariable("Path", "Machine")
    $userPath = [Environment]::GetEnvironmentVariable("Path", "User")
    $parts = @($machinePath, $userPath) | Where-Object { $_ }
    if ($parts.Count -gt 0) {
        $env:Path = ($parts -join ";")
    }
}

function Write-DependencyHelp {
    Write-Host ""
    Write-Host "Install the missing requirements, then rerun this installer."
    Write-Host ""
    Write-Host "Automatic install command examples:"
    Write-Host "  winget install --id Python.Python.3.12 -e --source winget"
    Write-Host "  winget install --id Git.Git -e --source winget"
    Write-Host ""
    Write-Host "Manual downloads:"
    Write-Host "  Python: https://www.python.org/downloads/windows/"
    Write-Host "  Git:    https://git-scm.com/download/win"
}

function Test-PythonReady {
    $script:UsePyLauncher = $false
    $code = "import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)"
    if (Get-Command $Python -ErrorAction SilentlyContinue) {
        & $Python -c $code *> $null
        if ($LASTEXITCODE -eq 0) {
            return $true
        }
        if ($env:ORCHLINK_PYTHON -or $Python -ne "python") {
            return $false
        }
    }
    if (-not $env:ORCHLINK_PYTHON -and $Python -eq "python" -and (Get-Command py -ErrorAction SilentlyContinue)) {
        & py -3 -c $code *> $null
        if ($LASTEXITCODE -eq 0) {
            $script:UsePyLauncher = $true
            return $true
        }
    }
    return $false
}

function Get-MissingRequirements {
    param([bool]$NeedsGit)
    $script:CustomPythonProblem = ""
    $missing = @()
    if ($NeedsGit -and -not (Get-Command git -ErrorAction SilentlyContinue)) {
        $missing += "Git"
    }
    if (-not (Test-PythonReady)) {
        if (-not $env:ORCHLINK_PYTHON -and $Python -eq "python") {
            $missing += "Python 3.11+"
        } else {
            $script:CustomPythonProblem = "Python command is missing or too old: $Python. Install Python 3.11+ or pass -Python with a compatible interpreter."
        }
    }
    return $missing
}

function Install-MissingRequirements {
    param([string[]]$Missing)
    Write-OrchWarn "Orchlink requires Python 3.11+ and Git."
    Write-OrchWarn "Missing requirements:"
    foreach ($item in $Missing) {
        Write-Host "  - $item"
    }
    if (-not (Test-Interactive)) {
        Write-DependencyHelp
        Fail "Missing requirements. Install them and rerun this installer."
    }
    if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
        Write-DependencyHelp
        Fail "winget was not found, so Orchlink cannot install requirements automatically."
    }
    if (-not (Confirm-OrchAction "Would you like Orchlink to install them with winget now? [y/N]")) {
        Write-DependencyHelp
        Fail "Missing requirements. Install them and rerun this installer."
    }
    if ($Missing -contains "Python 3.11+") {
        Write-OrchLog "Installing Python with winget"
        winget install --id Python.Python.3.12 -e --source winget --accept-package-agreements --accept-source-agreements
        if ($LASTEXITCODE -ne 0) { Fail "winget failed to install Python." }
    }
    if ($Missing -contains "Git") {
        Write-OrchLog "Installing Git with winget"
        winget install --id Git.Git -e --source winget --accept-package-agreements --accept-source-agreements
        if ($LASTEXITCODE -ne 0) { Fail "winget failed to install Git." }
    }
    Update-CurrentProcessPath
}

function Ensure-Requirements {
    param([bool]$NeedsGit)
    $missing = @(Get-MissingRequirements -NeedsGit $NeedsGit)
    if ($script:CustomPythonProblem) {
        Fail $script:CustomPythonProblem
    }
    if ($missing.Count -eq 0) {
        return
    }
    Install-MissingRequirements -Missing $missing
    $missing = @(Get-MissingRequirements -NeedsGit $NeedsGit)
    if ($script:CustomPythonProblem) {
        Fail $script:CustomPythonProblem
    }
    if ($missing.Count -gt 0) {
        Write-DependencyHelp
        Fail "Requirements are still missing after installation: $($missing -join ', ')"
    }
    Write-OrchLog "Requirements installed. Continuing."
}

function Invoke-Python([string[]]$Arguments) {
    if ($script:UsePyLauncher) {
        & py -3 @Arguments
    } else {
        & $Python @Arguments
    }
    if ($LASTEXITCODE -ne 0) {
        Fail "Python command failed: $($Arguments -join ' ')"
    }
}

function Resolve-Python {
    if (Test-PythonReady) {
        return
    }
    if (-not $env:ORCHLINK_PYTHON -and $Python -eq "python") {
        Fail "Python 3.11+ is required. Install Python or rerun this installer and accept the winget prompt."
    }
    Fail "Python command is missing or too old: $Python. Install Python 3.11+ or pass -Python with a compatible interpreter."
}

function Test-PythonVersion {
    $code = "import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)"
    try {
        Invoke-Python @("-c", $code)
    } catch {
        Fail "Python 3.11+ is required. Set ORCHLINK_PYTHON or pass -Python with a compatible interpreter."
    }
}

function Remove-CommandShims {
    foreach ($name in @("orch", "orch.cmd", "orchlink.cmd")) {
        Remove-Item -Force -ErrorAction SilentlyContinue (Join-Path $CommandDir $name)
    }
}

function Uninstall-Orchlink {
    if (Test-Path $InstallDir) {
        Write-OrchLog "Removing $InstallDir"
        try {
            Remove-Item -Recurse -Force $InstallDir
        } catch {
            Fail "Could not remove $InstallDir. Close any running Orchlink/Pi terminals and retry with -Uninstall. Original error: $($_.Exception.Message)"
        }
    }
    Write-OrchLog "Removing command shims from $CommandDir"
    Remove-CommandShims
    Write-OrchLog "Uninstalled Orchlink"
    Write-OrchWarn "If $CommandDir was added to your user PATH, remove it manually if you no longer need it."
}

function Copy-LocalSource([string]$SourceDir) {
    $resolvedSource = [System.IO.Path]::GetFullPath($ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($SourceDir))
    Write-OrchLog "Installing from local source: $resolvedSource"
    if ((Test-Path $InstallDir) -and ([System.IO.Path]::GetFullPath($InstallDir) -eq $resolvedSource)) {
        New-Item -ItemType File -Force $MarkerFile | Out-Null
        return
    }
    if (Test-Path $InstallDir) {
        if ((Test-Path $MarkerFile) -or $Force) {
            Remove-Item -Recurse -Force $InstallDir
        } else {
            Fail "$InstallDir already exists. Re-run with -Force to replace it."
        }
    }
    New-Item -ItemType Directory -Force $InstallDir | Out-Null
    $exclude = @(".git", ".venv", ".orch", ".opencode", "__pycache__", ".pytest_cache")
    Get-ChildItem -Force $resolvedSource | Where-Object { $exclude -notcontains $_.Name } | ForEach-Object {
        Copy-Item -Recurse -Force $_.FullName -Destination $InstallDir
    }
    New-Item -ItemType File -Force $MarkerFile | Out-Null
}

function Clone-OrUpdateRepo {
    Require-Command git
    New-Item -ItemType Directory -Force (Split-Path -Parent $InstallDir) | Out-Null
    $gitDir = Join-Path $InstallDir ".git"
    if ((Test-Path $gitDir) -and -not $Force) {
        Write-OrchLog "Updating existing checkout in $InstallDir"
        git -C $InstallDir fetch --tags --prune origin
        if ($LASTEXITCODE -ne 0) { Fail "git fetch failed" }
        git -C $InstallDir checkout $Ref
        if ($LASTEXITCODE -ne 0) { Fail "git checkout failed: $Ref" }
        $currentBranch = git -C $InstallDir symbolic-ref -q --short HEAD
        if ($LASTEXITCODE -eq 0 -and $currentBranch -eq $Ref) {
            git -C $InstallDir pull --ff-only origin $Ref
            if ($LASTEXITCODE -ne 0) { Fail "git pull failed: $Ref" }
        } else {
            Write-OrchWarn "Skipping git pull for detached ref: $Ref"
        }
        New-Item -ItemType File -Force $MarkerFile | Out-Null
        return
    }
    if (Test-Path $InstallDir) {
        if ((Test-Path $MarkerFile) -or $Force) {
            Remove-Item -Recurse -Force $InstallDir
        } else {
            Fail "$InstallDir already exists and is not managed by this installer. Re-run with -Force to replace it."
        }
    }
    Write-OrchLog "Cloning $Repo#$Ref into $InstallDir"
    git clone --depth 1 --branch $Ref $Repo $InstallDir
    if ($LASTEXITCODE -ne 0) {
        Remove-Item -Recurse -Force -ErrorAction SilentlyContinue $InstallDir
        git clone $Repo $InstallDir
        if ($LASTEXITCODE -ne 0) { Fail "git clone failed: $Repo" }
        git -C $InstallDir checkout $Ref
        if ($LASTEXITCODE -ne 0) { Fail "git checkout failed: $Ref" }
    }
    New-Item -ItemType File -Force $MarkerFile | Out-Null
}

function Install-Package {
    Resolve-Python
    Test-PythonVersion
    Write-OrchLog "Creating virtual environment: $VenvDir"
    Invoke-Python @("-m", "venv", $VenvDir)
    $VenvPython = Join-Path $VenvDir "Scripts\python.exe"
    Write-OrchLog "Installing Orchlink package"
    & $VenvPython -m pip install --upgrade pip setuptools wheel | Out-Null
    if ($LASTEXITCODE -ne 0) { Fail "pip upgrade failed" }
    & $VenvPython -m pip install -e $InstallDir
    if ($LASTEXITCODE -ne 0) { Fail "pip install failed" }
}

function Write-CommandShim {
    New-Item -ItemType Directory -Force $CommandDir | Out-Null
    $OrchExe = Join-Path $VenvDir "Scripts\orch.exe"
    $CmdShimPath = Join-Path $CommandDir "orch.cmd"
    $CmdShim = "@echo off`r`n`"$OrchExe`" %*`r`n"
    [System.IO.File]::WriteAllText($CmdShimPath, $CmdShim, [System.Text.Encoding]::ASCII)
    $ShellShimPath = Join-Path $CommandDir "orch"
    $ShellOrchExe = $OrchExe -replace "\\", "/"
    $ShellShim = "#!/usr/bin/env sh`nexec `"$ShellOrchExe`" `"`$@`"`n"
    [System.IO.File]::WriteAllText($ShellShimPath, $ShellShim, [System.Text.Encoding]::ASCII)
    Remove-Item -Force -ErrorAction SilentlyContinue (Join-Path $CommandDir "orchlink.cmd")
}

function Ensure-UserPath {
    $currentUserPath = [Environment]::GetEnvironmentVariable("Path", "User")
    if (-not $currentUserPath) { $currentUserPath = "" }
    $entries = $currentUserPath -split ";" | Where-Object { $_ }
    $alreadyPresent = $false
    foreach ($entry in $entries) {
        if ($entry.TrimEnd("\") -ieq $CommandDir.TrimEnd("\")) {
            $alreadyPresent = $true
            break
        }
    }
    if (-not $alreadyPresent) {
        $newPath = if ($currentUserPath) { "$currentUserPath;$CommandDir" } else { $CommandDir }
        [Environment]::SetEnvironmentVariable("Path", $newPath, "User")
        Write-OrchWarn "$CommandDir was added to your user PATH. Open a new terminal if 'orch' is not found."
    }
    if (($env:Path -split ";") -notcontains $CommandDir) {
        $env:Path = "$CommandDir;$env:Path"
    }
}

function Print-Success {
    Write-OrchLog "Installed Orchlink"
    Write-Host ""
    Write-Host "Commands:"
    Write-Host "  $CommandDir\orch.cmd"
    Write-Host "  $CommandDir\orch"
    Write-Host ""
    if (Get-Command orch -ErrorAction SilentlyContinue) {
        Write-OrchLog "orch is available on PATH: $((Get-Command orch).Source)"
    } else {
        Write-OrchWarn "$CommandDir is not visible on PATH in this terminal yet. Open a new terminal or run:"
        Write-Host "  `$env:Path = `"$CommandDir;`$env:Path`""
    }
    Write-Host ""
    Write-Host "Next steps:"
    Write-Host "  cd C:\path\to\your\project"
    Write-Host "  orch init"
    Write-Host "  orch lead    # terminal 1"
    Write-Host "  orch work    # terminal 2"
}

if ($Uninstall) {
    Uninstall-Orchlink
    exit 0
}

Write-OrchLog "Installing Orchlink to $InstallDir"
$LocalSourceDir = $env:ORCHLINK_SOURCE_DIR
$ScriptDir = if ($PSScriptRoot) { $PSScriptRoot } else { "" }
if (-not $LocalSourceDir -and $ScriptDir -and (Test-Path (Join-Path $ScriptDir "pyproject.toml")) -and (Test-Path (Join-Path $ScriptDir "src\orchlink"))) {
    $LocalSourceDir = $ScriptDir
}

if ($LocalSourceDir -and (Test-Path (Join-Path $LocalSourceDir "pyproject.toml")) -and (Test-Path (Join-Path $LocalSourceDir "src\orchlink")) -and -not $env:ORCHLINK_REPO_URL) {
    Ensure-Requirements -NeedsGit $false
    Copy-LocalSource $LocalSourceDir
} else {
    Ensure-Requirements -NeedsGit $true
    Clone-OrUpdateRepo
}

Install-Package
Write-CommandShim
Ensure-UserPath
& (Join-Path $CommandDir "orch.cmd") --help | Out-Null
if ($LASTEXITCODE -ne 0) { Fail "Installed orch command failed validation" }
Print-Success
