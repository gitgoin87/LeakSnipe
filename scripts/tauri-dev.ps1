# LeakSnipe dev launcher - run via Launch-LeakSnipe.bat or:
#   powershell -ExecutionPolicy Bypass -File scripts\tauri-dev.ps1
$ErrorActionPreference = "Stop"

function Find-VcVars {
    $candidates = @(
        "${env:ProgramFiles(x86)}\Microsoft Visual Studio\2022\BuildTools\VC\Auxiliary\Build\vcvars64.bat",
        "${env:ProgramFiles(x86)}\Microsoft Visual Studio\2022\Community\VC\Auxiliary\Build\vcvars64.bat",
        "${env:ProgramFiles(x86)}\Microsoft Visual Studio\2022\Professional\VC\Auxiliary\Build\vcvars64.bat",
        "${env:ProgramFiles(x86)}\Microsoft Visual Studio\2022\Enterprise\VC\Auxiliary\Build\vcvars64.bat",
        "${env:ProgramFiles}\Microsoft Visual Studio\2022\Community\VC\Auxiliary\Build\vcvars64.bat"
    )
    foreach ($path in $candidates) {
        if (Test-Path $path) { return $path }
    }
    return $null
}

$root = Split-Path -Parent $PSScriptRoot
$uiDir = Join-Path $root "leaksnipe-ui"
$req = Join-Path $root "sidecar\requirements.txt"

if (-not (Test-Path $req)) {
    Write-Error "Missing $req - is this the LeakSnipe repo root?"
    exit 1
}

if (-not (Get-Command node -ErrorAction SilentlyContinue)) {
    Write-Error "Node.js not found. Install from https://nodejs.org and reopen this window."
    exit 1
}

if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
    Write-Error "Python not found. Install Python 3.9+ and reopen this window."
    exit 1
}

Set-Location $root
Write-Host "Installing Python sidecar dependencies..."
python -m pip install -r $req -q
if ($LASTEXITCODE -ne 0) {
    Write-Warning "pip install had issues. Try: Install-Sidecar.bat"
}

Set-Location $uiDir
if (-not (Test-Path "node_modules")) {
    Write-Host "First run - installing npm packages (may take a minute)..."
    npm install
    if ($LASTEXITCODE -ne 0) {
        Write-Error "npm install failed in leaksnipe-ui"
        exit 1
    }
}

$portInUse = Get-NetTCPConnection -LocalPort 8765 -State Listen -ErrorAction SilentlyContinue
if ($portInUse) {
    Write-Host "Note: port 8765 in use (sidecar may already be running from a prior session)."
}

$vcvars = Find-VcVars
Write-Host "Starting LeakSnipe (first Rust compile can take 1-2 minutes)..."
Write-Host ""

if ($vcvars) {
    $vcvarsQuoted = '"' + $vcvars + '"'
    cmd /c "$vcvarsQuoted >nul 2>&1 && npm run tauri dev"
} else {
    Write-Warning 'Visual Studio C++ Build Tools not found - trying cargo without vcvars...'
    Write-Warning 'If link.exe errors appear, install VS Build Tools with Desktop C++ workload.'
    npm run tauri dev
}

exit $LASTEXITCODE
