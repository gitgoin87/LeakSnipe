"""
Analysis engines for poker hand statistics and leak detection.
Generates insights and generates summaries for hand analysis.
"""

from typing import List, Dict, Any, Tuple, Optional
from collections import defaultdict

from models import Hand

# Preflop action sets — posts/blinds are not voluntary decisions.
PREFLOP_VOLUNTARY = frozenset({"call", "check", "fold", "raise", "bet"})
PREFLOP_RAISE = frozenset({"raise", "bet"})


def hand_3bet_flags(hand: Hand, hero: str) -> Tuple[bool, bool]:
    """
    Return (had_3bet_opportunity, made_3bet) for one hand.

    Opportunity: hero's first voluntary preflop action faces exactly one open raise.
    Ignores blind/ante posts and uncalled-bet cleanup lines.
    """
    preflop = hand.streets[0] if hand.streets else None
    if not preflop or not hero:
        return False, False

    # Starting stacks let us spot all-in opens: a shove can't be 3-bet, so calling
    # one is not a missed 3-bet opportunity. (All-ins are stored as plain "raise".)
    stacks = {
        str(info.get("name") or ""): float(info.get("stack") or 0)
        for info in (getattr(hand, "players", None) or {}).values()
    }
    invested: Dict[str, float] = defaultdict(float)

    raise_count = 0
    facing_all_in = False
    for act in preflop.get("actions", []):
        player = act.get("player")
        action = act.get("action")
        amount = float(act.get("amount") or 0)
        if player == "Uncalled":
            continue
        if player != hero and action in PREFLOP_RAISE:
            raise_count += 1
            invested[player] = max(invested[player], amount)
            stk = stacks.get(player, 0.0)
            if stk and invested[player] >= stk - 0.01:
                facing_all_in = True
        elif player == hero and action in PREFLOP_VOLUNTARY:
            if raise_count == 1 and not facing_all_in:
                return True, action in PREFLOP_RAISE
            return False, False
    return False, False


def aggregate_3bet_stats(hands: List[Hand], settings: Dict[str, Any]) -> Dict[str, Any]:
    """3-bet opportunities and frequency across hands."""
    opportunities = 0
    made = 0
    for h in hands:
        hero = h.hero_name(settings)
        if not hero:
            continue
        opp, did = hand_3bet_flags(h, hero)
        if opp:
            opportunities += 1
            if did:
                made += 1
    return {
        "opportunities": opportunities,
        "made": made,
        "pct": round(100 * made / max(opportunities, 1), 1),
    }


