"""
Multi-way and heads-up pot odds for LeakSnipe.

Immediate pot odds (correct for any number of players already in the pot):
    to_call / (pot_before_hero_acts + to_call)

The common mistake in multi-way pots is using pot size *before* callers' chips
are counted — ``hu_pot_odds_wrong`` captures that naive heads-up-style error.

BetACR MTT antes are included via ``dead_money`` / ``pot_size_breakdown.antes``.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any, Dict, List, Optional, Sequence, Union

_HERO_DECISIONS = frozenset({"fold", "check", "call", "raise", "bet", "all-in", "allin"})
_STREET_NAMES = ("preflop", "flop", "turn", "river")


def compute_pot_odds(
    pot: float,
    to_call: float,
    *,
    num_callers: int = 0,
    callers_amount: float = 0,
    dead_money: float = 0,
) -> float:
    """Return immediate pot odds as a fraction in [0, 1].

    ``pot`` is the live pot before hero's call (includes dead money, blinds,
    prior-street bets, aggressor bet, and any callers who already matched).
    ``callers_amount`` is informational — callers' chips should already be in
    ``pot``; when ``pot`` excludes them, pass ``callers_amount`` to add them.
    """
    tc = max(0.0, float(to_call or 0))
    if tc <= 0:
        return 0.0
    live_pot = max(0.0, float(pot or 0)) + max(0.0, float(dead_money or 0))
    if callers_amount > 0 and num_callers > 0:
        # Allow explicit caller chips when pot omits them (manual calculator).
        live_pot += float(callers_amount)
    denom = live_pot + tc
    if denom <= 0:
        return 0.0
    return round(tc / denom, 4)


def heads_up_pot_odds_naive(pot_before_bet: float, to_call: float) -> float:
    """Naive pot odds ignoring callers — used to warn coaches in multi-way spots."""
    return compute_pot_odds(pot_before_bet, to_call)


def _blind_levels(streets: List[dict]) -> set:
    amounts = set()
    for street in streets or []:
        for act in street.get("actions") or []:
            if (act.get("action") or "").lower() == "post":
                amt = float(act.get("amount") or 0)
                if amt > 0:
                    amounts.add(amt)
    return set(sorted(amounts, reverse=True)[:2])


def extract_mtt_ante(streets: List[dict]) -> Dict[str, float]:
    """Parse BetACR-style ``posts ante X`` lines from the action log."""
    blinds = _blind_levels(streets)
    ante_amounts: List[float] = []
    for street in streets or []:
        for act in street.get("actions") or []:
            if (act.get("action") or "").lower() != "post":
                continue
            amt = float(act.get("amount") or 0)
            if amt > 0 and amt not in blinds:
                ante_amounts.append(amt)
    if not ante_amounts:
        return {"ante_per_player": 0.0, "num_players": 0.0, "dead_money": 0.0}
    counts: Dict[float, int] = defaultdict(int)
    for a in ante_amounts:
        counts[a] += 1
    ante = max(counts, key=counts.get)
    n = float(len(ante_amounts))
    return {
        "ante_per_player": ante,
        "num_players": n,
        "dead_money": ante * n,
    }


def _pot_size_breakdown(
    *,
    antes: float,
    blinds: float,
    prior_streets: float,
    current_street: float,
    total_pot: float,
    to_call: float,
) -> Dict[str, float]:
    return {
        "antes": round(antes, 2),
        "blinds": round(blinds, 2),
        "prior_streets": round(prior_streets, 2),
        "current_street": round(current_street, 2),
        "total_pot": round(total_pot, 2),
        "to_call": round(to_call, 2),
        "pot_if_call": round(total_pot + to_call, 2),
        "max_winnable": round(total_pot + to_call, 2),
    }


def walk_hero_spots(
    streets: List[dict],
    players: List[dict],
    hero_name: str,
) -> List[Dict[str, Any]]:
    """Walk the action log and compute pot-odds facts at each hero decision."""
    stacks = {
        str(p.get("name") or ""): float(p.get("stack") or 0) for p in (players or [])
    }
    blinds_set = _blind_levels(streets)
    tol = (max(blinds_set) if blinds_set else 0.0) or 1.0
    ante_info = extract_mtt_ante(streets)

    invested: Dict[str, float] = defaultdict(float)
    folded: set = set()
    spots: List[Dict[str, Any]] = []
    street_start_invested = 0.0
    antes_total = 0.0
    blinds_total = 0.0

    for street in streets or []:
        sname = (street.get("name") or "").lower()
        bet_line: Dict[str, float] = defaultdict(float)
        current_bet = 0.0
        aggressor: Optional[str] = None
        street_start_invested = sum(invested.values())

        for act in street.get("actions") or []:
            player = str(act.get("player") or "")
            action = (act.get("action") or "").lower()
            amount = float(act.get("amount") or 0)
            if not player or player == "Uncalled":
                continue

            if action == "fold":
                folded.add(player)

            if player == hero_name and action in _HERO_DECISIONS:
                to_call = max(0.0, current_bet - bet_line[hero_name])
                hero_rem = max(0.0, stacks.get(hero_name, 0.0) - invested[hero_name])
                facing_all_in = False
                for opp, contrib in bet_line.items():
                    if opp == hero_name or contrib < current_bet - 0.01:
                        continue
                    opp_stk = stacks.get(opp, 0.0)
                    if opp_stk and (opp_stk - invested[opp]) <= tol:
                        facing_all_in = True
                if to_call > 0 and hero_rem > 0 and to_call >= hero_rem - tol:
                    facing_all_in = True
                can_raise = (not facing_all_in) and hero_rem > to_call + tol

                pot_before = sum(invested.values())
                pot_odds = compute_pot_odds(pot_before, to_call)

                active = {
                    p for p in stacks if p and p not in folded and p != hero_name
                }
                num_players_in_pot = len(active) + 1  # include hero

                callers: List[str] = []
                if to_call > 0 and current_bet > 0:
                    for opp, contrib in bet_line.items():
                        if opp in (hero_name, aggressor) or opp in folded:
                            continue
                        if contrib >= current_bet - tol:
                            callers.append(opp)
                num_callers = len(callers)
                callers_amount = sum(bet_line[c] for c in callers)

                pot_before_aggression = street_start_invested + sum(
                    bet_line[p] for p in bet_line if p != aggressor
                ) if aggressor else pot_before - sum(bet_line.values())
                # Pot ignoring callers on this street (common coaching mistake).
                pot_naive = max(0.0, pot_before - callers_amount)
                if aggressor and to_call > 0:
                    pot_naive = max(
                        0.0,
                        street_start_invested + bet_line.get(aggressor or "", 0.0),
                    )
                hu_pot_odds_wrong = (
                    heads_up_pot_odds_naive(pot_naive, to_call) if to_call > 0 else 0.0
                )

                prior_streets = max(0.0, street_start_invested - antes_total - blinds_total)
                current_street_total = sum(bet_line.values())
                breakdown = _pot_size_breakdown(
                    antes=antes_total,
                    blinds=blinds_total,
                    prior_streets=prior_streets,
                    current_street=current_street_total,
                    total_pot=pot_before,
                    to_call=to_call,
                )
                if num_callers > 0:
                    breakdown["callers_chips"] = round(callers_amount, 2)
                    breakdown["num_callers"] = float(num_callers)

                multiway = num_players_in_pot >= 3 or num_callers >= 1
                players_behind = _players_left_to_act(
                    street, act, hero_name, folded, stacks
                )

                if to_call <= 0.01:
                    legal = ["check"] + (["bet"] if can_raise else [])
                else:
                    legal = ["fold", "call"] + (["raise"] if can_raise else [])

                spots.append({
                    "street": sname,
                    "action": action,
                    "amount": amount,
                    "to_call": round(to_call, 2),
                    "facing_all_in": facing_all_in,
                    "can_raise": can_raise,
                    "effective_stack": round(hero_rem, 2),
                    "pot_odds": pot_odds,
                    "hu_pot_odds_wrong": round(hu_pot_odds_wrong, 4),
                    "pot_before": round(pot_before, 2),
                    "multiway": multiway,
                    "num_players_in_pot": num_players_in_pot,
                    "num_callers_facing": num_callers,
                    "callers_amount": round(callers_amount, 2),
                    "players_behind": players_behind,
                    "closing_action": players_behind == 0,
                    "ante_per_player": ante_info["ante_per_player"],
                    "dead_money": ante_info["dead_money"],
                    "pot_size_breakdown": breakdown,
                    "legal_actions": legal,
                })

            # Apply action to running state.
            if action in ("raise", "bet") and amount > bet_line[player]:
                inc = amount - bet_line[player]
                bet_line[player] = amount
                aggressor = player
            else:
                inc = amount
                counts_to_line = action != "post" or amount in blinds_set
                if counts_to_line:
                    bet_line[player] += amount
            invested[player] += inc
            if action == "post" and amount in blinds_set:
                blinds_total += inc
            elif action == "post" and amount not in blinds_set and amount > 0:
                antes_total += inc
            if bet_line[player] > current_bet:
                current_bet = bet_line[player]

    return spots


def _players_left_to_act(
    street: dict,
    current_act: dict,
    hero_name: str,
    folded: set,
    stacks: Dict[str, float],
) -> int:
    """Count opponents still to act after hero on this street."""
    actions = street.get("actions") or []
    try:
        idx = actions.index(current_act)
    except ValueError:
        return 0
    seen_after: set = set()
    for act in actions[idx + 1 :]:
        player = str(act.get("player") or "")
        action = (act.get("action") or "").lower()
        if not player or player == hero_name or player in folded:
            continue
        if action in _HERO_DECISIONS and player not in seen_after:
            seen_after.add(player)
    return len(seen_after)


def multiway_pot_odds_from_hand(
    hand: Union[dict, Any],
    hero_name: str,
    *,
    hero_decision_index: Optional[int] = None,
) -> Union[Dict[str, Any], List[Dict[str, Any]]]:
    """Compute pot-odds spots from a hand dict or Hand model.

    When ``hero_decision_index`` is given, return that single spot; otherwise
    return all hero decision spots in order.
    """
    if hasattr(hand, "streets"):
        streets = list(getattr(hand, "streets", None) or [])
        players = [
            {"name": info.get("name", ""), "stack": float(info.get("stack") or 0)}
            for info in (getattr(hand, "players", None) or {}).values()
        ]
    else:
        streets = list(hand.get("streets") or [])
        players = list(hand.get("players") or [])

    spots = walk_hero_spots(streets, players, hero_name)
    if hero_decision_index is not None:
        if hero_decision_index < 0 or hero_decision_index >= len(spots):
            raise IndexError(f"hero_decision_index {hero_decision_index} out of range")
        return spots[hero_decision_index]
    return spots


def format_pot_odds_line(spot: Dict[str, Any]) -> str:
    """One-line summary for prompts or UI."""
    if float(spot.get("to_call") or 0) <= 0:
        return ""
    pct = float(spot.get("pot_odds") or 0)
    n = int(spot.get("num_players_in_pot") or 2)
    if spot.get("multiway"):
        callers = int(spot.get("num_callers_facing") or 0)
        wrong = float(spot.get("hu_pot_odds_wrong") or 0)
        line = (
            f"Pot odds (multi-way, {n} players): {pct:.1%}"
        )
        if callers > 0:
            line += f" — facing bet with {callers} caller{'s' if callers != 1 else ''}"
        if wrong > pct + 0.001:
            line += f" (not {wrong:.1%} heads-up naive)"
        return line
    return f"Pot odds ({n}-way): {pct:.1%}"


def multiway_equity_note(spot: Dict[str, Any]) -> str:
    """Short coaching note for equity vs multiple opponents."""
    if not spot.get("multiway"):
        return ""
    n = int(spot.get("num_players_in_pot") or 2)
    callers = int(spot.get("num_callers_facing") or 0)
    parts = [
        f"Multi-way ({n} players): hero equity vs one opponent overstates strength.",
    ]
    if callers >= 2:
        parts.append(
            "With multiple callers, assume tighter combined ranges — "
            "need stronger equity to continue."
        )
    elif callers == 1:
        parts.append(
            "One caller widens the pot but still requires equity vs a stronger field."
        )
    win = float((spot.get("pot_size_breakdown") or {}).get("pot_if_call") or 0)
    if win > 0:
        parts.append(f"Implied upside if hero hits: ~{win:,.0f} chips in the middle.")
    return " ".join(parts)
