## Learned User Preferences

- GitHub repo lives under the gitgoin87 account (separate from JohnDaWalka); push auth must use gitgoin87.
- Chose Tauri v2 with a Python backend over Electron and CustomTkinter for the desktop app; wants a modern dark poker UI, not a cheap-looking shell.
- Keep `poker_gui.py` as a working fallback; do not delete it when working on Tauri.
- Do not create git commits unless explicitly asked.
- Replayer UX: no standalone Replayer tab; single click shows hand stats with a Replay action, double-click opens the hand detail/replayer; don't auto-open on single click; include a show/hide opponent hole cards toggle.
- AI coach must use ASI:One (ASI1) when keys are present—never default to Ollama just because it is running locally; dual ASI1 keys (`ASI_ONE_API_KEY` + `ASI_ONE_API_KEY_FALLBACK`) split workloads so hand analysis (primary) and coach/chat (fallback) can run in parallel; official chat models are `asi1`, `asi1-ultra`, and `asi1-mini`; OpenAI and Gemini free tier remain alternatives.
- Web search for the coach should be optional and on-demand by default (`Off` / `On-demand` / `Always`); do not hit the web on routine hand analysis or simple stats questions; when enabled, ASI1 `web_search` is a body flag on the selected model (not a separate model name); coach still needs cited sources when research is requested.
- Never hardcode or commit API keys; user adds keys in local `.env` at repo root; after edits use Settings → Refresh or `POST /api/ai/reload` (full LeakSnipe restart still safest for sidecar env).
- AI hand analysis must be street-by-street, evaluating and explaining each action; advice must match actual hand outcomes and use real computed equities, not invented percentages.
- Wants unified Theory tab with CFR+, neural value nets, multi-way pot odds, antes in all tournament math, and CFR+-backed stack charts at 5, 10, 25, 35, 50, 75, and 100 BB.
- Live BetACR HUD must use the original Python overlay (`poker_gui.py --live-hud`, pywin32); reject the Tauri transparent overlay as primary; do not run Python HUD and Tauri overlay together; overlay must anchor to the ACR tournament table only (blacklist LeakSnipe/Cursor/etc.), hide when no table is found, dynamically update when players join/leave or the table switches, show full per-seat stats on hover/click even when locked (drag only when unlocked); hero bottom-center with opponents rotated around hero, badges inset from edges, large readable sizes, Lock HUD toggle with persisted positions.
- Equity calculator and coach grounding must support NLHE, Omaha Hi-Lo 8-or-better (high and low), 7-card stud, and hi-lo 7-card stud via Monte Carlo—not LLM-guessed percentages.

## Learned Workspace Facts

- Repo: https://github.com/gitgoin87/LeakSnipe at `C:\Users\Giuli\Projects\LeakSnipe`.
- Canonical desktop app: `leaksnipe-ui/` (Tauri) + `sidecar/server.py` (FastAPI on port 8765); launch via `Launch-LeakSnipe.bat` (starts sidecar via `scripts/start-sidecar.ps1`, sets `LEAKSNIPE_SIDECAR_EXTERNAL=1` only when port 8765 is healthy) or `scripts/tauri-dev.ps1`; first-time Python deps via `Install-Sidecar.bat` → repo-root `.venv` (`scripts/python-env.ps1`); manual fallback `Start-Sidecar.bat`; sidecar log at `%TEMP%\leaksnipe_sidecar.log`; Tauri v2 ACL must grant `sidecar_status`/`restart_sidecar` or the UI can falsely show offline; "failed to fetch" or blank hands usually means sidecar down, not lost DB data.
- `sidecar/server.py` must add `_SIDECAR_DIR` to `sys.path` so `paths.py` imports when launched from repo root.
- Primary live HUD: Python `poker_gui.py --live-hud` via `Launch-Python-Hud.bat`, `scripts/start-python-hud.ps1`, or Settings; PID/log at `%TEMP%\leaksnipe_python_hud.{pid,log}`; `settings.json` `live_hud_backend` defaults to `python`; experimental Tauri overlay uses `hud.html`, Rust `hud.rs`, and `/api/live/current-hand`.
- `leak-snipe-desktop/` and `PokerBuild/poker-trainer` are older/incomplete scaffolds; prefer `leaksnipe-ui`.
- Python engine at repo root: `models.py`, `parsers.py`, `analysis.py`, `importing.py`, `ai_processor.py`, `equity.py` (NLHE/Omaha8/stud), `config.py`, `settings.json`, `poker_hands.db`.
- Primary site is BetACR (ACR Poker) MTT tournaments; hand histories (including `"posts ante"` lines) live under `C:\ACR Poker\handHistory\{username}`.
- BetACR hero names in settings: GBOSS101 and JohnDaWalka.
- `poker_gui.py` is the CustomTkinter fallback GUI and primary Live HUD overlay at repo root.
- DB path should be repo-local `poker_hands.db`, not hardcoded `F:\` paths from an old machine.
- Python deps install into repo-root `.venv` via `Install-Sidecar.bat` or manually `pip install -r sidecar/requirements.txt` + `pip install -e .` from repo root (not `C:\WINDOWS\system32`).
- AI layer: `ai_processor.py` runs ASI:One at `https://api.asi1.ai/v1` with models `asi1`/`asi1-ultra`/`asi1-mini`; dual-key split when `ASI_ONE_API_KEY_FALLBACK` is set; image gen via `/v1/image/generate` (`asi1-mini`); `POST /api/ai/reload` reloads `.env`; `settings.json` `ai_web_search_mode` defaults to `on_demand`; supporting modules `coach_memory.py`, `dataset_context.py`, `web_context.py`.
- Theory stack in `theory/` (`cfr_solver.py`, `value_net.py`), sidecar `/api/theory/*`, and Theory tab in leaksnipe-ui for CFR+, neural value estimates, and BB-depth charts.