class LeakEngine:
    """Analyzes hands for poker leaks and game statistics."""

    def __init__(self, settings: Dict[str, Any]):
        self.settings = settings

    def analyze(self, hands: List[Hand]) -> Dict[str, Any]:
        """Analyze a list of hands and return comprehensive statistics."""
        stats: Dict[str, Any] = {
            "total_hands": 0, "vpip_hands": 0, "pfr_hands": 0,
            "bets_raises": 0, "calls": 0, "saw_flop": 0,
            "went_to_sd": 0, "won_at_sd": 0,
            "cbet_opportunities": 0, "cbet_made": 0,
            "three_bet_opportunities": 0, "three_bet_made": 0,
            "by_position": defaultdict(lambda: {"total": 0, "vpip": 0, "pfr": 0}),
            "by_site": defaultdict(lambda: {
                "total": 0, "vpip": 0, "pfr": 0,
                "won": 0.0, "lost": 0.0, "chip_net": 0.0,
            }),
            "biggest_wins": [], "biggest_losses": [],
        }
        for h in hands:
            hero = h.hero_name(self.settings)
            if not hero:
                continue
            stats["total_hands"] += 1
            stats["by_site"][h.site]["total"] += 1
            pos = h.hero_position
            stats["by_position"][pos]["total"] += 1

            if h.is_tournament:
                stats["by_site"][h.site]["chip_net"] += h.hero_won
            elif h.hero_won > 0:
                stats["by_site"][h.site]["won"] += h.hero_won
            else:
                stats["by_site"][h.site]["lost"] += abs(h.hero_won)

            preflop = h.streets[0] if h.streets else None
            hero_vpip = False
            hero_pfr = False
            hero_is_pfr = False
            if preflop:
                for act in preflop["actions"]:
                    if act["player"] == hero:
                        if act["action"] in ("call", "raise", "bet"):
                            hero_vpip = True
                        if act["action"] in ("raise", "bet"):
                            hero_pfr = True
                            hero_is_pfr = True

            if hero_vpip:
                stats["vpip_hands"] += 1
                stats["by_site"][h.site]["vpip"] += 1
                stats["by_position"][pos]["vpip"] += 1
            if hero_pfr:
                stats["pfr_hands"] += 1
                stats["by_site"][h.site]["pfr"] += 1
                stats["by_position"][pos]["pfr"] += 1

            saw_flop = False
            went_sd = False
            for street in h.streets:
                for act in street["actions"]:
                    if act["player"] == hero:
                        if act["action"] in ("bet", "raise"):
                            stats["bets_raises"] += 1
                        if act["action"] == "call":
                            stats["calls"] += 1
                    if street["name"] == "Flop":
                        saw_flop = True
                    if street["name"] == "River":
                        for a2 in street["actions"]:
                            if a2["player"] == hero and a2["action"] != "fold":
                                went_sd = True

            if saw_flop:
                stats["saw_flop"] += 1
            if went_sd:
                stats["went_to_sd"] += 1
                hero_won_hand = any(w["name"] == hero for w in h.winners)
                if hero_won_hand:
                    stats["won_at_sd"] += 1

            if hero_is_pfr and len(h.streets) > 1:
                flop_street = h.streets[1] if h.streets[1]["name"] == "Flop" else None
                if flop_street:
                    stats["cbet_opportunities"] += 1
                    for act in flop_street["actions"]:
                        if act["player"] == hero and act["action"] in ("bet", "raise"):
                            stats["cbet_made"] += 1
                            break

            opp_3b, made_3b = hand_3bet_flags(h, hero)
            if opp_3b:
                stats["three_bet_opportunities"] += 1
                if made_3b:
                    stats["three_bet_made"] += 1

            stats["biggest_wins"].append((h.hero_won, h))
            stats["biggest_losses"].append((h.hero_won, h))

        stats["biggest_wins"].sort(key=lambda x: x[0], reverse=True)
        stats["biggest_wins"] = stats["biggest_wins"][:5]
        stats["biggest_losses"].sort(key=lambda x: x[0])
        stats["biggest_losses"] = stats["biggest_losses"][:5]

        return self._compute_final(stats)

    def _compute_final(self, s: Dict[str, Any]) -> Dict[str, Any]:
        """Compute final statistics from raw analysis data."""
        t = s["total_hands"] or 1
        sf = s["saw_flop"] or 1
        sd = s["went_to_sd"] or 1
        result = {
            "total_hands": s["total_hands"],
            "vpip": round(100 * s["vpip_hands"] / t, 1),
            "pfr": round(100 * s["pfr_hands"] / t, 1),
            "af": round(s["bets_raises"] / max(s["calls"], 1), 2),
            "wtsd": round(100 * s["went_to_sd"] / sf, 1),
            "wsd": round(100 * s["won_at_sd"] / sd, 1),
            "cbet": round(100 * s["cbet_made"] / max(s["cbet_opportunities"], 1), 1),
            "three_bet": round(
                100 * s["three_bet_made"] / max(s["three_bet_opportunities"], 1), 1
            ),
            "three_bet_opportunities": s["three_bet_opportunities"],
            "three_bet_made": s["three_bet_made"],
            "by_position": {},
            "by_site": {},
            "biggest_wins": s["biggest_wins"],
            "biggest_losses": s["biggest_losses"],
            "alerts": [],
        }
        for pos, d in s["by_position"].items():
            pt = d["total"] or 1
            result["by_position"][pos] = {
                "total": d["total"],
                "vpip": round(100 * d["vpip"] / pt, 1),
                "pfr": round(100 * d["pfr"] / pt, 1),
            }
        for site, d in s["by_site"].items():
            st = d["total"] or 1
            result["by_site"][site] = {
                "total": d["total"],
                "vpip": round(100 * d["vpip"] / st, 1),
                "pfr": round(100 * d["pfr"] / st, 1),
                "won": round(d["won"], 2),
                "lost": round(d["lost"], 2),
                "chip_net": round(d.get("chip_net", 0.0), 0),
                "net": round(d["won"] - d["lost"], 2),
            }
        result["alerts"] = self._generate_alerts(result)
        return result

    def _generate_alerts(self, r: Dict[str, Any]) -> List[Tuple[str, str]]:
        """Generate leak alerts based on statistics."""
        alerts = []
        vpip = r["vpip"]
        pfr = r["pfr"]
        af = r["af"]
        wtsd = r["wtsd"]
        wsd = r["wsd"]
        cbet = r["cbet"]

        if vpip > 30:
            alerts.append(("red", f"VPIP too high ({vpip}%) — playing too many hands"))
        elif vpip < 15:
            alerts.append(("red", f"VPIP too low ({vpip}%) — playing too tight"))
        elif 15 <= vpip <= 22:
            alerts.append(("green", f"VPIP looks good ({vpip}%)"))
        else:
            alerts.append(("yellow", f"VPIP borderline ({vpip}%) — monitor closely"))

        if pfr > 25:
            alerts.append(("red", f"PFR too high ({pfr}%) — raising too much preflop"))
        elif pfr < 10:
            alerts.append(("red", f"PFR too low ({pfr}%) — not aggressive enough preflop"))
        elif 12 <= pfr <= 20:
            alerts.append(("green", f"PFR looks good ({pfr}%)"))
        else:
            alerts.append(("yellow", f"PFR borderline ({pfr}%)"))

        gap = vpip - pfr
        if gap > 12:
            alerts.append(("red", f"VPIP-PFR gap too wide ({gap:.1f}%) — calling too much preflop"))
        elif gap < 3:
            alerts.append(("yellow", f"VPIP-PFR gap narrow ({gap:.1f}%) — consider more calls"))
        else:
            alerts.append(("green", f"VPIP-PFR gap healthy ({gap:.1f}%)"))

        if af < 1.5:
            alerts.append(("red", f"AF too low ({af}) — too passive postflop"))
        elif af > 4.0:
            alerts.append(("yellow", f"AF very high ({af}) — may be over-aggressive"))
        else:
            alerts.append(("green", f"AF looks balanced ({af})"))

        if wtsd > 35:
            alerts.append(("yellow", f"WTSD high ({wtsd}%) — may be calling too much"))
        elif wtsd < 20:
            alerts.append(("yellow", f"WTSD low ({wtsd}%) — may be folding too much"))
        else:
            alerts.append(("green", f"WTSD balanced ({wtsd}%)"))

        if wsd < 45:
            alerts.append(("red", f"W$SD low ({wsd}%) — losing too often at showdown"))
        elif wsd > 55:
            alerts.append(("green", f"W$SD strong ({wsd}%)"))
        else:
            alerts.append(("green", f"W$SD acceptable ({wsd}%)"))

        if cbet > 80:
            alerts.append(("yellow", f"C-Bet too high ({cbet}%) — opponents can exploit"))
        elif cbet < 50:
            alerts.append(("yellow", f"C-Bet low ({cbet}%) — missing value"))
        else:
            alerts.append(("green", f"C-Bet % balanced ({cbet}%)"))

        return alerts


