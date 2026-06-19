#!/usr/bin/env python3
"""LeakSnipe REST API — wraps existing Python modules for the Tauri desktop shell."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import sys
import threading
import time
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any, Dict, List, Optional

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SIDECAR_DIR = os.path.dirname(os.path.abspath(__file__))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)
if _SIDECAR_DIR not in sys.path:
    sys.path.insert(0, _SIDECAR_DIR)

from config import bootstrap_env  # noqa: E402

bootstrap_env(os.path.join(_REPO_ROOT, ".env"))

from fastapi import FastAPI, HTTPException, Query  # noqa: E402
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402
from fastapi.responses import StreamingResponse  # noqa: E402
from pydantic import BaseModel, Field  # noqa: E402

import equity as equity_engine  # noqa: E402
import pot_odds as pot_odds_engine  # noqa: E402
from analysis import LeakEngine, PlayerAnalyzer, SummaryGenerator, player_stats_payload  # noqa: E402
from config import ENV_PATH, bootstrap_env, env_keys_detected, get_api_key, load_settings, save_settings  # noqa: E402
from dataset_context import invalidate_dataset_context_cache  # noqa: E402
from importing import HandImporter, discover_scan_dirs, merge_scan_dirs  # noqa: E402
from models import HandDatabase  # noqa: E402
from parsers import HandParser  # noqa: E402

from paths import resolve_db_path  # noqa: E402
from serializers import hand_to_detail, hand_to_summary, hands_to_summaries  # noqa: E402

API_PORT = int(os.environ.get("LEAKSNIPE_API_PORT", "8765"))
API_HOST = os.environ.get("LEAKSNIPE_API_HOST", "127.0.0.1")
API_VERSION = "0.2.0"
STATS_CACHE_TTL_SEC = 45

_log_handlers: list[logging.Handler] = [logging.StreamHandler()]
_sidecar_log = os.environ.get("LEAKSNIPE_SIDECAR_LOG")
if _sidecar_log:
    try:
        _log_handlers.append(
            logging.FileHandler(_sidecar_log, encoding="utf-8", mode="a")
        )
    except OSError:
        pass

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [leaksnipe] %(levelname)s %(message)s",
    handlers=_log_handlers,
    force=True,
)

_db: Optional[HandDatabase] = None
_importer: Optional[HandImporter] = None
_settings: Dict[str, Any] = {}
_import_lock = threading.Lock()
_last_import_at: Optional[str] = None
_last_import_count: int = 0
_sse_clients: List[asyncio.Queue] = []
_ai_processor = None
_stats_cache: Optional[Dict[str, Any]] = None
_stats_cache_at: float = 0.0
_stats_cache_lock = threading.Lock()
_stats_refreshing = False
_stats_ready = threading.Event()
_ai_env_fingerprint: Optional[tuple] = None
_ai_lock = threading.Lock()


def _env_fingerprint() -> tuple:
    """Detect .env key presence and value changes without exposing secrets."""
    base = tuple(sorted(env_keys_detected().items()))
    digests = tuple(
        (prov, hashlib.sha256(key.encode()).hexdigest()[:12])
        for prov in ("asi1", "openai", "gemini", "anthropic", "deepseek")
        if (key := get_api_key(prov))
    )
    return base + digests


def _get_ai(*, reload_env: bool = False):
    global _ai_processor, _ai_env_fingerprint
    with _ai_lock:
        if reload_env:
            bootstrap_env(reload_file=True)
        settings = load_settings()
        pref_fields = (
            "ai_provider", "ollama_model", "db_path",
            "ai_include_dataset_context", "ai_include_web_context", "ai_web_search_mode",
            "ai_personalization", "ai_agentic_tools", "asi1_model", "hero_names",
            "coach_memory_db", "coach_memory_hero",
        )
        db_path = resolve_db_path(settings)
        fp = _env_fingerprint()
        stale = _ai_processor is None or _ai_env_fingerprint != fp
        if not stale:
            old = _ai_processor._settings or {}
            stale = _ai_processor._db_path != db_path or any(
                old.get(k) != settings.get(k) for k in pref_fields
            )
        if stale:
            from ai_processor import AIProcessor

            _ai_env_fingerprint = fp
            _ai_processor = AIProcessor(settings=settings, db_path=db_path)
        else:
            _ai_processor._refresh_cloud_clients()
            if _ai_processor._asi1_client:
                pref = (settings.get("ai_provider") or "asi1").lower()
                if pref in ("asi1", "auto"):
                    chain = _ai_processor._provider_chain()
                    if chain and chain[0] == "asi1":
                        from ai_processor import ASI1_MODEL

                        _ai_processor._active_provider = f"asi1:{ASI1_MODEL}"
        return _ai_processor


def _reset_ai():
    global _ai_processor, _ai_env_fingerprint
    with _ai_lock:
        _ai_processor = None
        _ai_env_fingerprint = None


def _get_db() -> HandDatabase:
    global _db, _settings
    _settings = load_settings()
    path = resolve_db_path(_settings)
    if _db is None or _db.db_path != path:
        _db = HandDatabase(path)
    return _db


def _ensure_scan_dirs() -> Dict[str, Any]:
    """Merge auto-discovered BetACR hand-history folders into settings when missing."""
    settings = load_settings()
    discovered = discover_scan_dirs(settings)
    merged_dirs = merge_scan_dirs(settings.get("scan_dirs"), discovered)
    if merged_dirs != settings.get("scan_dirs"):
        updated = {**settings, "scan_dirs": merged_dirs}
        if save_settings(updated):
            logging.info(
                "Updated scan_dirs with %d folder(s) (%d discovered)",
                len(merged_dirs),
                len(discovered),
            )
            return updated
    return settings


def _get_importer(*, refresh_dirs: bool = False) -> HandImporter:
    global _importer, _settings
    if refresh_dirs or _importer is None:
        _settings = _ensure_scan_dirs()
    else:
        _settings = load_settings()
    if _importer is None:
        _importer = HandImporter(_settings, _get_db())
    else:
        _importer.update_settings(_settings)
        _importer.db = _get_db()
    return _importer


def _on_new_hands(saved: int, _files: int) -> None:
    global _last_import_at, _last_import_count
    if saved <= 0:
        return
    _last_import_at = datetime.now().isoformat()
    _last_import_count = saved
    _invalidate_stats_cache()
    logging.info("Imported %d new hand(s)", saved)
    event = {"type": "new_hands", "count": saved, "at": _last_import_at}
    for queue in list(_sse_clients):
        try:
            queue.put_nowait(event)
        except Exception:
            pass


def _invalidate_stats_cache() -> None:
    global _stats_cache, _stats_cache_at
    with _stats_cache_lock:
        _stats_cache = None
        _stats_cache_at = 0.0
    _stats_ready.clear()
    invalidate_dataset_context_cache()


def _compute_stats(settings: Dict[str, Any]) -> Dict[str, Any]:
    db = _get_db()
    hands = db.get_all_hands()
    return LeakEngine(settings).analyze(hands)


def _refresh_stats_background(force: bool = False) -> None:
    global _stats_refreshing

    def _worker() -> None:
        global _stats_cache, _stats_cache_at, _stats_refreshing
        with _stats_cache_lock:
            if _stats_refreshing:
                return
            now = time.time()
            if not force and _stats_cache and (now - _stats_cache_at) < STATS_CACHE_TTL_SEC:
                return
            _stats_refreshing = True
        try:
            settings = load_settings()
            stats = _compute_stats(settings)
            with _stats_cache_lock:
                _stats_cache = stats
                _stats_cache_at = time.time()
            _stats_ready.set()
        except Exception as exc:
            logging.error("Stats refresh failed: %s", exc, exc_info=True)
        finally:
            with _stats_cache_lock:
                _stats_refreshing = False

    threading.Thread(target=_worker, daemon=True).start()


def _placeholder_stats(*, total_hands: int = 0) -> Dict[str, Any]:
    """Non-blocking stats shell while background LeakEngine compute runs."""
    return {
        "total_hands": total_hands,
        "vpip": 0.0,
        "pfr": 0.0,
        "af": 0.0,
        "wtsd": 0.0,
        "wsd": 0.0,
        "cbet": 0.0,
        "by_position": {},
        "by_site": {},
        "alerts": [],
    }


def _get_cached_stats(*, wait: bool = False) -> tuple[Dict[str, Any], bool]:
    """Return (leak stats, stats_cached).

    stats_cached is True only when the payload came from _stats_cache. Placeholder
    shells are always paired with False so clients never see zeros with cached=True.
    """
    with _stats_cache_lock:
        cached = _stats_cache
        age = time.time() - _stats_cache_at if _stats_cache_at else None
    if cached is None or (age is not None and age > STATS_CACHE_TTL_SEC):
        _refresh_stats_background(force=cached is None and wait)
    if wait and cached is None:
        _stats_ready.wait(timeout=30.0)
        with _stats_cache_lock:
            if _stats_cache is not None:
                return dict(_stats_cache), True
        try:
            total = _get_db().count_hands()
        except Exception:
            total = 0
        logging.warning("Stats cache still warming after 30s — returning placeholder")
        return _placeholder_stats(total_hands=total), False
    with _stats_cache_lock:
        if _stats_cache is not None:
            return dict(_stats_cache), True
    try:
        total = _get_db().count_hands()
    except Exception:
        total = 0
    return _placeholder_stats(total_hands=total), False


def _run_reparse_missing_hero() -> int:
    """Backfill hero cards/position for hands imported with the wrong hero alias."""
    try:
        updated = _get_importer().reparse_hands_missing_hero()
        if updated:
            logging.info("Reparsed %d hand(s) missing hero cards or position", updated)
            _invalidate_stats_cache()
        return updated
    except Exception as exc:
        logging.error("Hero backfill reparse failed: %s", exc, exc_info=True)
        return 0


def _run_initial_scan_then_watch() -> None:
    """Run one import scan, then start the background watcher (avoid concurrent full_scan)."""

    def _worker() -> None:
        try:
            saved, files = _get_importer().full_scan()
            reparsed = _run_reparse_missing_hero()
            if saved > 0:
                _on_new_hands(saved, files)
            elif reparsed:
                logging.info(
                    "Startup import scan: %d file(s) checked, reparsed %d hand(s)",
                    files,
                    reparsed,
                )
            else:
                logging.info("Startup import scan: %d file(s) checked, no new hands", files)
        except Exception as exc:
            logging.error("Startup import scan failed: %s", exc, exc_info=True)
        finally:
            _start_watcher_if_enabled()

    threading.Thread(target=_worker, daemon=True).start()


def _start_watcher_if_enabled() -> None:
    settings = load_settings()
    if not settings.get("auto_refresh", True):
        return
    importer = _get_importer()
    if importer._thread and importer._thread.is_alive():
        return
    importer.start_watcher(_on_new_hands)


def _stop_watcher() -> None:
    global _importer
    if _importer is not None:
        _importer.stop_watcher()


def _find_hand(hand_id: str):
    return _get_db().get_hand_by_id(hand_id)


def _dashboard_payload(*, wait_for_stats: bool = False) -> Dict[str, Any]:
    settings = load_settings()
    db = _get_db()
    stats, stats_cached = _get_cached_stats(wait=wait_for_stats)
    by_site = db.get_hand_count()
    alerts = [
        {"level": level, "message": message}
        for level, message in stats.get("alerts", [])
    ]
    importer = _get_importer()
    import_status = importer.get_status()
    return {
        "ok": True,
        "api_version": API_VERSION,
        "total_hands": stats["total_hands"],
        "vpip": stats["vpip"],
        "pfr": stats["pfr"],
        "af": stats["af"],
        "wtsd": stats["wtsd"],
        "wsd": stats["wsd"],
        "cbet": stats["cbet"],
        "by_position": stats.get("by_position", {}),
        "hands_by_site": by_site,
        "by_site_stats": stats.get("by_site", {}),
        "alerts": alerts,
        "db_path": db.db_path,
        "project_root": _REPO_ROOT,
        "last_import_at": _last_import_at,
        "last_import_count": _last_import_count,
        "import_status": import_status,
        "stats_cached": stats_cached,
    }


@asynccontextmanager
async def lifespan(_app: FastAPI):
    logging.info("LeakSnipe API v%s starting on %s:%s", API_VERSION, API_HOST, API_PORT)
    _get_db()
    # Do not block startup on full AI status (Ollama probes can take 30s+).
    # Keys are loaded here; AIProcessor initializes lazily on first /api/ai/* call.
    bootstrap_env(reload_file=True)
    keys = env_keys_detected()
    logging.info(
        "[AI] Startup keys: asi1=%s openai=%s gemini=%s",
        keys.get("asi1"),
        keys.get("openai"),
        keys.get("gemini"),
    )
    _ensure_scan_dirs()
    _get_importer(refresh_dirs=True)
    _refresh_stats_background(force=True)
    _run_initial_scan_then_watch()
    yield
    _stop_watcher()


app = FastAPI(title="LeakSnipe API", version="0.2.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:1420",
        "http://localhost:5173",
        "http://127.0.0.1:1420",
        "http://127.0.0.1:5173",
        "tauri://localhost",
        "http://tauri.localhost",
        "https://tauri.localhost",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
def health() -> Dict[str, Any]:
    return {"status": "ok", "api_version": API_VERSION}


@app.get("/api/dashboard")
def dashboard(wait: bool = Query(False, description="Block until stats are computed")) -> Dict[str, Any]:
    return _dashboard_payload(wait_for_stats=wait)


@app.get("/api/stats")
def stats_detail() -> Dict[str, Any]:
    result, stats_cached = _get_cached_stats(wait=False)
    hands = _get_db().get_hands_page(min(result.get("total_hands", 50), 500), 0)
    summary_text = SummaryGenerator().generate(result, hands)
    return {
        "ok": True,
        "stats": result,
        "summary_text": summary_text,
        "stats_cached": stats_cached,
    }


@app.get("/api/hands")
def list_hands(
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
) -> Dict[str, Any]:
    settings = load_settings()
    db = _get_db()
    page = db.get_hands_page(limit, offset)
    return {
        "ok": True,
        "total": db.count_hands(),
        "offset": offset,
        "limit": limit,
        "hands": hands_to_summaries(page, settings),
    }


@app.get("/api/hands/recent")
def recent_hands(
    limit: int = Query(30, ge=1, le=100),
) -> Dict[str, Any]:
    """Most recent hands — used for live refresh polling."""
    settings = load_settings()
    db = _get_db()
    hands = db.get_hands_page(limit, 0)
    importer = _get_importer()
    status = importer.get_status()
    return {
        "ok": True,
        "count": len(hands),
        "total": db.count_hands(),
        "db_path": db.db_path,
        "last_import_at": _last_import_at or status.get("last_scan_at"),
        "last_import_count": _last_import_count or status.get("last_scan_saved", 0),
        "import_status": status,
        "hands": hands_to_summaries(hands, settings),
    }


@app.get("/api/live/current-hand")
def live_current_hand(
    site: Optional[str] = Query(None, description="Filter by site (e.g. BetACR)"),
) -> Dict[str, Any]:
    """Latest imported hand with seat map — drives live table HUD."""
    settings = load_settings()
    db = _get_db()
    heroes = set()
    for aliases in (settings.get("hero_names") or {}).values():
        for alias in str(aliases).split(","):
            alias = alias.strip()
            if alias:
                heroes.add(alias)

    hands = db.get_hands_page(50, 0)
    hand = None
    site_filter = (site or "").strip()
    for h in hands:
        if site_filter and h.site.lower() != site_filter.lower():
            continue
        hand = h
        break
    if hand is None and hands:
        hand = hands[0]

    if not hand:
        return {
            "ok": True,
            "hand_id": None,
            "site": None,
            "max_seats": 6,
            "seat_map": {},
            "opponents": [],
            "table_name": None,
        }

    seat_map: Dict[str, Any] = {}
    opponents: List[str] = []
    for seat, info in sorted(hand.players.items()):
        name = str(info.get("name") or "").strip()
        is_hero = bool(info.get("is_hero")) or name in heroes
        seat_map[str(seat)] = {"name": name, "is_hero": is_hero}
        if name and not is_hero:
            opponents.append(name)

    return {
        "ok": True,
        "hand_id": hand.hand_id,
        "site": hand.site,
        "max_seats": hand.max_seats or 6,
        "seat_map": seat_map,
        "opponents": opponents,
        "table_name": hand.table_name,
        "imported_at": getattr(hand, "imported_at", None),
    }


@app.get("/api/hands/{hand_id}")
def get_hand(hand_id: str) -> Dict[str, Any]:
    settings = load_settings()
    hand = _find_hand(hand_id)
    if not hand:
        raise HTTPException(status_code=404, detail="Hand not found")
    return {"ok": True, "hand": hand_to_detail(hand, settings)}


@app.get("/api/players/{name}/stats")
def get_player_stats(name: str) -> Dict[str, Any]:
    """HUD stats for a single opponent from the hand database."""
    settings = load_settings()
    db = _get_db()
    stats = player_stats_payload(name.strip(), settings=settings, db=db)
    return {"ok": True, "player": stats}


@app.get("/api/players/stats")
def get_players_stats(
    names: str = Query(..., description="Comma-separated player names"),
) -> Dict[str, Any]:
    """Batch HUD lookup for opponents seated in a hand."""
    settings = load_settings()
    db = _get_db()
    requested = [n.strip() for n in names.split(",") if n.strip()]
    if not requested:
        raise HTTPException(status_code=400, detail="names query param is required")
    if len(requested) > 20:
        raise HTTPException(status_code=400, detail="Maximum 20 players per request")

    hands = db.get_all_hands()
    players: Dict[str, Any] = {}
    for name in requested:
        players[name] = player_stats_payload(
            name, settings=settings, db=db, hands=hands,
        )
    return {"ok": True, "players": players}


@app.post("/api/players/refresh")
def refresh_player_types() -> Dict[str, Any]:
    """Recompute and persist opponent classifications for the HUD."""
    settings = load_settings()
    db = _get_db()
    hands = db.get_all_hands()
    analyzer = PlayerAnalyzer(settings)
    results = analyzer.apply_manual_overrides(analyzer.analyze_players(hands), db)
    saved = 0
    for p in results:
        try:
            db.save_player_type(
                name=p["name"],
                auto_type=p.get("auto_type", p["classification"]),
                hands=p["hands"],
                vpip=p["vpip"],
                pfr=p["pfr"],
                af=p["af"],
                fold_cbet=p["fold_cbet"],
                wtsd=p["wtsd"],
            )
            saved += 1
        except Exception:
            pass
    return {"ok": True, "players": saved, "total_hands": len(hands)}


@app.get("/api/settings")
def get_settings() -> Dict[str, Any]:
    return load_settings()


class SettingsUpdate(BaseModel):
    settings: Dict[str, Any]


@app.put("/api/settings")
def update_settings(body: SettingsUpdate) -> Dict[str, Any]:
    current = load_settings()
    merged = {**current, **body.settings}
    if not save_settings(merged):
        raise HTTPException(status_code=500, detail="Failed to save settings")
    global _db, _settings, _importer
    _settings = merged
    _db = None
    if _importer is not None:
        _importer.update_settings(merged)
        _importer.db = _get_db()
    else:
        _get_importer(refresh_dirs=True)
    _stop_watcher()
    _start_watcher_if_enabled()
    _reset_ai()
    return merged


@app.get("/api/watch-folders")
def watch_folders() -> List[Dict[str, str]]:
    settings = load_settings()
    return list(settings.get("scan_dirs", []))


@app.post("/api/import/scan")
def import_scan() -> Dict[str, Any]:
    with _import_lock:
        saved, files = _get_importer().full_scan()
        reparsed = _run_reparse_missing_hero()
    if saved > 0:
        _on_new_hands(saved, files)
    else:
        _invalidate_stats_cache()
    return {"ok": True, "saved": saved, "files_scanned": files, "reparsed": reparsed}


@app.post("/api/import/reparse-hero")
def import_reparse_hero() -> Dict[str, Any]:
    """Re-parse stored raw text for hands missing hero cards or position."""
    with _import_lock:
        reparsed = _run_reparse_missing_hero()
    return {"ok": True, "reparsed": reparsed}


@app.get("/api/import/status")
def import_status() -> Dict[str, Any]:
    importer = _get_importer()
    status = importer.get_status()
    db = _get_db()
    return {
        "ok": True,
        "api_version": API_VERSION,
        "total_hands": db.count_hands(),
        "last_import_at": _last_import_at or status.get("last_scan_at"),
        "last_import_count": _last_import_count or status.get("last_scan_saved", 0),
        **status,
    }


@app.post("/api/import/watcher/start")
def watcher_start() -> Dict[str, str]:
    _start_watcher_if_enabled()
    return {"ok": True, "status": "started"}


@app.post("/api/import/watcher/stop")
def watcher_stop() -> Dict[str, str]:
    _stop_watcher()
    return {"ok": True, "status": "stopped"}


class ParseRequest(BaseModel):
    raw_text: str = Field(min_length=1)
    site: str = ""


@app.post("/api/parse")
def parse_hand(body: ParseRequest) -> Dict[str, Any]:
    settings = load_settings()
    parser = HandParser(settings)
    site = body.site.strip() or parser.detect_site(body.raw_text)
    if not site:
        raise HTTPException(status_code=422, detail="Could not detect poker site")
    hand = parser._parse_single(body.raw_text.strip(), site)
    if not hand or not hand.hand_id:
        raise HTTPException(status_code=422, detail="Could not parse hand history")
    hand.raw_text = body.raw_text.strip()
    if hand.site in ("ACR", "BetACR"):
        hand.site = "BetACR"
    return hand_to_summary(hand, settings)


class AnalyzeHandRequest(BaseModel):
    provider: Optional[str] = None
    hand_id: Optional[str] = None


class AnalyzeSessionRequest(BaseModel):
    provider: Optional[str] = None
    limit: int = Field(default=20, ge=1, le=100)


class ChatRequest(BaseModel):
    message: str = Field(min_length=1)
    provider: Optional[str] = None
    clear: bool = False


class ContextRequest(BaseModel):
    context: str = ""


class ImageRequest(BaseModel):
    prompt: str = Field(min_length=1, max_length=2000)
    model: Optional[str] = None
    size: Optional[str] = None


def _run_hand_analysis(hand_id: str, provider: Optional[str] = None) -> Dict[str, Any]:
    bootstrap_env(reload_file=True)
    try:
        from ai_processor import AIProcessor  # noqa: F401
    except ImportError as exc:
        raise HTTPException(status_code=503, detail=f"AI module unavailable: {exc}") from exc

    settings = load_settings()
    hand = _find_hand(hand_id)
    if not hand:
        raise HTTPException(status_code=404, detail="Hand not found")
    if not hand.raw_text:
        raise HTTPException(status_code=422, detail="Hand has no raw text for analysis")

    processor = _get_ai(reload_env=True)
    if not processor.is_available():
        hint = _ai_setup_hint(processor, env_keys_detected())
        raise HTTPException(
            status_code=503,
            detail=hint or "No AI provider available. Start Ollama or add API keys to .env",
        )

    hero = hand.hero_name(settings)
    from ai_processor import hand_meta_from_hand

    result = processor.analyze_hand(
        hand.raw_text,
        hero_name=hero or "Hero",
        hand_id=hand.hand_id,
        provider=provider,
        hand_meta=hand_meta_from_hand(hand),
    )
    if not result:
        err = processor.get_last_error()
        raise HTTPException(
            status_code=503,
            detail=err or "AI analysis returned no result",
        )
    return {
        "ok": True,
        "hand_id": hand_id,
        "analysis": result,
        "provider": result.get("provider"),
        "model": result.get("model"),
        "dataset_context_hands": processor.get_status().get("dataset_context_hands", 0),
        "dataset_context_included": processor.get_status().get("ai_include_dataset_context", True),
        "web_context_included": bool(result.get("web_context_included")),
    }


def _ai_setup_hint(processor, keys: Dict[str, bool]) -> Optional[str]:
    """User-facing hint when no LLM is available (no secret values)."""
    if processor.is_available():
        return None
    pref = processor.get_status().get("ai_provider_pref", "auto")
    if keys.get("asi1"):
        return (
            "ASI_ONE_API_KEY detected — set Settings → AI provider → ASI:One (or Auto) "
            "for cloud coaching. Restart after editing .env."
        )
    if pref == "ollama" or not any(keys.values()):
        from ai_processor import OLLAMA_RECOMMENDED_PULL

        return (
            "Start Ollama (ollama serve or the Ollama app), then run: "
            f"ollama pull {OLLAMA_RECOMMENDED_PULL} — no API key required."
        )
    return (
        "Add cloud API keys to "
        f"{ENV_PATH} (DEEPSEEK_API_KEY, OPENAI_API_KEY, GEMINI_API_KEY, "
        "ANTHROPIC_API_KEY, ASI_ONE_API_KEY), or switch Settings → AI provider → Ollama."
    )


@app.get("/api/ai/dataset-context")
def ai_dataset_context() -> Dict[str, Any]:
    """Return cached player profile summary for AI coaching (preview/debug)."""
    settings = load_settings()
    processor = _get_ai()
    ctx = processor.get_dataset_context()
    return {
        "ok": True,
        "hand_count": ctx.get("hand_count", 0),
        "include_enabled": ctx.get("include_enabled", True),
        "profile": ctx.get("profile", {}),
        "text": ctx.get("text", ""),
    }


@app.get("/api/ai/web-context")
def ai_web_context(q: str = Query("", description="Search query for live web snippets")) -> Dict[str, Any]:
    """Preview DuckDuckGo snippets used for non-ASI1 web context injection."""
    from web_context import fetch_web_snippets

    query = (q or "").strip() or "poker GTO strategy trends"
    payload = fetch_web_snippets(query)
    return {"ok": payload.get("ok", False), **payload}


@app.post("/api/ai/test/{provider}")
def ai_test_provider(provider: str) -> Dict[str, Any]:
    bootstrap_env(reload_file=True)
    _reset_ai()
    processor = _get_ai(reload_env=True)
    result = processor.test_provider(provider)
    return {"ok": result.get("ok", False), **result}


@app.post("/api/ai/test-all")
def ai_test_all() -> Dict[str, Any]:
    bootstrap_env(reload_file=True)
    _reset_ai()
    processor = _get_ai(reload_env=True)
    payload = processor.test_all_providers()
    results = payload.get("results", {})
    any_ok = any(r.get("ok") for r in results.values())
    return {"ok": any_ok, **payload}


@app.post("/api/ai/reload")
def ai_reload() -> Dict[str, Any]:
    """Reload .env keys and rebuild the AI processor (no full app restart)."""
    try:
        bootstrap_env(reload_file=True)
        _reset_ai()
        processor = _get_ai(reload_env=True)
        keys = env_keys_detected()
        status = processor.get_status()
        return {
            "ok": True,
            **status,
            "env_path": ENV_PATH,
            "env_file_exists": os.path.isfile(ENV_PATH),
            "keys_detected": keys,
            "setup_hint": _ai_setup_hint(processor, keys),
        }
    except Exception as exc:
        return {
            "ok": False,
            "llm_available": False,
            "error": str(exc),
            "env_path": ENV_PATH,
            "env_file_exists": os.path.isfile(ENV_PATH),
            "keys_detected": env_keys_detected(),
            "setup_hint": (
                "Add ASI_ONE_API_KEY to .env for ASI:One (recommended), or start Ollama "
                "as a local fallback. See .env.example."
            ),
        }


@app.get("/api/ai/status")
def ai_status() -> Dict[str, Any]:
    try:
        processor = _get_ai()
        keys = env_keys_detected()
        status = processor.get_status()
        return {
            "ok": True,
            **status,
            "env_path": ENV_PATH,
            "env_file_exists": os.path.isfile(ENV_PATH),
            "keys_detected": keys,
            "setup_hint": _ai_setup_hint(processor, keys),
        }
    except Exception as exc:
        return {
            "ok": False,
            "llm_available": False,
            "error": str(exc),
            "env_path": ENV_PATH,
            "env_file_exists": os.path.isfile(ENV_PATH),
            "keys_detected": env_keys_detected(),
            "setup_hint": (
                "Add ASI_ONE_API_KEY to .env for ASI:One (recommended), or start Ollama "
                "as a local fallback. See .env.example."
            ),
        }


@app.post("/api/analyze/hand")
def analyze_hand_body(body: AnalyzeHandRequest) -> Dict[str, Any]:
    if not body.hand_id:
        raise HTTPException(status_code=422, detail="hand_id is required")
    return _run_hand_analysis(body.hand_id, body.provider)


@app.post("/api/ai/analyze/{hand_id}")
def analyze_hand(hand_id: str, body: AnalyzeHandRequest | None = None) -> Dict[str, Any]:
    provider = body.provider if body else None
    return _run_hand_analysis(hand_id, provider)


@app.post("/api/analyze/session")
def analyze_session(body: AnalyzeSessionRequest | None = None) -> Dict[str, Any]:
    body = body or AnalyzeSessionRequest()
    settings = load_settings()
    processor = _get_ai()
    if not processor.is_available():
        raise HTTPException(status_code=503, detail="No AI provider configured")

    hands = _get_db().get_all_hands()
    sample = hands[: body.limit]
    engine = LeakEngine(settings)
    stats = engine.analyze(hands)

    lines = []
    for h in sample:
        hero = h.hero_name(settings) or "Hero"
        won = h.hero_won
        result_s = f"+{won:.2f}" if won >= 0 else f"{won:.2f}"
        lines.append(
            f"{h.hand_id} [{h.hero_cards}] {h.hero_position} {result_s} pot={h.pot:.2f}"
        )
    hero_name = sample[0].hero_name(settings) if sample else "Hero"
    report = processor.analyze_session(
        "\n".join(lines),
        hero_name=hero_name or "Hero",
        stats=stats,
        provider=body.provider,
    )
    status = processor.get_status()
    return {
        "ok": True,
        "report": report,
        "hands_analyzed": len(sample),
        "dataset_context_hands": status.get("dataset_context_hands", 0),
        "dataset_context_included": status.get("ai_include_dataset_context", True),
        "web_context_included": processor._last_web_context_used,
    }


@app.post("/api/chat")
def chat(body: ChatRequest) -> Dict[str, Any]:
    bootstrap_env(reload_file=True)
    processor = _get_ai()
    if not processor.is_available():
        raise HTTPException(status_code=503, detail="No AI provider configured")
    if body.clear:
        processor.clear_chat()
    reply = processor.chat(body.message, provider=body.provider)
    status = processor.get_status()
    return {
        "ok": True,
        "reply": reply,
        "provider": status.get("llm_provider"),
        "web_context_included": processor._last_web_context_used,
    }


@app.post("/api/ai/image")
def ai_generate_image(body: ImageRequest) -> Dict[str, Any]:
    """Generate a poker visual via ASI:One image API. Returns hosted URL(s) or data URI."""
    processor = _get_ai()
    if not processor.asi1_image_available():
        raise HTTPException(
            status_code=503,
            detail=(
                "Image generation requires an ASI:One key. Add ASI_ONE_API_KEY to "
                f"{ENV_PATH} and restart LeakSnipe."
            ),
        )
    result = processor.generate_image(
        body.prompt,
        model=body.model,
        size=body.size,
    )
    if not result.get("ok"):
        raise HTTPException(
            status_code=502,
            detail=result.get("error") or "Image generation failed",
        )
    return {"ok": True, **result}


@app.post("/api/ai/context")
def set_ai_context(body: ContextRequest) -> Dict[str, str]:
    processor = _get_ai()
    processor.set_context(body.context)
    return {"ok": True, "status": "context_set"}


@app.delete("/api/chat")
def clear_chat() -> Dict[str, str]:
    _get_ai().clear_chat()
    return {"ok": True, "status": "cleared"}


class MemoryNoteRequest(BaseModel):
    text: str = Field(min_length=1, max_length=1200)


@app.get("/api/ai/memory")
def ai_memory(limit: int = Query(50, ge=1, le=200)) -> Dict[str, Any]:
    """Durable coach memory for the active hero (personalization store)."""
    return {"ok": True, **_get_ai().memory_list(limit=limit)}


@app.delete("/api/ai/memory")
def ai_memory_clear() -> Dict[str, Any]:
    """Forget all stored coach memory for the active hero."""
    return _get_ai().memory_clear()


@app.post("/api/ai/memory")
def ai_memory_add(body: MemoryNoteRequest) -> Dict[str, Any]:
    """Add an explicit 'remember this' note to durable coach memory."""
    result = _get_ai().add_memory_note(body.text)
    if not result.get("ok"):
        raise HTTPException(status_code=503, detail=result.get("error") or "Memory unavailable")
    return result


class EquityRequest(BaseModel):
    hero: str = Field(min_length=2, description="Hero hole cards, e.g. 'Kh2d'")
    board: str = ""
    villain_hand: Optional[str] = None
    villain_range: Optional[str] = None
    villain_position: Optional[str] = None
    action_context: str = "open"
    iters: int = Field(default=12000, ge=200, le=200000)


class Omaha8Request(BaseModel):
    hero: str = Field(min_length=2, description="4 hole cards, e.g. 'As2sKsQh'")
    opponents: int = Field(default=1, ge=1, le=8)
    villains: Optional[List[str]] = None
    board: str = ""
    iters: int = Field(default=8000, ge=200, le=200000)


class PotOddsRequest(BaseModel):
    pot: float = Field(ge=0, description="Live pot before hero's call")
    to_call: float = Field(ge=0)
    num_callers: int = Field(default=0, ge=0)
    callers_amount: float = Field(default=0, ge=0)
    dead_money: float = Field(default=0, ge=0)


@app.post("/api/pot-odds")
def pot_odds_calc(body: PotOddsRequest) -> Dict[str, Any]:
    """Immediate pot odds (multi-way aware when callers' chips are in pot)."""
    odds = pot_odds_engine.compute_pot_odds(
        body.pot,
        body.to_call,
        num_callers=body.num_callers,
        callers_amount=body.callers_amount,
        dead_money=body.dead_money,
    )
    naive = (
        pot_odds_engine.heads_up_pot_odds_naive(body.pot - body.callers_amount, body.to_call)
        if body.num_callers > 0 and body.callers_amount > 0
        else odds
    )
    return {
        "ok": True,
        "pot_odds": odds,
        "pot_odds_pct": round(odds * 100, 2),
        "hu_naive_odds": naive,
        "multiway": body.num_callers > 0,
    }


@app.post("/api/equity")
def equity_nlhe(body: EquityRequest) -> Dict[str, Any]:
    """NLHE Monte Carlo equity: hero vs a hand, a range, or a position range."""
    try:
        board = body.board or None
        if body.villain_hand:
            res = equity_engine.equity_hand_vs_hand(
                body.hero, body.villain_hand, board=board, iters=body.iters
            )
            res["mode"] = "hand_vs_hand"
            res["villain"] = body.villain_hand
        elif body.villain_range:
            res = equity_engine.equity_hand_vs_range(
                body.hero, body.villain_range, board=board, iters=body.iters
            )
            res["mode"] = "hand_vs_range"
            res["villain_range"] = body.villain_range
            res["villain_range_pct"] = equity_engine.range_frequency(body.villain_range)
        elif body.villain_position:
            res = equity_engine.equity_vs_position_range(
                body.hero, body.villain_position, body.action_context,
                board=board, iters=body.iters,
            )
            res["mode"] = "hand_vs_position"
        else:
            grounding = equity_engine.preflop_equity_grounding(body.hero, iters=min(body.iters, 6000))
            if not grounding:
                raise HTTPException(status_code=422, detail="Could not parse hero cards")
            return {"ok": True, "mode": "reference", **grounding}
    except HTTPException:
        raise
    except (ValueError, IndexError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"ok": True, "hero": body.hero, "board": body.board, **res}


@app.post("/api/equity/omaha8")
def equity_omaha8(body: Omaha8Request) -> Dict[str, Any]:
    """Omaha Hi/Lo (8-or-better) equity — separate high, low, scoop, overall."""
    try:
        opponents: Any = body.villains if body.villains else body.opponents
        res = equity_engine.monte_carlo_omaha8(
            body.hero, opponents=opponents, board=body.board or None, iters=body.iters
        )
    except (ValueError, IndexError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"ok": True, "hero": body.hero, "board": body.board, **res}


class StudRequest(BaseModel):
    hero: str = Field(min_length=2, description="Hero cards, up to 7, e.g. 'AhKhQhJh2c'")
    villain_hand: Optional[str] = None
    villain_range: Optional[str] = None
    opponents: int = Field(default=1, ge=1, le=6)
    dead_cards: str = ""
    iters: int = Field(default=12000, ge=200, le=200000)


class Stud8Request(BaseModel):
    hero: str = Field(min_length=2, description="Hero cards, up to 7")
    villains: Optional[List[str]] = None
    opponents: int = Field(default=1, ge=1, le=6)
    dead_cards: str = ""
    iters: int = Field(default=10000, ge=200, le=200000)


@app.post("/api/equity/stud")
def equity_stud(body: StudRequest) -> Dict[str, Any]:
    """Seven Card Stud (high) Monte Carlo equity with dead-card removal."""
    try:
        dead = body.dead_cards or None
        if body.villain_hand:
            res = equity_engine.equity_stud_hand_vs_hand(
                body.hero, body.villain_hand, dead=dead, iters=body.iters
            )
            res["mode"] = "hand_vs_hand"
            res["villain"] = body.villain_hand
        elif body.villain_range:
            res = equity_engine.equity_stud_hand_vs_range(
                body.hero, body.villain_range, dead=dead, iters=body.iters
            )
            res["mode"] = "hand_vs_range"
            res["villain_range"] = body.villain_range
            res["villain_range_pct"] = equity_engine.range_frequency(body.villain_range)
        else:
            players = [body.hero] + [{"cards": []} for _ in range(body.opponents)]
            res = equity_engine.monte_carlo_stud(players, dead=dead, iters=body.iters)
            res["mode"] = "vs_random"
    except (ValueError, IndexError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"ok": True, "hero": body.hero, "dead_cards": body.dead_cards, **res}


@app.post("/api/equity/stud8")
def equity_stud8(body: Stud8Request) -> Dict[str, Any]:
    """Stud Hi/Lo (8-or-better) equity — separate high, low, scoop, overall."""
    try:
        opponents: Any = body.villains if body.villains else body.opponents
        res = equity_engine.monte_carlo_stud8(
            body.hero, opponents=opponents, dead=body.dead_cards or None, iters=body.iters
        )
    except (ValueError, IndexError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"ok": True, "hero": body.hero, "dead_cards": body.dead_cards, **res}


@app.get("/api/equity/ranges")
def equity_ranges() -> Dict[str, Any]:
    """Solver-approximation reference ranges (open / defend / 3-bet) + frequencies."""
    rfi = {
        pos: {"range": spec, "pct": equity_engine.range_frequency(spec)}
        for pos, spec in equity_engine.RFI_RANGES.items()
    }
    three_bet = {
        pos: {"range": spec, "pct": equity_engine.range_frequency(spec)}
        for pos, spec in equity_engine.THREE_BET_RANGES.items()
    }
    return {
        "ok": True,
        "note": "Solver approximations of GTO/Nash frequencies — reference ranges, not a live solver.",
        "rfi": rfi,
        "three_bet": three_bet,
        "bb_defend_vs_steal": {
            "range": equity_engine.BB_DEFEND_VS_STEAL,
            "pct": equity_engine.range_frequency(equity_engine.BB_DEFEND_VS_STEAL),
        },
    }


# ── Theory (CFR+ / value net) ─────────────────────────────────────────────────

class CfrRequest(BaseModel):
    game: str = Field(default="kuhn", description="kuhn | leduc | push_fold | tournament_push_fold")
    iterations: int = Field(default=10000, ge=100, le=500000)
    seed: int = Field(default=42)
    ante_per_player: float = Field(default=500.0, ge=0.0, description="MTT ante per player (chips)")
    num_players: int = Field(default=9, ge=2, le=10, description="Table size for dead-money calc")
    bb: float = Field(default=1000.0, ge=1.0, description="Big blind size (chips)")
    stack_bb: float = Field(default=10.0, ge=2.0, le=100.0, description="Effective stack in BB")


class ValueNetRequest(BaseModel):
    hero: str = Field(min_length=2, description="Hero hole cards, e.g. 'AsKh'")
    board: str = ""
    pot_odds: float = Field(default=0.33, ge=0.0, le=1.0)
    position: float = Field(default=0.5, ge=0.0, le=1.0)
    ante_per_player: float = Field(default=0.0, ge=0.0)
    dead_money: float = Field(default=0.0, ge=0.0)
    bb: float = Field(default=1000.0, ge=1.0)
    stack_bb: float = Field(default=25.0, ge=2.0, le=100.0)


@app.get("/api/theory")
def theory_overview() -> Dict[str, Any]:
    """Unified theory module overview."""
    from theory import CHART_DEPTHS, CHART_POSITIONS, SOLVABLE_GAMES
    from theory.value_net import TORCH_AVAILABLE

    return {
        "ok": True,
        "module": "theory",
        "components": ["cfr_plus", "value_net", "stack_charts"],
        "chart_depths_bb": list(CHART_DEPTHS),
        "chart_positions": list(CHART_POSITIONS),
        "cfr_games": list(SOLVABLE_GAMES.keys()),
        "torch_available": TORCH_AVAILABLE,
        "defaults": {
            "ante_per_player": 500.0,
            "num_players": 9,
            "bb": 1000.0,
        },
        "note": (
            "CFR+ runs toy subgames; charts combine CFR+ push/fold with scaled open/defend ranges; "
            "value net approximates equity/EV with stack_bb + ante features."
        ),
    }


@app.get("/api/theory/depths")
def theory_depths() -> Dict[str, Any]:
    """Available MTT stack-depth chart sizes (BB)."""
    from theory.charts import list_chart_depths

    return {"ok": True, "depths": list_chart_depths()}


@app.get("/api/theory/charts")
def theory_charts(
    stack_bb: float = Query(..., ge=5.0, le=100.0),
    position: str = Query(default="BTN"),
    ante_per_player: float = Query(default=500.0, ge=0.0),
    num_players: int = Query(default=9, ge=2, le=10),
    bb: float = Query(default=1000.0, ge=1.0),
    include_nn: bool = Query(default=True),
) -> Dict[str, Any]:
    """CFR+-backed stack-depth chart for position (13×13 combo grid)."""
    from theory.charts import CHART_DEPTHS, get_chart

    if int(stack_bb) not in CHART_DEPTHS:
        raise HTTPException(
            status_code=422,
            detail=f"stack_bb must be one of {list(CHART_DEPTHS)}",
        )
    try:
        chart = get_chart(
            int(stack_bb),
            position,
            ante_per_player=ante_per_player,
            num_players=num_players,
            bb=bb,
            include_nn=include_nn,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"ok": True, **chart}


class ValueNetTrainRequest(BaseModel):
    n_samples: int = Field(default=300, ge=50, le=2000)
    epochs: int = Field(default=60, ge=10, le=300)
    seed: int = Field(default=42)


@app.get("/api/theory/games")
def theory_games() -> Dict[str, Any]:
    """List solvable toy subgames for CFR+."""
    from theory.cfr_solver import SOLVABLE_GAMES

    return {
        "ok": True,
        "note": (
            "Educational theory tooling — toy subgames only. "
            "Full NLHE requires abstraction + subgame solving beyond this module."
        ),
        "games": list(SOLVABLE_GAMES.values()),
    }


@app.post("/api/theory/cfr")
def theory_cfr(body: CfrRequest) -> Dict[str, Any]:
    """Run CFR+ on a supported subgame and return average strategy + exploitability."""
    from theory.cfr_solver import run_cfr_for_game

    try:
        result = run_cfr_for_game(
            body.game,
            iterations=body.iterations,
            seed=body.seed,
            ante_per_player=body.ante_per_player,
            num_players=body.num_players,
            bb=body.bb,
            stack_bb=body.stack_bb,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"ok": True, **result}


@app.post("/api/theory/value")
def theory_value(body: ValueNetRequest) -> Dict[str, Any]:
    """Neural (or MC fallback) value estimate for a poker spot."""
    from theory.value_net import predict_value

    try:
        result = predict_value(
            body.hero,
            body.board,
            pot_odds=body.pot_odds,
            position=body.position,
            ante_per_player=body.ante_per_player,
            dead_money=body.dead_money,
            bb=body.bb,
            stack_bb=body.stack_bb,
        )
    except (ValueError, IndexError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"ok": True, **result}


@app.post("/api/theory/value/train")
def theory_value_train(body: ValueNetTrainRequest) -> Dict[str, Any]:
    """Train value net on Monte Carlo equity samples (may take ~30-60s)."""
    from theory.value_net import train_value_net

    try:
        meta = train_value_net(
            n_samples=body.n_samples, epochs=body.epochs, seed=body.seed,
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return {"ok": True, **meta}


@app.get("/api/events")
async def sse_events():
    """Server-sent events for new hand imports."""

    async def event_stream():
        queue: asyncio.Queue = asyncio.Queue()
        _sse_clients.append(queue)
        try:
            yield f"data: {json.dumps({'type': 'connected'})}\n\n"
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=30.0)
                    yield f"data: {json.dumps(event)}\n\n"
                except asyncio.TimeoutError:
                    yield f"data: {json.dumps({'type': 'ping'})}\n\n"
        finally:
            if queue in _sse_clients:
                _sse_clients.remove(queue)

    return StreamingResponse(event_stream(), media_type="text/event-stream")


def run_server() -> None:
    import uvicorn

    uvicorn.run(
        app,
        host=API_HOST,
        port=API_PORT,
        reload=False,
        log_level="info",
    )


if __name__ == "__main__":
    run_server()
