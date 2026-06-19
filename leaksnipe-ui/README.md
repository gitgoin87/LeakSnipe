# LeakSnipe UI (Tauri 2)

Modern desktop shell for LeakSnipe using **Tauri 2 + React + TypeScript + Vite**.

The Python engine (`parsers.py`, `models.py`, `analysis.py`, `ai_processor.py`, `poker_hands.db`) stays in place and is served through a **FastAPI sidecar** (`../sidecar/server.py` on port `8765`). Tauri spawns the sidecar on launch.

`poker_gui.py` remains available as a fallback.

## Prerequisites

- **Node.js 18+**
- **Rust / Cargo** (rustup)
- **Python 3.9+** with LeakSnipe deps
- **Visual Studio Build Tools** (Windows, C++ workload) for Rust linking

## Setup

**Important:** run `pip` from the **LeakSnipe repo root** (`C:\Users\Giuli\Projects\LeakSnipe`), not from `leaksnipe-ui\`.

```powershell
cd C:\Users\Giuli\Projects\LeakSnipe
pip install -r sidecar\requirements.txt
```

Or use the helper script (works from any cwd):

```powershell
.\scripts\install-sidecar.ps1
```

Then install the frontend:

```powershell
cd leaksnipe-ui
npm install
```

### AI provider (default: Ollama — local, no API key)

1. Install [Ollama](https://ollama.com/download) and keep it running
2. Pull a model:
   ```powershell
   ollama pull deepseek-r1:8b
   ```
   Smaller alternatives: `qwen2.5:7b`, `qwen2.5:1.5b`
3. Settings → **AI provider** → **Ollama** (default)

Optional cloud keys in `C:\Users\Giuli\Projects\LeakSnipe\.env`:

| Variable | Provider |
|----------|----------|
| `OLLAMA_BASE_URL` | Ollama URL (default `http://localhost:11434`) |
| `OPENAI_API_KEY` | OpenAI fallback |
| `GEMINI_API_KEY` / `GOOGLE_API_KEY` | Gemini fallback |
| `ASI_ONE_API_KEY` / `ASI1_API_KEY` | ASI:One fallback |

**Provider preference:** Settings → AI provider → `ollama` (default), `auto`, or a cloud provider.

Keys are loaded from `.env` only — never stored in `settings.json`.

## Run (development)

### Easiest — double-click

In File Explorer, double-click:

**`Launch-LeakSnipe.bat`** (repo root)

Do **not** double-click `.ps1` files — Windows may ask which app to open them with.

### Terminal

From repo root:

```powershell
cd C:\Users\Giuli\Projects\LeakSnipe
.\Launch-LeakSnipe.bat
```

Or:

```powershell
cd C:\Users\Giuli\Projects\LeakSnipe
powershell -ExecutionPolicy Bypass -File scripts\tauri-dev.ps1
```

Sidecar only (for debugging):

```powershell
cd C:\Users\Giuli\Projects\LeakSnipe
python sidecar/server.py
```

### Port 1420 already in use

Tauri dev expects Vite on **port 1420** (`strictPort: true` in `vite.config.ts`). If a prior LeakSnipe session left a stale `node`/Vite listener behind, `Launch-LeakSnipe.bat` / `scripts/tauri-dev.ps1` will stop it automatically when the process looks like LeakSnipe/Vite/Tauri.

If another app owns 1420, close that app (or close old LeakSnipe windows) and relaunch.

## Features

| Tab | What it does |
|-----|----------------|
| **Hands** | Browse DB, click row → detail panel, open replayer |
| **Stats** | VPIP, PFR, AF, WTSD, leak alerts via `LeakEngine` |
| **Replayer** | Table oval, hero-at-bottom seats, step-through actions |
| **AI Coach** | Session report, hand analysis, chat (OpenAI / Gemini) |
| **Settings** | Hero names, watch folders, auto-refresh, AI provider |

Watch folders poll on `refresh_interval` (default 5s). Use **Scan Now** for immediate import.

## API endpoints (sidecar)

| Method | Path | Description |
|--------|------|-------------|
| GET | `/health` | Health check |
| GET | `/api/dashboard` | Stats + leak alerts |
| GET | `/api/hands?limit=50` | Hand list |
| GET | `/api/hands/{id}` | Full hand JSON (streets, players, actions) |
| GET | `/api/hands/recent` | Recent hands + import metadata |
| GET | `/api/settings` | Read `settings.json` |
| PUT | `/api/settings` | Update settings |
| POST | `/api/import/scan` | Scan watch folders |
| GET | `/api/ai/status` | AI provider status |
| POST | `/api/analyze/hand` | Analyze one hand |
| POST | `/api/analyze/session` | Session coaching report |
| POST | `/api/chat` | Coach chat |
| GET | `/api/events` | SSE for new imports |

## Architecture

```
leaksnipe-ui/          Tauri + React frontend
sidecar/server.py      FastAPI HTTP API (port 8765)
models.py, analysis.py, ai_processor.py, importing.py
poker_hands.db         SQLite hand database
poker_gui.py           Legacy Tkinter app (fallback)
```

## Build production app

```powershell
cd leaksnipe-ui
npm run tauri build
```

Output: `src-tauri/target/release/bundle/`

## Environment

| Variable | Purpose |
|----------|---------|
| `LEAKSNIPE_DB` | Override database path |
| `LEAKSNIPE_API_PORT` | Sidecar port (default 8765) |
| `LEAKSNIPE_PYTHON` | Python executable path |

## TODO

- HUD overlay (live table stats)
- Production packaging / code signing
- OCR / screen-scrape bridge
