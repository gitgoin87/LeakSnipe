"""
Poker equity engine for LeakSnipe.

Provides a correct 7-card Texas Hold'em hand evaluator, Monte Carlo equity
(hand vs hand, hand vs range, range vs range on any board), standard range
notation parsing, solver-approximation positional ranges (the "Nash reference"
ranges), and an Omaha Hi/Lo (8-or-better) evaluator with separate high / low /
scoop equity.

Everything here is self-contained pure Python (no third-party dependency) so it
runs identically on the user's Windows box and in CI, and is unit-tested against
known equities (see tests/test_equity.py).

The positional ranges below are SOLVER APPROXIMATIONS hand-picked to mirror
typical GTO/Nash open / defend / 3-bet frequencies. They are reference ranges,
not the output of a live solver.
"""

from __future__ import annotations

import random
import re
from collections import Counter
from itertools import combinations
from typing import Dict, List, Optional, Sequence, Tuple, Union

# ── Card model ────────────────────────────────────────────────────────────────
RANK_CHARS = "23456789TJQKA"
RANK_VALUES: Dict[str, int] = {ch: i for i, ch in enumerate(RANK_CHARS, start=2)}
VAL_TO_CHAR: Dict[int, str] = {v: k for k, v in RANK_VALUES.items()}
SUITS = "cdhs"
SUIT_INDEX: Dict[str, int] = {s: i for i, s in enumerate(SUITS)}

Card = Tuple[int, int]  # (rank 2..14, suit 0..3)
Combo = Tuple[Card, Card]

FULL_DECK: List[Card] = [(r, s) for r in range(2, 15) for s in range(4)]

_CARD_RE = re.compile(r"(10|[2-9TJQKAtjqka])\s*([cdhsCDHS])")


def parse_card(token: str) -> Card:
    """Parse a single card like 'Ah', 'Td', '10s'."""
    t = token.strip()
    if t[:2] == "10":
        rank_ch, suit_ch = "T", t[2:3]
    else:
        rank_ch, suit_ch = t[0:1], t[1:2]
    rank_ch = rank_ch.upper()
    suit_ch = suit_ch.lower()
    if rank_ch not in RANK_VALUES or suit_ch not in SUIT_INDEX:
        raise ValueError(f"invalid card: {token!r}")
    return (RANK_VALUES[rank_ch], SUIT_INDEX[suit_ch])


def parse_cards(value: Union[str, Sequence]) -> List[Card]:
    """Parse a card collection from a string ('AhKd2c') or a sequence.

    Accepts already-parsed (rank, suit) tuples, single card strings, or one
    blob string like 'Kh 2d' / 'Kh2d'.
    """
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        out: List[Card] = []
        for item in value:
            if isinstance(item, tuple) and len(item) == 2 and isinstance(item[0], int):
                out.append((int(item[0]), int(item[1])))
            else:
                out.extend(parse_cards(str(item)))
        return out
    return [parse_card(m.group(1) + m.group(2)) for m in _CARD_RE.finditer(str(value))]


def card_str(card: Card) -> str:
    return f"{VAL_TO_CHAR[card[0]]}{SUITS[card[1]]}"


def cards_str(cards: Sequence[Card]) -> str:
    return " ".join(card_str(c) for c in cards)


def describe_hole_cards(hero_cards: Union[str, Sequence]) -> Optional[Dict]:
    """Canonical facts for a two-card Hold'em hand.

    Returns exact cards (e.g. 'Js 9h'), notation (J9o / J9s / JJ), and suit
    status so the AI coach cannot confuse offsuit with suited.
    """
    cards = parse_cards(hero_cards)
    if len(cards) != 2:
        return None
    (r1, s1), (r2, s2) = cards
    hi, lo = max(r1, r2), min(r1, r2)
    hi_c, lo_c = VAL_TO_CHAR[hi], VAL_TO_CHAR[lo]
    pair = hi == lo
    suited = s1 == s2
    gap = hi - lo
    if pair:
        notation = f"{hi_c}{lo_c}"
        kind = "pair"
    elif suited:
        notation = f"{hi_c}{lo_c}s"
        kind = "suited"
    else:
        notation = f"{hi_c}{lo_c}o"
        kind = "offsuit"
    return {
        "exact_cards": cards_str(cards),
        "notation": notation,
        "pair": pair,
        "suited": suited,
        "offsuit": not pair and not suited,
        "connector": (gap == 1) and not pair,
        "one_gapper": (gap == 2) and not pair,
        "kind": kind,
    }


# ── 5-card evaluation ─────────────────────────────────────────────────────────
# A hand is scored as a tuple (category, tiebreakers...). Bigger tuple == better.
# Categories: 8 straight flush, 7 quads, 6 full house, 5 flush, 4 straight,
# 3 trips, 2 two pair, 1 pair, 0 high card.
HandScore = Tuple[int, Tuple[int, ...]]


def _straight_high(distinct_desc: List[int]) -> Optional[int]:
    """Return the straight's high card for 5 distinct ranks, else None."""
    if len(distinct_desc) != 5:
        return None
    if distinct_desc[0] - distinct_desc[4] == 4:
        return distinct_desc[0]
    if distinct_desc == [14, 5, 4, 3, 2]:  # wheel (A-5)
        return 5
    return None


