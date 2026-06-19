@echo off
title LeakSnipe Python Live HUD
cd /d "%~dp0"

echo ========================================
echo   LeakSnipe Python Live HUD
echo ========================================
echo.

powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\start-python-hud.ps1"
set EXITCODE=%ERRORLEVEL%

if %EXITCODE% NEQ 0 (
  echo.
  echo [FAILED] Python Live HUD did not start ^(exit %EXITCODE%^).
  echo Check %%TEMP%%\leaksnipe_python_hud.log
  echo.
  pause
  exit /b %EXITCODE%
)

echo.
echo HUD process launched. Open an ACR table window to see overlays.
echo Log: %TEMP%\leaksnipe_python_hud.log
pause
