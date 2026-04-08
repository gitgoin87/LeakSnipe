# Poker Build — Copilot Instructions

## Repository Structure

This repo contains **three independent projects** that share the same root:

| Directory | What it is |
|---|---|
| `D:\poker-build\` (root) | **Poker Hand Tracker** — Python/tkinter desktop app (`poker_gui.py`) + AI analysis layer |
| `D:\poker-build\Poker-Suite\` | **Poker Training Suite** — Flask web app for hand eval, equity calc, GTO training |
| `D:\poker-build\PokerBuild\poker-trainer\` | **Poker Therapist** — Electron + React + TypeScript desktop app |

Each project is self-contained. Do not mix dependencies or imports across them.

---

## Poker Hand Tracker (`poker_gui.py`)

This is a single-file Python desktop application built with `customtkinter`. Everything — GUI, database, parsing, analytics, file watching, and DriveHUD 2 sync — lives in that one file.

**Dual-drive setup is critical:**
- `D:\poker-build\` — codebase and compiled output live here (old C: drive)
- `C:\` — all live data sources live here (hand histories, DriveHUD 2 database)
- `settings.json` maps C: paths; always verify drive letters before touching file paths

**Data flow:**
```
C:\Hand2Note4Hh\CoinPoker\*.txt       ──┐
C:\ACR Poker\handHistory\*.txt        ──┤─► HandParser ──► HandDatabase (poker_hands.db on D:)
C:\Users\admin\...\drivehud.db (DH2)  ──┘
```

**Key class responsibilities:**

| Class | Role |
|---|---|
| `Hand` | Data model for a single poker hand |
| `HandDatabase` | SQLite wrapper for `poker_hands.db` — all DB ops use a `threading.Lock` |
| `HandParser` | Parses `.txt` hand history files for CoinPoker and ACR/WPN formats |
| `HandImporter` | File watcher — scans directories, detects changes by mtime, imports new hands |
| `LeakEngine` | Computes VPIP, PFR, AF, WTSD, W$SD, C-Bet and generates color-coded alerts |
| `TableDetector` | Finds CoinPoker/ACR window via `win32gui`, polls rect every 1.5s |
| `CurrentHandMonitor` | Polls `poker_hands.db` every 2s for newest hand, emits seat map on change |
| `LiveHUDOverlay` | Borderless `tk.Toplevel` (topmost, alpha) sized to match poker window |
| `SeatBadge` | Per-seat `tk.Frame` widget: name, type, VPIP/PFR, exploit tip |

## Build & Run

**Run from source:**
```
cd D:\poker-build
python poker_gui.py
```

**Install Live HUD dependency (one-time):**
```
pip install pywin32
```

**Compile to EXE (PyInstaller):**
```
pyinstaller PokerTracker.spec
```
Output: `dist\PokerTracker.exe`

**Run tests (root-level):**
```
cd D:\poker-build
python test_parse.py        # tests HandParser against ACR/CoinPoker hand history samples
python test_extracted.py    # tests the extracted.py standalone parser module
```

No linter is configured for the tracker.

## Key Conventions

### Theme System
- All colors come from the `THEMES` dict — never hardcode hex values in UI code.
- Active theme is accessed as `t = THEMES[settings["theme"]]`.
- Default theme is `"Midnight Purple"`. When adding UI elements, match its color keys.
- Legacy globals (`BG_DARK`, `BG_PANEL`, etc.) exist for backward compat; new code should use theme dict keys directly (e.g., `t["bg_base"]`, `t["text"]`).

### Database
- All `HandDatabase` methods acquire `self.lock` before opening a connection — do not call `_connect()` outside the class.
- Use `INSERT OR REPLACE` (not `INSERT OR IGNORE`) for `hands` — re-imports overwrite existing records.
- `hand_id` format: `CP_<number>` for CoinPoker, `ACR_<number>` for ACR.
- `drivehud.db` on C: is **read-only source of truth** — never write to it.

### Parser
- `HandParser.detect_site()` identifies format by the first matching header line:
  - CoinPoker: `"CoinPoker Hand #"`
  - ACR/WPN: `"Game Hand #"`
- Hero names are per-site in `settings.json → hero_names`: `{"CoinPoker": "jdwalka", "ACR": "JohnDaWalka"}`.
- `hero_won` = `winnings - amount_invested` (not just winnings).

### Settings
- `settings.json` lives next to `poker_gui.py` (or the compiled `.exe`).
- At startup, if not found in `BASE_DIR`, the app checks the parent directory — supports running from `dist/`.
- `dh2_db_path` defaults to `C:\Users\admin\AppData\Roaming\DriveHUD 2\drivehud.db`.

### Threading
- `HandImporter` and DriveHUD sync each run on background threads.
- GUI updates from background threads **must** use `app.after(0, callback)` — direct widget writes from threads will crash tkinter.

### OCR (optional feature)
- `pytesseract` is optional; app degrades gracefully if absent (`HAS_TESSERACT = False`).
- Tesseract binary expected at `C:\Program Files\Tesseract-OCR\tesseract.exe`.
- `poker_ocr_bridge.ps1` is an alternative OCR path using the native **Windows WinRT OCR engine** (`Windows.Media.Ocr`) — no Tesseract dependency. Takes an image path argument, outputs recognized text to stdout.

## GUI Tab Structure

All tabs are built in `_build_ui()` as a `CTkTabview`. Each tab has a dedicated `_build_*` method:

| Tab | Attribute | Builder method | Purpose |
|---|---|---|---|
| Dashboard | `tab_dash` | `_build_dashboard()` | Stat cards (Hands, VPIP, PFR, AF, Won, Lost, EV Diff), per-site breakdown, tilt meter |
| Hands | `tab_hands` | `_build_hands_tab()` | Scrollable hand list, filters, hand detail viewer |
| Leaks | `tab_leak` | `_build_leak_tab()` | LeakEngine alerts, positional breakdown, graphs |
| OCR | `tab_ocr` | `_build_ocr_tab()` | Screenshot import, Tesseract OCR parsing |
| AI / GTO | `tab_ai` | `_build_ai_tab()` | SummaryGenerator output, GTO notes, ChatGPT/Grok export |
| Settings | `tab_settings` | `_build_settings_tab()` | Hero names, scan dirs, theme picker, DH2 integration config |

**UI helper methods** (use these when adding new widgets — don't construct raw `CTkFrame`/`CTkButton` everywhere):
- `self._panel(parent, ...)` — creates a themed bordered `CTkFrame` and packs it
- `self._section_label(parent, text)` — gold `Consolas` bold label for section headers
- `self._action_button(parent, text, command, tone=...)` — themed button with `tone` options: `"neutral"`, `"accent"`, `"success"`, `"danger"`

**Dashboard stat cards** are stored in `self.dash_cards` (dict keyed by label name, value is the `CTkLabel`). Update them via `self.dash_cards["VPIP"].configure(text="22%")`.

## DriveHUD 2 Sync

`DriveHUD2Sync` polls `drivehud.db` (on C:) for new rows in the `HandHistories` table, using `HandHistoryId > last_id` as a cursor.

**Sync state** is persisted in `dh2_sync_state.json` (next to `poker_hands.db` on D:):
```json
{"last_id": 12345, "total_imported": 892}
```
Deleting this file (or clicking "Reset Sync" in Settings) causes a full re-import on the next sync.

**DH2 database is opened read-only** via SQLite URI (`?mode=ro`) with WAL journal mode — never open it with a writable connection.

**Two hand formats** come out of DH2's `HandHistory` column:
- XML (`<?xml` / `<HandHistory>`) → `_parse_dh2_xml()` — used for CoinPoker cash games
- Plain text → `_parse_dh2_text()` → delegates to the regular `HandParser`

**Site ID mapping** (`DH2_SITE_MAP`):
```python
{44: "CoinPoker", 12: "ACR", 24: "ACR", 21: "BetOnline", 10: "Ignition"}
```
Unknown site IDs are logged to stdout but not fatal.

**Two-way note sync** — notes and player types *can* be written back to DH2:
- `DriveHUD2Sync.push_hand_note(hand_id, note)` — writes to DH2's notes table
- `DriveHUD2Sync.push_player_note(player_name, note)` — writes to DH2's player notes
- These are the **only** cases where DH2's database is written to; all other access is read-only.

**Background polling** is started in `_start_dh2_polling()` and calls `_dh2_callback(new_count)` on the background thread — use `self.after(0, ...)` inside the callback for any GUI updates.

## Live HUD Overlay

Activated via the **⬡ Live HUD** toggle button in the taskbar, or `live_hud_enabled: true` in `settings.json`. Requires `pywin32`.

**Flow:**
```
TableDetector (win32gui, every 1.5s)
  → on_rect_change → app.after(0) → LiveHUDOverlay.update_rect(x, y, w, h)

