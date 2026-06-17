"""
Analysis engines for poker hand statistics and leak detection.
Generates insights and generates summaries for hand analysis.
"""

from typing import List, Dict, Any, Tuple
from collections import defaultdict

from models import Hand


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
