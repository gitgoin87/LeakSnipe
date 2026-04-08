#!/bin/bash
cd "$(dirname "$0")/poker-trainer"
echo "=== Poker Trainer Build ==="
echo "Step 1: Installing dependencies"
pnpm install --no-frozen-lockfile
echo "Step 2: Building"
pnpm run build
echo "Step 3: Creating portable EXE"
npx electron-builder --win portable
echo "Step 4: Checking result"
ls -lh release/PokerTherapistSuite.exe