CurrentHandMonitor (polls DB, every 2s)
  → on_new_hand(hand_id, seat_map, max_seats)
  → app.after(0) → LiveHUDOverlay.update_hand(seat_map, max_seats)
  → destroys old SeatBadge widgets, places new ones from player_types table
```

**Seat layout** is looked up from `SEAT_POSITIONS` (keyed 2/6/9-max). Badge positions are `(x_pct * w, y_pct * h)` relative to the poker window. Auto-selected from `hand.max_seats`; can be forced via `hud_seat_layout` in settings.

**Exploit tips** come from `EXPLOIT_TIPS` dict (keyed by player classification). Badge name colors use `TYPE_COLORS` dict → theme key → hex.

**Settings keys:**
- `live_hud_enabled` — auto-start on launch
- `hud_opacity` — float 0.3–1.0 (default 0.85)
- `hud_seat_layout` — `"auto"` | `"2max"` | `"6max"` | `"9max"`

**Lifecycle rule:** All three components (`TableDetector`, `CurrentHandMonitor`, `LiveHUDOverlay`) are created and destroyed together via `_start_live_hud()` / `_stop_live_hud()`. Never call overlay methods directly from background threads — always route through `self.after(0, ...)`.

## AI Analysis Layer (`ai_processor.py` / `ai_router.py`)

Two standalone modules at the repo root that can be imported independently of `poker_gui.py`.

**`AIRouter`** (`ai_router.py`) — decides analysis depth for a hand:
- Returns `"light"`, `"deep"`, or `"skip"` based on variant, pot size, and tags
- Rules come from `config/models.json` (`routing_rules` key); if the file is absent, all routing defaults to light
- Entry point: `AIRouter().route(hand_json)` where `hand_json` needs at least `variant`, `pot_size`, and optionally `tags`

**`AIProcessor`** (`ai_processor.py`) — tags, analyzes, and summarizes hands:
- `LLMGateway` provides a unified interface for Ollama (local) and OpenAI/Grok (external); gracefully falls back if providers are unavailable
- Requires `pip install requests`; optional `vector_store` module enables embedding-based hand search (`HAS_VECTOR` flag)
- Output JSON files are written to `ai_outputs/`
- Provider config lives in `config/models.json` under `default_provider` (`"ollama"` | `"openai"` | `"grok"`)

---

## Poker Training Suite (`Poker-Suite/`)

Flask web app. All server logic is in `poker_server.py`; engine in `poker_engine.py`.

**Run:**
```
cd D:\poker-build\Poker-Suite
pip install -r requirements.txt
python poker_server.py        # main app → http://localhost:5000
python web_ui.py              # minimal equity UI → http://localhost:5001
python nash_train.py          # CFR Nash equilibrium trainer (CLI)
```

**Run tests:**
```
cd D:\poker-build\Poker-Suite
python test_poker_engine.py
python test_new_features.py
python test_nash_train.py
```

**Key modules:**

| File | Role |
|---|---|
| `poker_engine.py` | Card, hand evaluation (Hold'em + Omaha + Stud + Razz), `PokerGame` |
| `equity_sim.py` | Monte Carlo equity calculator; `multi_way_equity(ranges, trials)` |
| `advanced_tools.py` | Range visualizer, GTO trainer, hand history parser endpoints |
| `training_scenarios.py` | `ScenarioGenerator` — squeeze, 3-bet pots, c-bet, etc. |
| `statistics_tracker.py` | `StatisticsTracker` + `MultiplayerSessionManager` |
| `nash_train.py` | CFR self-play; uses PyTorch if available, falls back to NumPy |
| `pqc.py` / `pqc_api.py` | Post-quantum cryptography layer (see `PQC_SECURITY.md`) |

Card notation: ranks `2-9 T J Q K A`, suits `h d c s` (e.g. `As`, `Kh`, `Td`).

---

## Poker Therapist (`PokerBuild/poker-trainer/`)

Electron + React + TypeScript desktop app. Uses `better-sqlite3` for local DB and `drizzle-orm` for schema management; `chokidar` for hand history file watching; `get-windows` for window monitoring.

**Run (development):**
```
cd D:\poker-build\PokerBuild\poker-trainer
pnpm install
pnpm run dev
```

**Build executable:**
```
pnpm run package:win     # NSIS installer + portable .exe → release/
pnpm run package:portable
```

**Lint:**
```
pnpm run lint
```

**Key scripts:** `dev`, `build` (`tsc -b && vite build`), `lint` (`eslint .`), `package`, `package:portable`, `package:win`.

Frontend entry: `index.html` / `src/`. Electron main process: `electron/`. Built output: `dist/` (renderer) + `dist-electron/` (main).
