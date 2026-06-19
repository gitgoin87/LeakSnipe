# Launch the original Python Live HUD overlay (transparent pywin32 overlay for BetACR).
$ErrorActionPreference = "Stop"

$ScriptDir = if ($PSScriptRoot) {
    $PSScriptRoot
} elseif ($MyInvocation -and $MyInvocation.MyCommand.Path) {
    Split-Path -Parent $MyInvocation.MyCommand.Path
} else {
    throw "Cannot resolve start-python-hud.ps1 directory"
}
if ($ScriptDir -match '^\\\\\?\\(.+)') { $ScriptDir = $Matches[1] }

$Root = Split-Path -Parent $ScriptDir
Set-Location $Root

$Script = Join-Path $Root "poker_gui.py"
if (-not (Test-Path $Script)) {
    Write-Error "poker_gui.py not found at $Script"
}

$LogPath = Join-Path $env:TEMP "leaksnipe_python_hud.log"
$PythonCandidates = @()
if ($env:LEAKSNIPE_PYTHON) { $PythonCandidates += $env:LEAKSNIPE_PYTHON }
$PythonCandidates += @("python", "python3", "py")

$lastErr = $null
foreach ($py in $PythonCandidates) {
    try {
        if ($py -eq "py") {
            $proc = Start-Process -FilePath $py -ArgumentList @("-3", $Script, "--live-hud") `
                -WorkingDirectory $Root -PassThru -RedirectStandardOutput $LogPath -RedirectStandardError $LogPath
        } else {
            $proc = Start-Process -FilePath $py -ArgumentList @($Script, "--live-hud") `
                -WorkingDirectory $Root -PassThru -RedirectStandardOutput $LogPath -RedirectStandardError $LogPath
        }
        Start-Sleep -Milliseconds 800
        if ($proc.HasExited -and $proc.ExitCode -ne 0) {
            $tail = Get-Content $LogPath -Tail 20 -ErrorAction SilentlyContinue
            $lastErr = "Exit $($proc.ExitCode). Log tail:`n$($tail -join "`n")"
            continue
        }
        Write-Host "Python Live HUD started (PID $($proc.Id)). Open an ACR table to see overlays."
        $PidPath = Join-Path $env:TEMP "leaksnipe_python_hud.pid"
        Set-Content -Path $PidPath -Value $proc.Id -Encoding ascii
        Write-Host "Log: $LogPath"
        exit 0
    } catch {
        $lastErr = $_.Exception.Message
        continue
    }
}

Write-Error "Could not start Python Live HUD. Install Python 3.9+ and pywin32 (pip install pywin32). Last error: $lastErr. Log: $LogPath"
