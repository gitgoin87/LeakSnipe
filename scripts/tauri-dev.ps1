# LeakSnipe dev launcher - run via Launch-LeakSnipe.bat or:
#   powershell -ExecutionPolicy Bypass -File scripts\tauri-dev.ps1
$ErrorActionPreference = "Stop"

$ViteDevPort = 1420

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

function Get-PortListenerProcessIds {
    param([int]$Port)
    $conns = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
    @($conns | Select-Object -ExpandProperty OwningProcess -Unique | Where-Object { $_ -gt 0 })
}

function Test-StaleLeakSnipeViteProcess {
    param([int]$ProcessId)
    try {
        $proc = Get-Process -Id $ProcessId -ErrorAction Stop
    } catch {
        return $false
    }
    $name = $proc.ProcessName.ToLowerInvariant()
    if ($name -eq "leaksnipe-ui") {
        return $true
    }
    if ($name -ne "node") {
        return $false
    }
    $cmd = (Get-CimInstance Win32_Process -Filter "ProcessId = $ProcessId" -ErrorAction SilentlyContinue).CommandLine
    if (-not $cmd) {
        return $false
    }
    $cmdLower = $cmd.ToLowerInvariant()
    foreach ($marker in @("vite", "leaksnipe-ui", "tauri", "@vitejs")) {
        if ($cmdLower.Contains($marker)) {
            return $true
        }
    }
    return $false
}

function Clear-StaleViteDevPort {
    param([int]$Port = $ViteDevPort)
    $pids = @(Get-PortListenerProcessIds -Port $Port)
    if ($pids.Count -eq 0) {
        return
    }

    $blocked = @()
    foreach ($procId in $pids) {
        if (Test-StaleLeakSnipeViteProcess -ProcessId $procId) {
            Write-Host "Stopping stale Vite dev listener on port $Port (PID $procId)..."
            Stop-Process -Id $procId -Force -ErrorAction SilentlyContinue
            Start-Sleep -Milliseconds 500
        } else {
            $name = (Get-Process -Id $procId -ErrorAction SilentlyContinue).ProcessName
            $blocked += "$name (PID $procId)"
        }
    }

    if ($blocked.Count -gt 0) {
        Write-Error "Port $Port is in use by another application: $($blocked -join ', '). Close it first or stop that app."
        exit 1
    }

    for ($i = 0; $i -lt 10; $i++) {
        if (@(Get-PortListenerProcessIds -Port $Port).Count -eq 0) {
            return
        }
        Start-Sleep -Milliseconds 200
    }
    Write-Error "Port $Port is still in use after stopping stale Vite processes. Close any old LeakSnipe windows and retry."
    exit 1
}

$ScriptDir = if ($PSScriptRoot) {
    $PSScriptRoot
} elseif ($MyInvocation -and $MyInvocation.MyCommand.Path) {
    Split-Path -Parent $MyInvocation.MyCommand.Path
} else {
    throw "Cannot resolve tauri-dev.ps1 directory"
}
if ($ScriptDir -match '^\\\\\?\\(.+)') { $ScriptDir = $Matches[1] }
$Script:LeakSnipeScriptDir = $ScriptDir

. (Join-Path $ScriptDir "python-env.ps1")
$root = Get-LeakSnipeRoot
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

Set-Location $root
if (-not (Test-LeakSnipeSidecarDeps -Root $root)) {
    Write-Host "Installing Python sidecar dependencies into .venv ..."
    try {
        Install-LeakSnipePythonDeps -Root $root | Out-Null
    } catch {
        Write-Warning "pip install had issues: $($_.Exception.Message). Try: Install-Sidecar.bat"
    }
} else {
    Write-Host "Sidecar Python deps OK (.venv)"
}
$env:LEAKSNIPE_PYTHON = Resolve-LeakSnipePython -Root $root

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
$sidecarHealthy = $false
if ($portInUse) {
    try {
        $health = Invoke-RestMethod -Uri "http://127.0.0.1:8765/health" -TimeoutSec 2
        $sidecarHealthy = ($null -ne $health.api_version)
    } catch {
        $sidecarHealthy = $false
    }
}
if ($sidecarHealthy) {
    Write-Host "Sidecar already healthy on port 8765 - Tauri will reuse it."
    $env:LEAKSNIPE_SIDECAR_EXTERNAL = "1"
} elseif ($portInUse) {
    Write-Host "Note: port 8765 is in use but health check failed (stale listener?). Tauri may clear and respawn."
}

Clear-StaleViteDevPort -Port $ViteDevPort

$vcvars = Find-VcVars
Write-Host "Starting LeakSnipe (first Rust compile can take 1-2 minutes)..."
Write-Host ""

if ($vcvars) {
    # Build cmd line without nested PS double-quotes (vcvars path contains quotes).
    $cmdLine = 'call "' + $vcvars + '" >nul 2>&1 && cd /d "' + $uiDir + '" && npm run tauri dev'
    cmd /c $cmdLine
} else {
    Write-Warning 'Visual Studio C++ Build Tools not found - trying cargo without vcvars...'
    Write-Warning 'If link.exe errors appear, install VS Build Tools with Desktop C++ workload.'
    npm run tauri dev
}

exit $LASTEXITCODE
