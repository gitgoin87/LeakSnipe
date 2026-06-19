"""
Persistent coach memory for LeakSnipe.

ASI:One (and the OpenAI-compatible chat endpoint) is effectively stateless for our
coaching use case: the `x-session-id` header only buffers context for agentic
Agentverse runs and was verified NOT to retain plain-chat facts across calls.
So LeakSnipe keeps its own durable memory here — a per-hero SQLite log of coaching
conversations and distilled takeaways/leak notes that get summarised back into every
future prompt. This is what gives the coach "remembers context + builds its own
database" regardless of what the remote API persists.
"""

from __future__ import annotations

import logging
import os
import sqlite3
import threading
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

log = logging.getLogger(__name__)

_BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_MEMORY_DB = os.path.join(_BASE_DIR, "coach_memory.db")

# Stable namespace so a hero always maps to the same x-session-id across restarts.
_SESSION_NAMESPACE = uuid.UUID("6f1d6c2e-1f3a-4f5b-9c7d-1ea5b0c0de01")

KIND_CHAT = "chat"
KIND_TAKEAWAY = "takeaway"
KIND_NOTE = "note"

_lock = threading.Lock()


def normalize_hero(hero: Optional[str]) -> str:
    h = (hero or "").strip()
    return h or "default"


def stable_session_id(hero: Optional[str]) -> str:
    """Deterministic session id for a hero (used as ASI:One x-session-id)."""
    return str(uuid.uuid5(_SESSION_NAMESPACE, f"leaksnipe:{normalize_hero(hero)}"))


class CoachMemory:
    """SQLite-backed durable memory of coaching sessions, scoped per hero."""

    def __init__(self, db_path: Optional[str] = None):
        self.db_path = db_path or DEFAULT_MEMORY_DB
        self._ensure_table()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=10)
        conn.row_factory = sqlite3.Row
        return conn

    def _ensure_table(self) -> None:
        with _lock:
            conn = self._connect()
            try:
                conn.execute("PRAGMA journal_mode=WAL;")
                conn.execute(
                    """CREATE TABLE IF NOT EXISTS coach_memory (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        hero TEXT NOT NULL,
                        kind TEXT NOT NULL DEFAULT 'chat',
                        user_text TEXT DEFAULT '',
                        assistant_text TEXT DEFAULT '',
                        provider TEXT DEFAULT '',
                        created_at TEXT
                    )"""
                )
                conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_coach_memory_hero "
                    "ON coach_memory(hero, id DESC)"
                )
                conn.commit()
            finally:
                conn.close()

    # ── writes ────────────────────────────────────────────────────────────
    def add_turn(
        self,
        hero: str,
        user_text: str,
        assistant_text: str,
        *,
        provider: str = "",
    ) -> None:
        self._insert(hero, KIND_CHAT, user_text, assistant_text, provider)

    def add_note(
        self,
        hero: str,
        content: str,
        *,
        kind: str = KIND_TAKEAWAY,
        provider: str = "",
    ) -> None:
        content = (content or "").strip()
        if not content:
            return
        self._insert(hero, kind, "", content[:1200], provider)

    def _insert(
        self, hero: str, kind: str, user_text: str, assistant_text: str, provider: str
    ) -> None:
        try:
            with _lock:
                conn = self._connect()
                try:
                    conn.execute(
                        "INSERT INTO coach_memory "
                        "(hero, kind, user_text, assistant_text, provider, created_at) "
                        "VALUES (?,?,?,?,?,?)",
                        (
                            normalize_hero(hero),
                            kind,
                            (user_text or "")[:4000],
                            (assistant_text or "")[:4000],
                            provider or "",
                            datetime.now().isoformat(timespec="seconds"),
                        ),
                    )
                    conn.commit()
                finally:
                    conn.close()
        except Exception as exc:
            log.warning("[memory] insert failed: %s", exc)

    # ── reads ─────────────────────────────────────────────────────────────
    def _rows(self, hero: str, *, kind: Optional[str] = None, limit: int = 50) -> List[sqlite3.Row]:
        with _lock:
            conn = self._connect()
            try:
                if kind:
                    cur = conn.execute(
                        "SELECT * FROM coach_memory WHERE hero=? AND kind=? "
                        "ORDER BY id DESC LIMIT ?",
                        (normalize_hero(hero), kind, limit),
                    )
                else:
                    cur = conn.execute(
                        "SELECT * FROM coach_memory WHERE hero=? ORDER BY id DESC LIMIT ?",
                        (normalize_hero(hero), limit),
                    )
                return cur.fetchall()
            finally:
                conn.close()

    def count(self, hero: Optional[str] = None) -> int:
        with _lock:
            conn = self._connect()
            try:
                if hero:
                    row = conn.execute(
                        "SELECT COUNT(*) FROM coach_memory WHERE hero=?",
                        (normalize_hero(hero),),
                    ).fetchone()
                else:
                    row = conn.execute("SELECT COUNT(*) FROM coach_memory").fetchone()
                return int(row[0]) if row else 0
            finally:
                conn.close()

    def list_entries(self, hero: str, *, limit: int = 50) -> List[Dict[str, Any]]:
        """Most-recent-first entries for UI display."""
        entries = [dict(r) for r in self._rows(hero, limit=limit)]
        return entries

    def memory_block(self, hero: str, *, max_chars: int = 2200) -> str:
        """
        Render durable memory as a prompt block: distilled takeaways/notes first
        (most valuable), then the last few raw exchanges for continuity.
        """
        notes = self._rows(hero, limit=40)
        takeaways = [r for r in notes if r["kind"] in (KIND_TAKEAWAY, KIND_NOTE)]
        chats = [r for r in notes if r["kind"] == KIND_CHAT]
        if not takeaways and not chats:
            return ""

        lines: List[str] = [
            "COACH MEMORY (durable, from this player's prior LeakSnipe sessions — "
            "use it to stay consistent and track progress on known leaks):"
        ]
        if takeaways:
            lines.append("Remembered takeaways / leak notes:")
            for r in takeaways[:8]:
                when = (r["created_at"] or "")[:10]
                lines.append(f"  • [{when}] {r['assistant_text']}")
        if chats:
            lines.append("Recent conversation:")
            for r in reversed(chats[:5]):  # oldest of the recent first
                u = (r["user_text"] or "").strip().replace("\n", " ")
                a = (r["assistant_text"] or "").strip().replace("\n", " ")
                if u:
                    lines.append(f"  Player: {u[:200]}")
                if a:
                    lines.append(f"  Coach: {a[:280]}")

        text = "\n".join(lines)
        if len(text) > max_chars:
            text = text[: max_chars - 20] + "\n… [memory truncated]"
        return text

    def clear(self, hero: Optional[str] = None) -> int:
        """Delete memory for one hero (or all heroes when hero is None)."""
        with _lock:
            conn = self._connect()
            try:
                if hero:
                    cur = conn.execute(
                        "DELETE FROM coach_memory WHERE hero=?", (normalize_hero(hero),)
                    )
                else:
                    cur = conn.execute("DELETE FROM coach_memory")
                conn.commit()
                return cur.rowcount
            finally:
                conn.close()
