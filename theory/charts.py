"""
MTT stack-depth charts — CFR+-backed with NN / MC fallbacks.

Charts at 5–100 BB integrate:
  - CFR+ tournament push/fold (3-bucket) per depth → shove/call frequencies
  - Scaled open/defend/3-bet approximations for deeper stacks
  - Neural value net for per-combo EV hints on borderline hands
  - equity.py Monte Carlo for cross-check (optional)

Designed so full 169-combo CFR+ can replace bucket mapping later.
"""

from __future__ import annotations

from functools import lru_cache
from functools import lru_cache
from typing import Any, Dict, List, Optional, Tuple

import equity as equity_engine
from theory.cfr_solver import BUCKET_NAMES, run_cfr_for_game

# ── Defaults (BetACR MTT) ─────────────────────────────────────────────────────

CHART_DEPTHS: Tuple[int, ...] = (5, 10, 25, 35, 50, 75, 100)
CHART_POSITIONS: Tuple[str, ...] = ("UTG", "MP", "CO", "BTN", "SB", "BB")
CHART_ACTIONS: Tuple[str, ...] = ("fold", "push", "open", "call", "defend", "3bet")

DEFAULT_ANTE_PER_PLAYER = 500.0
DEFAULT_NUM_PLAYERS = 9
DEFAULT_BB = 1000.0
DEFAULT_CFR_ITERATIONS = 8000

RANKS = "AKQJT98765432"

# Depth modes
PUSH_FOLD_DEPTHS = frozenset({5, 10})
MIXED_DEPTHS = frozenset({25, 35})
DEEP_DEPTHS = frozenset({50, 75, 100})


def list_chart_depths() -> List[int]:
    return list(CHART_DEPTHS)


def iter_hand_notations() -> List[str]:
    out: List[str] = []
    for i, r1 in enumerate(RANKS):
        for j, r2 in enumerate(RANKS):
            if i == j:
                out.append(f"{r1}{r2}")
            elif i < j:
                out.append(f"{r1}{r2}s")
            else:
                out.append(f"{r2}{r1}o")
    return out


ALL_HANDS = iter_hand_notations()


def _chen_strength(notation: str) -> float:
    """Rough preflop strength score (0–1) for bucket assignment."""
    n = notation
    if len(n) == 2:
        r = equity_engine.RANK_VALUES[n[0]]
        return min(1.0, 0.45 + r / 14.0 * 0.55)
    hi = equity_engine.RANK_VALUES[n[0]]
    lo = equity_engine.RANK_VALUES[n[1]]
    suited = n.endswith("s")
    gap = abs(hi - lo)
    score = (hi + lo) / 28.0
    if suited:
        score += 0.08
    if gap == 1:
        score += 0.04
    elif gap == 2:
        score += 0.02
    if hi >= 12 and lo >= 10:
        score += 0.06
    return float(min(1.0, max(0.0, score)))


def _build_hand_buckets() -> Dict[str, str]:
    scored = [(h, _chen_strength(h)) for h in ALL_HANDS]
    scored.sort(key=lambda x: x[1], reverse=True)
    n = len(scored)
    strong_cut = int(n * 0.18)
    medium_cut = int(n * 0.45)
    buckets: Dict[str, str] = {}
    for i, (hand, _) in enumerate(scored):
        if i < strong_cut:
            buckets[hand] = "strong"
        elif i < medium_cut:
            buckets[hand] = "medium"
        else:
            buckets[hand] = "weak"
    return buckets


HAND_BUCKETS: Dict[str, str] = _build_hand_buckets()


def _grid_coords(notation: str) -> Tuple[int, int]:
    """Return (row, col) for 13×13 grid (row=high rank, col=low rank)."""
    if len(notation) == 2:
        r = notation[0]
        return RANKS.index(r), RANKS.index(r)
    hi, lo = notation[0], notation[1]
    if notation.endswith("s"):
        return RANKS.index(hi), RANKS.index(lo)
    return RANKS.index(lo), RANKS.index(hi)


