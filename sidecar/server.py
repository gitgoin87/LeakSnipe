#!/usr/bin/env python3
"""LeakSnipe REST API — wraps existing Python modules for the Tauri desktop shell."""

from __future__ import annotations

import asyncio
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
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from config import bootstrap_env  # noqa: E402

bootstrap_env(os.path.join(_REPO_ROOT, ".env"))

from fastapi import FastAPI, HTTPException, Query  # noqa: E402
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402
from fastapi.responses import StreamingResponse  # noqa: E402
from pydantic import BaseModel, Field  # noqa: E402

from analysis import LeakEngine, SummaryGenerator  # noqa: E402
from config import ENV_PATH, bootstrap_env, env_keys_detected, load_settings, save_settings  # noqa: E402
from importing import HandImporter  # noqa: E402
from models import HandDatabase  # noqa: E402
from parsers import HandParser  # noqa: E402

from paths import resolve_db_path  # noqa: E402
from serializers import hand_to_detail, hand_to_summary, hands_to_summaries  # noqa: E402

API_PORT = int(os.environ.get("LEAKSNIPE_API_PORT", "8765"))
API_HOST = os.environ.get("LEAKSNIPE_API_HOST", "127.0.0.1")
API_VERSION = "0.2.0"
STATS_CACHE_TTL_SEC = 45

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [leaksnipe] %(levelname)s %(message)s",
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


def _get_ai():
    global _ai_processor
    settings = load_settings()
    if _ai_processor is None:
        from ai_processor import AIProcessor

        _ai_processor = AIProcessor(settings=settings, db_path=resolve_db_path(settings))
    return _ai_processor


def _reset_ai():
    global _ai_processor
    _ai_processor = None


def _get_db() -> HandDatabase:
    global _db, _settings
    _settings = load_settings()
    path = resolve_db_path(_settings)
    if _db is None or _db.db_path != path:
        _db = HandDatabase(path)
    return _db


def _get_importer() -> HandImporter:
    global _importer, _settings
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
        except Exception as exc:
            logging.error("Stats refresh failed: %s", exc, exc_info=True)
        finally:
            with _stats_cache_lock:
                _stats_refreshing = False

    threading.Thread(target=_worker, daemon=True).start()


def _get_cached_stats(*, wait: bool = False) -> Dict[str, Any]:
    """Return leak stats, optionally blocking until first compute finishes."""
    with _stats_cache_lock:
        cached = _stats_cache
        age = time.time() - _stats_cache_at if _stats_cache_at else None
    if cached is None or (age is not None and age > STATS_CACHE_TTL_SEC):
        _refresh_stats_background(force=cached is None and wait)
    if wait and cached is None:
        for _ in range(120):
            time.sleep(0.25)
            with _stats_cache_lock:
                if _stats_cache is not None:
                    return dict(_stats_cache)
        settings = load_settings()
        return _compute_stats(settings)
    with _stats_cache_lock:
        if _stats_cache is not None:
            return dict(_stats_cache)
    settings = load_settings()
    return _compute_stats(settings)


def _run_initial_scan() -> None:
    def _worker() -> None:
        try:
            with _import_lock:
                saved, files = _get_importer().full_scan()
            if saved > 0:
                _on_new_hands(saved, files)
            else:
                logging.info("Startup import scan: %d file(s) checked, no new hands", files)
        except Exception as exc:
            logging.error("Startup import scan failed: %s", exc, exc_info=True)

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
    stats = _get_cached_stats(wait=wait_for_stats)
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
        "stats_cached": _stats_cache is not None,
    }


@asynccontextmanager
async def lifespan(_app: FastAPI):
    logging.info("LeakSnipe API v%s starting on %s:%s", API_VERSION, API_HOST, API_PORT)
    _get_db()
    try:
        ai_status = _get_ai().get_status()
        logging.info(
            "[AI] Startup provider=%s ollama_ready=%s ollama_model=%s",
            ai_status.get("llm_provider"),
            ai_status.get("ollama_ready"),
            ai_status.get("ollama_model"),
        )
    except Exception as exc:
        logging.warning("[AI] Startup status check failed: %s", exc)
    _start_watcher_if_enabled()
    _refresh_stats_background(force=True)
    _run_initial_scan()
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
    settings = load_settings()
    result = _get_cached_stats(wait=True)
    hands = _get_db().get_hands_page(min(result.get("total_hands", 50), 500), 0)
    summary_text = SummaryGenerator().generate(result, hands)
    return {
        "ok": True,
        "stats": result,
        "summary_text": summary_text,
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
        "last_import_at": _last_import_at or status.get("last_scan_at"),
        "last_import_count": _last_import_count or status.get("last_scan_saved", 0),
        "import_status": status,
        "hands": hands_to_summaries(hands, settings),
    }


