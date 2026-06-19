# LeakSnipe Desktop (Tauri + React + Python)

> **Legacy scaffold — not the canonical app.** Use [`leaksnipe-ui/`](../leaksnipe-ui/) + [`sidecar/server.py`](../sidecar/server.py) instead (`Launch-LeakSnipe.bat`). This folder is kept for reference; new work belongs in `leaksnipe-ui/`.

Modern desktop shell for [LeakSnipe](../) — replaces the CustomTkinter GUI over time while keeping `poker_gui.py` as a fallback.

**Stack:** Tauri v2 · React 19 · Vite · Tailwind CSS · FastAPI (Python sidecar on port `8765`)

## Prerequisites (Windows)

| Tool | Install |
|------|---------|
| **Node.js 20+** | `winget install OpenJS.NodeJS.LTS` |
| **Rust** | `winget install Rustlang.Rustup` then `rustup default stable` |
| **Python 3.9+** | `winget install Python.Python.3.12` |
| **WebView2** | Usually pre-installed on Windows 11 |

Tauri CLI is included via npm (`@tauri-apps/cli`); no global `cargo tauri` install required.

### Python API dependencies

From the repo root:

```powershell
pip install -r leak-snipe-desktop/backend/requirements.txt
```

The backend imports existing modules from the repo root (`models`, `analysis`, `config`, `parsers`, etc.) — no logic duplication.

## Development

### Option A — Full desktop (recommended)

Terminal 1 — optional if Tauri spawn fails; otherwise Tauri starts this automatically:

```powershell
cd C:\Users\Giuli\Projects\LeakSnipe
python leak-snipe-desktop\backend\main.py
```

Terminal 2 — Tauri dev (starts Vite + spawns Python backend):

```powershell
cd C:\Users\Giuli\Projects\LeakSnipe\leak-snipe-desktop
npm install
npm run tauri dev
```

### Option B — Browser-only (no Rust build)

Useful when Rust/Tauri tooling is unavailable:

```powershell
# Terminal 1
cd C:\Users\Giuli\Projects\LeakSnipe
python leak-snipe-desktop\backend\main.py

# Terminal 2
cd C:\Users\Giuli\Projects\LeakSnipe\leak-snipe-desktop
npm run dev
```

Open http://localhost:1420 — the React app talks to `http://127.0.0.1:8765`.

## Phase 1 UI

- **Dashboard** — total hands, VPIP, PFR, aggression (from `analysis.LeakEngine`)
- **Hands table** — first 50 hands from `poker_hands.db`
- **Settings** — reads `settings.json` (hero names, theme, DB path)
- **Watch folders** — `scan_dirs` from settings

## API endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/health` | Liveness check |
| GET | `/api/dashboard` | Stats + alerts |
| GET | `/api/hands?limit=50&offset=0` | Paginated hand list |
| GET | `/api/hands/{id}` | Single hand detail |
| GET | `/api/settings` | Current settings |
| PUT | `/api/settings` | Update settings |
| GET | `/api/watch-folders` | Scan directories |
| POST | `/api/parse` | Parse raw hand text |

## Environment variables

| Variable | Default | Purpose |
|----------|---------|---------|
| `LEAKSNIPE_ROOT` | repo parent of `leak-snipe-desktop` | Python import path |
| `LEAKSNIPE_API_PORT` | `8765` | API port |
| `LEAKSNIPE_PYTHON` | `python` / `py` | Python executable for Tauri spawn |

## Project layout

```
leak-snipe-desktop/
├── backend/           # FastAPI sidecar
│   ├── main.py
│   ├── paths.py
│   └── serializers.py
├── src/               # React frontend
├── src-tauri/         # Tauri shell (spawns/kills Python)
└── package.json
```

## Next steps (full migration)

1. **Hand replayer** — street-by-street UI using `/api/hands/{id}` raw_text + streets
2. **Live import watcher** — expose `HandImporter` watch loop via WebSocket or polling
3. **Leak panels** — position/site breakdowns, tilt detection from `analysis.py`
4. **AI coach** — wire `ai_processor.py` endpoints
5. **HUD overlay** — Tauri transparent window (replaces Electron `overlayManager`)
6. **Packaging** — bundle Python with PyInstaller + Tauri sidecar for distribution

## Fallback

The original GUI remains at the repo root:

```powershell
python poker_gui.py
```
