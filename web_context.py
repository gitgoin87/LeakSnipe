"""
Optional live web context for AI coaching (DuckDuckGo — no API key).
Used as prompt injection for non-ASI1 providers; ASI1 can use native web_search.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

log = logging.getLogger(__name__)

WEB_CONTEXT_HEADER = (
    "LIVE WEB CONTEXT (retrieved snippets — use for current strategy trends; "
    "verify against hand-specific facts above):"
)

_DEFAULT_MAX_RESULTS = 4
_DEFAULT_MAX_CHARS = 1800


def build_hand_web_query(hand_meta: Optional[dict], hero_name: str = "Hero") -> str:
    """Targeted search query for a hand spot."""
    meta = hand_meta or {}
    position = (meta.get("hero_position") or "").strip()
    cards = (meta.get("hero_cards") or "").strip()
    game = (meta.get("game_type") or "poker").strip()
    parts = ["poker", "preflop", "GTO", "solver"]
    if position:
        parts.append(f"{position} spot")
    if cards and cards not in ("unknown", "??", ""):
        parts.append(cards)
    if game and game.lower() != "unknown":
        parts.append(game)
    parts.append("2025")
    return " ".join(parts)


def build_topic_web_query(topic: str) -> str:
    """Search query from chat text or leak topic."""
    cleaned = re.sub(r"\s+", " ", (topic or "").strip())[:120]
    if not cleaned:
        return "poker GTO strategy trends 2025"
    if "poker" not in cleaned.lower():
        return f"poker {cleaned} strategy GTO"
    return cleaned


def fetch_web_snippets(
    query: str,
    *,
    max_results: int = _DEFAULT_MAX_RESULTS,
    max_chars: int = _DEFAULT_MAX_CHARS,
) -> Dict[str, Any]:
    """
    Run DuckDuckGo text search. Returns {query, snippets, text, retrieved_at, ok, error}.
    """
    query = (query or "").strip()
    if not query:
        return {
            "ok": False,
            "query": "",
            "snippets": [],
            "text": "",
            "retrieved_at": None,
            "error": "empty query",
        }

    snippets: List[Dict[str, str]] = []
    try:
        try:
            # `duckduckgo_search` was renamed to `ddgs`; prefer the new package.
            from ddgs import DDGS
        except ImportError:
            from duckduckgo_search import DDGS

        with DDGS() as ddgs:
            for row in ddgs.text(query, max_results=max_results):
                title = (row.get("title") or "").strip()
                body = (row.get("body") or "").strip()
                href = (row.get("href") or row.get("link") or "").strip()
                if title or body:
                    snippets.append({"title": title, "body": body, "url": href})
    except ImportError:
        log.warning("[web] web search package not installed — pip install ddgs")
        return {
            "ok": False,
            "query": query,
            "snippets": [],
            "text": "",
            "retrieved_at": datetime.now(timezone.utc).isoformat(),
            "error": "web search package not installed (pip install ddgs)",
        }
    except Exception as exc:
        log.warning("[web] search failed for %r: %s", query, exc)
        return {
            "ok": False,
            "query": query,
            "snippets": [],
            "text": "",
            "retrieved_at": datetime.now(timezone.utc).isoformat(),
            "error": str(exc),
        }

    lines = [WEB_CONTEXT_HEADER, f"Query: {query}"]
    for i, sn in enumerate(snippets, 1):
        title = sn.get("title") or "Result"
        body = (sn.get("body") or "")[:280]
        url = sn.get("url") or ""
        line = f"{i}. {title}: {body}"
        if url:
            line += f" ({url})"
        lines.append(line)

    text = "\n".join(lines)
    if len(text) > max_chars:
        text = text[: max_chars - 20] + "\n… [truncated]"

    return {
        "ok": bool(snippets),
        "query": query,
        "snippets": snippets,
        "text": text if snippets else "",
        "retrieved_at": datetime.now(timezone.utc).isoformat(),
        "error": None if snippets else "no results",
    }


def format_web_context_block(payload: Dict[str, Any]) -> str:
    """Render search payload for LLM system prompt injection."""
    if not payload.get("ok") or not payload.get("text"):
        return ""
    return str(payload["text"])
