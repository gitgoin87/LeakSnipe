@echo off
title LeakSnipe - Start Python Sidecar
cd /d "%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\start-sidecar.ps1"
set EXITCODE=%ERRORLEVEL%
if %EXITCODE% NEQ 0 (
  echo.
  echo [FAILED] Sidecar did not start. Run Install-Sidecar.bat once, then try again.
  pause
  exit /b %EXITCODE%
)
echo.
pause