def _depth_open_range(position: str, stack_bb: float) -> str:
    """Scale baseline RFI by stack depth (tighter when short)."""
    base = equity_engine.position_range(position, "open")
    if stack_bb <= 10:
        # Jam-or-fold subset
        return "22+,A2s+,A8o+,K9s+,KTo+,Q9s+,QTo+,J9s+,T9s,98s"
    if stack_bb <= 25:
        return "22+,A2s+,A5s,A7s+,K9s+,KJs+,Q9s+,J9s+,T9s,98s,87s,ATo+,KJo+,QJo"
    if stack_bb <= 35:
        return base
    if stack_bb <= 50:
        return base
    return base


def _depth_threebet_range(position: str, stack_bb: float) -> str:
    base = equity_engine.position_range(position, "3bet")
    if stack_bb <= 35:
        return "TT+,AQs+,AKo"
    if stack_bb <= 50:
        return base
    return base


def _depth_scale_factor(stack_bb: float) -> float:
    """0 = very tight, 1 = full deep range."""
    if stack_bb <= 10:
        return 0.35
    if stack_bb <= 25:
        return 0.55
    if stack_bb <= 35:
        return 0.72
    if stack_bb <= 50:
        return 0.85
    return 1.0


@lru_cache(maxsize=128)
def _cfr_bucket_strategy(
    stack_bb: float,
    ante_per_player: float,
    num_players: int,
    bb: float,
    iterations: int,
) -> Dict[str, Any]:
    """Run CFR+ push/fold at stack depth; cache by params."""
    result = run_cfr_for_game(
        "tournament_push_fold",
        iterations=iterations,
        seed=42,
        ante_per_player=ante_per_player,
        num_players=num_players,
        bb=bb,
        stack_bb=stack_bb,
    )
    strat = result.get("strategy") or {}
    buckets: Dict[str, Dict[str, float]] = {}
    for bucket in BUCKET_NAMES:
        shove_key = f"P0:{bucket}:root"
        call_key = f"P1:{bucket}:vs_shove"
        shove = strat.get(shove_key, {})
        call = strat.get(call_key, {})
        buckets[bucket] = {
            "shove": float(shove.get("shove", shove.get("bet", 0.0))),
            "fold_sb": float(shove.get("fold", shove.get("check", 0.0))),
            "call_bb": float(call.get("call", 0.0)),
            "fold_bb": float(call.get("fold", 0.0)),
        }
    cfg = result.get("config") or {}
    return {
        "exploitability": result.get("exploitability"),
        "buckets": buckets,
        "config": cfg,
        "iterations": iterations,
    }


def _action_from_cfr(
    bucket: str,
    cfr: Dict[str, Any],
    *,
    role: str,
) -> Tuple[str, float]:
    """Map CFR bucket strategy to primary chart action + frequency."""
    b = cfr["buckets"][bucket]
    if role == "sb_shove":
        if b["shove"] >= 0.55:
            return "push", b["shove"]
        if b["shove"] >= 0.15:
            return "push", b["shove"]
        return "fold", b["fold_sb"]
    if role == "bb_defend":
        if b["call_bb"] >= 0.55:
            return "call", b["call_bb"]
        if b["call_bb"] >= 0.15:
            return "call", b["call_bb"]
        return "fold", b["fold_bb"]
    # BTN/CO open-spot at short stacks: treat shove as push
    if b["shove"] >= 0.5:
        return "push", b["shove"]
    return "fold", max(b["fold_sb"], 1.0 - b["shove"])


