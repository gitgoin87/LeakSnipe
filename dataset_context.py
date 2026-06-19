"""
Build compact player/dataset summaries for AI coaching context.
Summarizes the full hand database without dumping raw histories.
"""

from __future__ import annotations

import threading
import time
from collections import Counter, defaultdict
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from analysis import LeakEngine, aggregate_3bet_stats
from models import Hand, HandDatabase

DATASET_CACHE_TTL_SEC = 120

_cache_lock = threading.Lock()
_cache: Dict[str, Any] = {
    "db_path": None,
    "settings_key": None,
    "profile": None,
    "text": None,
    "built_at": 0.0,
}


def invalidate_dataset_context_cache() -> None:
    """Clear cached dataset profile (call after imports or DB changes)."""
    with _cache_lock:
        _cache["profile"] = None
        _cache["text"] = None
        _cache["built_at"] = 0.0


def _settings_cache_key(settings: Dict[str, Any]) -> str:
    hero = settings.get("hero_names") or {}
    return str(sorted(hero.items()))


def _hand_date(h: Hand) -> Optional[datetime]:
    if h.date:
        return h.date
    return None



def _recent_window_stats(
    hands: List[Hand], settings: Dict[str, Any], n: int = 100
) -> Dict[str, Any]:
    """Stats for the most recent N hands (hands assumed date-desc)."""
    sample = hands[:n]
    if not sample:
        return {}
    engine = LeakEngine(settings)
    s = engine.analyze(sample)
    won = sum(1 for h in sample if h.hero_won > 0)
    net_cash = sum(h.hero_won for h in sample if not h.is_tournament)
    return {
        "hands": len(sample),
        "vpip": s.get("vpip"),
        "pfr": s.get("pfr"),
        "win_pct": round(100 * won / len(sample), 1),
        "net_cash": round(net_cash, 2),
    }


def _session_trends(hands: List[Hand], settings: Dict[str, Any], limit: int = 8) -> List[Dict[str, Any]]:
    """Per-day session aggregates (most recent sessions first)."""
    by_day: Dict[str, Dict[str, Any]] = defaultdict(
        lambda: {"hands": 0, "net_cash": 0.0, "net_chips": 0.0, "won": 0}
    )
    for h in hands:
        hero = h.hero_name(settings)
        if not hero:
            continue
        dt = _hand_date(h)
        day = dt.strftime("%Y-%m-%d") if dt else "unknown"
        by_day[day]["hands"] += 1
        if h.is_tournament:
            by_day[day]["net_chips"] += h.hero_won
        else:
            by_day[day]["net_cash"] += h.hero_won
        if h.hero_won > 0:
            by_day[day]["won"] += 1

    sessions = []
    for day in sorted(by_day.keys(), reverse=True):
        if day == "unknown":
            continue
        d = by_day[day]
        sessions.append(
            {
                "date": day,
                "hands": d["hands"],
                "net_cash": round(d["net_cash"], 2),
                "net_chips": round(d["net_chips"], 0),
                "win_pct": round(100 * d["won"] / max(d["hands"], 1), 1),
            }
        )
    return sessions[:limit]


def _notable_spots(stats: Dict[str, Any], limit: int = 5) -> Dict[str, List[str]]:
    """Short descriptions of biggest wins/losses."""
    wins: List[str] = []
    losses: List[str] = []
    for amt, h in stats.get("biggest_wins", [])[:limit]:
        if amt > 0:
            board = " ".join(h.board_cards) or "—"
            wins.append(
                f"+{amt:.0f} | {h.site} | {h.hero_cards} | {h.hero_position} | "
                f"board {board} | {h.hand_id}"
            )
    for amt, h in stats.get("biggest_losses", [])[:limit]:
        if amt < 0:
            board = " ".join(h.board_cards) or "—"
            losses.append(
                f"{amt:.0f} | {h.site} | {h.hero_cards} | {h.hero_position} | "
                f"board {board} | {h.hand_id}"
            )
    return {"biggest_wins": wins, "biggest_losses": losses}


def build_player_profile(
    settings: Dict[str, Any],
    hands: List[Hand],
) -> Dict[str, Any]:
    """Structured player profile from all hands in the database."""
    engine = LeakEngine(settings)
    stats = engine.analyze(hands)

    game_types = Counter(h.game_type or "unknown" for h in hands)
    sites = Counter(h.site for h in hands if h.site)
    cash_hands = sum(1 for h in hands if not h.is_tournament)
    mtt_hands = sum(1 for h in hands if h.is_tournament)
    net_cash = round(
        sum(h.hero_won for h in hands if not h.is_tournament), 2
    )
    net_chips = round(
        sum(h.hero_won for h in hands if h.is_tournament), 0
    )
    won_hands = sum(1 for h in hands if h.hero_won > 0)

    hero_names = {
        site: name
        for site, name in (settings.get("hero_names") or {}).items()
        if name
    }

    dates = [d for h in hands if (d := _hand_date(h))]
    date_range = None
    if dates:
        date_range = {
            "earliest": min(dates).strftime("%Y-%m-%d"),
            "latest": max(dates).strftime("%Y-%m-%d"),
        }

    alerts = [
        {"level": level, "message": msg}
        for level, msg in stats.get("alerts", [])
    ]

    return {
        "total_hands": stats.get("total_hands", 0),
        "won_hands": won_hands,
        "win_pct": round(100 * won_hands / max(stats.get("total_hands", 1), 1), 1),
        "career": {
            "vpip": stats.get("vpip"),
            "pfr": stats.get("pfr"),
            "af": stats.get("af"),
            "wtsd": stats.get("wtsd"),
            "wsd": stats.get("wsd"),
            "cbet": stats.get("cbet"),
            "net_cash": net_cash,
            "net_chips": net_chips,
        },
        "three_bet": aggregate_3bet_stats(hands, settings),
        "recent_100": _recent_window_stats(hands, settings, 100),
        "by_position": stats.get("by_position", {}),
        "by_site": stats.get("by_site", {}),
        "game_types": dict(game_types.most_common(6)),
        "sites": dict(sites),
        "cash_hands": cash_hands,
        "mtt_hands": mtt_hands,
        "date_range": date_range,
        "sessions": _session_trends(hands, settings),
        "leak_alerts": alerts,
        "notable_spots": _notable_spots(stats),
        "hero_names": hero_names,
        "built_at": datetime.now().isoformat(),
    }


