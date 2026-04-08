# Comprehensive Build Script for Poker Therapist

Write-Host "Starting Build Process..."

# 1. Clean previous build
if (Test-Path "dist-electron") { Remove-Item "dist-electron" -Recurse -Force }
if (Test-Path "dist") { Remove-Item "dist" -Recurse -Force }
if (Test-Path "release") { Remove-Item "release" -Recurse -Force }
if (Test-Path "Poker Therapist.exe") { Remove-Item "Poker Therapist.exe" -Force }

# 2. Build Frontend (React + Vite)
Write-Host "Building Frontend..."
pnpm exec vite build --outDir dist
if ($LASTEXITCODE -ne 0) { Write-Error "Frontend build failed"; exit 1 }

# 3. Manually Bundle Electron Main Process (esbuild)
Write-Host "Bundling Main Process..."
npx esbuild electron/main.ts --bundle --platform=node --format=esm --target=node18 --outfile=dist-electron/main.js --external:electron --external:better-sqlite3 --external:chokidar --external:get-windows --external:mock-aws-s3 --external:aws-sdk --external:nock
if ($LASTEXITCODE -ne 0) { Write-Error "Main process build failed"; exit 1 }

# 4. Manually Bundle Electron Preload Script (esbuild)
Write-Host "Bundling Preload Script..."
npx esbuild electron/preload.ts --bundle --platform=node --format=cjs --target=node18 --outfile=dist-electron/preload.js --external:electron
if ($LASTEXITCODE -ne 0) { Write-Error "Preload script build failed"; exit 1 }

# 5. Package with Electron Builder
Write-Host "Packaging Application..."
# Ensure package.json points to the right main file
$pkg = Get-Content package.json | ConvertFrom-Json
if ($pkg.main -ne "dist-electron/main.js") {
    Write-Warning "package.json 'main' field is $($pkg.main), expected 'dist-electron/main.js'. This might cause issues."
}

# Use a unique output directory to avoid file lock issues from previous builds
$timestamp = Get-Date -Format "yyyyMMddHHmmss"
$outputDir = "release_$timestamp"

# Remove old releases if possible, but ignore errors if files are locked
if (Test-Path "release") { 
    try { Remove-Item "release" -Recurse -Force -ErrorAction SilentlyContinue } catch { Write-Warning "Could not clean 'release' directory. Proceeding with new output directory." }
}

Write-Host "Building to $outputDir..."
npx electron-builder --win portable --config.directories.output="$outputDir"
if ($LASTEXITCODE -ne 0) { Write-Error "Packaging failed"; exit 1 }

# Archive the exact output directory so the build can be shared as a ZIP.
$archiveScript = Join-Path $PSScriptRoot 'scripts\compress-release.ps1'
if (Test-Path $archiveScript) {
    try {
        & $archiveScript -SourceDir $outputDir
    } catch {
        Write-Warning "Release archive failed: $_"
    }
}

# 6. Move the exe to the root directory
Write-Host "Moving Executable to the main folder..."
# The portable exe is usually named based on product name
$exePath = "$outputDir\Poker Therapist.exe"
if (-not (Test-Path $exePath)) {
    # Fallback search if name varies
    $exePath = (Get-ChildItem -Path $outputDir -Filter "*.exe" | Select-Object -First 1).FullName
}

if ($exePath -and (Test-Path $exePath)) {
    Copy-Item $exePath -Destination "Poker Therapist.exe" -Force
    Write-Host "Successfully copied executable to root."
} else {
    Write-Error "Could not find generated executable in $outputDir"
    exit 1
}

# Cleanup temp dir if possible
try { Remove-Item $outputDir -Recurse -Force -ErrorAction SilentlyContinue } catch { Write-Warning "Could not fully remove temp $outputDir" }

Write-Host "Build Complete! You can now double-click 'Poker Therapist.exe' in this directory."