@app.get("/api/hands/{hand_id}")
def get_hand(hand_id: str) -> Dict[str, Any]:
    settings = load_settings()
    hand = _find_hand(hand_id)
    if not hand:
        raise HTTPException(status_code=404, detail="Hand not found")
    return {"ok": True, "hand": hand_to_detail(hand, settings)}


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
    if saved > 0:
        _on_new_hands(saved, files)
    else:
        _invalidate_stats_cache()
    return {"ok": True, "saved": saved, "files_scanned": files}


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


def _run_hand_analysis(hand_id: str, provider: Optional[str] = None) -> Dict[str, Any]:
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

    processor = _get_ai()
    if not processor.is_available():
        hint = _ai_setup_hint(processor, env_keys_detected())
        raise HTTPException(
            status_code=503,
            detail=hint or "No AI provider available. Start Ollama or add API keys to .env",
        )

    hero = hand.hero_name(settings)
    result = processor.analyze_hand(
        hand.raw_text,
        hero_name=hero or "Hero",
        hand_id=hand.hand_id,
        provider=provider,
    )
    if not result:
        err = processor.get_last_error()
        raise HTTPException(
            status_code=503,
            detail=err or "AI analysis returned no result",
        )
    return {"ok": True, "hand_id": hand_id, "analysis": result, "provider": result.get("provider")}


def _ai_setup_hint(processor, keys: Dict[str, bool]) -> Optional[str]:
    """User-facing hint when no LLM is available (no secret values)."""
    if processor.is_available():
        return None
    pref = processor.get_status().get("ai_provider_pref", "ollama")
    if pref == "ollama" or not any(keys.values()):
        from ai_processor import OLLAMA_RECOMMENDED_PULL

        return (
            "Start Ollama (ollama serve or the Ollama app), then run: "
            f"ollama pull {OLLAMA_RECOMMENDED_PULL} — no API key required."
        )
    return (
        "Add ASI_ONE_API_KEY (or OPENAI_API_KEY / GEMINI_API_KEY) to "
        f"{ENV_PATH}, or switch Settings → AI provider → Ollama."
    )


@app.get("/api/ai/status")
def ai_status() -> Dict[str, Any]:
    try:
        bootstrap_env(reload_file=True)
        processor = _get_ai()
        if not processor.is_available():
            _reset_ai()
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
                "Start Ollama and run: ollama pull deepseek-r1:8b "
                "(default provider). See .env.example for cloud keys."
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

    hands = _get_db().get_all_hands()[: body.limit]
    engine = LeakEngine(settings)
    stats = engine.analyze(hands)

    lines = []
    for h in hands:
        hero = h.hero_name(settings) or "Hero"
        won = h.hero_won
        result_s = f"+{won:.2f}" if won >= 0 else f"{won:.2f}"
        lines.append(
            f"{h.hand_id} [{h.hero_cards}] {h.hero_position} {result_s} pot={h.pot:.2f}"
        )
    hero_name = hands[0].hero_name(settings) if hands else "Hero"
    report = processor.analyze_session(
        "\n".join(lines),
        hero_name=hero_name or "Hero",
        stats=stats,
        provider=body.provider,
    )
    return {"ok": True, "report": report, "hands_analyzed": len(hands)}


@app.post("/api/chat")
def chat(body: ChatRequest) -> Dict[str, Any]:
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
    }


@app.post("/api/ai/context")
def set_ai_context(body: ContextRequest) -> Dict[str, str]:
    processor = _get_ai()
    processor.set_context(body.context)
    return {"ok": True, "status": "context_set"}


@app.delete("/api/chat")
def clear_chat() -> Dict[str, str]:
    _get_ai().clear_chat()
    return {"ok": True, "status": "cleared"}


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
