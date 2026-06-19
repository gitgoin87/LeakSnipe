# Install Python deps for leaksnipe-ui sidecar. Run from anywhere.
$ErrorActionPreference = "Stop"

$ScriptDir = if ($PSScriptRoot) {
    $PSScriptRoot
} elseif ($MyInvocation -and $MyInvocation.MyCommand.Path) {
    Split-Path -Parent $MyInvocation.MyCommand.Path
} else {
    throw "Cannot resolve install-sidecar.ps1 directory"
}
if ($ScriptDir -match '^\\\\\?\\(.+)') { $ScriptDir = $Matches[1] }
$Script:LeakSnipeScriptDir = $ScriptDir

. (Join-Path $ScriptDir "python-env.ps1")

$root = Get-LeakSnipeRoot
Set-Location $root

try {
    Install-LeakSnipePythonDeps -Root $root | Out-Null
    Write-Host ""
    Write-Host "Sidecar dependencies installed in .venv"
    Write-Host "Start with Start-Sidecar.bat or Launch-LeakSnipe.bat"
} catch {
    Write-Error $_.Exception.Message
    exit 1
}
