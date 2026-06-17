"""
Utility functions for Poker Hand Tracker.
Handles path operations, font styling, and color utilities (legacy compatibility).
"""

import os
import re
import sys
from typing import Any, Dict, List, Optional

# Legacy re-exports for compatibility with poker_gui.py
from themes import lighten as _lighten, darken as _darken, blend as _blend


def font_style(*styles: str) -> str:
    """Return a tkinter-compatible font style string."""
    return " ".join(style for style in styles if style)


def canonical_path(path: str) -> str:
    """Normalize a file path to canonical form."""
    return os.path.normpath(os.path.abspath(path))


def normalize_path(path: str) -> str:
    """Normalize a file path."""
    return os.path.normpath(path)


def hero_aliases_from_settings(settings: Dict[str, Any], site: str) -> List[str]:
    """Return configured hero aliases for a site."""
    hero_names = settings.get("hero_names", {})
    configured = hero_names.get(site) or hero_names.get(
        "BetACR" if site in ("ACR", "BetACR") else site, ""
    ) or ""
    aliases: List[str] = []
    for part in re.split(r"[,;|]", str(configured)):
        part = part.strip()
        if part and part not in aliases:
            aliases.append(part)
    return aliases


def resolve_hand_hero_name(
    settings: Dict[str, Any],
    site: str,
    players: Optional[Dict[int, Dict[str, Any]]] = None,
    raw_text: str = "",
    hero_player: str = "",
) -> str:
    """Resolve the single player name used for stats and result math."""
    if hero_player:
        return hero_player
    if players:
        for info in players.values():
            if info.get("is_hero") and info.get("name"):
                return str(info["name"])
        seat_names = {str(info.get("name", "")) for info in players.values() if info.get("name")}
        for alias in hero_aliases_from_settings(settings, site):
            if alias in seat_names:
                return alias
    if raw_text:
        dealt: List[str] = []
        for match in re.finditer(r"Dealt to (.+?) \[(.+?)\]", raw_text):
            name = match.group(1).strip()
            if name and name not in dealt:
                dealt.append(name)
        if dealt:
            for alias in hero_aliases_from_settings(settings, site):
                if alias in dealt:
                    return alias
            return dealt[0]
    aliases = hero_aliases_from_settings(settings, site)
    return aliases[0] if aliases else ""


def format_hero_result(hand, value: float = None) -> str:
    """Format hero net for display — dollars for cash, chips for tournaments."""
    v = value if value is not None else (getattr(hand, "hero_won", 0) or 0)
    if getattr(hand, "is_tournament", False):
        if v > 0:
            return f"+{v:,.0f}"
        if v < 0:
            return f"{v:,.0f}"
        return "±0"
    if abs(v) < 0.005:
        return "±$0.00"
    if v > 0:
        return f"+${v:.2f}"
    return f"-${abs(v):.2f}"


def format_hero_result_plain(value: float, is_tournament: bool = False) -> str:
    """Format a numeric result without a hand object."""
    if is_tournament:
        if value > 0:
            return f"+{value:,.0f}"
        if value < 0:
            return f"{value:,.0f}"
        return "±0"
    if abs(value) < 0.005:
        return "±$0.00"
    if value > 0:
        return f"+${value:.2f}"
    return f"-${abs(value):.2f}"