def eval5(cards: Sequence[Card]) -> HandScore:
    """Evaluate exactly 5 cards into a comparable score."""
    ranks = sorted((c[0] for c in cards), reverse=True)
    is_flush = len({c[1] for c in cards}) == 1
    distinct = sorted(set(ranks), reverse=True)
    straight = _straight_high(distinct)

    counts = Counter(ranks)
    # Order by (count, rank) descending so pairs/trips lead the tiebreakers.
    by_count = sorted(counts.items(), key=lambda kv: (kv[1], kv[0]), reverse=True)
    shape = tuple(cnt for _, cnt in by_count)
    rank_order = tuple(r for r, _ in by_count)

    if straight and is_flush:
        return (8, (straight,))
    if shape[0] == 4:
        return (7, rank_order)
    if shape[0] == 3 and len(shape) > 1 and shape[1] == 2:
        return (6, rank_order)
    if is_flush:
        return (5, tuple(ranks))
    if straight:
        return (4, (straight,))
    if shape[0] == 3:
        return (3, rank_order)
    if shape[0] == 2 and len(shape) > 1 and shape[1] == 2:
        return (2, rank_order)
    if shape[0] == 2:
        return (1, rank_order)
    return (0, tuple(ranks))


def evaluate_seven(cards: Sequence[Card]) -> HandScore:
    """Best 5-card score out of 5, 6, or 7 cards (Texas Hold'em)."""
    if len(cards) == 5:
        return eval5(cards)
    best: Optional[HandScore] = None
    for combo in combinations(cards, 5):
        score = eval5(combo)
        if best is None or score > best:
            best = score
    assert best is not None
    return best


HAND_CATEGORY_NAMES = {
    8: "Straight Flush",
    7: "Four of a Kind",
    6: "Full House",
    5: "Flush",
    4: "Straight",
    3: "Three of a Kind",
    2: "Two Pair",
    1: "Pair",
    0: "High Card",
}

_RANK_PLURAL: Dict[int, str] = {
    14: "Aces",
    13: "Kings",
    12: "Queens",
    11: "Jacks",
    10: "Tens",
    9: "Nines",
    8: "Eights",
    7: "Sevens",
    6: "Sixes",
    5: "Fives",
    4: "Fours",
    3: "Threes",
    2: "Deuces",
}


def _rank_plural(rank: int) -> str:
    return _RANK_PLURAL.get(rank, VAL_TO_CHAR[rank] + "s")


def _rank_singular(rank: int) -> str:
    for name, val in (
        ("Ace", 14), ("King", 13), ("Queen", 12), ("Jack", 11), ("Ten", 10),
        ("Nine", 9), ("Eight", 8), ("Seven", 7), ("Six", 6), ("Five", 5),
        ("Four", 4), ("Three", 3), ("Deuce", 2),
    ):
        if val == rank:
            return name
    return VAL_TO_CHAR[rank]


def _set_position_label(trips_rank: int, board_ranks: Sequence[int]) -> str:
    """Top / middle / bottom set relative to unique board ranks."""
    uniq = sorted(set(board_ranks), reverse=True)
    if not uniq:
        return "set"
    if trips_rank >= uniq[0]:
        return "top set"
    if trips_rank <= uniq[-1]:
        return "bottom set"
    return "middle set"


def _pair_position_label(pair_rank: int, board_ranks: Sequence[int]) -> str:
    uniq = sorted(set(board_ranks), reverse=True)
    if not uniq:
        return "Pair"
    if pair_rank == uniq[0]:
        return "Top pair"
    if pair_rank == uniq[-1]:
        return "Bottom pair"
    return "Second pair"


def describe_made_hand(
    hero_cards: Union[str, Sequence],
    board_cards: Union[str, Sequence, None],
) -> Optional[Dict[str, Union[str, int, List[str]]]]:
    """Human-readable made-hand label at a decision point (hole + board so far).

    Returns None preflop for unpaired hands; pocket pairs get a preflop label.
    """
    hole = parse_cards(hero_cards)
    if len(hole) != 2:
        return None
    board = parse_cards(board_cards)
    hr1, hr2 = hole[0][0], hole[1][0]
    pocket_pair = hr1 == hr2

    if len(board) < 3:
        if pocket_pair:
            notation = f"{VAL_TO_CHAR[hr1]}{VAL_TO_CHAR[hr2]}"
            label = f"Pocket {_rank_plural(hr1)} ({notation})"
            return {
                "label": label,
                "short": f"Pocket {notation}",
                "category": -1,
                "category_name": "Pocket Pair",
                "forbidden_terms": ["top pair", "one pair", "set of", "full house"],
            }
        return None

    score = evaluate_seven(hole + board)
    cat, tb = score
    board_ranks = [c[0] for c in board]
    forbidden: List[str] = []

    if cat >= 6:
        forbidden = [
            "top pair", "one pair", "pair of", "top set", "middle set",
            "bottom set", "set of", "trips", "two pair",
        ]
    elif cat >= 3:
        forbidden = ["top pair", "one pair", "pair of"]
    elif cat >= 2:
        forbidden = ["top pair"]
    elif cat == 1 and pocket_pair:
        forbidden = []

    if cat == 8:
        label = f"Straight flush, {_rank_singular(tb[0])}-high"
    elif cat == 7:
        label = f"Four of a kind, {_rank_plural(tb[0])}"
    elif cat == 6:
        trips_rank, pair_rank = tb[0], tb[1]
        label = f"Full house, {_rank_plural(trips_rank)} full of {_rank_plural(pair_rank)}"
    elif cat == 5:
        label = f"Flush, {_rank_singular(tb[0])}-high"
    elif cat == 4:
        label = f"Straight, {_rank_singular(tb[0])}-high"
    elif cat == 3:
        trips_rank = tb[0]
        board_matches = sum(1 for r in board_ranks if r == trips_rank)
        if pocket_pair and hr1 == trips_rank and board_matches >= 1:
            pos = _set_position_label(trips_rank, board_ranks)
            label = f"Set of {_rank_plural(trips_rank)} ({pos})"
            if pos != "top set":
                forbidden.append("top set")
        else:
            label = f"Trips, {_rank_plural(trips_rank)}"
    elif cat == 2:
        hi_pair, lo_pair = tb[0], tb[1]
        label = f"Two pair, {_rank_plural(hi_pair)} and {_rank_plural(lo_pair)}"
    elif cat == 1:
        pair_rank = tb[0]
        kicker = tb[1] if len(tb) > 1 else 0
        if pocket_pair and pair_rank == hr1:
            label = f"Overpair, {_rank_plural(pair_rank)}"
        elif pair_rank in (hr1, hr2):
            pos = _pair_position_label(pair_rank, board_ranks)
            kicker_txt = f" with {_rank_singular(kicker)} kicker" if kicker else ""
            label = f"{pos}, {_rank_plural(pair_rank)}{kicker_txt}"
        else:
            label = f"Pair of {_rank_plural(pair_rank)} (board pair)"
    else:
        high = max(hr1, hr2)
        label = f"High card, {_rank_singular(high)}"

    return {
        "label": label,
        "short": label.split(",")[0],
        "category": cat,
        "category_name": HAND_CATEGORY_NAMES.get(cat, "Unknown"),
        "forbidden_terms": forbidden,
    }


