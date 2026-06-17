# Install Python deps for leaksnipe-ui sidecar. Run from anywhere.
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$req = Join-Path $root "sidecar\requirements.txt"

if (-not (Test-Path $req)) {
    Write-Error "Requirements file not found: $req`nRun this script from the LeakSnipe repo (scripts/install-sidecar.ps1)."
    exit 1
}

Set-Location $root
Write-Host "Installing sidecar deps from: $req"
python -m pip install -r $req
if ($LASTEXITCODE -ne 0) {
    Write-Error "pip install failed (exit $LASTEXITCODE). Try: py -3 -m pip install -r sidecar\requirements.txt"
    exit $LASTEXITCODE
}
Write-Host "Sidecar dependencies installed."
