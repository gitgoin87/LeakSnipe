@echo off
title LeakSnipe - Install Python Sidecar
cd /d "%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\install-sidecar.ps1"
echo.
pause
