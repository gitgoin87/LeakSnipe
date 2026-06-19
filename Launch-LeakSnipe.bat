@echo off
title LeakSnipe Launcher
cd /d "%~dp0"

echo ========================================
echo   LeakSnipe - Poker Therapist
echo ========================================
echo.

REM Start Python sidecar in background; Tauri shows immediately and polls health.
start "LeakSnipe Sidecar" /MIN powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\start-sidecar.ps1"
set "LEAKSNIPE_SIDECAR_EXTERNAL=1"
echo Sidecar starting in background — UI will connect when port 8765 is ready.
echo Stale Vite on port 1420 from a prior session is cleared automatically.
echo You can also close old LeakSnipe windows before relaunching.
echo.

REM Double-click friendly: always invoke PowerShell explicitly
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\tauri-dev.ps1"
set EXITCODE=%ERRORLEVEL%

if %EXITCODE% NEQ 0 (
  echo.
  echo [FAILED] LeakSnipe did not start ^(exit %EXITCODE%^).
  echo.
  echo If you saw "Port 1420 is already in use", close any old LeakSnipe
  echo windows or kill the blocking app, then run this launcher again.
  echo.
  echo Try in a terminal instead:
  echo   cd "%~dp0"
  echo   powershell -ExecutionPolicy Bypass -File scripts\tauri-dev.ps1
  echo.
  pause
  exit /b %EXITCODE%
)

echo.
echo LeakSnipe closed.
pause