class SummaryGenerator:
    """Generates human-readable analysis summaries."""

    def generate(self, stats: Dict[str, Any], hands: List[Hand]) -> str:
        """Generate a text summary of hand statistics."""
        lines = []
        lines.append("=" * 60)
        lines.append("POKER HAND TRACKER — AI ANALYSIS SUMMARY")
        lines.append("=" * 60)
        lines.append(f"Total Hands Analyzed: {stats['total_hands']}")
        lines.append("")

        lines.append("── Overall Stats ──")
        lines.append(f"  VPIP:    {stats['vpip']}%")
        lines.append(f"  PFR:     {stats['pfr']}%")
        lines.append(f"  AF:      {stats['af']}")
        lines.append(f"  WTSD:    {stats['wtsd']}%")
        lines.append(f"  W$SD:    {stats['wsd']}%")
        lines.append(f"  C-Bet:   {stats['cbet']}%")
        lines.append("")

        lines.append("── Per-Site Breakdown ──")
        for site, sd in stats.get("by_site", {}).items():
            lines.append(f"  {site}: {sd['total']} hands | "
                         f"VPIP {sd['vpip']}% | PFR {sd['pfr']}% | "
                         f"Net: {sd['net']:+.2f}")
        lines.append("")

        lines.append("── Positional Analysis ──")
        for pos in ["EP", "MP", "CO", "BTN", "SB", "BB"]:
            pd = stats.get("by_position", {}).get(pos)
            if pd:
                lines.append(f"  {pos:3s}: {pd['total']:4d} hands | "
                             f"VPIP {pd['vpip']:5.1f}% | PFR {pd['pfr']:5.1f}%")
        lines.append("")

        lines.append("── Leak Alerts ──")
        for color, msg in stats.get("alerts", []):
            icon = {"green": "\u2705", "yellow": "\u26a0\ufe0f", "red": "\u274c"}.get(color, "")
            lines.append(f"  {icon} {msg}")
        lines.append("")

        lines.append("── Top 5 Biggest Pots Won ──")
        for amt, h in stats.get("biggest_wins", []):
            if amt > 0:
                lines.append(f"  +{amt:.0f} | {h.site} | {h.hero_cards} | "
                             f"Board: {' '.join(h.board_cards)} | {h.hand_id}")
        lines.append("")

        lines.append("── Top 5 Biggest Pots Lost ──")
        for amt, h in stats.get("biggest_losses", []):
            if amt < 0:
                lines.append(f"  {amt:.0f} | {h.site} | {h.hero_cards} | "
                             f"Board: {' '.join(h.board_cards)} | {h.hand_id}")
        lines.append("")
        lines.append("=" * 60)
        lines.append("Generated by Poker Hand Tracker")
        lines.append("Paste this into ChatGPT or Grok for further analysis.")
        return "\n".join(lines)


