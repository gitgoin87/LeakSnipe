"""Ad-hoc verification of the 8-or-better low evaluator.

Enumerates every distinct qualifying low hand (all 5-rank subsets of A-8,
ranks A low), ranks them best-to-worst using the ENGINE's own low logic
(`stud_low`), and cross-checks against a user-supplied reference list.

Run:  python tests/verify_low_ranking.py
"""

import os
import sys
from itertools import combinations

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from equity import parse_cards, stud_low  # noqa: E402

# Ranks usable in an 8-or-better low, "A" low. Printed Ace-first by convention.
LOW_RANKS = ["A", "2", "3", "4", "5", "6", "7", "8"]
_ORDER = {ch: i for i, ch in enumerate(LOW_RANKS)}  # A<2<...<8 for display


def hand_label(ranks):
    return "".join(sorted(ranks, key=lambda r: _ORDER[r]))


def engine_low_key(ranks):
    """Rank a 5-rank low via the engine. Suits are irrelevant for a low."""
    cards = parse_cards("".join(f"{r}{s}" for r, s in zip(ranks, "cdhsc")))
    return stud_low(cards)


# User's reference list (best -> worst), 54 hands.
USER_LIST = [
    "A2345", "A2346", "A2356", "A2456", "A3456",
    "A2347", "A2357", "A2457", "A3457", "A2367", "A2467", "A3467", "A2567", "A3567", "A4567",
    "A2348", "A2358", "A2458", "A3458", "A2368", "A2468", "A3468", "A2568", "A3568", "A4568",
    "A2378", "A2478", "A3478", "A2578", "A3578", "A4578", "A2678", "A3678", "A4678", "A5678",
    "23456", "23457", "23467", "23567", "24567", "34567", "23458", "23468", "23568", "24568",
    "34568", "23478", "23578", "24578", "34578", "23678", "24678", "34678", "45678",
]


def main():
    all_hands = [hand_label(c) for c in combinations(LOW_RANKS, 5)]
    print(f"Distinct qualifying lows enumerated: {len(all_hands)} (expect 56)")

    # Every enumerated hand must qualify (the engine returns a low tuple).
    keys = {h: engine_low_key(list(h)) for h in all_hands}
    assert all(k is not None for k in keys.values()), "engine rejected a valid low"

    # Engine ranking, best (smallest tuple) -> worst.
    ranked = sorted(all_hands, key=lambda h: keys[h])
    print(f"Engine best: {ranked[0]}  worst: {ranked[-1]}")

    missing = [h for h in ranked if h not in USER_LIST]
    print(f"\nMissing from user's 54-hand list ({len(missing)}):")
    for h in missing:
        print(f"  {h}  -> correct rank {ranked.index(h) + 1} of 56")

    # Spot-check standard low rules the engine must satisfy.
    assert keys["A2345"] < keys["A2346"] < keys["23456"], "wheel/6-high order"
    assert keys["23456"] < keys["A2347"], "a 6-high low must beat any 7-high low"
    assert keys["A2678"] < keys["45678"], "8-high tiebreak by lower cards"

    # Ordering cross-check: user's list, with the missing hands removed from the
    # engine ranking, should be identical in order IF the user's list were a
    # correct best->worst ranking.
    engine_minus_missing = [h for h in ranked if h in USER_LIST]
    discrepancies = [
        (i + 1, u, e)
        for i, (u, e) in enumerate(zip(USER_LIST, engine_minus_missing))
        if u != e
    ]
    if discrepancies:
        print(f"\nOrdering discrepancies vs engine: {len(discrepancies)} positions.")
        print("First few (pos: user vs engine-correct):")
        for pos, u, e in discrepancies[:5]:
            print(f"  #{pos}: user={u} engine={e}")
        # Diagnose the cause: is the user grouping all ace-hands ahead of non-ace?
        user_no_missing = USER_LIST  # already lacks the 2 missing hands
        ace_first = sorted(user_no_missing, key=lambda h: ("A" not in h, keys[h]))
        if user_no_missing == ace_first:
            print(
                "\nDiagnosis: user's list is mis-ordered. It is sorted ACE-HANDS-"
                "FIRST (all 35 ace lows, then non-ace lows), each group internally "
                "correct. True low ranking interleaves them by high card "
                "(e.g. 23456 is a 6-low and beats the 7-low A2347)."
            )
    else:
        print("\nNo ordering discrepancies: user's 54 match engine order exactly.")


if __name__ == "__main__":
    main()
