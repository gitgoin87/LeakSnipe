"""
CFR+ (Counterfactual Regret Minimization Plus) for small poker subgames.

Educational/theory tooling — NOT a full NLHE solver. Supports Kuhn poker,
Leduc Hold'em, and an abstracted heads-up push/fold spot. Full NLHE requires
card/action abstraction and subgame solving beyond laptop-scale CFR.

CFR+ (Tammelin et al.): regrets are floored at zero; average strategy over
iterations converges toward a Nash equilibrium in two-player zero-sum games.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple


# ── CFR+ core ────────────────────────────────────────────────────────────────

@dataclass
class CFRPlusState:
    regrets: Dict[str, Dict[int, float]] = field(default_factory=dict)
    strategy_sum: Dict[str, Dict[int, float]] = field(default_factory=dict)

    def ensure(self, info_set: str, n_actions: int) -> None:
        if info_set not in self.regrets:
            self.regrets[info_set] = {a: 0.0 for a in range(n_actions)}
            self.strategy_sum[info_set] = {a: 0.0 for a in range(n_actions)}

    def strategy(self, info_set: str, n_actions: int) -> List[float]:
        self.ensure(info_set, n_actions)
        pos = [max(0.0, self.regrets[info_set][a]) for a in range(n_actions)]
        s = sum(pos)
        if s <= 0:
            return [1.0 / n_actions] * n_actions
        return [p / s for p in pos]

    def accumulate_strategy(self, info_set: str, sigma: List[float], weight: float) -> None:
        n = len(sigma)
        self.ensure(info_set, n)
        for a in range(n):
            self.strategy_sum[info_set][a] += weight * sigma[a]

    def average_strategy(self) -> Dict[str, List[float]]:
        out: Dict[str, List[float]] = {}
        for info_set, sums in self.strategy_sum.items():
            total = sum(sums.values())
            n = len(sums)
            if total <= 0:
                out[info_set] = [1.0 / n] * n
            else:
                out[info_set] = [sums[a] / total for a in range(n)]
        return out


def _cfr_plus_traversal(
    game: "Subgame",
    state: CFRPlusState,
    history: str,
    reach: Tuple[float, float],
    iteration: int,
) -> Tuple[float, float]:
    """Returns (utility_player_0, utility_player_1) at this node."""
    if game.is_chance(history):
        u0 = u1 = 0.0
        for outcome, prob in game.chance_outcomes(history):
            nh = game.apply_chance(history, outcome)
            c0, c1 = _cfr_plus_traversal(game, state, nh, reach, iteration)
            u0 += prob * c0
            u1 += prob * c1
        return u0, u1

    if game.is_terminal(history):
        return game.payoff(history, 0), game.payoff(history, 1)

    current = game.current_player(history)
    info_set = game.info_set(history, current)
    n_actions = game.num_actions(history)
    sigma = state.strategy(info_set, n_actions)
    state.accumulate_strategy(info_set, sigma, reach[current] * iteration)

    util0 = util1 = 0.0
    action_utils0: List[float] = []
    action_utils1: List[float] = []
    for a in range(n_actions):
        next_hist = game.next_history(history, a)
        if current == 0:
            child_reach = (reach[0] * sigma[a], reach[1])
        else:
            child_reach = (reach[0], reach[1] * sigma[a])
        c0, c1 = _cfr_plus_traversal(game, state, next_hist, child_reach, iteration)
        action_utils0.append(c0)
        action_utils1.append(c1)
        util0 += sigma[a] * c0
        util1 += sigma[a] * c1

    state.ensure(info_set, n_actions)
    opp_reach = reach[1 - current]
    action_utils = action_utils0 if current == 0 else action_utils1
    util = util0 if current == 0 else util1
    for a in range(n_actions):
        regret = action_utils[a] - util
        state.regrets[info_set][a] = max(0.0, state.regrets[info_set][a] + opp_reach * regret)

    return util0, util1


def run_cfr_plus(game: "Subgame", iterations: int = 5000, seed: int = 42) -> Dict[str, Any]:
    random.seed(seed)
    state = CFRPlusState()
    for t in range(1, iterations + 1):
        _cfr_plus_traversal(game, state, "", (1.0, 1.0), t)

    avg = state.average_strategy()
    ev0 = _expected_value(game, avg, 0)
    ev1 = _expected_value(game, avg, 1)
    exploit = _exploitability(game, avg)
    return {
        "game": game.name,
        "iterations": iterations,
        "strategy": {k: _label_actions(game, k, v) for k, v in sorted(avg.items())},
        "ev": {"player_0": round(ev0, 6), "player_1": round(ev1, 6)},
        "exploitability": round(exploit, 6),
        "description": game.description,
    }


def _label_actions(game: "Subgame", info_set: str, probs: List[float]) -> Dict[str, float]:
    labels = game.action_labels(info_set)
    return {labels[i]: round(probs[i], 4) for i in range(len(probs))}


def _expected_value(game: "Subgame", strategy: Dict[str, List[float]], player: int) -> float:
    return _ev_recursive(game, "", strategy, player)


def _ev_recursive(game: "Subgame", history: str, strategy: Dict[str, List[float]], player: int) -> float:
    if game.is_chance(history):
        util = 0.0
        for outcome, prob in game.chance_outcomes(history):
            util += prob * _ev_recursive(game, game.apply_chance(history, outcome), strategy, player)
        return util
    if game.is_terminal(history):
        return game.payoff(history, player)
    current = game.current_player(history)
    info_set = game.info_set(history, current)
    n = game.num_actions(history)
    sigma = strategy.get(info_set, [1.0 / n] * n)
    util = 0.0
    for a in range(n):
        util += sigma[a] * _ev_recursive(game, game.next_history(history, a), strategy, player)
    return util


def _best_response_value(game: "Subgame", strategy: Dict[str, List[float]], br_player: int) -> float:
    return _br_recursive(game, "", strategy, br_player)


def _br_recursive(game: "Subgame", history: str, strategy: Dict[str, List[float]], br_player: int) -> float:
    if game.is_chance(history):
        util = 0.0
        for outcome, prob in game.chance_outcomes(history):
            util += prob * _br_recursive(game, game.apply_chance(history, outcome), strategy, br_player)
        return util
    if game.is_terminal(history):
        return game.payoff(history, br_player)
    current = game.current_player(history)
    n = game.num_actions(history)
    if current == br_player:
        return max(_br_recursive(game, game.next_history(history, a), strategy, br_player) for a in range(n))
    info_set = game.info_set(history, current)
    sigma = strategy.get(info_set, [1.0 / n] * n)
    return sum(sigma[a] * _br_recursive(game, game.next_history(history, a), strategy, br_player) for a in range(n))


def _exploitability(game: "Subgame", strategy: Dict[str, List[float]]) -> float:
    br0 = _best_response_value(game, strategy, 0)
    br1 = _best_response_value(game, strategy, 1)
    ev0 = _expected_value(game, strategy, 0)
    ev1 = _expected_value(game, strategy, 1)
    return max(0.0, br0 - ev0) + max(0.0, br1 - ev1)


# ── Subgame interface ─────────────────────────────────────────────────────────

class Subgame:
    name: str = "subgame"
    description: str = ""

    def is_chance(self, history: str) -> bool:
        return False

    def chance_outcomes(self, history: str) -> List[Tuple[str, float]]:
        return []

    def apply_chance(self, history: str, outcome: str) -> str:
        return history + outcome

    def is_terminal(self, history: str) -> bool:
        raise NotImplementedError

    def current_player(self, history: str) -> int:
        raise NotImplementedError

    def num_actions(self, history: str) -> int:
        raise NotImplementedError

    def next_history(self, history: str, action: int) -> str:
        raise NotImplementedError

    def info_set(self, history: str, player: int) -> str:
        raise NotImplementedError

    def payoff(self, history: str, player: int) -> float:
        raise NotImplementedError

    def action_labels(self, info_set: str) -> List[str]:
        raise NotImplementedError


# ── Kuhn poker (full game with deal chance node) ──────────────────────────────

KUHN_CARDS = ("J", "Q", "K")


class KuhnPoker(Subgame):
    """
    Kuhn poker with chance dealing at root. History: ``deal|actions``
    e.g. ``2,0|c`` = P0 has K, P1 has J, P0 checked.
    """

    name = "kuhn"
    description = (
        "Classic 3-card Kuhn poker (J/Q/K). Player 0 acts first. "
        "Known Nash: P0 bets K always, bluffs Q at ~1/3, checks J; "
        "P1 calls K, calls Q at ~1/3 vs bet, folds J."
    )

    def is_chance(self, history: str) -> bool:
        return history == ""

    def chance_outcomes(self, history: str) -> List[Tuple[str, float]]:
        deals = []
        for c0 in range(3):
            for c1 in range(3):
                if c0 != c1:
                    deals.append((f"{c0},{c1}|", 1.0 / 6.0))
        return deals

    def apply_chance(self, history: str, outcome: str) -> str:
        return outcome

    def _cards(self, history: str) -> Tuple[int, int]:
        deal = history.split("|")[0]
        c0, c1 = (int(x) for x in deal.split(","))
        return c0, c1

    def _actions(self, history: str) -> str:
        parts = history.split("|", 1)
        return parts[1] if len(parts) > 1 else ""

    def is_terminal(self, history: str) -> bool:
        if self.is_chance(history):
            return False
        act = self._actions(history)
        return act in ("cc", "bc", "bf", "cbf", "cbc")

    def current_player(self, history: str) -> int:
        act = self._actions(history)
        if act == "":
            return 0
        if act == "c":
            return 1
        if act in ("b", "cb"):
            return 1 if act == "b" else 0
        raise RuntimeError(f"non-decision: {history}")

    def num_actions(self, history: str) -> int:
        return 2

    def next_history(self, history: str, action: int) -> str:
        deal = history.split("|")[0]
        act = self._actions(history)
        if act == "":
            new_act = "c" if action == 0 else "b"
        elif act == "c":
            new_act = "cc" if action == 0 else "cb"
        elif act == "b":
            new_act = "bc" if action == 0 else "bf"
        elif act == "cb":
            new_act = "cbc" if action == 0 else "cbf"
        else:
            raise RuntimeError(act)
        return f"{deal}|{new_act}"

    def info_set(self, history: str, player: int) -> str:
        c0, c1 = self._cards(history)
        card = KUHN_CARDS[c0 if player == 0 else c1]
        act = self._actions(history)
        return f"P{player}:{card}:{act or 'root'}"

    def payoff(self, history: str, player: int) -> float:
        c0, c1 = self._cards(history)
        act = self._actions(history)
        pot = 2
        p0_extra = p1_extra = 0
        if act == "cc":
            pass
        elif act == "bc":
            pot += 2
            p0_extra = p1_extra = 1
        elif act == "bf":
            pot += 1
            p0_extra = 1
        elif act == "cbf":
            pot += 1
            p1_extra = 1
        elif act == "cbc":
            pot += 2
            p0_extra = p1_extra = 1
        else:
            raise RuntimeError(act)

        if act == "bf":
            winner = 0
        elif act == "cbf":
            winner = 1
        else:
            winner = 0 if c0 > c1 else 1

        invested = 1 + (p0_extra if player == 0 else p1_extra)
        if player == winner:
            return pot - invested
        return -invested

    def action_labels(self, info_set: str) -> List[str]:
        hist = info_set.split(":")[-1]
        if hist in ("b", "cb"):
            return ["call", "fold"]
        return ["check", "bet"]


# ── Leduc Hold'em ─────────────────────────────────────────────────────────────

LEDUC_RANKS = ("J", "Q", "K")
LEDUC_SUITS = ("a", "b")


class LeducHoldem(Subgame):
    """Leduc Hold'em: 6-card deck, private + public card, two limit betting rounds."""

    name = "leduc"
    description = (
        "Leduc Hold'em (6-card deck, one private + one board card, two betting rounds). "
        "Educational toy game — converges slower than Kuhn; use 5k+ iterations."
    )
    BET = (2, 4)

    def is_chance(self, history: str) -> bool:
        return history == ""

    def chance_outcomes(self, history: str) -> List[Tuple[str, float]]:
        deck = list(range(6))
        outcomes: List[Tuple[str, float]] = []
        n = 0
        for i in range(6):
            for j in range(6):
                if i == j:
                    continue
                for k in range(6):
                    if k in (i, j):
                        continue
                    outcomes.append((f"{i},{j},{k}|", 1.0))
                    n += 1
        return [(h, p / n) for h, p in outcomes]

    def apply_chance(self, history: str, outcome: str) -> str:
        return outcome

    def _deal(self, history: str) -> Tuple[int, int, int]:
        deal = history.split("|")[0]
        return tuple(int(x) for x in deal.split(","))  # type: ignore

    def _state(self, history: str) -> Dict[str, Any]:
        deal = self._deal(history)
        tail = history.split("|", 1)[1] if "|" in history else ""
        parts = tail.split("/") if tail else []
        street = int(parts[0]) if parts and parts[0].isdigit() else 0
        bets = [0, 0]
        pot = 2
        folded: Optional[int] = None
        checked = False
        if len(parts) > 1 and parts[1]:
            for tok in parts[1].split("-"):
                if tok == "c":
                    if bets[0] == bets[1] and checked:
                        street += 1
                        checked = False
                    else:
                        checked = True
                elif tok == "b":
                    p = (bets[0] + bets[1]) % 2
                    amt = self.BET[min(street, 1)]
                    bets[p] += amt
                    pot += amt
                    checked = False
                elif tok == "f":
                    folded = (bets[0] + bets[1]) % 2
        return {
            "private": (deal[0], deal[1]),
            "board": deal[2],
            "street": street,
            "bets": bets,
            "pot": pot,
            "folded": folded,
            "checked": checked,
            "tail": tail,
        }

    def _card_label(self, idx: int) -> str:
        r, s = divmod(idx, 2)
        return f"{LEDUC_RANKS[r]}{LEDUC_SUITS[s]}"

    def _rank(self, private: int, board: int) -> int:
        pr, _ = divmod(private, 2)
        br, _ = divmod(board, 2)
        if pr == br:
            return pr + 3
        return max(pr, br)

    def is_terminal(self, history: str) -> bool:
        if self.is_chance(history):
            return False
        st = self._state(history)
        return st["folded"] is not None or st["street"] >= 2

    def current_player(self, history: str) -> int:
        st = self._state(history)
        return (st["bets"][0] + st["bets"][1]) % 2

    def num_actions(self, history: str) -> int:
        return 2

    def next_history(self, history: str, action: int) -> str:
        deal = history.split("|")[0]
        st = self._state(history)
        p = self.current_player(history)
        opp = 1 - p
        to_call = st["bets"][opp] - st["bets"][p]
        street = st["street"]
        bets = list(st["bets"])
        pot = st["pot"]
        tail = st["tail"]

        if to_call > 0:
            tok = "c" if action == 0 else "f"
            if action == 0:
                bets[p] += to_call
                pot += to_call
                street += 1
                new_tail = f"{street}/" + (tail.split("/")[1] + "-c" if "/" in tail else "c")
                if street >= 2:
                    return f"{deal}|{street}/show"
                return f"{deal}|{street}/"
            return f"{deal}|{street}/" + (tail.split("/")[1] + "-f" if "/" in tail and tail.split("/")[1] else "f")

        tok = "c" if action == 0 else "b"
        if action == 0:
            if st["checked"]:
                street += 1
                if street >= 2:
                    return f"{deal}|{street}/show"
                return f"{deal}|{street}/"
            prev = tail.split("/")[1] if "/" in tail and tail.split("/")[1] else ""
            return f"{deal}|{street}/{prev + ('-' if prev else '') + 'c'}"

        amt = self.BET[min(street, 1)]
        bets[p] += amt
        pot += amt
        prev = tail.split("/")[1] if "/" in tail and tail.split("/")[1] else ""
        return f"{deal}|{street}/{prev + ('-' if prev else '') + 'b'}"

    def info_set(self, history: str, player: int) -> str:
        st = self._state(history)
        priv = self._card_label(st["private"][player])
        pub = self._card_label(st["board"]) if st["street"] >= 1 else "-"
        return f"P{player}:{priv}:{pub}:s{st['street']}"

    def payoff(self, history: str, player: int) -> float:
        st = self._state(history)
        if st["folded"] is not None:
            winner = 1 - st["folded"]
            return (st["pot"] / 2.0) if player == winner else -(st["pot"] / 2.0)
        p0, p1 = st["private"]
        board = st["board"]
        r0, r1 = self._rank(p0, board), self._rank(p1, board)
        if r0 > r1:
            return (st["pot"] / 2.0) if player == 0 else -(st["pot"] / 2.0)
        if r1 > r0:
            return (st["pot"] / 2.0) if player == 1 else -(st["pot"] / 2.0)
        return 0.0

    def action_labels(self, info_set: str) -> List[str]:
        return ["check", "bet"]