def spot_equity_pct(
    hero_cards: Union[str, Sequence],
    board_cards: Union[str, Sequence, None],
    *,
    iters: int = 1200,
    seed: Optional[int] = 7,
) -> Optional[float]:
    """Monte Carlo equity at a decision point (hero vs continuing CO range)."""
    hole = parse_cards(hero_cards)
    if len(hole) != 2:
        return None
    board = parse_cards(board_cards)
    try:
        if len(board) >= 3:
            eq = equity_hand_vs_range(
                hero_cards, RFI_RANGES["CO"], board=board, iters=iters, seed=seed
            )["hero_equity"]
        else:
            eq = preflop_equity_grounding(hero_cards, iters=iters, seed=seed)
            if not eq:
                return None
            eq = eq["rows"][1]["equity"]  # vs CO open
        return round(float(eq), 1)
    except Exception:
        return None


# ── Omaha Hi/Lo (8-or-better) ─────────────────────────────────────────────────
_LOW_VALUE = {14: 1, 2: 2, 3: 3, 4: 4, 5: 5, 6: 6, 7: 7, 8: 8}


def omaha_high(hole: Sequence[Card], board: Sequence[Card]) -> HandScore:
    """Best high hand using EXACTLY 2 hole + 3 board cards."""
    best: Optional[HandScore] = None
    for h2 in combinations(hole, 2):
        for b3 in combinations(board, 3):
            score = eval5(h2 + b3)
            if best is None or score > best:
                best = score
    assert best is not None
    return best


def _low_value_5(cards5: Sequence[Card]) -> Optional[Tuple[int, ...]]:
    """Low score for 5 cards (smaller is better), or None if no qualifying low."""
    lows = []
    for rank, _ in cards5:
        lv = _LOW_VALUE.get(rank)
        if lv is None:
            return None
        lows.append(lv)
    if len(set(lows)) != 5:  # low needs 5 distinct ranks
        return None
    return tuple(sorted(lows, reverse=True))


_QUALIFYING_LOW_CHARS = ("A", "2", "3", "4", "5", "6", "7", "8")
_LOW_CHAR_ORDER = {ch: i for i, ch in enumerate(_QUALIFYING_LOW_CHARS)}


def low_hand_label(ranks: Sequence[str]) -> str:
    """Canonical label for a 5-rank 8-or-better low (e.g. 'A2345')."""
    return "".join(sorted(ranks, key=lambda r: _LOW_CHAR_ORDER[r]))


def low_score_from_ranks(ranks: Sequence[str]) -> Tuple[int, ...]:
    """Comparable low score for five distinct ranks A-8 (smaller is better)."""
    cards = parse_cards("".join(f"{r}{s}" for r, s in zip(ranks, "cdhsc")))
    score = _low_value_5(cards)
    if score is None:
        raise ValueError(f"not a qualifying low: {''.join(ranks)!r}")
    return score


def enumerate_qualifying_lows() -> List[Tuple[str, Tuple[int, ...]]]:
    """All C(8,5)=56 distinct 8-or-better lows, best to worst (label, score)."""
    hands: List[Tuple[str, Tuple[int, ...]]] = []
    for combo in combinations(_QUALIFYING_LOW_CHARS, 5):
        label = low_hand_label(combo)
        hands.append((label, low_score_from_ranks(combo)))
    hands.sort(key=lambda item: item[1])
    return hands


def omaha_low(hole: Sequence[Card], board: Sequence[Card]) -> Optional[Tuple[int, ...]]:
    """Best (lowest) qualifying 8-or-better low using exactly 2 hole + 3 board.

    Returns a comparable tuple where SMALLER is better, or None if the player
    cannot make an 8-or-better low.
    """
    best: Optional[Tuple[int, ...]] = None
    for h2 in combinations(hole, 2):
        for b3 in combinations(board, 3):
            lv = _low_value_5(h2 + b3)
            if lv is None:
                continue
            if best is None or lv < best:
                best = lv
    return best


# ── Range notation ────────────────────────────────────────────────────────────
def _pair_combos(rank: int) -> List[Combo]:
    return [tuple(sorted([(rank, s1), (rank, s2)])) for s1, s2 in combinations(range(4), 2)]


def _suited_combos(hi: int, lo: int) -> List[Combo]:
    return [tuple(sorted([(hi, s), (lo, s)])) for s in range(4)]


def _offsuit_combos(hi: int, lo: int) -> List[Combo]:
    return [
        tuple(sorted([(hi, s1), (lo, s2)]))
        for s1 in range(4)
        for s2 in range(4)
        if s1 != s2
    ]