def classify_player_type(vpip: float, pfr: float, af: float, hands: int) -> str:
    """Classify opponent type from aggregate stats (matches poker_gui StationDetector)."""
    if hands < 10:
        return "Unknown"
    if vpip > 50 and pfr > 30:
        return "Maniac"
    if vpip > 40 and pfr < 10 and af < 1.5:
        return "Calling Station"
    if vpip > 35 and (vpip - pfr) > 15:
        return "Fish"
    if vpip > 28 and pfr > 20 and af > 2.5:
        return "LAG"
    if 15 <= vpip <= 25 and 12 <= pfr <= 22 and af > 2:
        return "TAG"
    if vpip < 15 and pfr < 10:
        return "Nit"
    return "Regular"


class PlayerAnalyzer:
    """Analyze opponents across all hands and classify player types."""

    def __init__(self, settings: Dict[str, Any]):
        self.settings = settings

    def analyze_players(self, hands: List[Hand]) -> List[Dict[str, Any]]:
        player_data: Dict[str, Dict[str, Any]] = defaultdict(lambda: {
            "total_hands": 0, "vpip_hands": 0, "pfr_hands": 0,
            "bets_raises": 0, "calls": 0, "folds_to_cbet": 0,
            "cbet_faced": 0, "saw_flop": 0, "went_to_sd": 0,
            "three_bet_opportunities": 0, "three_bet_made": 0,
        })

        for h in hands:
            hero = h.hero_name(self.settings)
            player_names = {info["name"] for info in h.players.values()}
            preflop = h.streets[0] if h.streets else None

            pfr_player = None
            if preflop:
                for act in preflop["actions"]:
                    if act["action"] in ("raise", "bet"):
                        pfr_player = act["player"]

            for pname in player_names:
                if pname == hero:
                    continue
                player_data[pname]["total_hands"] += 1

                if preflop:
                    p_vpip = False
                    p_pfr = False
                    for act in preflop["actions"]:
                        if act["player"] == pname:
                            if act["action"] in ("call", "raise", "bet"):
                                p_vpip = True
                            if act["action"] in ("raise", "bet"):
                                p_pfr = True
                    if p_vpip:
                        player_data[pname]["vpip_hands"] += 1
                    if p_pfr:
                        player_data[pname]["pfr_hands"] += 1

                saw_flop = False
                went_sd = False
                for street in h.streets:
                    for act in street["actions"]:
                        if act["player"] == pname:
                            if act["action"] in ("bet", "raise"):
                                player_data[pname]["bets_raises"] += 1
                            if act["action"] == "call":
                                player_data[pname]["calls"] += 1
                    if street["name"] == "Flop":
                        saw_flop = True
                    if street["name"] == "River":
                        for a2 in street["actions"]:
                            if a2["player"] == pname and a2["action"] != "fold":
                                went_sd = True
                if saw_flop:
                    player_data[pname]["saw_flop"] += 1
                if went_sd:
                    player_data[pname]["went_to_sd"] += 1

                opp_3b, made_3b = hand_3bet_flags(h, pname)
                if opp_3b:
                    player_data[pname]["three_bet_opportunities"] += 1
                    if made_3b:
                        player_data[pname]["three_bet_made"] += 1

                if len(h.streets) > 1 and pfr_player and pfr_player != pname:
                    flop_st = h.streets[1] if h.streets[1]["name"] == "Flop" else None
                    if flop_st:
                        pfr_cbet = False
                        for act in flop_st["actions"]:
                            if act["player"] == pfr_player and act["action"] in ("bet", "raise"):
                                pfr_cbet = True
                            if pfr_cbet and act["player"] == pname:
                                player_data[pname]["cbet_faced"] += 1
                                if act["action"] == "fold":
                                    player_data[pname]["folds_to_cbet"] += 1
                                break

        results: List[Dict[str, Any]] = []
        for pname, d in player_data.items():
            t = d["total_hands"] or 1
            sf = d["saw_flop"] or 1
            vpip = round(100 * d["vpip_hands"] / t, 1)
            pfr = round(100 * d["pfr_hands"] / t, 1)
            af = round(d["bets_raises"] / max(d["calls"], 1), 2)
            fold_cbet = round(100 * d["folds_to_cbet"] / max(d["cbet_faced"], 1), 1)
            wtsd = round(100 * d["went_to_sd"] / sf, 1)
            three_bet = round(
                100 * d["three_bet_made"] / max(d["three_bet_opportunities"], 1), 1
            )
            classification = classify_player_type(vpip, pfr, af, d["total_hands"])
            results.append({
                "name": pname,
                "hands": d["total_hands"],
                "vpip": vpip,
                "pfr": pfr,
                "af": af,
                "fold_cbet": fold_cbet,
                "wtsd": wtsd,
                "three_bet": three_bet,
                "auto_type": classification,
                "manual_type": "",
                "classification": classification,
                "effective_type": classification,
            })
        results.sort(key=lambda x: x["hands"], reverse=True)
        return results

    def apply_manual_overrides(self, results: List[Dict[str, Any]], db: Any) -> List[Dict[str, Any]]:
        for p in results:
            try:
                db_info = db.get_player_type(p["name"])
                if db_info and db_info.get("manual_type"):
                    p["manual_type"] = db_info["manual_type"]
                    p["classification"] = db_info["manual_type"]
                    p["effective_type"] = db_info["manual_type"]
            except Exception:
                pass
        return results