# ── Abstract push/fold ────────────────────────────────────────────────────────

PUSH_FOLD_EQUITY = {
    (0, 0): 0.50, (0, 1): 0.32, (0, 2): 0.18,
    (1, 0): 0.68, (1, 1): 0.50, (1, 2): 0.35,
    (2, 0): 0.82, (2, 1): 0.65, (2, 2): 0.50,
}
BUCKET_NAMES = ("weak", "medium", "strong")


class PushFold3(Subgame):
    """Heads-up preflop push/fold with 3 hand-strength buckets per player."""

    name = "push_fold"
    description = (
        "Abstracted HU preflop push/fold (3 buckets: weak/medium/strong). "
        "Demonstrates CFR+ on a poker-shaped spot without full combo enumeration."
    )

    def is_chance(self, history: str) -> bool:
        return history == ""

    def chance_outcomes(self, history: str) -> List[Tuple[str, float]]:
        out: List[Tuple[str, float]] = []
        for sb in range(3):
            for bb in range(3):
                out.append((f"{sb},{bb}|", 1.0 / 9.0))
        return out

    def apply_chance(self, history: str, outcome: str) -> str:
        return outcome

    def _buckets(self, history: str) -> Tuple[int, int]:
        deal = history.split("|")[0]
        sb, bb = (int(x) for x in deal.split(","))
        return sb, bb

    def _actions(self, history: str) -> str:
        parts = history.split("|", 1)
        return parts[1] if len(parts) > 1 else ""

    def is_terminal(self, history: str) -> bool:
        act = self._actions(history)
        return act in ("f", "sc", "sf")

    def current_player(self, history: str) -> int:
        act = self._actions(history)
        return 0 if act == "" else 1

    def num_actions(self, history: str) -> int:
        return 2

    def next_history(self, history: str, action: int) -> str:
        deal = history.split("|")[0]
        act = self._actions(history)
        if act == "":
            return f"{deal}|{'f' if action == 0 else 's'}"
        if act == "s":
            return f"{deal}|{'sc' if action == 0 else 'sf'}"
        raise RuntimeError(history)

    def info_set(self, history: str, player: int) -> str:
        sb, bb = self._buckets(history)
        b = BUCKET_NAMES[sb if player == 0 else bb]
        act = self._actions(history)
        if player == 0:
            return f"P0:{b}:root"
        return f"P1:{b}:vs_shove"

    def payoff(self, history: str, player: int) -> float:
        sb, bb = self._buckets(history)
        act = self._actions(history)
        if act == "f":
            return -0.5 if player == 0 else 0.5
        if act == "sf":
            return 1.0 if player == 0 else -1.0
        eq = PUSH_FOLD_EQUITY[(sb, bb)]
        sb_ev = eq * 20 - 10
        return sb_ev if player == 0 else -sb_ev

    def action_labels(self, info_set: str) -> List[str]:
        if "vs_shove" in info_set:
            return ["call", "fold"]
        return ["fold", "shove"]


