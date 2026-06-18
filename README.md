# LeakSnipe — Multi-site Poker Hand Tracker with AI-Assisted Analysis

![LeakSnipe](docs/assets/hero-placeholder.png)

LeakSnipe is a multi-site poker hand tracker and analysis suite built for serious players who want a single, powerful tool to collect, analyze, and learn from their hand histories across multiple platforms. It combines a lightweight Python desktop tracker (with a live HUD), a Flask web-based poker training suite, and an Electron-based therapy-style trainer to provide real-time insights, post-session analytics, and guided practice.

- Language composition: Python, TypeScript, PowerShell, CSS, Batchfile, JavaScript.
- Repo layout: three independent projects sharing a single repository root.

Quick links
- Website / Landing page: docs/index.html
- Tracker (Desktop): `poker_gui.py` in the repository root
- Web Trainer (Flask): `Poker-Suite/`
- Therapist (Electron + React): `PokerBuild/poker-trainer/`

## Highlights
- Multi-site parsing support (CoinPoker, ACR/WPN)
- Live HUD overlay that attaches to table windows using win32 APIs
- Thread-safe SQLite storage and re-import safe operations
- AI-assisted leak detection (VPIP, PFR, AF, WTSD, W$SD, C-Bet) with color-coded alerts
- Optional OCR integration (Tesseract or Windows OCR via PowerShell bridge)
- Cross-drive setup: code and binary on D:\, live data and DriveHUD on C:\ (configurable)

---

## Projects in this repository

1. Poker Hand Tracker (root)
   - `poker_gui.py` — single-file customtkinter app implementing the tracker, HUD overlay, parsers, and analytics.
   - Database: `poker_hands.db` (SQLite) created on the D: drive.
   - File watchers and DriveHUD 2 (read-only) sync.

2. Poker Training Suite (`Poker-Suite/`)
   - Flask app for hand evaluation, equity calculation, and GTO drills.
   - Designed to run on a local web server for training and analysis sessions.

3. Poker Therapist (`PokerBuild/poker-trainer/`)
   - Electron + React + TypeScript desktop application delivering bite-sized training and behavioral nudges.

Each project is self-contained: do not mix dependencies across folders.

---

## Installation & Quick Start

Tracker (run from source):

```powershell
cd D:\poker-build
pip install -r requirements.txt  # if provided; otherwise install customtkinter and pywin32
pip install pywin32  # required for Live HUD integration
python poker_gui.py
```

Compile to executable (PyInstaller):

```powershell
pyinstaller PokerTracker.spec
# output: dist\PokerTracker.exe
```

Web trainer (Flask):

```bash
cd Poker-Suite
python -m venv venv
source venv/bin/activate  # or venv\Scripts\activate on Windows
pip install -r requirements.txt
flask run
```

Therapist (Electron/React):

```bash
cd PokerBuild/poker-trainer
npm install
npm run build
npm run electron  # or follow the project README
```

---

## Configuration & Settings

- settings.json lives next to `poker_gui.py` (or next to the compiled `.exe`). At startup the app will also look in the parent directory to support running from `dist/`.
- Important paths in settings.json map live data on C: (hand histories and DriveHUD DB) to the codebase on D:. Always verify drive letters before editing paths.
- Default DriveHUD path: `C:\Users\admin\AppData\Roaming\DriveHUD 2\drivehud.db` (read-only).
- Hero names are per-site in settings.json:
  ```json
  {"hero_names": {"CoinPoker": "jdwalka", "ACR": "JohnDaWalka"}}
  ```

---

## Testing

From the repository root:

```powershell
cd D:\poker-build
python test_parse.py
python test_extracted.py
```

These run parser unit tests against stored sample hand history files.

---

## Architecture & Key Classes

- Hand — data model for a single hand
- HandDatabase — SQLite wrapper with threading.Lock for safe concurrent access
- HandParser — robust parser supporting CoinPoker and ACR/WPN history formats
- HandImporter — directory/file watcher that imports new hands by mtime
- LeakEngine — computes player stats and generates leak alerts
- TableDetector — finds table windows (CoinPoker, ACR) and polls window rect
- CurrentHandMonitor — monitors DB for the newest hand and emits seat map updates
- LiveHUDOverlay — borderless topmost overlay matched to poker table window
- SeatBadge — per-seat UI element that shows quick stats and exploit tips

---

## Contributing

Contributions are welcome. Please follow these rules:
- Keep changes scoped to the relevant subproject.
- Preserve the dual-drive path conventions in any code touching file paths.
- For parser changes, add or update tests under the root test files.
- GUI updates must preserve theme system (THEMES dict) and use theme keys.

---

## License

This project is released under the MIT License. See LICENSE for details.

---

## Contact

Maintainer: gitgoin87
Project: LeakSnipe — multi-site poker hand tracker with AI-assisted analysis

