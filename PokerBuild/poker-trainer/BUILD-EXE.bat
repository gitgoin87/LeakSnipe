@echo off
setlocal EnableDelayedExpansion
echo ================================================================
echo   Poker Therapist Suite - Portable EXE Builder
echo ================================================================
echo.

pushd "%~dp0" >nul
if %ERRORLEVEL% NEQ 0 (
    echo ERROR: Cannot access %~dp0
    exit /b 1
)

REM ── Step 1: Verify tools ────────────────────────────────────────
echo [1/5] Checking prerequisites...
call node --version >nul 2>&1
if %ERRORLEVEL% NEQ 0 (
    echo ERROR: Node.js not found. Install from https://nodejs.org
    exit /b 1
)
for /f "tokens=*" %%v in ('node --version') do echo   Node: %%v
for /f "tokens=*" %%v in ('npm --version') do echo   npm:  %%v
echo.

REM ── Step 2: Install dependencies ───────────────────────────────
echo [2/5] Installing dependencies...
call npm install --prefer-offline 2>&1
if %ERRORLEVEL% NEQ 0 (
    echo WARNING: npm install had issues, attempting to continue...
)
echo   Dependencies ready.
echo.

REM ── Step 3: TypeScript check ───────────────────────────────────
echo [3/5] TypeScript compilation check...
call npx tsc -b --noEmit 2>&1
if %ERRORLEVEL% NEQ 0 (
    echo WARNING: TypeScript found issues (non-blocking, continuing build)
)
echo   TypeScript check complete.
echo.

REM ── Step 4: Vite build (frontend + electron bundles) ───────────
echo [4/5] Building application with Vite...
call npx vite build 2>&1
if %ERRORLEVEL% NEQ 0 (
    echo ERROR: Vite build failed!
    echo Check that index.html and src/main.tsx exist and are valid.
    exit /b 1
)
echo   Vite build complete.
echo.

REM ── Step 5: Package as portable .exe ───────────────────────────
echo [5/5] Packaging portable Windows EXE...
set CSC_IDENTITY_AUTO_DISCOVERY=false
set ELECTRON_BUILDER_ALLOW_UNRESOLVED_DEPENDENCIES=true
call npx electron-builder --win portable 2>&1
if %ERRORLEVEL% NEQ 0 (
    echo ERROR: electron-builder failed!
    exit /b 1
)
echo.

echo [bonus] Creating ZIP archive from release output...
if exist "scripts\compress-release.ps1" (
    powershell -NoProfile -ExecutionPolicy Bypass -File "scripts\compress-release.ps1"
    if !ERRORLEVEL! NEQ 0 (
        echo WARNING: ZIP archive creation failed.
    ) else (
        echo   ZIP archive created.
    )
)
echo.

REM ── Report results ─────────────────────────────────────────────
echo ================================================================
echo   BUILD COMPLETE
echo ================================================================
set PORTABLE_EXE=
for /f "delims=" %%F in ('dir /b /o-d "release\*Portable*.exe" 2^>nul') do (
    if not defined PORTABLE_EXE set "PORTABLE_EXE=%CD%\release\%%F"
)
if defined PORTABLE_EXE (
    for %%F in ("!PORTABLE_EXE!") do (
        echo   Output: %%~fF
        set /a sizeMB=%%~zF / 1048576
        echo   Size:   !sizeMB! MB
    )
    echo.
    echo   To run: double-click "!PORTABLE_EXE!"
) else (
    echo   WARNING: Expected portable output not found under release\
    echo   Checking release folder...
    dir /b release\*.exe 2>nul
)

set ARCHIVE_ZIP=
for /f "delims=" %%F in ('dir /b /o-d "artifacts\*.zip" 2^>nul') do (
    if not defined ARCHIVE_ZIP set "ARCHIVE_ZIP=%CD%\artifacts\%%F"
)
if defined ARCHIVE_ZIP (
    echo.
    echo   Archive: !ARCHIVE_ZIP!
)
echo ================================================================
popd >nul