def format_profile_for_prompt(profile: Dict[str, Any], max_chars: int = 6000) -> str:
    """Render profile as compact text for LLM system/user prompts."""
    if not profile or not profile.get("total_hands"):
        return ""

    lines = [
        "PLAYER DATABASE SUMMARY (authoritative career stats — use for personalized coaching):",
        f"- Total hands: {profile['total_hands']} | Win rate: {profile.get('win_pct')}%",
    ]

    cr = profile.get("career") or {}
    lines.append(
        f"- Career: VPIP {cr.get('vpip')}% | PFR {cr.get('pfr')}% | AF {cr.get('af')} | "
        f"WTSD {cr.get('wtsd')}% | W$SD {cr.get('wsd')}% | C-bet {cr.get('cbet')}%"
    )

    tb = profile.get("three_bet") or {}
    if tb.get("opportunities", 0) > 0:
        lines.append(
            f"- 3-bet: {tb.get('pct')}% ({tb.get('made')}/{tb.get('opportunities')} spots)"
        )

    if cr.get("net_cash") is not None:
        lines.append(f"- Net cash: {cr.get('net_cash'):+.2f}")
    if profile.get("mtt_hands"):
        lines.append(
            f"- MTT hands: {profile.get('mtt_hands')} | Net chips: {cr.get('net_chips'):+}"
        )

    recent = profile.get("recent_100") or {}
    if recent.get("hands"):
        lines.append(
            f"- Last {recent['hands']} hands: VPIP {recent.get('vpip')}% | "
            f"PFR {recent.get('pfr')}% | Win {recent.get('win_pct')}% | "
            f"Net {recent.get('net_cash'):+.2f}"
        )

    dr = profile.get("date_range")
    if dr:
        lines.append(f"- Date range: {dr.get('earliest')} to {dr.get('latest')}")

    heroes = profile.get("hero_names") or {}
    if heroes:
        hero_str = ", ".join(f"{s}: {n}" for s, n in heroes.items())
        lines.append(f"- Hero aliases: {hero_str}")

    sites = profile.get("sites") or {}
    if sites:
        site_str = ", ".join(f"{s} ({c})" for s, c in sites.items())
        lines.append(f"- Sites: {site_str}")

    lines.append("")
    lines.append("Positional (VPIP / PFR):")
    for pos in ("EP", "MP", "CO", "BTN", "SB", "BB"):
        pd = (profile.get("by_position") or {}).get(pos)
        if pd and pd.get("total", 0) > 0:
            lines.append(
                f"  {pos}: {pd['total']}h | VPIP {pd.get('vpip')}% | PFR {pd.get('pfr')}%"
            )

    alerts = profile.get("leak_alerts") or []
    if alerts:
        lines.append("")
        lines.append("Known leak patterns:")
        for a in alerts[:8]:
            lines.append(f"  • {a.get('message')}")

    sessions = profile.get("sessions") or []
    if sessions:
        lines.append("")
        lines.append("Recent sessions:")
        for s in sessions[:6]:
            net = s.get("net_cash", 0)
            lines.append(
                f"  {s.get('date')}: {s.get('hands')}h | net {net:+.2f} | "
                f"win {s.get('win_pct')}%"
            )

    spots = profile.get("notable_spots") or {}
    wins = spots.get("biggest_wins") or []
    losses = spots.get("biggest_losses") or []
    if wins or losses:
        lines.append("")
        lines.append("Notable spots (hand_id for reference):")
        for w in wins[:3]:
            lines.append(f"  WIN: {w}")
        for l in losses[:3]:
            lines.append(f"  LOSS: {l}")

    text = "\n".join(lines)
    if len(text) > max_chars:
        text = text[: max_chars - 20] + "\n… [truncated]"
    return text


def build_dataset_context(
    db_path: str,
    settings: Dict[str, Any],
    *,
    force_refresh: bool = False,
) -> Tuple[Dict[str, Any], str]:
    """Load hands, build profile, return (profile_dict, prompt_text). Cached."""
    sk = _settings_cache_key(settings)
    now = time.time()

    with _cache_lock:
        if (
            not force_refresh
            and _cache.get("profile")
            and _cache.get("db_path") == db_path
            and _cache.get("settings_key") == sk
            and (now - (_cache.get("built_at") or 0)) < DATASET_CACHE_TTL_SEC
        ):
            return dict(_cache["profile"]), str(_cache["text"] or "")

    db = HandDatabase(db_path)
    hands = db.get_all_hands()
    profile = build_player_profile(settings, hands)
    text = format_profile_for_prompt(profile)

    with _cache_lock:
        _cache["db_path"] = db_path
        _cache["settings_key"] = sk
        _cache["profile"] = profile
        _cache["text"] = text
        _cache["built_at"] = now

    return profile, text
