# Shared LeakSnipe Python environment helpers (venv at repo root).
# Dot-source from install-sidecar.ps1 / start-sidecar.ps1 / tauri-dev.ps1

function Normalize-LeakSnipePath {
    param([string]$Path)
    if (-not $Path) { return $Path }
    if ($Path -match '^\\\\\?\\(.+)') { return $Matches[1] }
    return $Path
}

function Get-LeakSnipeScriptDir {
    if ($Script:LeakSnipeScriptDir) {
        return $Script:LeakSnipeScriptDir
    }
    $dir = if ($PSScriptRoot) {
        $PSScriptRoot
    } elseif ($MyInvocation -and $MyInvocation.MyCommand.Path) {
        Split-Path -Parent $MyInvocation.MyCommand.Path
    } else {
        throw "Cannot resolve LeakSnipe scripts directory"
    }
    $Script:LeakSnipeScriptDir = Normalize-LeakSnipePath $dir
    return $Script:LeakSnipeScriptDir
}

function Get-LeakSnipeRoot {
    if ($env:LEAKSNIPE_ROOT -and (Test-Path (Join-Path $env:LEAKSNIPE_ROOT "sidecar\server.py"))) {
        return (Resolve-Path (Normalize-LeakSnipePath $env:LEAKSNIPE_ROOT)).Path
    }
    return (Resolve-Path (Join-Path (Get-LeakSnipeScriptDir) "..")).Path
}

function Get-LeakSnipeVenvPython {
    param([string]$Root = (Get-LeakSnipeRoot))
    $venvPy = Join-Path $Root ".venv\Scripts\python.exe"
    if (Test-Path $venvPy) { return $venvPy }
    return $null
}

function Test-IsWindowsStorePythonStub {
    param([string]$Path)
    if (-not $Path) { return $false }
    $normalized = $Path -replace '/', '\'
    return ($normalized -match '(?i)\\Microsoft\\WindowsApps\\python(\d)?\.exe$') `
        -or ($normalized -match '(?i)\\WindowsApps\\PythonSoftwareFoundation') `
        -or ($normalized -match '(?i)\\WindowsApps\\python(\d+)?\.exe$')
}

function Get-LeakSnipePythonCandidates {
    param([string]$Root = (Get-LeakSnipeRoot))
    $candidates = @()
    if ($env:LEAKSNIPE_PYTHON -and -not (Test-IsWindowsStorePythonStub $env:LEAKSNIPE_PYTHON)) {
        $candidates += $env:LEAKSNIPE_PYTHON
    }
    $venvPy = Get-LeakSnipeVenvPython -Root $Root
    if ($venvPy) { $candidates += $venvPy }
    # Bare "python" on Windows often resolves to the Store stub; prefer py launcher instead.
    if (Get-Command py -ErrorAction SilentlyContinue) { $candidates += "py" }
    return $candidates | Select-Object -Unique
}

function Resolve-LeakSnipePython {
    param([string]$Root = (Get-LeakSnipeRoot))
    foreach ($py in (Get-LeakSnipePythonCandidates -Root $Root)) {
        try {
            if ($py -eq "py") {
                $ver = & py -3 -c "import sys; print(sys.executable)" 2>$null
            } else {
                $ver = & $py -c "import sys; print(sys.executable)" 2>$null
            }
            if ($LASTEXITCODE -eq 0 -and $ver) {
                $resolved = $ver.Trim()
                if (Test-IsWindowsStorePythonStub $resolved) { continue }
                return $resolved
            }
        } catch {
            continue
        }
    }
    return $null
}

function Ensure-LeakSnipeVenv {
    param([string]$Root = (Get-LeakSnipeRoot))
    $venvPy = Get-LeakSnipeVenvPython -Root $Root
    if ($venvPy) { return $venvPy }

    $bootstrap = Resolve-LeakSnipePython -Root $Root
    if (-not $bootstrap) {
        throw "Python 3.9+ not found on PATH. Install from https://python.org and reopen the terminal."
    }

    Write-Host "Creating virtual environment at $Root\.venv ..."
    & $bootstrap -m venv (Join-Path $Root ".venv")
    if ($LASTEXITCODE -ne 0) {
        throw "python -m venv failed (exit $LASTEXITCODE)"
    }

    $venvPy = Get-LeakSnipeVenvPython -Root $Root
    if (-not $venvPy) {
        throw "Virtual environment created but python.exe missing at $venvPy"
    }
    return $venvPy
}

function Install-LeakSnipePythonDeps {
    param([string]$Root = (Get-LeakSnipeRoot))
    $python = Ensure-LeakSnipeVenv -Root $Root
    $req = Join-Path $Root "sidecar\requirements.txt"
    if (-not (Test-Path $req)) {
        throw "Requirements file not found: $req"
    }

    Write-Host "Upgrading pip in .venv ..."
    & $python -m pip install --upgrade pip wheel
    if ($LASTEXITCODE -ne 0) { throw "pip upgrade failed (exit $LASTEXITCODE)" }

    Write-Host "Installing sidecar requirements from $req ..."
    & $python -m pip install -r $req
    if ($LASTEXITCODE -ne 0) { throw "pip install -r sidecar\requirements.txt failed (exit $LASTEXITCODE)" }

    Write-Host "Installing LeakSnipe engine (pip install -e .) ..."
    & $python -m pip install -e $Root
    if ($LASTEXITCODE -ne 0) { throw "pip install -e . failed (exit $LASTEXITCODE)" }

    $marker = Join-Path $Root ".venv\.sidecar-deps-ok"
    Set-Content -Path $marker -Value (Get-Date -Format "o") -Encoding ascii
    Write-Host "Python environment ready: $python"
    return $python
}

function Test-LeakSnipeSidecarDeps {
    param([string]$Root = (Get-LeakSnipeRoot))
    $python = Get-LeakSnipeVenvPython -Root $Root
    if (-not $python) { return $false }
    $marker = Join-Path $Root ".venv\.sidecar-deps-ok"
    if (-not (Test-Path $marker)) { return $false }
    try {
        & $python -c "import fastapi, uvicorn" 2>$null
        return ($LASTEXITCODE -eq 0)
    } catch {
        return $false
    }
}