# ── Tournament push/fold (ACR MTT antes) ─────────────────────────────────────

def pot_odds_with_ante(
    to_call: float,
    pot_before: float,
    *,
    ante_per_player: float = 0.0,
    num_players: int = 0,
) -> float:
    """Pot odds including MTT antes / dead money already in the pot."""
    dead = max(0.0, ante_per_player) * max(0, num_players)
    pot = pot_before + dead
    denom = pot + to_call
    if to_call <= 0 or denom <= 0:
        return 0.0
    return round(to_call / denom, 4)


class TournamentPushFold(Subgame):
    """
    Heads-up preflop push/fold with BetACR-style antes.

    ``dead_money = num_players * ante_per_player`` (all antes in the pot).
    Payoffs are in BB units; fold lines stay ±0.5 BB (antes sunk), while
    shove/call EV scales with the larger pot — so higher antes widen calling ranges.
    """

    name = "tournament_push_fold"
    description = (
        "HU MTT push/fold with antes (BetACR-style). "
        "Dead money = num_players × ante_per_player inflates pot odds vs shoves. "
        "Educational abstraction — not a full ICM/chip-EV chart."
    )

    def __init__(
        self,
        *,
        ante_per_player: float = 500.0,
        num_players: int = 9,
        bb: float = 1000.0,
        stack_bb: float = 10.0,
    ) -> None:
        self.ante_per_player = max(0.0, float(ante_per_player))
        self.num_players = max(2, int(num_players))
        self.bb = max(1.0, float(bb))
        self.sb = self.bb / 2.0
        self.stack_bb = max(2.0, float(stack_bb))

    @property
    def ante_bb(self) -> float:
        return self.ante_per_player / self.bb

    @property
    def dead_money(self) -> float:
        return self.num_players * self.ante_per_player

    @property
    def pot_base_bb(self) -> float:
        """Blinds + all antes (BB units) before shove action."""
        return 1.5 + self.num_players * self.ante_bb

    @property
    def inv_sb_bb(self) -> float:
        return 0.5 + self.ante_bb

    @property
    def inv_bb_bb(self) -> float:
        return 1.0 + self.ante_bb

    def is_chance(self, history: str) -> bool:
        return history == ""

    def chance_outcomes(self, history: str) -> List[Tuple[str, float]]:
        out: List[Tuple[str, float]] = []
        for sb in range(3):
            for bb in range(3):
                out.append((f"{sb},{bb}|", 1.0 / 9.0))
        return out

    def apply_chance(self, history: str, outcome: str) -> str:
        return outcome

    def _buckets(self, history: str) -> Tuple[int, int]:
        deal = history.split("|")[0]
        sb, bb = (int(x) for x in deal.split(","))
        return sb, bb

    def _actions(self, history: str) -> str:
        parts = history.split("|", 1)
        return parts[1] if len(parts) > 1 else ""

    def is_terminal(self, history: str) -> bool:
        return self._actions(history) in ("f", "sc", "sf")

    def current_player(self, history: str) -> int:
        act = self._actions(history)
        return 0 if act == "" else 1

    def num_actions(self, history: str) -> int:
        return 2

    def next_history(self, history: str, action: int) -> str:
        deal = history.split("|")[0]
        act = self._actions(history)
        if act == "":
            return f"{deal}|{'f' if action == 0 else 's'}"
        if act == "s":
            return f"{deal}|{'sc' if action == 0 else 'sf'}"
        raise RuntimeError(history)

    def info_set(self, history: str, player: int) -> str:
        sb, bb = self._buckets(history)
        b = BUCKET_NAMES[sb if player == 0 else bb]
        act = self._actions(history)
        if player == 0:
            return f"P0:{b}:root"
        return f"P1:{b}:vs_shove"

    def _showdown_ev_sb(self, sb_bucket: int, bb_bucket: int) -> float:
        eq = PUSH_FOLD_EQUITY[(sb_bucket, bb_bucket)]
        shove = self.stack_bb - self.inv_sb_bb
        total_pot = self.pot_base_bb + 2.0 * shove
        return eq * total_pot - self.stack_bb

    def payoff(self, history: str, player: int) -> float:
        sb, bb = self._buckets(history)
        act = self._actions(history)
        if act == "f":
            return -self.inv_sb_bb if player == 0 else self.inv_sb_bb
        if act == "sf":
            sb_net = self.inv_bb_bb
            return sb_net if player == 0 else -sb_net
        sb_ev = self._showdown_ev_sb(sb, bb)
        return sb_ev if player == 0 else -sb_ev

    def action_labels(self, info_set: str) -> List[str]:
        if "vs_shove" in info_set:
            return ["call", "fold"]
        return ["fold", "shove"]

    def config_summary(self) -> Dict[str, Any]:
        return {
            "ante_per_player": self.ante_per_player,
            "num_players": self.num_players,
            "dead_money": self.dead_money,
            "bb": self.bb,
            "stack_bb": self.stack_bb,
            "pot_base_bb": round(self.pot_base_bb, 4),
        }