def _cell_from_ranges(
    notation: str,
    open_combos: set,
    threebet_combos: set,
    defend_combos: set,
    cfr: Dict[str, Any],
    position: str,
    stack_bb: float,
) -> Dict[str, Any]:
    bucket = HAND_BUCKETS[notation]
    combo = equity_engine.parse_range(notation)
    combo_key = tuple(sorted(combo[0])) if combo else None
    in_open = combo_key in open_combos if combo_key else False
    in_3bet = combo_key in threebet_combos if combo_key else False
    in_defend = combo_key in defend_combos if combo_key else False

    pos = position.upper()
    if pos == "SB" and stack_bb <= 35:
        action, freq = _action_from_cfr(bucket, cfr, role="sb_shove")
        return {
            "notation": notation,
            "action": action,
            "freq": round(freq, 3),
            "bucket": bucket,
            "source": "cfr_plus",
        }
    if stack_bb <= 10 and pos in ("SB", "BTN", "CO"):
        role = "sb_shove" if pos == "SB" else "open_push"
        if role == "sb_shove":
            action, freq = _action_from_cfr(bucket, cfr, role="sb_shove")
        else:
            action, freq = _action_from_cfr(bucket, cfr, role="open_push")
            if in_open and action == "fold":
                action, freq = "push", 0.85
    elif pos == "BB" and stack_bb <= 35:
        action, freq = _action_from_cfr(bucket, cfr, role="bb_defend")
        if in_defend and action == "fold" and freq > 0.6:
            action, freq = "defend", 0.7
    elif in_3bet:
        action, freq = "3bet", 1.0
    elif in_open:
        action, freq = "open", 1.0
    elif in_defend:
        action, freq = "defend", 1.0
    else:
        action, freq = "fold", 1.0

    return {
        "notation": notation,
        "action": action,
        "freq": round(freq, 3),
        "bucket": bucket,
        "source": "cfr_plus" if action in ("push", "call") and bucket else "approximation",
    }


def _combo_set(range_str: str) -> set:
    combos = equity_engine.parse_range(range_str)
    return {tuple(sorted(c)) for c in combos}


def get_chart(
    stack_bb: float,
    position: str,
    *,
    ante_per_player: float = DEFAULT_ANTE_PER_PLAYER,
    num_players: int = DEFAULT_NUM_PLAYERS,
    bb: float = DEFAULT_BB,
    game_type: str = "mtt_ante",
    cfr_iterations: int = DEFAULT_CFR_ITERATIONS,
    include_nn: bool = True,
) -> Dict[str, Any]:
    """
    Return a 169-combo chart for stack depth + position.

    CFR+ populates push/fold/call frequencies; open/defend/3-bet ranges scale by depth.
    """
    stack_bb = float(stack_bb)
    position = (position or "BTN").strip().upper()
    if stack_bb not in CHART_DEPTHS:
        raise ValueError(f"stack_bb must be one of {list(CHART_DEPTHS)}")
    if position not in CHART_POSITIONS:
        raise ValueError(f"position must be one of {list(CHART_POSITIONS)}")

    cfr = _cfr_bucket_strategy(
        stack_bb, ante_per_player, num_players, bb, cfr_iterations,
    )
    cfg = cfr.get("config") or {}
    pot_base_bb = cfg.get("pot_base_bb") or (
        1.5 + num_players * (ante_per_player / bb)
    )

    scale = _depth_scale_factor(stack_bb)
    open_str = _depth_open_range(position, stack_bb)
    if scale < 1.0 and stack_bb > 10:
        # Tighten by intersecting with a smaller percent range
        pct = max(8.0, equity_engine.range_frequency(open_str) * scale)
        open_str = f"top {pct:.0f}%"

    open_combos = _combo_set(open_str)
    threebet_combos = _combo_set(_depth_threebet_range(position, stack_bb)) if stack_bb >= 25 else set()
    defend_combos = (
        _combo_set(equity_engine.position_range("BB", "defend"))
        if position == "BB" and stack_bb >= 25
        else set()
    )

    cells: Dict[str, Dict[str, Any]] = {}
    for notation in ALL_HANDS:
        cells[notation] = _cell_from_ranges(
            notation, open_combos, threebet_combos, defend_combos,
            cfr, position, stack_bb,
        )

    if include_nn:
        try:
            from theory.value_net import predict_value

            pos_idx = CHART_POSITIONS.index(position) / max(1, len(CHART_POSITIONS) - 1)
            pot_odds = 0.25 if stack_bb <= 10 else 0.33
            for notation in ALL_HANDS:
                sample = _notation_to_sample_cards(notation)
                if not sample:
                    continue
                pred = predict_value(
                    sample,
                    "",
                    pot_odds=pot_odds,
                    position=pos_idx,
                    ante_per_player=ante_per_player,
                    dead_money=ante_per_player * num_players,
                    bb=bb,
                    stack_bb=stack_bb,
                )
                cells[notation]["nn_value_pct"] = pred.get("value_pct")
        except ImportError:
            pass

    grid: List[List[Optional[Dict[str, Any]]]] = [
        [None for _ in range(13)] for _ in range(13)
    ]
    for notation, cell in cells.items():
        row, col = _grid_coords(notation)
        grid[row][col] = cell

    if stack_bb <= 10:
        mode = "push_fold"
    elif stack_bb <= 35:
        mode = "push_open"
    else:
        mode = "open_defend"

    return {
        "stack_bb": int(stack_bb),
        "position": position,
        "game_type": game_type,
        "mode": mode,
        "ante_per_player": ante_per_player,
        "num_players": num_players,
        "bb": bb,
        "pot_base_bb": round(float(pot_base_bb), 3),
        "dead_money": round(ante_per_player * num_players, 1),
        "source": "cfr_plus+approximation",
        "cfr": {
            "exploitability": cfr.get("exploitability"),
            "iterations": cfr_iterations,
            "buckets": cfr.get("buckets"),
        },
        "cells": cells,
        "grid": grid,
        "legend": list(CHART_ACTIONS),
        "note": (
            "CFR+ tournament push/fold (3-bucket) calibrates shove/call frequencies per depth; "
            "open/defend/3-bet cells use scaled solver approximations. "
            "NN value_pct is a fast EV hint — not full equilibrium."
        ),
    }