def _parse_range_token(token: str) -> List[Combo]:
    tok = token.strip()
    if not tok:
        return []
    plus = tok.endswith("+")
    core = tok[:-1] if plus else tok
    suited: Optional[bool] = None
    if core and core[-1] in "sS":
        suited, core = True, core[:-1]
    elif core and core[-1] in "oO":
        suited, core = False, core[:-1]
    if len(core) != 2:
        raise ValueError(f"invalid range token: {token!r}")
    r1 = RANK_VALUES.get(core[0].upper())
    r2 = RANK_VALUES.get(core[1].upper())
    if r1 is None or r2 is None:
        raise ValueError(f"invalid range token: {token!r}")

    combos: List[Combo] = []
    if r1 == r2:  # pair, e.g. 22 / TT / 88+
        ranks = range(r1, 15) if plus else [r1]
        for r in ranks:
            combos += _pair_combos(r)
    else:
        hi, lo = max(r1, r2), min(r1, r2)
        kickers = range(lo, hi) if plus else [lo]  # e.g. A2s+ -> 2..K with A high
        for k in kickers:
            if suited is None:
                combos += _suited_combos(hi, k) + _offsuit_combos(hi, k)
            elif suited:
                combos += _suited_combos(hi, k)
            else:
                combos += _offsuit_combos(hi, k)
    return combos


def _hand_class_score(hi: int, lo: int, pair: bool, suited: bool) -> float:
    """Approximate preflop strength used only to order 'top X%' ranges."""
    if pair:
        return 3 * hi + 30  # pairs sit above all non-pairs, ordered by rank
    base = 2 * hi + lo
    if suited:
        base += 4
    gap = hi - lo
    if gap == 1:
        base += 3
    elif gap == 2:
        base += 2
    elif gap == 3:
        base += 1
    if hi == 14:
        base += 2
    return base


def _ranked_hand_classes() -> List[Tuple[float, List[Combo]]]:
    classes: List[Tuple[float, List[Combo]]] = []
    for hi in range(2, 15):
        for lo in range(2, hi + 1):
            if hi == lo:
                classes.append((_hand_class_score(hi, lo, True, False), _pair_combos(hi)))
            else:
                classes.append((_hand_class_score(hi, lo, False, True), _suited_combos(hi, lo)))
                classes.append((_hand_class_score(hi, lo, False, False), _offsuit_combos(hi, lo)))
    classes.sort(key=lambda c: c[0], reverse=True)
    return classes


def _percent_range(pct: float) -> List[Combo]:
    """Top X% of all starting hands by the approximate ordering above."""
    target = max(0.0, min(100.0, pct)) / 100.0 * 1326
    combos: List[Combo] = []
    for _, group in _ranked_hand_classes():
        combos.extend(group)
        if len(combos) >= target:
            break
    return combos


_PCT_RE = re.compile(r"(?:top\s*)?(\d+(?:\.\d+)?)\s*%", re.IGNORECASE)


def parse_range(spec: Union[str, Sequence]) -> List[Combo]:
    """Parse a range into a deduplicated list of concrete 2-card combos.

    Supports standard notation ('22+, A2s+, KTs+, QJs, AKo'), explicit combos,
    and percentage ranges ('top 15%' / '15%').
    """
    if isinstance(spec, (list, tuple, set)):
        combos: List[Combo] = []
        for item in spec:
            if (
                isinstance(item, tuple)
                and len(item) == 2
                and isinstance(item[0], tuple)
            ):
                combos.append(tuple(sorted(item)))  # already a combo
            else:
                combos.extend(parse_range(str(item)))
        return _dedupe_combos(combos)

    text = str(spec).strip()
    if not text:
        return []
    pct = _PCT_RE.fullmatch(text)
    if pct:
        return _dedupe_combos(_percent_range(float(pct.group(1))))

    combos = []
    for token in re.split(r"[,\s]+", text):
        if token:
            combos.extend(_parse_range_token(token))
    return _dedupe_combos(combos)


def _dedupe_combos(combos: List[Combo]) -> List[Combo]:
    seen = set()
    out: List[Combo] = []
    for c in combos:
        key = tuple(sorted(c))
        if key not in seen:
            seen.add(key)
            out.append(key)
    return out


def range_frequency(spec: Union[str, Sequence]) -> float:
    """Percentage of all 1326 starting-hand combos covered by the range."""
    return round(len(parse_range(spec)) / 1326.0 * 100.0, 1)


# ── Solver-approximation positional ranges (Nash reference) ───────────────────
# These mirror typical GTO open / defend / 3-bet frequencies. Approximations,
# not a live solver. Used as the villain "Nash equilibrium" reference ranges.
RFI_RANGES: Dict[str, str] = {
    "UTG": "22+, ATs+, KTs+, QTs+, JTs, T9s, AJo+, KQo",
    "UTG+1": "22+, ATs+, KTs+, QTs+, JTs, T9s, AJo+, KQo",
    "MP": "22+, A9s+, KTs+, QTs+, J9s+, T9s, 98s, AJo+, KQo, KJo",
    "HJ": "22+, A7s+, A5s, K9s+, Q9s+, J9s+, T8s+, 98s, 87s, ATo+, KJo+, QJo",
    "CO": "22+, A2s+, K8s+, Q9s+, J9s+, T8s+, 98s, 87s, 76s, 65s, ATo+, KTo+, QTo+, JTo",
    "BTN": (
        "22+, A2s+, K4s+, Q6s+, J7s+, T7s+, 96s+, 85s+, 75s+, 64s+, 54s, "
        "A2o+, K8o+, Q8o+, J8o+, T8o+, 98o, 87o"
    ),
    "SB": (
        "22+, A2s+, K6s+, Q8s+, J8s+, T8s+, 97s+, 86s+, 76s, 65s, "
        "A2o+, K9o+, Q9o+, JTo"
    ),
}