def _player_three_bet_pct(
    name: str, hands: List[Hand], settings: Dict[str, Any],
) -> float:
    """Compute 3-bet % for one opponent from hand history."""
    opportunities = 0
    made = 0
    for h in hands:
        hero = h.hero_name(settings)
        if not hero:
            continue
        if name not in {info.get("name") for info in h.players.values()}:
            continue
        if name == hero:
            continue
        opp, did = hand_3bet_flags(h, name)
        if opp:
            opportunities += 1
            if did:
                made += 1
    if opportunities == 0:
        return 0.0
    return round(100 * made / opportunities, 1)


def player_stats_payload(
    name: str,
    *,
    settings: Dict[str, Any],
    db: Any,
    hands: Optional[List[Hand]] = None,
) -> Dict[str, Any]:
    """Return HUD stats for one opponent, using DB cache when available."""
    cached = db.get_player_type(name)
    if cached and cached.get("hands", 0) > 0:
        pos_stats = db.get_player_position_stats(name)
        three_bet = _player_three_bet_pct(name, hands, settings) if hands is not None else 0.0
        return {
            "name": name,
            "hands": cached["hands"],
            "vpip": cached["vpip"],
            "pfr": cached["pfr"],
            "af": cached["af"],
            "fold_cbet": cached["fold_cbet"],
            "wtsd": cached["wtsd"],
            "three_bet": three_bet,
            "auto_type": cached.get("auto_type", "Unknown"),
            "manual_type": cached.get("manual_type", ""),
            "effective_type": cached.get("effective_type", "Unknown"),
            "by_position": pos_stats,
            "cached": True,
        }

    if hands is None:
        hands = db.get_all_hands()
    analyzer = PlayerAnalyzer(settings)
    all_stats = analyzer.apply_manual_overrides(analyzer.analyze_players(hands), db)
    match = next((p for p in all_stats if p["name"] == name), None)
    if not match:
        return {
            "name": name,
            "hands": 0,
            "vpip": 0.0,
            "pfr": 0.0,
            "af": 0.0,
            "fold_cbet": 0.0,
            "wtsd": 0.0,
            "three_bet": 0.0,
            "auto_type": "Unknown",
            "manual_type": "",
            "effective_type": "Unknown",
            "by_position": {},
            "cached": False,
        }

    try:
        db.save_player_type(
            name=name,
            auto_type=match.get("auto_type", match["classification"]),
            hands=match["hands"],
            vpip=match["vpip"],
            pfr=match["pfr"],
            af=match["af"],
            fold_cbet=match["fold_cbet"],
            wtsd=match["wtsd"],
        )
    except Exception:
        pass

    pos_stats = db.get_player_position_stats(name)
    return {
        "name": name,
        "hands": match["hands"],
        "vpip": match["vpip"],
        "pfr": match["pfr"],
        "af": match["af"],
        "fold_cbet": match["fold_cbet"],
        "wtsd": match["wtsd"],
        "three_bet": match.get("three_bet", 0.0),
        "auto_type": match.get("auto_type", match["classification"]),
        "manual_type": match.get("manual_type", ""),
        "effective_type": match.get("effective_type", match["classification"]),
        "by_position": pos_stats,
        "cached": False,
    }
