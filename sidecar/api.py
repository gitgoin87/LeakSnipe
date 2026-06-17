#!/usr/bin/env python3
"""LeakSnipe JSON API for Tauri sidecar invocations."""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime
from typing import Any, Dict, List

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from models import HandDatabase  # noqa: E402


def _db_path() -> str:
    env_path = os.environ.get("LEAKSNIPE_DB")
    if env_path and os.path.isfile(env_path):
        return env_path
    settings_path = os.path.join(_REPO_ROOT, "settings.json")
    if os.path.isfile(settings_path):
        try:
            with open(settings_path, encoding="utf-8") as f:
                settings = json.load(f)
            configured = settings.get("db_path", "")
            if configured:
                if not os.path.isabs(configured):
                    configured = os.path.join(_REPO_ROOT, configured)
                if os.path.isfile(configured):
                    return configured
        except (OSError, json.JSONDecodeError):
            pass
    return os.path.join(_REPO_ROOT, "poker_hands.db")


def _hand_summary(hand) -> Dict[str, Any]:
    return {
        "hand_id": hand.hand_id,
        "site": hand.site,
        "date": hand.date.isoformat() if hand.date else None,
        "game_type": hand.game_type,
        "is_tournament": bool(hand.is_tournament),
        "hero_cards": hand.hero_cards,
        "hero_position": hand.hero_position,
        "hero_player": getattr(hand, "hero_player", ""),
        "hero_won": hand.hero_won,
        "pot": hand.pot,
        "table_name": hand.table_name,
    }


def cmd_stats() -> Dict[str, Any]:
    db_path = _db_path()
    if not os.path.isfile(db_path):
        return {
            "ok": False,
            "error": f"Database not found: {db_path}",
            "db_path": db_path,
            "total": 0,
            "by_site": {},
        }
    db = HandDatabase(db_path)
    by_site = db.get_hand_count()
    return {
        "ok": True,
        "db_path": db_path,
        "total": sum(by_site.values()),
        "by_site": by_site,
        "updated_at": datetime.now().isoformat(),
    }


def cmd_hands(limit: int = 25) -> Dict[str, Any]:
    db_path = _db_path()
    if not os.path.isfile(db_path):
        return {"ok": False, "error": f"Database not found: {db_path}", "hands": []}
    db = HandDatabase(db_path)
    hands = db.get_all_hands()[: max(1, min(limit, 500))]
    return {
        "ok": True,
        "db_path": db_path,
        "count": len(hands),
        "hands": [_hand_summary(h) for h in hands],
    }


def cmd_serve() -> Dict[str, Any]:
    """Start HTTP server (blocking — use as subprocess from Tauri)."""
    from server import run_server

    run_server()
    return {"ok": True}


COMMANDS = {
    "stats": lambda _args: cmd_stats(),
    "hands": lambda args: cmd_hands(int(args[0]) if args else 25),
    "serve": lambda _args: cmd_serve(),
}


def main() -> None:
    command = sys.argv[1] if len(sys.argv) > 1 else "stats"
    args = sys.argv[2:]
    handler = COMMANDS.get(command)
    if not handler:
        print(json.dumps({"ok": False, "error": f"Unknown command: {command}"}))
        sys.exit(1)
    try:
        result = handler(args)
        print(json.dumps(result))
    except Exception as exc:  # noqa: BLE001
        print(json.dumps({"ok": False, "error": str(exc)}))
        sys.exit(1)


if __name__ == "__main__":
    main()