# Big blind defend (call) range vs a late-position steal — wide.
BB_DEFEND_VS_STEAL = (
    "22+, A2s+, K2s+, Q5s+, J7s+, T7s+, 96s+, 85s+, 75s+, 64s+, 54s, "
    "A2o+, K7o+, Q8o+, J8o+, T8o+, 98o, 87o"
)

# Merged 3-bet ranges (approximation) by the position you're 3-betting from.
THREE_BET_RANGES: Dict[str, str] = {
    "default": "TT+, AQs+, AKo, A5s, A4s, KJs+",
    "BTN": "99+, ATs+, KJs+, QJs, AQo+, A5s, A4s",
    "SB": "99+, AJs+, KQs, AQo+, A5s",
    "BB": "TT+, AJs+, KQs, AQo+, A5s, A4s",
}

# What "facing a steal" means by villain seat (their RFI).
STEAL_POSITIONS = {"CO", "BTN", "SB"}


def position_range(position: str, action: str = "open") -> str:
    """Return the reference range string for a position + action context.

    action: 'open' / 'steal' / 'rfi' -> raise-first range; '3bet' -> 3-bet
    range; 'defend' -> BB call-vs-steal range.
    """
    pos = (position or "").strip().upper()
    act = (action or "open").strip().lower()
    if act in ("3bet", "3-bet", "threebet"):
        return THREE_BET_RANGES.get(pos, THREE_BET_RANGES["default"])
    if act == "defend":
        return BB_DEFEND_VS_STEAL
    return RFI_RANGES.get(pos, RFI_RANGES["CO"])


# ── Monte Carlo (Texas Hold'em) ───────────────────────────────────────────────
def _normalize_player(spec: Union[str, Sequence, Dict]) -> Dict:
    """Normalize a player spec into {'fixed': cards} or {'range': combos}."""
    if isinstance(spec, dict):
        if spec.get("cards"):
            return {"fixed": parse_cards(spec["cards"])}
        if spec.get("range") is not None:
            return {"range": parse_range(spec["range"])}
        raise ValueError("player dict needs 'cards' or 'range'")
    if isinstance(spec, str):
        cards = parse_cards(spec)
        if len(cards) == 2:
            return {"fixed": cards}
        return {"range": parse_range(spec)}
    if isinstance(spec, (list, tuple)):
        # A concrete 2-card hand given as parsed (rank, suit) tuples.
        if len(spec) == 2 and all(
            isinstance(c, tuple) and len(c) == 2 and isinstance(c[0], int) for c in spec
        ):
            return {"fixed": [(int(c[0]), int(c[1])) for c in spec]}
        return {"range": parse_range(spec)}
    return {"range": parse_range(spec)}


def _sample_combo(
    combos: List[Combo], used: set, rng: random.Random, attempts: int = 16
) -> Optional[Combo]:
    for _ in range(attempts):
        c = rng.choice(combos)
        if c[0] not in used and c[1] not in used:
            return c
    avail = [c for c in combos if c[0] not in used and c[1] not in used]
    return rng.choice(avail) if avail else None


def monte_carlo(
    players: Sequence[Union[str, Sequence, Dict]],
    board: Union[str, Sequence, None] = None,
    dead: Union[str, Sequence, None] = None,
    iters: int = 10000,
    seed: Optional[int] = None,
) -> Dict:
    """Monte Carlo equity for >=2 Hold'em players.

    Each player is a fixed 2-card hand or a range (string/list). Returns
    per-player equity plus hero (index 0) win/tie split. Card removal and dead
    cards are respected.
    """
    if len(players) < 2:
        raise ValueError("need at least 2 players")
    iters = max(1, min(int(iters), 500000))
    rng = random.Random(seed)

    board_cards = parse_cards(board)
    if len(board_cards) > 5:
        raise ValueError("board cannot exceed 5 cards")
    dead_cards = parse_cards(dead)
    specs = [_normalize_player(p) for p in players]

    fixed_used = set(board_cards) | set(dead_cards)
    for sp in specs:
        if "fixed" in sp:
            fixed_used |= set(sp["fixed"])

    n = len(specs)
    equity = [0.0] * n
    wins = [0.0] * n
    ties = [0.0] * n
    need = 5 - len(board_cards)
    valid = 0

    for _ in range(iters):
        used = set(fixed_used)
        hands: List[List[Card]] = []
        ok = True
        for sp in specs:
            if "fixed" in sp:
                hands.append(sp["fixed"])
            else:
                combo = _sample_combo(sp["range"], used, rng)
                if combo is None:
                    ok = False
                    break
                hands.append(list(combo))
                used.add(combo[0])
                used.add(combo[1])
        if not ok:
            continue

        deck = [c for c in FULL_DECK if c not in used]
        runout = rng.sample(deck, need) if need else []
        full_board = board_cards + runout

        scores = [evaluate_seven(h + full_board) for h in hands]
        best = max(scores)
        winners = [i for i, s in enumerate(scores) if s == best]
        share = 1.0 / len(winners)
        for i in winners:
            equity[i] += share
        if len(winners) == 1:
            wins[winners[0]] += 1
        else:
            for i in winners:
                ties[i] += 1
        valid += 1

    valid = valid or 1
    return {
        "iterations": valid,
        "equity": [round(e / valid * 100, 2) for e in equity],
        "win": [round(w / valid * 100, 2) for w in wins],
        "tie": [round(t / valid * 100, 2) for t in ties],
        "hero_equity": round(equity[0] / valid * 100, 2),
        "hero_win": round(wins[0] / valid * 100, 2),
        "hero_tie": round(ties[0] / valid * 100, 2),
    }


