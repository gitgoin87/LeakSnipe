Write-Host "Starting Build Process for Windows Installer..."

# 1. Clean previous build
if (Test-Path "dist-electron") { Remove-Item "dist-electron" -Recurse -Force }
if (Test-Path "dist") { Remove-Item "dist" -Recurse -Force }
if (Test-Path "release") { Remove-Item "release" -Recurse -Force }
if (Test-Path "Poker Therapist Setup.exe") { Remove-Item "Poker Therapist Setup.exe" -Force }

# 2. Build Frontend (React + Vite)
Write-Host "Building Frontend..."
# Use npx instead of pnpm for broader compatibility
npx vite build --outDir dist
if ($LASTEXITCODE -ne 0) { Write-Error "Frontend build failed"; exit 1 }

# 3. Manually Bundle Electron Main Process (esbuild)
Write-Host "Bundling Main Process..."
npx esbuild electron/main.ts --bundle --platform=node --format=esm --target=node18 --outfile=dist-electron/main.js --external:electron --external:better-sqlite3 --external:chokidar --external:get-windows --external:mock-aws-s3 --external:aws-sdk --external:nock
if ($LASTEXITCODE -ne 0) { Write-Error "Main process build failed"; exit 1 }

# 4. Manually Bundle Electron Preload Script (esbuild)
Write-Host "Bundling Preload Script..."
npx esbuild electron/preload.ts --bundle --platform=node --format=cjs --target=node18 --outfile=dist-electron/preload.js --external:electron
if ($LASTEXITCODE -ne 0) { Write-Error "Preload script build failed"; exit 1 }

# 5. Package with Electron Builder (Generating NSIS Installer and Portable Executable)
Write-Host "Packaging Application (NSIS + Portable)..."

$timestamp = Get-Date -Format "yyyyMMddHHmmss"
$outputDir = "release_$timestamp"

Write-Host "Building to $outputDir..."
# The 'nsis' target generates a standard installer (Setup.exe)
npx electron-builder --win nsis portable --config.directories.output="$outputDir"
if ($LASTEXITCODE -ne 0) { Write-Error "Packaging failed"; exit 1 }

# Archive the exact output directory so both installer and portable builds are compressed.
$archiveScript = Join-Path $PSScriptRoot 'scripts\compress-release.ps1'
if (Test-Path $archiveScript) {
    try {
        & $archiveScript -SourceDir $outputDir
    } catch {
        Write-Warning "Release archive failed: $_"
    }
}

# 6. Locate and copy the installer to root
Write-Host "Moving Installer to the main folder..."
# The installer is usually named "Poker Therapist Setup <version>.exe" or similar.
# Since we updated package.json artifactName, check for generated files.

$installerPath = (Get-ChildItem -Path $outputDir -Filter "*Setup*.exe" | Select-Object -First 1).FullName
if (-not $installerPath) {
    $installerPath = (Get-ChildItem -Path $outputDir -Filter "*.exe" | Where-Object { $_.Name -notlike "*portable*" } | Select-Object -First 1).FullName
}

if ($installerPath -and (Test-Path $installerPath)) {
    Copy-Item $installerPath -Destination "Poker Therapist Setup.exe" -Force
    Write-Host "Successfully copied installer to root as 'Poker Therapist Setup.exe'."
} else {
    Write-Warning "Could not find generated installer in $outputDir. Check the release folder."
}

# Also copy the portable exe if found
$portablePath = (Get-ChildItem -Path $outputDir -Filter "*portable*.exe" | Select-Object -First 1).FullName
if (-not $portablePath) {
    # If artifactName was just productName.exe, it might be that.
    $portablePath = (Get-ChildItem -Path $outputDir -Filter "*.exe" | Where-Object { $_.Name -notlike "*Setup*" } | Select-Object -First 1).FullName
}

if ($portablePath -and (Test-Path $portablePath)) {
    Copy-Item $portablePath -Destination "Poker Therapist Portable.exe" -Force
    Write-Host "Successfully copied portable executable to root as 'Poker Therapist Portable.exe'."
}

Write-Host "Build Complete! Check 'Poker Therapist Setup.exe' and 'Poker Therapist Portable.exe'."