# ── Public API ────────────────────────────────────────────────────────────────

SOLVABLE_GAMES: Dict[str, Dict[str, Any]] = {
    "kuhn": {
        "id": "kuhn",
        "name": "Kuhn Poker",
        "description": KuhnPoker.description,
        "default_iterations": 10000,
        "max_iterations": 500000,
    },
    "leduc": {
        "id": "leduc",
        "name": "Leduc Hold'em",
        "description": LeducHoldem.description,
        "default_iterations": 5000,
        "max_iterations": 100000,
    },
    "push_fold": {
        "id": "push_fold",
        "name": "HU Push/Fold (3-bucket)",
        "description": PushFold3.description,
        "default_iterations": 5000,
        "max_iterations": 100000,
    },
    "tournament_push_fold": {
        "id": "tournament_push_fold",
        "name": "MTT Push/Fold (with antes)",
        "description": TournamentPushFold.description,
        "default_iterations": 5000,
        "max_iterations": 100000,
        "default_ante_per_player": 500.0,
        "default_num_players": 9,
        "default_bb": 1000.0,
        "default_stack_bb": 10.0,
    },
}


def _game_factory(
    game_id: str,
    *,
    ante_per_player: float = 500.0,
    num_players: int = 9,
    bb: float = 1000.0,
    stack_bb: float = 10.0,
) -> Subgame:
    if game_id == "kuhn":
        return KuhnPoker()
    if game_id == "leduc":
        return LeducHoldem()
    if game_id == "push_fold":
        return PushFold3()
    if game_id == "tournament_push_fold":
        return TournamentPushFold(
            ante_per_player=ante_per_player,
            num_players=num_players,
            bb=bb,
            stack_bb=stack_bb,
        )
    raise ValueError(f"unknown game: {game_id}")