def equity_hand_vs_hand(
    hero: Union[str, Sequence],
    villain: Union[str, Sequence],
    board: Union[str, Sequence, None] = None,
    iters: int = 20000,
    seed: Optional[int] = None,
) -> Dict:
    return monte_carlo([hero, villain], board=board, iters=iters, seed=seed)


def equity_hand_vs_range(
    hero: Union[str, Sequence],
    villain_range: Union[str, Sequence],
    board: Union[str, Sequence, None] = None,
    iters: int = 10000,
    seed: Optional[int] = None,
) -> Dict:
    return monte_carlo([hero, {"range": villain_range}], board=board, iters=iters, seed=seed)


def equity_vs_position_range(
    hero_cards: Union[str, Sequence],
    villain_position: str,
    action_context: str = "open",
    board: Union[str, Sequence, None] = None,
    iters: int = 8000,
    seed: Optional[int] = None,
) -> Dict:
    """Hero equity vs a position's reference (Nash-approx) range on a board."""
    rng_spec = position_range(villain_position, action_context)
    result = equity_hand_vs_range(hero_cards, rng_spec, board=board, iters=iters, seed=seed)
    result["villain_position"] = (villain_position or "").upper()
    result["villain_action"] = action_context
    result["villain_range"] = rng_spec
    result["villain_range_pct"] = range_frequency(rng_spec)
    return result


# ── Monte Carlo (Omaha Hi/Lo 8-or-better) ─────────────────────────────────────
def monte_carlo_omaha8(
    hero: Union[str, Sequence],
    opponents: Union[int, Sequence] = 1,
    board: Union[str, Sequence, None] = None,
    dead: Union[str, Sequence, None] = None,
    iters: int = 8000,
    seed: Optional[int] = None,
) -> Dict:
    """Omaha-8 equity returning separate high, low, scoop and overall equity.

    hero: 4 hole cards. opponents: an int (N random opponents) or a list of
    opponent hands (each 4 cards). Pot is split high/low; if nobody qualifies
    for the 8-or-better low, the high hand scoops the low half.
    """
    iters = max(1, min(int(iters), 500000))
    rng = random.Random(seed)

    hero_cards = parse_cards(hero)
    if len(hero_cards) != 4:
        raise ValueError("Omaha hero needs exactly 4 hole cards")
    board_cards = parse_cards(board)
    dead_cards = parse_cards(dead)

    fixed_opps: List[List[Card]] = []
    n_random = 0
    if isinstance(opponents, int):
        n_random = opponents
    else:
        for opp in opponents:
            oc = parse_cards(opp)
            if len(oc) != 4:
                raise ValueError("each Omaha opponent needs 4 hole cards")
            fixed_opps.append(oc)
    n_players = 1 + len(fixed_opps) + n_random
    if n_players < 2:
        raise ValueError("need at least 1 opponent")

    base_used = set(hero_cards) | set(board_cards) | set(dead_cards)
    for oc in fixed_opps:
        base_used |= set(oc)
    need = 5 - len(board_cards)

    high_eq = 0.0
    low_eq = 0.0
    overall = 0.0
    scoops = 0
    low_possible = 0
    valid = 0

    for _ in range(iters):
        used = set(base_used)
        deck = [c for c in FULL_DECK if c not in used]
        rng.shuffle(deck)
        di = 0
        all_hands = [hero_cards] + fixed_opps
        for _o in range(n_random):
            all_hands.append(deck[di : di + 4])
            di += 4
        runout = deck[di : di + need]
        full_board = board_cards + runout

        high_scores = [omaha_high(h, full_board) for h in all_hands]
        best_high = max(high_scores)
        high_winners = [i for i, s in enumerate(high_scores) if s == best_high]

        low_scores = [omaha_low(h, full_board) for h in all_hands]
        qualed = [i for i, lw in enumerate(low_scores) if lw is not None]
        shares = [0.0] * len(all_hands)
        if qualed:
            low_possible += 1
            best_low = min(low_scores[i] for i in qualed)
            low_winners = [i for i in qualed if low_scores[i] == best_low]
            for i in high_winners:
                shares[i] += 0.5 / len(high_winners)
            for i in low_winners:
                shares[i] += 0.5 / len(low_winners)
            if 0 in high_winners:
                high_eq += 1.0 / len(high_winners)
            if 0 in low_winners:
                low_eq += 1.0 / len(low_winners)
        else:
            for i in high_winners:
                shares[i] += 1.0 / len(high_winners)
            if 0 in high_winners:
                high_eq += 1.0 / len(high_winners)

        overall += shares[0]
        if shares[0] >= 0.999:  # hero takes the entire pot
            scoops += 1
        valid += 1

    valid = valid or 1
    return {
        "iterations": valid,
        "players": n_players,
        "high_equity": round(high_eq / valid * 100, 2),
        "low_equity": round(low_eq / valid * 100, 2),
        "scoop_equity": round(scoops / valid * 100, 2),
        "overall_equity": round(overall / valid * 100, 2),
        "low_possible_pct": round(low_possible / valid * 100, 2),
    }


# ── Seven Card Stud (high) and Stud Hi/Lo (8-or-better) ───────────────────────
# Stud has no shared board: each player makes the best 5-card hand from their own
# (up to) 7 cards. Opponents' visible upcards and folded cards are KNOWN, so both
# variants accept `dead` cards that are removed from the simulated deck. Unlike
# Omaha, the stud low uses ANY 5 of the 7 cards (no 2-from-hand restriction).
def stud_high(cards: Sequence[Card]) -> HandScore:
    """Best 5-card high hand from up to 7 stud cards."""
    return evaluate_seven(cards)


