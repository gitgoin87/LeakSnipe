@echo off
setlocal EnableDelayedExpansion
for %%I in ("%~dp0poker-trainer") do set "APP_DIR=%%~fI"
if not exist "!APP_DIR!\package.json" (
    echo Canonical app folder not found: !APP_DIR!
    exit /b 1
)
pushd "!APP_DIR!" >nul
echo === Starting Build Process ===
echo.

echo === Step 1: Check Node/npm versions ===
call node --version
call npm --version
call pnpm --version
echo.

echo === Step 2: Running pnpm install ===
call pnpm install --no-frozen-lockfile
if %ERRORLEVEL% NEQ 0 (
    echo Install failed with error code %ERRORLEVEL%
    exit /b 1
)
echo Install completed successfully
echo.

echo === Step 3: Running build ===
call pnpm run build
if %ERRORLEVEL% NEQ 0 (
    echo Build failed with error code %ERRORLEVEL%
    exit /b 1
)
echo Build completed successfully
echo.

echo === Step 4: Creating portable EXE ===
call npx electron-builder --win portable
if %ERRORLEVEL% NEQ 0 (
    echo Electron builder failed with error code %ERRORLEVEL%
    exit /b 1
)
echo EXE creation completed successfully
echo.

echo === Step 5: Creating ZIP archive ===
if exist "scripts\compress-release.ps1" (
    powershell -NoProfile -ExecutionPolicy Bypass -File "scripts\compress-release.ps1"
    if !ERRORLEVEL! NEQ 0 (
        echo ZIP archive creation failed with error code !ERRORLEVEL!
        exit /b 1
    )
)
echo Archive creation completed successfully
echo.

echo === Step 6: Reporting results ===
set PORTABLE_EXE=
for /f "delims=" %%F in ('dir /b /o-d "release\*Portable*.exe" 2^>nul') do (
    if not defined PORTABLE_EXE set "PORTABLE_EXE=!CD!\release\%%F"
)
if defined PORTABLE_EXE (
    for %%F in ("!PORTABLE_EXE!") do (
        echo EXE File: %%~fF
        echo Size: %%~zF bytes
    )
) else (
    echo Portable EXE not found under release\
)

set ARCHIVE_ZIP=
for /f "delims=" %%F in ('dir /b /o-d "artifacts\*.zip" 2^>nul') do (
    if not defined ARCHIVE_ZIP set "ARCHIVE_ZIP=!CD!\artifacts\%%F"
)
if defined ARCHIVE_ZIP (
    echo Archive File: !ARCHIVE_ZIP!
)

popd >nul