def solve_kuhn(iterations: int = 10000, seed: int = 42) -> Dict[str, Any]:
    return run_cfr_plus(KuhnPoker(), iterations=iterations, seed=seed)


def solve_leduc(iterations: int = 5000, seed: int = 42) -> Dict[str, Any]:
    return run_cfr_plus(LeducHoldem(), iterations=iterations, seed=seed)


def solve_push_fold(iterations: int = 5000, seed: int = 42) -> Dict[str, Any]:
    return run_cfr_plus(PushFold3(), iterations=iterations, seed=seed)


def solve_tournament_push_fold(
    iterations: int = 5000,
    seed: int = 42,
    *,
    ante_per_player: float = 500.0,
    num_players: int = 9,
    bb: float = 1000.0,
    stack_bb: float = 10.0,
) -> Dict[str, Any]:
    game = TournamentPushFold(
        ante_per_player=ante_per_player,
        num_players=num_players,
        bb=bb,
        stack_bb=stack_bb,
    )
    result = run_cfr_plus(game, iterations=iterations, seed=seed)
    result["config"] = game.config_summary()
    return result


def run_cfr_for_game(
    game_id: str,
    iterations: int = 5000,
    seed: int = 42,
    *,
    ante_per_player: float = 500.0,
    num_players: int = 9,
    bb: float = 1000.0,
    stack_bb: float = 10.0,
) -> Dict[str, Any]:
    meta = SOLVABLE_GAMES.get(game_id)
    if not meta:
        raise ValueError(f"unsupported game: {game_id}")
    cap = int(meta["max_iterations"])
    iters = max(100, min(iterations, cap))
    game = _game_factory(
        game_id,
        ante_per_player=ante_per_player,
        num_players=num_players,
        bb=bb,
        stack_bb=stack_bb,
    )
    result = run_cfr_plus(game, iterations=iters, seed=seed)
    result["game_id"] = game_id
    result["game_name"] = meta["name"]
    if isinstance(game, TournamentPushFold):
        result["config"] = game.config_summary()
    result["note"] = (
        "Educational CFR+ output for a toy subgame — not a full NLHE equilibrium. "
        "Strategies are averaged over all deals; increase iterations for tighter convergence."
    )
    return result
