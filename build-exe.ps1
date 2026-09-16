#Requires -Version 7.0
<#
.SYNOPSIS
    Builds appsettings_editor.py into a standalone Windows .exe using uv + PyInstaller.

.DESCRIPTION
    Run this from the same folder as appsettings_editor.py (or pass -ScriptPath).
    Must be run on Windows (PyInstaller does not cross-compile).

.PARAMETER ScriptPath
    Path to the python script to build. Defaults to .\appsettings_editor.py

.PARAMETER OutputName
    Name of the resulting executable (without .exe). Defaults to AppSettingsEditor.

.PARAMETER OneDir
    Use --onedir instead of --onefile. Slower to distribute (a whole folder)
    but often plays nicer with antivirus/SmartScreen than a single-file exe.

.EXAMPLE
    .\build-exe.ps1

.EXAMPLE
    .\build-exe.ps1 -OneDir -OutputName SettingsEditor
#>

[CmdletBinding()]
param(
    [string]$ScriptPath = ".\appsettings_editor.py",
    [string]$OutputName = "AppSettingsEditor",
    [switch]$OneDir
)

$ErrorActionPreference = "Stop"

function Write-Step($msg) {
    Write-Host "`n==> $msg" -ForegroundColor Cyan
}

function Fail($msg) {
    Write-Host "ERROR: $msg" -ForegroundColor Red
    exit 1
}

# --- Sanity checks -----------------------------------------------------
if (-not $IsWindows) {
    Fail "This must be run on Windows -- PyInstaller cannot cross-compile a .exe from another OS."
}

if (-not (Test-Path $ScriptPath)) {
    Fail "Could not find script at '$ScriptPath'. Pass -ScriptPath if it's elsewhere."
}

if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    Fail "uv is not on PATH. Install it first: https://docs.astral.sh/uv/getting-started/installation/"
}

$ScriptDir  = Split-Path -Parent (Resolve-Path $ScriptPath)
$ScriptFile = Split-Path -Leaf (Resolve-Path $ScriptPath)
Push-Location $ScriptDir

try {
    # --- 1. venv ---------------------------------------------------------
    Write-Step "Creating/refreshing virtual environment with uv"
    uv venv
    if ($LASTEXITCODE -ne 0) { Fail "uv venv failed." }

    # --- 2. Tkinter check --------------------------------------------------
    Write-Step "Checking Tkinter is available in this Python"
    $tkCheck = uv run python -c "import tkinter; print('ok')" 2>&1
    if ($tkCheck -notmatch "ok") {
        Fail @"
Tkinter is not available in uv's managed Python build.
Fix: install Python from https://python.org (it always bundles Tk), then re-run:
    uv venv --python "C:\Path\To\python.exe"
and run this script again.
Details: $tkCheck
"@
    }
    Write-Host "Tkinter OK." -ForegroundColor Green

    # --- 3. Install PyInstaller -------------------------------------------
    Write-Step "Installing PyInstaller"
    uv pip install pyinstaller
    if ($LASTEXITCODE -ne 0) { Fail "Failed to install pyinstaller." }

    # --- 4. Build ----------------------------------------------------------
    Write-Step "Building executable"
    $mode = if ($OneDir) { "--onedir" } else { "--onefile" }
    uv run pyinstaller $mode --windowed --noconfirm --name $OutputName $ScriptFile
    if ($LASTEXITCODE -ne 0) { Fail "PyInstaller build failed." }

    # --- 5. Report -----------------------------------------------------------
    Write-Step "Build complete"
    if ($OneDir) {
        $outPath = Join-Path $ScriptDir "dist\$OutputName"
        Write-Host "Output folder: $outPath" -ForegroundColor Green
        Write-Host "Copy the WHOLE folder to the server and run $OutputName.exe inside it."
    } else {
        $outPath = Join-Path $ScriptDir "dist\$OutputName.exe"
        Write-Host "Output file: $outPath" -ForegroundColor Green
        Write-Host "Copy this single .exe to the server."
    }
    Write-Host "`nNote: Tkinter apps need a desktop session (RDP/console login) to render on Windows Server." -ForegroundColor Yellow
}
finally {
    Pop-Location
}