def _notation_to_sample_cards(notation: str) -> str:
    """Pick one suited combo for notation (for NN encode)."""
    combos = equity_engine.parse_range(notation)
    if not combos:
        return ""
    c0, c1 = combos[0]
    return equity_engine.card_str(c0) + equity_engine.card_str(c1)


def chart_action_for_hand(
    hero_notation: str,
    stack_bb: float,
    position: str,
    **kwargs: Any,
) -> Optional[Dict[str, Any]]:
    """Lookup chart action for hero hand notation."""
    facts = equity_engine.describe_hole_cards(
        _notation_to_sample_cards(hero_notation) if len(hero_notation) <= 3 else hero_notation
    )
    notation = (facts or {}).get("notation") or hero_notation
    chart = get_chart(stack_bb, position, include_nn=False, **kwargs)
    return chart["cells"].get(notation)


def build_coach_theory_block(
    meta: dict,
    spots: List[Dict[str, Any]],
    *,
    hero_name: str = "Hero",
) -> str:
    """Unified CFR + chart + NN block for AI coach injection."""
    try:
        from theory.value_net import predict_value, theory_context_block
    except ImportError:
        return ""

    hero_cards = meta.get("hero_cards") or ""
    if not hero_cards or hero_cards == "unknown":
        return ""

    is_tournament = bool(meta.get("is_tournament"))
    if not is_tournament:
        return theory_context_block(
            hero_cards.replace(" ", ""),
            " ".join(meta.get("board_cards") or []),
            pot_odds=float((spots[-1] or {}).get("pot_odds") or 0.33),
        )

    streets = meta.get("streets") or []
    ante_info = _extract_ante_from_streets(streets)
    ante = float(ante_info.get("ante_per_player") or DEFAULT_ANTE_PER_PLAYER)
    num_players = int(ante_info.get("num_players") or DEFAULT_NUM_PLAYERS)
    dead = float(ante_info.get("dead_money") or ante * num_players)
    position = (meta.get("hero_position") or "BTN").upper()
    board = " ".join(meta.get("board_cards") or [])

    eff_stack = 25.0
    for s in spots:
        es = float(s.get("effective_stack") or 0)
        if es > 0:
            eff_stack = es
            break
    bb_size = float(meta.get("bb") or DEFAULT_BB)
    stack_bb = max(5.0, min(100.0, eff_stack / bb_size if bb_size else eff_stack / 1000.0))
    nearest = min(CHART_DEPTHS, key=lambda d: abs(d - stack_bb))

    lines = [
        "UNIFIED THEORY (CFR+ charts + neural value — educational, not full NLHE solver):",
        "NOTE: Push/fold charts and CFR+ subgames here are heads-up (HU) abstractions. "
        "They do not model multi-way pots — use multi-way pot odds from spot facts when 3+ players.",
        theory_context_block(
            hero_cards.replace(" ", ""),
            board.replace(" ", ""),
            pot_odds=float((spots[-1] or {}).get("pot_odds") or 0.33),
            ante_per_player=ante,
            dead_money=dead,
            stack_bb=nearest,
            position=position,
        ),
    ]

    try:
        chart = get_chart(
            nearest, position,
            ante_per_player=ante,
            num_players=num_players,
            include_nn=False,
        )
        facts = equity_engine.describe_hole_cards(hero_cards)
        notation = (facts or {}).get("notation")
        if notation and notation in chart["cells"]:
            cell = chart["cells"][notation]
            lines.append(
                f"Chart @ {nearest}BB {position} (ante {ante:.0f}/player, pot≈{chart['pot_base_bb']:.1f}bb): "
                f"{notation} → {cell['action']} ({cell['freq']*100:.0f}%) "
                f"[CFR bucket={cell.get('bucket')}, exploit≈{chart['cfr'].get('exploitability')}]"
            )
        cfr_b = chart["cfr"].get("buckets") or {}
        for bname in BUCKET_NAMES:
            b = cfr_b.get(bname)
            if b:
                lines.append(
                    f"  CFR+ {bname}: shove {b.get('shove', 0)*100:.0f}% | "
                    f"BB call {b.get('call_bb', 0)*100:.0f}%"
                )
    except Exception:
        pass

    return "\n".join(lines)