def stud_low(cards: Sequence[Card]) -> Optional[Tuple[int, ...]]:
    """Best (lowest) 8-or-better low using ANY 5 of the cards, or None.

    A plays low; needs 5 distinct ranks all 8-or-lower. Returns a comparable
    tuple where SMALLER is better.
    """
    lows = sorted({_LOW_VALUE[r] for r, _ in cards if r in _LOW_VALUE})
    if len(lows) < 5:
        return None
    return tuple(sorted(lows[:5], reverse=True))


def _normalize_stud_player(spec: Union[str, Sequence, Dict]) -> Dict:
    """Normalize a stud player into {'fixed': cards} (0-7) or {'range': combos}."""
    if isinstance(spec, dict):
        if spec.get("cards") is not None:
            return {"fixed": parse_cards(spec["cards"])}
        if spec.get("range") is not None:
            return {"range": parse_range(spec["range"])}
        return {"fixed": []}
    if isinstance(spec, str):
        cards = parse_cards(spec)
        return {"fixed": cards} if cards else {"range": parse_range(spec)}
    if isinstance(spec, (list, tuple)):
        if spec and all(
            isinstance(c, tuple) and len(c) == 2 and isinstance(c[0], int) for c in spec
        ):
            return {"fixed": [(int(c[0]), int(c[1])) for c in spec]}
        return {"range": parse_range(spec)}
    return {"fixed": []}


def _stud_simulate(
    players: Sequence[Union[str, Sequence, Dict]],
    dead: Union[str, Sequence, None],
    iters: int,
    seed: Optional[int],
) -> Dict:
    """Core stud Monte Carlo — deals each player to 7 cards and scores hi + lo."""
    if len(players) < 2:
        raise ValueError("need at least 2 players")
    iters = max(1, min(int(iters), 500000))
    rng = random.Random(seed)
    dead_cards = parse_cards(dead)
    specs = [_normalize_stud_player(p) for p in players]
    n = len(specs)
    for sp in specs:
        if "fixed" in sp and len(sp["fixed"]) > 7:
            raise ValueError("a stud hand cannot exceed 7 cards")
    if 7 * n + len(set(dead_cards)) > 52:
        raise ValueError("too many players/dead cards for a 52-card deck")

    base_used = set(dead_cards)
    for sp in specs:
        if "fixed" in sp:
            base_used |= set(sp["fixed"])

    high_eq = [0.0] * n
    high_win = [0.0] * n
    high_tie = [0.0] * n
    low_eq = [0.0] * n
    overall = [0.0] * n
    scoops = [0.0] * n
    low_possible = 0
    valid = 0

    for _ in range(iters):
        used = set(base_used)
        hands: List[List[Card]] = []
        ok = True
        for sp in specs:
            if "fixed" in sp:
                hands.append(list(sp["fixed"]))
            else:
                combo = _sample_combo(sp["range"], used, rng)
                if combo is None:
                    ok = False
                    break
                hands.append([combo[0], combo[1]])
                used.add(combo[0])
                used.add(combo[1])
        if not ok:
            continue

        deck = [c for c in FULL_DECK if c not in used]
        rng.shuffle(deck)
        di = 0
        full: List[List[Card]] = []
        for h in hands:
            need = 7 - len(h)
            full.append(h + deck[di : di + need])
            di += need

        high_scores = [stud_high(h) for h in full]
        best_high = max(high_scores)
        hw = [i for i, s in enumerate(high_scores) if s == best_high]
        for i in hw:
            high_eq[i] += 1.0 / len(hw)
        if len(hw) == 1:
            high_win[hw[0]] += 1
        else:
            for i in hw:
                high_tie[i] += 1

        low_scores = [stud_low(h) for h in full]
        qualed = [i for i, lw in enumerate(low_scores) if lw is not None]
        shares = [0.0] * n
        if qualed:
            low_possible += 1
            best_low = min(low_scores[i] for i in qualed)
            low_winners = [i for i in qualed if low_scores[i] == best_low]
            for i in hw:
                shares[i] += 0.5 / len(hw)
            for i in low_winners:
                shares[i] += 0.5 / len(low_winners)
            for i in low_winners:
                low_eq[i] += 1.0 / len(low_winners)
        else:
            for i in hw:
                shares[i] += 1.0 / len(hw)
        for i in range(n):
            overall[i] += shares[i]
            if shares[i] >= 0.999:
                scoops[i] += 1
        valid += 1

    return {
        "iterations": valid or 1,
        "n": n,
        "high_eq": high_eq,
        "high_win": high_win,
        "high_tie": high_tie,
        "low_eq": low_eq,
        "overall": overall,
        "scoops": scoops,
        "low_possible": low_possible,
    }


def monte_carlo_stud(
    players: Sequence[Union[str, Sequence, Dict]],
    dead: Union[str, Sequence, None] = None,
    iters: int = 12000,
    seed: Optional[int] = None,
) -> Dict:
    """Seven Card Stud (high only) equity. Hero is index 0. Honors dead cards."""
    raw = _stud_simulate(players, dead, iters, seed)
    v = raw["iterations"]
    return {
        "iterations": v,
        "equity": [round(e / v * 100, 2) for e in raw["high_eq"]],
        "win": [round(w / v * 100, 2) for w in raw["high_win"]],
        "tie": [round(t / v * 100, 2) for t in raw["high_tie"]],
        "hero_equity": round(raw["high_eq"][0] / v * 100, 2),
        "hero_win": round(raw["high_win"][0] / v * 100, 2),
        "hero_tie": round(raw["high_tie"][0] / v * 100, 2),
    }


