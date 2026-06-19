"""
LeakSnipe REST API — wraps existing Python modules for the Tauri desktop shell.
Run standalone: python main.py  (from leak-snipe-desktop/backend)
"""

from __future__ import annotations

import os
import sys
from contextlib import asynccontextmanager
from typing import Any, Dict, List, Optional

# Repo root (LeakSnipe/) must be on sys.path to import models, analysis, etc.
_BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))
_DESKTOP_ROOT = os.path.dirname(_BACKEND_DIR)
_PROJECT_ROOT = os.environ.get(
    "LEAKSNIPE_ROOT",
    os.path.abspath(os.path.join(_DESKTOP_ROOT, "..")),
)
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from analysis import LeakEngine
from config import load_settings, save_settings
from models import HandDatabase
from parsers import HandParser

from paths import resolve_db_path
from serializers import hand_to_summary, hands_to_summaries

API_PORT = int(os.environ.get("LEAKSNIPE_API_PORT", "8765"))
API_HOST = os.environ.get("LEAKSNIPE_API_HOST", "127.0.0.1")

_db: Optional[HandDatabase] = None
_settings: Dict[str, Any] = {}


def _get_db() -> HandDatabase:
    global _db, _settings
    _settings = load_settings()
    path = resolve_db_path(_settings)
    if _db is None or _db.db_path != path:
        _db = HandDatabase(path)
    return _db


def _dashboard_payload() -> Dict[str, Any]:
    settings = load_settings()
    db = _get_db()
    hands = db.get_all_hands()
    stats = LeakEngine(settings).analyze(hands)
    by_site = db.get_hand_count()
    alerts = [
        {"level": level, "message": message}
        for level, message in stats.get("alerts", [])
    ]
    return {
        "total_hands": stats["total_hands"],
        "vpip": stats["vpip"],
        "pfr": stats["pfr"],
        "af": stats["af"],
        "wtsd": stats["wtsd"],
        "wsd": stats["wsd"],
        "cbet": stats["cbet"],
        "hands_by_site": by_site,
        "by_site_stats": stats.get("by_site", {}),
        "alerts": alerts,
        "db_path": db.db_path,
        "project_root": _PROJECT_ROOT,
    }


@asynccontextmanager
async def lifespan(_app: FastAPI):
    _get_db()
    yield


app = FastAPI(title="LeakSnipe API", version="0.1.0", lifespan=lifespan)

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
def health() -> Dict[str, str]:
    return {"status": "ok"}


@app.get("/api/dashboard")
def dashboard() -> Dict[str, Any]:
    return _dashboard_payload()


@app.get("/api/hands")
def list_hands(
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
) -> Dict[str, Any]:
    settings = load_settings()
    hands = _get_db().get_all_hands()
    page = hands[offset : offset + limit]
    return {
        "total": len(hands),
        "offset": offset,
        "limit": limit,
        "hands": hands_to_summaries(page, settings),
    }


@app.get("/api/hands/{hand_id}")
def get_hand(hand_id: str) -> Dict[str, Any]:
    settings = load_settings()
    for hand in _get_db().get_all_hands():
        if hand.hand_id == hand_id:
            payload = hand_to_summary(hand, settings)
            payload["board_cards"] = hand.board_cards
            payload["streets"] = hand.streets
            payload["players"] = hand.players
            payload["winners"] = hand.winners
            payload["raw_text"] = hand.raw_text
            return payload
    raise HTTPException(status_code=404, detail="Hand not found")


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
    global _db, _settings
    _settings = merged
    _db = None
    return merged


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


@app.get("/api/watch-folders")
def watch_folders() -> List[Dict[str, str]]:
    settings = load_settings()
    return list(settings.get("scan_dirs", []))


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "main:app",
        host=API_HOST,
        port=API_PORT,
        reload=False,
        log_level="info",
    )