def _extract_ante_from_streets(streets: List[dict]) -> Dict[str, float]:
    """Pull ante / table size from parsed hand streets."""
    ante = 0.0
    n = 9
    for st in streets or []:
        for act in st.get("actions") or []:
            if act.get("action") == "ante":
                amt = float(act.get("amount") or 0)
                if amt > ante:
                    ante = amt
            if act.get("action") == "post" and "ante" in str(act.get("type") or "").lower():
                amt = float(act.get("amount") or 0)
                if amt > 0:
                    ante = max(ante, amt)
        players = st.get("players") or []
        if players:
            n = max(n, len(players))
    return {
        "ante_per_player": ante,
        "num_players": n,
        "dead_money": ante * n,
    }


def validate_chart_vs_cfr(
    stack_bb: float,
    *,
    ante_per_player: float = DEFAULT_ANTE_PER_PLAYER,
    position: str = "SB",
) -> Dict[str, Any]:
    """Sanity: bucket-level chart actions align with CFR+ shove/call frequencies."""
    chart = get_chart(stack_bb, position, ante_per_player=ante_per_player, include_nn=False)
    cfr_buckets = chart["cfr"]["buckets"]
    mismatches: List[str] = []
    for bucket in BUCKET_NAMES:
        cfr = cfr_buckets[bucket]
        shove_freq = cfr.get("shove", 0.0)
        sample_hands = [h for h, b in HAND_BUCKETS.items() if b == bucket][:3]
        for h in sample_hands:
            cell = chart["cells"][h]
            if position == "SB":
                if shove_freq >= 0.6 and cell["action"] not in ("push", "open"):
                    mismatches.append(f"{h}: expected push, got {cell['action']}")
                if shove_freq <= 0.1 and cell["action"] == "push" and cell["freq"] > 0.5:
                    mismatches.append(f"{h}: expected fold, got push")
    return {
        "stack_bb": stack_bb,
        "position": position,
        "ok": len(mismatches) == 0,
        "mismatches": mismatches,
    }