def equity_stud_hand_vs_hand(
    hero: Union[str, Sequence],
    villain: Union[str, Sequence],
    dead: Union[str, Sequence, None] = None,
    iters: int = 15000,
    seed: Optional[int] = None,
) -> Dict:
    return monte_carlo_stud([hero, villain], dead=dead, iters=iters, seed=seed)


def equity_stud_hand_vs_range(
    hero: Union[str, Sequence],
    villain_range: Union[str, Sequence],
    dead: Union[str, Sequence, None] = None,
    iters: int = 12000,
    seed: Optional[int] = None,
) -> Dict:
    return monte_carlo_stud([hero, {"range": villain_range}], dead=dead, iters=iters, seed=seed)


def monte_carlo_stud8(
    hero: Union[str, Sequence],
    opponents: Union[int, Sequence] = 1,
    dead: Union[str, Sequence, None] = None,
    iters: int = 10000,
    seed: Optional[int] = None,
) -> Dict:
    """Stud Hi/Lo (8-or-better) — separate high, low, scoop and overall equity.

    opponents: an int (N random opponents) or a list of opponent hands/ranges.
    Honors dead cards (known upcards / folded cards removed from the deck).
    """
    players: List[Union[str, Sequence, Dict]] = [hero]
    if isinstance(opponents, int):
        if opponents < 1:
            raise ValueError("need at least 1 opponent")
        players += [{"cards": []} for _ in range(opponents)]
    else:
        players += list(opponents)
    raw = _stud_simulate(players, dead, iters, seed)
    v = raw["iterations"]
    return {
        "iterations": v,
        "players": raw["n"],
        "high_equity": round(raw["high_eq"][0] / v * 100, 2),
        "low_equity": round(raw["low_eq"][0] / v * 100, 2),
        "scoop_equity": round(raw["scoops"][0] / v * 100, 2),
        "overall_equity": round(raw["overall"][0] / v * 100, 2),
        "low_possible_pct": round(raw["low_possible"] / v * 100, 2),
    }


# ── Coach grounding helpers ───────────────────────────────────────────────────
# Reference ranges (and their frequency) used to ground the AI coach so it never
# invents equity. "loose" ≈ a BTN steal, "medium" ≈ CO open, "tight" ≈ UTG open.
COACH_REFERENCE_RANGES: List[Tuple[str, str]] = [
    ("tight UTG open (~%s%%)", RFI_RANGES["UTG"]),
    ("CO open (~%s%%)", RFI_RANGES["CO"]),
    ("BTN steal (~%s%%)", RFI_RANGES["BTN"]),
]


def preflop_equity_grounding(
    hero_cards: Union[str, Sequence], iters: int = 2500, seed: Optional[int] = 7
) -> Optional[Dict]:
    """Compute hero's real preflop equity vs reference villain ranges.

    Returns a dict with computed equities and a ready-to-inject text block, or
    None if hero cards are unusable.
    """
    cards = parse_cards(hero_cards)
    if len(cards) != 2:
        return None
    rows = []
    allowed: List[float] = []
    for label_tmpl, rng_spec in COACH_REFERENCE_RANGES:
        freq = range_frequency(rng_spec)
        eq = equity_hand_vs_range(cards, rng_spec, iters=iters, seed=seed)["hero_equity"]
        allowed.append(eq)
        rows.append({"label": label_tmpl % freq, "equity": eq, "range_pct": freq})
    rnd = monte_carlo([cards, {"range": "100%"}], iters=iters, seed=seed)["hero_equity"]
    allowed.append(rnd)
    rows.append({"label": "a random hand (100%)", "equity": rnd, "range_pct": 100.0})
    return {"hero_cards": cards_str(cards), "rows": rows, "allowed_equities": allowed}


def coach_equity_block(
    hero_cards: Union[str, Sequence],
    board: Union[str, Sequence, None] = None,
    iters: int = 2500,
) -> Optional[Dict]:
    """Build a ground-truth equity block for the hand-analysis prompt.

    Returns {'text': str, 'allowed_equities': [..]} or None when not applicable.
    """
    grounding = preflop_equity_grounding(hero_cards, iters=iters)
    if not grounding:
        return None
    hc_facts = describe_hole_cards(hero_cards)
    notation = f" ({hc_facts['notation']})" if hc_facts else ""
    lines = [
        "GROUND-TRUTH EQUITIES (computed by this app's Monte Carlo engine — "
        "USE THESE NUMBERS, never invent your own equity %):",
        f"- Hero {grounding['hero_cards']}{notation} all-in preflop equity:",
    ]
    for row in grounding["rows"]:
        lines.append(f"    vs {row['label']}: {row['equity']:.0f}%")
    allowed = list(grounding["allowed_equities"])

    board_cards = parse_cards(board)
    if 3 <= len(board_cards) <= 5:
        eq = equity_hand_vs_range(
            hero_cards, RFI_RANGES["CO"], board=board_cards, iters=iters
        )["hero_equity"]
        allowed.append(eq)
        lines.append(
            f"- Hero on board [{cards_str(board_cards)}] vs a continuing CO range: {eq:.0f}%"
        )
    lines.append(
        "If you reference equity, quote the figures above. Do not state any other "
        "equity percentage as fact."
    )
    return {"text": "\n".join(lines), "allowed_equities": allowed}


if __name__ == "__main__":  # quick smoke test
    print("AA vs KK:", equity_hand_vs_hand("AhAs", "KdKc", iters=20000, seed=1)["equity"])
    print("AKs vs 22:", equity_hand_vs_hand("AhKh", "2d2c", iters=20000, seed=1)["equity"])
    print(
        "K2o vs BTN steal:",
        equity_vs_position_range("Kh2d", "BTN", "open", iters=8000, seed=1)["hero_equity"],
    )
