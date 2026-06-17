@echo off
title LeakSnipe Launcher
cd /d "%~dp0"

echo ========================================
echo   LeakSnipe - Poker Therapist
echo ========================================
echo.

REM Double-click friendly: always invoke PowerShell explicitly
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\tauri-dev.ps1"
set EXITCODE=%ERRORLEVEL%

if %EXITCODE% NEQ 0 (
  echo.
  echo [FAILED] LeakSnipe did not start ^(exit %EXITCODE%^).
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
