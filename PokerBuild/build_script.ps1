$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$appDir = (Resolve-Path (Join-Path $scriptDir 'poker-trainer')).Path
$releaseDir = Join-Path $appDir 'release'
$artifactsDir = Join-Path $appDir 'artifacts'
$archiveScript = Join-Path $appDir 'scripts\compress-release.ps1'

Set-Location $appDir
Write-Host "Starting build at $(Get-Date)" -ForegroundColor Cyan
Write-Host "Command: npx electron-builder --win portable" -ForegroundColor Cyan
Write-Host "=========================================" -ForegroundColor Cyan

try {
    & npx electron-builder --win portable 2>&1
    if ($LASTEXITCODE -ne 0) {
        throw "electron-builder exited with code $LASTEXITCODE"
    }

    if (Test-Path -LiteralPath $archiveScript) {
        Write-Host "Creating ZIP archive from release output..." -ForegroundColor Cyan
        & $archiveScript
    }

    Write-Host "`n=========================================" -ForegroundColor Cyan
    Write-Host "Build completed at $(Get-Date)" -ForegroundColor Green
}
catch {
    Write-Host "`n=========================================" -ForegroundColor Red
    Write-Host "Build failed at $(Get-Date)" -ForegroundColor Red
    Write-Host "Error: $_" -ForegroundColor Red
}

# Find the latest executable
Write-Host "`nLooking for output executables..." -ForegroundColor Cyan
Get-ChildItem -Path $releaseDir -Filter "Poker*.exe" -ErrorAction SilentlyContinue | Sort-Object LastWriteTime -Descending | ForEach-Object {
    Write-Host "Found: $($_.FullName)" -ForegroundColor Yellow
    Write-Host "  Size: $([math]::Round($_.Length/1MB,2)) MB" -ForegroundColor Yellow
    Write-Host "  Modified: $($_.LastWriteTime)" -ForegroundColor Yellow
}

$latestArchive = Get-ChildItem -Path $artifactsDir -Filter "*.zip" -ErrorAction SilentlyContinue | Sort-Object LastWriteTime -Descending | Select-Object -First 1
if ($latestArchive) {
    Write-Host "Archive: $($latestArchive.FullName)" -ForegroundColor Yellow
    Write-Host "  Size: $([math]::Round($latestArchive.Length/1MB,2)) MB" -ForegroundColor Yellow
    Write-Host "  Modified: $($latestArchive.LastWriteTime)" -ForegroundColor Yellow
}
