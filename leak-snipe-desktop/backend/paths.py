"""Path resolution for LeakSnipe backend (reuses repo-root settings & DB)."""

from __future__ import annotations

import os
from typing import Any, Dict

from config import BASE_DIR, load_settings

_DEFAULT_DB = os.path.join(BASE_DIR, "poker_hands.db")


def resolve_db_path(settings: Dict[str, Any] | None = None) -> str:
    """Return DB path from settings, env var, or repo-local default."""
    if settings is None:
        settings = load_settings()
    raw = str(settings.get("db_path", "")).strip()
    if raw:
        path = raw if os.path.isabs(raw) else os.path.join(BASE_DIR, raw)
        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        return path
    env = os.environ.get("LEAKSNIPE_DB_PATH", "").strip()
    if env:
        return env
    parent = os.path.dirname(_DEFAULT_DB)
    if parent:
        os.makedirs(parent, exist_ok=True)
    return _DEFAULT_DB
