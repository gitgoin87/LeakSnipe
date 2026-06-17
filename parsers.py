"""
Hand parsing logic for multiple poker sites.
Supports CoinPoker, BetACR (WPN), GGPoker, ClubGG, PokerStars, 888poker,
Ignition/Bovada, and Replay Poker hand history formats.
"""

import re
import logging
from typing import Optional, Dict, List, Any
from datetime import datetime

try:
    from dateutil import tz as _tz
    _LOCAL_TZ = _tz.tzlocal()
    _SITE_TZ_MAP: Dict[str, Any] = {
        "UTC":  _tz.UTC,
        "GMT":  _tz.UTC,
        "ET":   _tz.gettz("America/New_York"),
        "EST":  _tz.gettz("America/New_York"),
        "EDT":  _tz.gettz("America/New_York"),
        "CET":  _tz.gettz("Europe/Berlin"),
        "CEST": _tz.gettz("Europe/Berlin"),
        "PT":   _tz.gettz("America/Los_Angeles"),
        "CT":   _tz.gettz("America/Chicago"),
    }
    _HAS_DATEUTIL = True
except ImportError:
    _HAS_DATEUTIL = False
    _SITE_TZ_MAP = {}

from models import Hand


def _parse_hand_datetime(dt_str: str, tz_suffix: str = "",
                         default_tz_key: str = "") -> datetime:
    """Parse a hand timestamp and convert it to a naive local datetime.

    *dt_str*        – the bare date/time string extracted from the header.
    *tz_suffix*     – timezone label found in the header (e.g. "ET", "UTC").
    *default_tz_key*– fallback timezone key when no suffix is present in
                      the header (e.g. "ET" for BetACR which always uses ET).
    """
    for fmt in ("%Y/%m/%d %H:%M:%S", "%Y-%m-%d %H:%M:%S",
                "%Y/%m/%d %H:%M", "%Y-%m-%d %H:%M"):
        try:
            parsed = datetime.strptime(dt_str.strip(), fmt)
            break
        except ValueError:
            continue
    else:
        return datetime.now()

    if not _HAS_DATEUTIL:
        return parsed

    key = (tz_suffix.strip().upper() or default_tz_key.upper())
    tz_info = _SITE_TZ_MAP.get(key)
    if tz_info is None:
        return parsed  # unknown suffix — treat as local

    # Convert: attach the site tz → convert to local → strip tz info
    aware = parsed.replace(tzinfo=tz_info)
    return aware.astimezone(_LOCAL_TZ).replace(tzinfo=None)


class HandParser:
    """Parses hand history text from various poker sites into Hand objects."""

    def __init__(self, settings: Dict[str, Any]):
        self.settings = settings

    def _hero_candidates(self, site_label: str) -> List[str]:
        """Configured hero aliases plus names dealt cards in a hand."""
        hero_names = self.settings.get("hero_names", {})
        configured = (
            hero_names.get(site_label)
            or hero_names.get("BetACR" if site_label in ("ACR", "BetACR") else site_label, "")
            or ""
        )
        candidates: List[str] = []
        for part in re.split(r"[,;|]", str(configured)):
            part = part.strip()
            if part and part not in candidates:
                candidates.append(part)
        return candidates

    def _resolve_hero(self, text: str, site_label: str) -> str:
        """Pick the hero name that actually played this hand."""
        dealt: List[str] = []
        for match in re.finditer(r"Dealt to (.+?) \[(.+?)\]", text):
            name = match.group(1).strip()
            if name and name not in dealt:
                dealt.append(name)
        if dealt:
            for alias in self._hero_candidates(site_label):
                if alias in dealt:
                    return alias
            return dealt[0]

        seat_names = set(re.findall(r"Seat \d+: (.+?) \(", text))
        for alias in self._hero_candidates(site_label):
            if alias in seat_names:
                return alias
        return self._hero_candidates(site_label)[0] if self._hero_candidates(site_label) else ""

    def detect_site(self, text: str) -> Optional[str]:
        """Detect which poker site the hand is from based on text content."""
        stripped_text = text.lstrip()
        if stripped_text.startswith("<?xml") or "<HandHistory" in text:
            site_match = re.search(r"<Site>([^<]+)</Site>", text, re.IGNORECASE)
            if site_match:
                site_name = site_match.group(1).strip()
                if site_name in ("ACR", "BetACR"):
                    return "BetACR"
                if site_name in ("CoinPoker", "PokerStars", "Ignition", "Bovada"):
                    return "Ignition" if site_name == "Bovada" else site_name
                return site_name
        for line in text.split("\n"):
            stripped = line.strip()
            if stripped.startswith("CoinPoker Hand #"):
                return "CoinPoker"
            if stripped.startswith("Game Hand #") or stripped.startswith("Hand #"):
                return "BetACR"
            if "GG Poker" in stripped or "GGPoker" in stripped or stripped.startswith("Poker Hand #PT"):
                return "GGPoker"
            if "ClubGG" in stripped or stripped.startswith("Poker Hand #RC"):
                return "ClubGG"
            if "PokerStars Hand #" in stripped or "PokerStars Game #" in stripped:
                return "PokerStars"
            if "888poker" in stripped.lower() or "#Game No" in stripped:
                return "888poker"
            if stripped.startswith("Ignition Hand #") or "Ignition Casino" in stripped:
                return "Ignition"
            if stripped.startswith("Bovada Hand #") or "Bovada" in stripped:
                return "Ignition"
            if (
                stripped.startswith("Replay Poker Hand #")
                or stripped.startswith("***** Replay Poker Hand History for Game")
                or ("Replay Poker" in stripped and ("Hand" in stripped or "Game" in stripped))
            ):
                return "ReplayPoker"
        return None

    def split_hands(self, text: str, site: str) -> List[str]:
        """Split raw text into individual hand texts."""
        stripped_text = text.lstrip()
        if stripped_text.startswith("<?xml") or "<HandHistory" in text:
            if "<?xml" in text:
                parts = re.split(r"(?=<\?xml\b)", text)
            else:
                parts = re.split(r"(?=<HandHistory\b)", text)
            return [part.strip() for part in parts if part.strip()]

        hands = []
        current: List[str] = []
        for line in text.split("\n"):
            if site == "CoinPoker" and line.strip().startswith("CoinPoker Hand #"):
                if current:
                    hands.append("\n".join(current))
                current = [line]
            elif site in ("ACR", "BetACR") and (line.strip().startswith("Game Hand #") or line.strip().startswith("Hand #")):
                if current:
                    hands.append("\n".join(current))
                current = [line]
            elif site == "GGPoker" and (line.strip().startswith("Poker Hand #") or "GGPoker" in line or "GG Poker" in line):
                if current:
                    hands.append("\n".join(current))
                current = [line]
            elif site == "ClubGG" and (line.strip().startswith("Poker Hand #") or "ClubGG" in line):
                if current:
                    hands.append("\n".join(current))
                current = [line]
            elif site == "PokerStars" and (line.strip().startswith("PokerStars Hand #") or line.strip().startswith("PokerStars Game #")):
                if current:
                    hands.append("\n".join(current))
                current = [line]
            elif site == "888poker" and line.strip().startswith("#Game No"):
                if current:
                    hands.append("\n".join(current))
                current = [line]
            elif site == "Ignition" and (line.strip().startswith("Ignition Hand #") or line.strip().startswith("Bovada Hand #")):
                if current:
                    hands.append("\n".join(current))
                current = [line]
            elif site == "ReplayPoker" and (
                line.strip().startswith("***** Replay Poker Hand History for Game")
                or line.strip().startswith("Replay Poker Hand #")
            ):
                if current:
                    hands.append("\n".join(current))
                current = [line]
            else:
                current.append(line)
        if current:
            hands.append("\n".join(current))
        return hands

    def parse_file(self, filepath: str, site: str) -> List[Hand]:
        """Parse a hand history file and return list of Hand objects."""
        results = []
        try:
            with open(filepath, "r", encoding="utf-8", errors="replace") as f:
                content = f.read()
        except Exception:
            return results
        if not content.strip():
            return results
        detected = self.detect_site(content)
        if detected is None:
            return results
        raw_hands = self.split_hands(content, detected)
        for raw in raw_hands:
            try:
                h = self._parse_single(raw.strip(), detected)
                if h and h.hand_id:
                    # All WPN hands are BetACR
                    if h.site in ("ACR", "BetACR"):
                        h.site = "BetACR"
                    h.raw_text = raw.strip()
                    results.append(h)
            except Exception as e:
                logging.error(f"Error parsing hand from {filepath}: {e}, content start: {str(raw.strip())[:100]}")
                continue
        return results

    def _parse_single(self, text: str, site: str) -> Optional[Hand]:
        """Parse a single hand based on detected site."""
        stripped_text = text.lstrip()
        if stripped_text.startswith("<?xml") or "<HandHistory" in text:
            return self._parse_dh2_xml(text, site)

        if site == "CoinPoker":
            return self._parse_coinpoker(text)
        elif site in ("ACR", "BetACR"):
            return self._parse_acr(text, site_label="BetACR")
        elif site == "GGPoker":
            return self._parse_ggpoker(text)
        elif site == "ClubGG":
            return self._parse_ggpoker(text)  # ClubGG uses same format as GGPoker
        elif site == "PokerStars":
            return self._parse_pokerstars(text)
        elif site == "888poker":
            return self._parse_888poker(text)
        elif site == "Ignition":
            return self._parse_ignition(text)
        elif site == "ReplayPoker":
            return self._parse_replaypoker(text)

        # Fallback: Try to detect format from content
        if "CoinPoker Hand #" in text:
            return self._parse_coinpoker(text)
        if "Game Hand #" in text:
            return self._parse_acr(text, site_label="BetACR")
        if "Replay Poker" in text:
            return self._parse_replaypoker(text)

        return None

    def _parse_coinpoker(self, text: str) -> Optional[Hand]:
        """Parse CoinPoker hand history format."""
        h = Hand()
        h.site = "CoinPoker"
        lines = text.split("\n")
        hero = self.settings.get("hero_names", {}).get("CoinPoker", "jdwalka")

        header = lines[0] if lines else ""
        m = re.search(r"CoinPoker Hand #(\d+)", header)
        if not m:
            return None
        h.hand_id = f"CP_{m.group(1)}"

        tm = re.search(r"Tournament #(\d+)", header)
        if tm:
            h.is_tournament = True
            h.tournament_id = tm.group(1)
        bi = re.search(r"[₮$€](\d+(?:\.\d+)?)\+[₮$€]?(\d+(?:\.\d+)?)", header)
        if bi:
            h.buy_in = f"{bi.group(1)}+{bi.group(2)}"
        h.game_type = "NLHE"
        dm = re.search(r"(\d{4}/\d{2}/\d{2} \d{2}:\d{2}:\d{2})\s*([A-Z]{2,4})?", header)
        if dm:
            h.date = _parse_hand_datetime(dm.group(1), dm.group(2) or "",
                                          default_tz_key="UTC")
        else:
            h.date = datetime.now()

        table_line = lines[1] if len(lines) > 1 else ""
        tm2 = re.search(r"Table '([^']+)'", table_line)
        if tm2:
            h.table_name = tm2.group(1)
        sm = re.search(r"(\d+)-max", table_line)
        if sm:
            h.max_seats = int(sm.group(1))
        bm = re.search(r"Seat #(\d+) is the button", table_line)
        if bm:
            h.button_seat = int(bm.group(1))

        for line in lines:
            seat_m = re.match(r"Seat (\d+): (.+?) \((\d+(?:\.\d+)?) in chips\)", line.strip())
            if seat_m:
                seat_num = int(seat_m.group(1))
                name = seat_m.group(2)
                stack = float(seat_m.group(3))
                h.players[seat_num] = {"name": name, "stack": stack, "is_hero": name == hero}

        hc = re.search(r"Dealt to " + re.escape(hero) + r" \[(.+?)\]", text)
        if hc:
            h.hero_cards = hc.group(1)

        h.streets = self._parse_streets_coinpoker(lines, hero)
        h.board_cards = self._extract_board(text)

        pot_m = re.search(r"Total pot (\d+(?:\.\d+)?)", text)
        if pot_m:
            h.pot = float(pot_m.group(1))
        rake_m = re.search(r"Rake (\d+(?:\.\d+)?)", text)
        if rake_m:
            h.rake = float(rake_m.group(1))

        for line in lines:
            wm = re.match(r"(.+?) collected (\d+(?:\.\d+)?) from", line.strip())
            if wm:
                h.winners.append({"name": wm.group(1), "amount": float(wm.group(2))})

        h.hero_won = self._calc_hero_result(h, hero)
        h.hero_position = self._calc_position(h, hero)
        return h

    def _parse_dh2_xml(self, xml_text: str, site_name: str) -> Optional[Hand]:
        """Parse DriveHUD2-style XML hand histories stored in raw_text."""
        h = Hand()
        h.site = "BetACR" if site_name in ("ACR", "BetACR") else site_name
        h.raw_text = xml_text
        hero = self.settings.get("hero_names", {}).get(h.site, "")

        def xval(tag: str) -> str:
            match = re.search(rf"<{tag}[^>]*>([^<]*)</{tag}>", xml_text, re.IGNORECASE)
            return match.group(1).strip() if match else ""

        def xattr(element: str, attr: str) -> str:
            match = re.search(rf'{attr}="([^"]*)"', element, re.IGNORECASE)
            return match.group(1) if match else ""

        hand_num = xval("HandId") or xval("HandNumber") or xval("GameNumber")
        if not hand_num:
            return None
        prefix = "CP" if h.site == "CoinPoker" else "ACR"
        h.hand_id = f"{prefix}_{hand_num}"

        game_type = (xval("GameType") or "").lower()
        if "omaha" in game_type:
            h.game_type = "PLO"
        elif "holdem" in game_type:
            h.game_type = "NLHE"
        else:
            h.game_type = "NLHE"

        tournament_id = xval("TournamentId")
        h.is_tournament = bool(tournament_id)
        h.tournament_id = tournament_id

        h.table_name = xval("TableName")
        try:
            h.max_seats = int(xval("TotalSeatNumber") or xval("NumPlayersSeated") or "0")
        except ValueError:
            h.max_seats = 0
        try:
            h.button_seat = int(xval("DealerButtonPosition") or "0")
        except ValueError:
            h.button_seat = 0

        timestamp = xval("DateOfHandUtc") or xval("DateOfHand")
        if timestamp:
            for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%m/%d/%Y %I:%M:%S %p"):
                try:
                    h.date = datetime.strptime(timestamp.split(".")[0], fmt)
                    break
                except ValueError:
                    continue
        if not h.date:
            h.date = datetime.now()

        xml_hero = xval("HeroName") or hero

        player_count = 0
        for player_match in re.finditer(r"<Player\b([^/]*?)/>", xml_text, re.DOTALL):
            elem = player_match.group(0)
            pname = xattr(elem, "PlayerName")
            try:
                seat = int(xattr(elem, "SeatNumber") or "0")
            except ValueError:
                seat = 0
            try:
                stack = float(xattr(elem, "StartingStack") or "0")
            except ValueError:
                stack = 0.0
            is_hero = pname == xml_hero
            h.players[seat] = {"name": pname, "stack": stack, "is_hero": is_hero}
            player_count += 1
            if is_hero:
                cards_str = xattr(elem, "HoleCards") or xattr(elem, "Cards") or ""
                if cards_str:
                    h.hero_cards = " ".join(
                        cards_str[i:i + 2] for i in range(0, len(cards_str) - 1, 2)
                    )
        if h.max_seats == 0:
            h.max_seats = player_count

        action_map = {
            "SMALL_BLIND": "post",
            "BIG_BLIND": "post",
            "ANTE": "post",
            "POSTS": "post",
            "RAISE": "raise",
            "CALL": "call",
            "CHECK": "check",
            "BET": "bet",
            "FOLD": "fold",
            "UNCALLED_BET": "return",
            "WINS": "win",
            "WINS_SIDE_POT": "win",
            "ALL_IN": "raise",
        }
        street_map = {"Preflop": "Preflop", "Flop": "Flop", "Turn": "Turn", "River": "River", "Summary": "Showdown", "Showdown": "Showdown"}
        streets_order = ["Preflop", "Flop", "Turn", "River", "Showdown"]
        streets_map: Dict[str, Dict[str, Any]] = {}
        for action_match in re.finditer(r"<HandAction\b([^/]*?)/>", xml_text, re.DOTALL):
            elem = action_match.group(0)
            pname = xattr(elem, "PlayerName")
            raw_type = xattr(elem, "HandActionType")
            street_name = street_map.get(xattr(elem, "Street") or "Preflop", "Preflop")
            try:
                amount = abs(float(xattr(elem, "Amount") or "0"))
            except ValueError:
                amount = 0.0
            action = action_map.get(raw_type, raw_type.lower())
            if street_name not in streets_map:
                streets_map[street_name] = {"name": street_name, "cards": [], "actions": []}
            streets_map[street_name]["actions"].append(
                {"player": pname, "action": action, "amount": amount}
            )
            if action == "win" and amount > 0:
                h.winners.append({"name": pname, "amount": amount})

        h.streets = [streets_map[name] for name in streets_order if name in streets_map]

        community_cards = xval("CommunityCards")
        if community_cards:
            h.board_cards = [community_cards[i:i + 2] for i in range(0, len(community_cards) - 1, 2)]

        try:
            h.pot = float(xval("TotalPot") or "0")
        except ValueError:
            h.pot = 0.0
        try:
            h.rake = float(xval("Rake") or "0")
        except ValueError:
            h.rake = 0.0

        if not h.winners:
            for player_match in re.finditer(r"<Player\b([^/]*?)/>", xml_text, re.DOTALL):
                elem = player_match.group(0)
                pname = xattr(elem, "PlayerName")
                try:
                    win_amt = float(xattr(elem, "Win") or "0")
                except ValueError:
                    win_amt = 0.0
                if win_amt > 0:
                    h.winners.append({"name": pname, "amount": win_amt})

        hero_invested = 0.0
        hero_won_amt = 0.0
        for street in h.streets:
            for act in street.get("actions", []):
                if act["player"] != xml_hero:
                    continue
                if act["action"] in ("post", "raise", "call", "bet"):
                    hero_invested += act["amount"]
                elif act["action"] in ("win", "return"):
                    hero_won_amt += act["amount"]
        h.hero_won = hero_won_amt - hero_invested
        h.hero_position = self._calc_position(h, xml_hero)
        return h

    def _parse_streets_coinpoker(self, lines: List[str], hero: str) -> List[Dict[str, Any]]:
        """Parse streets and actions from CoinPoker format."""
        current_street = {"name": "Preflop", "cards": [], "actions": []}
        streets = [current_street]
        in_actions = True
        for line in lines:
            stripped = line.strip()
            if stripped.startswith("*** HOLE CARDS ***"):
                continue
            if stripped.startswith("*** FLOP ***"):
                cards_m = re.search(r"\[(.+?)\]", stripped)
                cards = cards_m.group(1).split() if cards_m else []
                current_street = {"name": "Flop", "cards": cards, "actions": []}
                streets.append(current_street)
                in_actions = True
                continue
            if stripped.startswith("*** TURN ***"):
                cards_m = re.findall(r"\[(.+?)\]", stripped)
                cards = cards_m[-1].split() if cards_m else []
                current_street = {"name": "Turn", "cards": cards, "actions": []}
                streets.append(current_street)
                in_actions = True
                continue
            if stripped.startswith("*** RIVER ***"):
                cards_m = re.findall(r"\[(.+?)\]", stripped)
                cards = cards_m[-1].split() if cards_m else []
                current_street = {"name": "River", "cards": cards, "actions": []}
                streets.append(current_street)
                in_actions = True
                continue
            if stripped.startswith("*** SHOW DOWN ***") or stripped.startswith("*** SUMMARY ***"):
                in_actions = False
                continue
            if in_actions and current_street is not None and ": " in stripped:
                if stripped.startswith("Seat "):
                    continue
                act_m = re.match(r"(.+?): (.+)", stripped)
                if act_m:
                    pname = act_m.group(1)
                    action_str = act_m.group(2)
                    action, amount = self._parse_action(action_str)
                    if action and not stripped.startswith("Dealt to"):
                        current_street["actions"].append(
                            {"player": pname, "action": action, "amount": amount}
                        )
        return streets

    def _parse_acr(self, text: str, site_label: str = "BetACR") -> Optional[Hand]:
        """Parse BetACR / WPN hand history format."""
        h = Hand()
        h.site = site_label
        h.raw_text = text
        lines = text.split("\n")
        hero = self._resolve_hero(text, site_label)
        h.hero_player = hero

        header = lines[0] if lines else ""
        m = re.search(r"(?:Game )?Hand #(\d+)", header)
        if not m:
            return None
        prefix = "ACR"  # All WPN-format hands use ACR_ prefix
        h.hand_id = f"{prefix}_{m.group(1)}"

        tm = re.search(r"Tournament #(\d+)", header)
        if tm:
            h.is_tournament = True
            h.tournament_id = tm.group(1)
        h.game_type = "NLHE"
        dm = re.search(r"(\d{4}/\d{2}/\d{2} \d{2}:\d{2}:\d{2})\s*([A-Z]{2,4})?", header)
        if dm:
            h.date = _parse_hand_datetime(dm.group(1), dm.group(2) or "",
                                          default_tz_key="ET")
        else:
            h.date = datetime.now()

        table_line = lines[1] if len(lines) > 1 else ""
        tm2 = re.search(r"Table '([^']+)'", table_line)
        if tm2:
            h.table_name = tm2.group(1)
        elif table_line:
            # BetACR format: "Eton 6-max Seat #2 is the button"
            tn_m = re.match(r"^(.+?)\s+\d+-max", table_line)
            if tn_m:
                h.table_name = tn_m.group(1).strip()
        sm = re.search(r"(\d+)-max", table_line)
        if sm:
            h.max_seats = int(sm.group(1))
        bm = re.search(r"Seat #(\d+) is the button", table_line)
        if bm:
            h.button_seat = int(bm.group(1))

        for line in lines:
            seat_m = re.match(r"Seat (\d+): (.+?) \(\$?(\d+(?:\.\d+)?)\)", line.strip())
            if seat_m:
                seat_num = int(seat_m.group(1))
                name = seat_m.group(2)
                stack = float(seat_m.group(3))
                h.players[seat_num] = {"name": name, "stack": stack, "is_hero": name == hero}

        hc = re.search(r"Dealt to " + re.escape(hero) + r" \[(.+?)\]", text)
        if hc:
            h.hero_cards = hc.group(1)

        h.streets = self._parse_streets_acr(lines, hero)
        h.board_cards = self._extract_board(text)

        pot_m = re.search(r"Total pot \$?(\d+(?:\.\d+)?)", text)
        if pot_m:
            h.pot = float(pot_m.group(1))

        for line in lines:
            stripped = line.strip()
            wm = re.match(r"(.+?) collected \$?(\d+(?:\.\d+)?) from", stripped)
            if wm:
                h.winners.append({"name": wm.group(1), "amount": float(wm.group(2))})
                continue
            summary_wm = re.match(
                r"Seat \d+: (.+?)(?: \([^)]*\))* (?:showed \[[^\]]+\]|did not show|mucked(?: \[[^\]]+\])?) and won \$?(\d+(?:\.\d+)?)",
                stripped,
            )
            if summary_wm:
                h.winners.append({"name": summary_wm.group(1), "amount": float(summary_wm.group(2))})

        h.hero_won = self._calc_hero_result(h, hero)
        h.hero_position = self._calc_position(h, hero)
        return h

    def _parse_ggpoker(self, text: str) -> Optional[Hand]:
        """Stub GGPoker parser — returns None until full implementation is added."""
        return None

    def _parse_replaypoker(self, text: str) -> Optional[Hand]:
        """Parse Replay Poker / casino.org hand history format."""
        h = Hand()
        h.site = "ReplayPoker"
        lines = text.split("\n")
        hero = self.settings.get("hero_names", {}).get("ReplayPoker", "")

        hand_id: Optional[str] = None
        for pattern in (
            r"Replay Poker Hand #(\d+)",
            r"Replay Poker Hand History for Game (\d+)",
            r"\*{5}\s*Hand (\d+)\s*\*{5}",
        ):
            match = re.search(pattern, text)
            if match:
                hand_id = match.group(1)
                break
        if not hand_id:
            return None
        h.hand_id = f"RP_{hand_id}"

        h.game_type = "PLO" if "omaha" in text.lower() else "NLHE"

        dm = re.search(r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})", text)
        if dm:
            h.date = _parse_hand_datetime(dm.group(1))
        else:
            h.date = datetime.now()

        table_line = next((line.strip() for line in lines if line.strip().startswith("Table:")), "")
        if table_line:
            table_text = table_line.split(":", 1)[1].strip()
            table_text = re.sub(r"\s+\(\d+\)$", "", table_text)
            seats_match = re.search(r"\((\d+)\s*max\)", table_text, re.IGNORECASE)
            if seats_match:
                h.max_seats = int(seats_match.group(1))
                table_text = re.sub(r"\s*\(\d+\s*max\)", "", table_text, flags=re.IGNORECASE).strip()
            h.table_name = table_text

        players_m = re.search(r"Players:\s*(\d+)", text, re.IGNORECASE)
        if players_m and not h.max_seats:
            h.max_seats = int(players_m.group(1))

        button_line = re.search(r"Seat #(\d+) is the button", text)
        if button_line:
            h.button_seat = int(button_line.group(1))

        for line in lines:
            seat_m = re.match(
                r"Seat (\d+): (.+?)(?: \(([^)]*)\))? \(\$?([\d,]+(?:\.\d+)?) in chips\)",
                line.strip(),
            )
            if not seat_m:
                continue
            seat_num = int(seat_m.group(1))
            name = seat_m.group(2).strip()
            role = (seat_m.group(3) or "").strip().upper()
            stack = self._parse_amount(seat_m.group(4))
            if role in {"BTN", "BUTTON", "DEALER"} and not h.button_seat:
                h.button_seat = seat_num
            h.players[seat_num] = {"name": name, "stack": stack, "is_hero": name == hero}

        if hero:
            hc = re.search(r"Dealt to " + re.escape(hero) + r" \[(.+?)\]", text)
            if hc:
                h.hero_cards = hc.group(1)

        h.streets = self._parse_streets_replaypoker(lines)
        h.board_cards = self._extract_board(text) or self._collect_board_from_streets(h.streets)

        pot_m = re.search(r"(?:Total pot|Pot):\s*\$?([\d,]+(?:\.\d+)?)", text, re.IGNORECASE)
        if pot_m:
            h.pot = self._parse_amount(pot_m.group(1))
        rake_m = re.search(r"Rake:?\s*\$?([\d,]+(?:\.\d+)?)", text, re.IGNORECASE)
        if rake_m:
            h.rake = self._parse_amount(rake_m.group(1))

        for line in lines:
            stripped = line.strip()
            collected_m = re.match(r"(.+?) collected \$?([\d,]+(?:\.\d+)?) from", stripped)
            if collected_m:
                h.winners.append(
                    {"name": collected_m.group(1), "amount": self._parse_amount(collected_m.group(2))}
                )
                continue
            winner_m = re.match(
                r"Winner:\s*(.+?)(?:\s+\(\$?([\d,]+(?:\.\d+)?)\))?$",
                stripped,
                re.IGNORECASE,
            )
            if winner_m:
                h.winners.append(
                    {
                        "name": winner_m.group(1).strip(),
                        "amount": self._parse_amount(winner_m.group(2) or "0"),
                    }
                )

        if len(h.winners) == 1 and h.winners[0]["amount"] == 0.0 and h.pot > 0:
            h.winners[0]["amount"] = h.pot

        h.hero_won = self._calc_hero_result(h, hero)
        h.hero_position = self._calc_position(h, hero)
        return h

    def _parse_streets_replaypoker(self, lines: List[str]) -> List[Dict[str, Any]]:
        """Parse streets and actions from Replay Poker format."""
        current_street: Dict[str, Any] = {"name": "Preflop", "cards": [], "actions": []}
        streets = [current_street]
        player_names = sorted(
            {
                match.group(2).strip()
                for match in (
                    re.match(
                        r"Seat (\d+): (.+?)(?: \(([^)]*)\))? \(\$?([\d,]+(?:\.\d+)?) in chips\)",
                        line.strip(),
                    )
                    for line in lines
                )
                if match
            },
            key=len,
            reverse=True,
        )

        for line in lines:
            stripped = line.strip()
            if stripped.startswith("*** HOLE CARDS ***"):
                continue
            if stripped.startswith("*** FLOP ***"):
                cards_m = re.search(r"\[(.+?)\]", stripped)
                cards = cards_m.group(1).split() if cards_m else []
                current_street = {"name": "Flop", "cards": cards, "actions": []}
                streets.append(current_street)
                continue
            if stripped.startswith("*** TURN ***"):
                cards_m = re.findall(r"\[(.+?)\]", stripped)
                cards = cards_m[-1].split() if cards_m else []
                current_street = {"name": "Turn", "cards": cards, "actions": []}
                streets.append(current_street)
                continue
            if stripped.startswith("*** RIVER ***"):
                cards_m = re.findall(r"\[(.+?)\]", stripped)
                cards = cards_m[-1].split() if cards_m else []
                current_street = {"name": "River", "cards": cards, "actions": []}
                streets.append(current_street)
                continue
            if stripped.startswith("*** SHOW DOWN ***") or stripped.startswith("*** SUMMARY ***"):
                continue
            if not stripped or stripped.startswith("Seat ") or stripped.startswith("Table:") or stripped.startswith("Players:"):
                continue
            if stripped.startswith("Dealt to"):
                continue
            if ": " in stripped:
                pname, action_str = stripped.split(": ", 1)
                action, amount = self._parse_action(action_str)
                if action:
                    current_street["actions"].append({"player": pname, "action": action, "amount": amount})
                continue
            for pname in player_names:
                if stripped.startswith(pname + " "):
                    action, amount = self._parse_action(stripped[len(pname) + 1 :])
                    if action:
                        current_street["actions"].append({"player": pname, "action": action, "amount": amount})
                    break
        return streets

    @staticmethod
    def _parse_amount(value: str) -> float:
        """Parse a currency or chip amount that may include commas or a $ prefix."""
        cleaned = re.sub(r"[^\d.]", "", value or "")
        return float(cleaned) if cleaned else 0.0

    def _parse_streets_acr(self, lines: List[str], hero: str) -> List[Dict[str, Any]]:
        """Parse streets and actions from ACR/BetACR format."""
        current_street = {"name": "Preflop", "cards": [], "actions": []}
        streets = [current_street]
        in_actions = True
        player_names = set()
        for line in lines:
            sm = re.match(r"Seat \d+: (.+?) \(\$?", line.strip())
            if sm:
                player_names.add(sm.group(1))

        for line in lines:
            stripped = line.strip()
            if stripped.startswith("*** HOLE CARDS ***"):
                continue
            if stripped.startswith("*** FLOP ***"):
                cards_m = re.search(r"\[(.+?)\]", stripped)
                cards = cards_m.group(1).split() if cards_m else []
                current_street = {"name": "Flop", "cards": cards, "actions": []}
                streets.append(current_street)
                in_actions = True
                continue
            if stripped.startswith("*** TURN ***"):
                cards_m = re.findall(r"\[(.+?)\]", stripped)
                cards = cards_m[-1].split() if cards_m else []
                current_street = {"name": "Turn", "cards": cards, "actions": []}
                streets.append(current_street)
                in_actions = True
                continue
            if stripped.startswith("*** RIVER ***"):
                cards_m = re.findall(r"\[(.+?)\]", stripped)
                cards = cards_m[-1].split() if cards_m else []
                current_street = {"name": "River", "cards": cards, "actions": []}
                streets.append(current_street)
                in_actions = True
                continue
            if stripped.startswith("*** SHOW DOWN ***") or stripped.startswith("*** SUMMARY ***"):
                in_actions = False
                continue
            if in_actions and current_street is not None:
                if stripped.startswith("Dealt to"):
                    continue
                for pname in player_names:
                    if stripped.startswith(pname + " "):
                        rest = stripped[len(pname) + 1:]
                        action, amount = self._parse_action(rest)
                        if action:
                            current_street["actions"].append(
                                {"player": pname, "action": action, "amount": amount}
                            )
                        break
                else:
                    parts = stripped.split(" ", 1)
                    if len(parts) == 2:
                        action, amount = self._parse_action(parts[1])
                        if action:
                            current_street["actions"].append(
                                {"player": parts[0], "action": action, "amount": amount}
                            )
        return streets

    @staticmethod
    def _parse_action(action_str: str) -> tuple[Optional[str], float]:
        """Parse an action string into action type and amount."""
        def _find_amount(pattern: str = r"(\d[\d,]*(?:\.\d+)?)") -> float:
            match = re.search(pattern, action_str)
            return HandParser._parse_amount(match.group(1)) if match else 0.0

        action_str = action_str.strip().lower()
        if action_str.startswith("fold"):
            return "fold", 0.0
        if action_str.startswith("check"):
            return "check", 0.0
        if action_str.startswith("call"):
            return "call", _find_amount()
        if action_str.startswith("raise"):
            amount = _find_amount(r"to (\d[\d,]*(?:\.\d+)?)")
            return "raise", amount if amount else _find_amount()
        if action_str.startswith("bet"):
            return "bet", _find_amount()
        if "all-in" in action_str or "allin" in action_str:
            return "raise", _find_amount()
        if action_str.startswith("posts"):
            return "post", _find_amount()
        return None, 0.0

    @staticmethod
    def _extract_board(text: str) -> List[str]:
        """Extract community cards from hand text."""
        m = re.search(r"Board \[(.+?)\]", text)
        if m:
            return m.group(1).split()
        return []

    @staticmethod
    def _collect_board_from_streets(streets: List[Dict[str, Any]]) -> List[str]:
        """Assemble a full board when the hand history omits a summary board line."""
        board: List[str] = []
        for street in streets:
            board.extend(street.get("cards", []))
        return board

    @staticmethod
    def _calc_hero_result(h: Hand, hero: str) -> float:
        """Calculate hero's net result in the hand."""
        won: float = 0.0
        for w in h.winners:
            if w.get("name") == hero:
                won += float(w.get("amount", 0.0))

        # Credit uncalled bet returned to hero
        raw = getattr(h, "raw_text", "") or ""
        if raw and hero:
            for ub in re.finditer(
                r"Uncalled bet \(\$?(\d+(?:\.\d+)?)\) returned to "
                + re.escape(hero),
                raw,
            ):
                won += float(ub.group(1))

        invested: float = 0.0
        preflop_raised = False
        for street in h.streets:
            hero_acts = [
                (act.get("action", ""), float(act.get("amount", 0.0)))
                for act in street.get("actions", [])
                if act.get("player") == hero
            ]
            last_raise_idx: Optional[int] = None
            for i, (a, _) in enumerate(hero_acts):
                if a == "raise":
                    last_raise_idx = i
            if last_raise_idx is not None:
                if street.get("name") == "Preflop":
                    preflop_raised = True
                street_total = hero_acts[last_raise_idx][1]
                for a, amt in hero_acts[last_raise_idx + 1:]:
                    if a in ("call", "bet"):
                        street_total += amt
            else:
                street_total = sum(
                    amt for a, amt in hero_acts if a in ("call", "bet", "post")
                )
            invested += street_total

        if preflop_raised and raw and hero:
            for ante in re.finditer(
                re.escape(hero) + r" posts ante (\d+(?:\.\d+)?)",
                raw,
                re.IGNORECASE,
            ):
                invested += float(ante.group(1))

        if won > 0:
            return won - invested
        if won == 0 and invested == 0:
            return 0.0
        return -invested if invested > 0 else 0.0

    @staticmethod
    def _calc_position(h: Hand, hero: str) -> str:
        """Calculate hero's position at the table."""
        hero_seat = None
        for seat, info in h.players.items():
            if info["name"] == hero:
                hero_seat = seat
                break
        if hero_seat is None:
            return "?"
        if hero_seat == h.button_seat:
            return "BTN"
        seats_sorted = sorted(h.players.keys())
        n = len(seats_sorted)
        if n <= 1:
            return "?"
        btn_idx = seats_sorted.index(h.button_seat) if h.button_seat in seats_sorted else 0
        sb_idx = (btn_idx + 1) % n
        bb_idx = (btn_idx + 2) % n
        if seats_sorted[sb_idx] == hero_seat:
            return "SB"
        if seats_sorted[bb_idx] == hero_seat:
            return "BB"
        hero_idx = seats_sorted.index(hero_seat)
        dist = (hero_idx - btn_idx) % n
        if n <= 4:
            return "CO"
        if dist == n - 1:
            return "CO"
        if dist <= n // 2:
            return "EP"
        return "MP"

    def _parse_pokerstars(self, text: str) -> Optional[Hand]:
        """Parse PokerStars hand history format."""
        h = Hand()
        h.site = "PokerStars"
        lines = text.split("\n")
        hero = self.settings.get("hero_names", {}).get("PokerStars", "")

        header = lines[0] if lines else ""
        m = re.search(r"PokerStars (?:Hand|Game) #(\d+)", header)
        if not m:
            return None
        h.hand_id = f"PS_{m.group(1)}"

        if "Tournament" in header:
            h.is_tournament = True
            tm = re.search(r"Tournament #(\d+)", header)
            if tm:
                h.tournament_id = tm.group(1)

        dm = re.search(r"(\d{4}/\d{2}/\d{2} \d{1,2}:\d{2}:\d{2})\s*([A-Z]{2,4})?", header)
        if dm:
            h.date = _parse_hand_datetime(dm.group(1), dm.group(2) or "",
                                          default_tz_key="ET")
        else:
            h.date = datetime.now()

        h.game_type = "NLHE"
        if "Omaha" in header:
            h.game_type = "PLO"

        table_line = next((l for l in lines if l.startswith("Table '")), "")
        tm2 = re.search(r"Table '([^']+)'", table_line)
        if tm2:
            h.table_name = tm2.group(1)
        sm = re.search(r"(\d+)-max", table_line)
        if sm:
            h.max_seats = int(sm.group(1))
        bm = re.search(r"Seat #(\d+) is the button", table_line)
        if bm:
            h.button_seat = int(bm.group(1))

        for line in lines:
            seat_m = re.match(r"Seat (\d+): (.+?) \((\d+(?:\.\d+)?) in chips\)", line.strip())
            if seat_m:
                seat_num = int(seat_m.group(1))
                name = seat_m.group(2)
                stack = float(seat_m.group(3))
                h.players[seat_num] = {"name": name, "stack": stack, "is_hero": name == hero}

        if hero:
            hc = re.search(r"Dealt to " + re.escape(hero) + r" \[(.+?)\]", text)
            if hc:
                h.hero_cards = hc.group(1)

        h.board_cards = self._extract_board(text)
        h.streets = self._parse_streets_generic(lines, hero, "PokerStars")

        pot_m = re.search(r"Total pot (\d+(?:\.\d+)?)", text)
        if pot_m:
            h.pot = float(pot_m.group(1))
        rake_m = re.search(r"Rake \$?(\d+(?:\.\d+)?)", text)
        if rake_m:
            h.rake = float(rake_m.group(1))

        for line in lines:
            wm = re.match(r"(.+?) collected \$?(\d+(?:\.\d+)?) from", line.strip())
            if wm:
                h.winners.append({"name": wm.group(1), "amount": float(wm.group(2))})

        h.hero_won = self._calc_hero_result(h, hero)
        h.hero_position = self._calc_position(h, hero)
        return h

    def _parse_888poker(self, text: str) -> Optional[Hand]:
        """Parse 888poker hand history format."""
        h = Hand()
        h.site = "888poker"
        lines = text.split("\n")
        hero = self.settings.get("hero_names", {}).get("888poker", "")

        m = re.search(r"#Game No\s*:\s*(\d+)", text)
        if not m:
            return None
        h.hand_id = f"888_{m.group(1)}"

        dm = re.search(r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\s*([A-Z]{2,4})?", text)
        if dm:
            h.date = _parse_hand_datetime(dm.group(1), dm.group(2) or "")
        else:
            h.date = datetime.now()

        h.game_type = "NLHE"
        if "Omaha" in text:
            h.game_type = "PLO"

        table_m = re.search(r"Table:\s*(.+?)(?:\n|$)", text)
        if table_m:
            h.table_name = table_m.group(1).strip()

        seats_m = re.search(r"Total number of players\s*:\s*(\d+)", text)
        if seats_m:
            h.max_seats = int(seats_m.group(1))

        for line in lines:
            seat_m = re.match(r"Seat (\d+): (.+?) \(\s*(\d+(?:\.\d+)?)\s*\)", line.strip())
            if seat_m:
                seat_num = int(seat_m.group(1))
                name = seat_m.group(2).strip()
                stack = float(seat_m.group(3))
                h.players[seat_num] = {"name": name, "stack": stack, "is_hero": name == hero}

        if hero:
            hc = re.search(r"Player:\s*" + re.escape(hero) + r"[^\n]*Cards:\s*\[(.+?)\]", text)
            if hc:
                h.hero_cards = hc.group(1)

        h.board_cards = self._extract_board(text)
        h.streets = self._parse_streets_generic(lines, hero, "888poker")

        pot_m = re.search(r"Total pot\s*(\d+(?:\.\d+)?)", text)
        if pot_m:
            h.pot = float(pot_m.group(1))

        for line in lines:
            wm = re.match(r"(.+?) collected\s+(\d+(?:\.\d+)?)", line.strip())
            if wm:
                h.winners.append({"name": wm.group(1).strip(), "amount": float(wm.group(2))})

        h.hero_won = self._calc_hero_result(h, hero)
        h.hero_position = self._calc_position(h, hero)
        return h

    def _parse_ignition(self, text: str) -> Optional[Hand]:
        """Parse Ignition/Bovada hand history format (anonymous tables)."""
        h = Hand()
        h.site = "Ignition"
        lines = text.split("\n")
        hero = self.settings.get("hero_names", {}).get("Ignition", "")

        m = re.search(r"(?:Ignition|Bovada) Hand #(\w+)", text)
        if not m:
            return None
        h.hand_id = f"IGN_{m.group(1)}"

        if "Tournament" in text:
            h.is_tournament = True
            tm = re.search(r"TournamentId: #(\d+)", text)
            if tm:
                h.tournament_id = tm.group(1)

        dm = re.search(r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\s*([A-Z]{2,4})?", text)
        if dm:
            h.date = _parse_hand_datetime(dm.group(1), dm.group(2) or "")
        else:
            h.date = datetime.now()

        h.game_type = "NLHE"
        if "Omaha" in text:
            h.game_type = "PLO"

        table_m = re.search(r"Table: (.+?)(?:\n|$)", text)
        if table_m:
            h.table_name = table_m.group(1).strip()
        sm = re.search(r"(\d+)-max", text)
        if sm:
            h.max_seats = int(sm.group(1))

        for line in lines:
            # Ignition uses [ME] to mark the hero
            seat_m = re.match(r"Seat (\d+): (.+?) \((\d+(?:\.\d+)?)\)", line.strip())
            if seat_m:
                seat_num = int(seat_m.group(1))
                name = seat_m.group(2).strip()
                stack = float(seat_m.group(3))
                is_hero = "[ME]" in name or name == hero
                clean_name = name.replace("[ME]", "").strip()
                h.players[seat_num] = {"name": clean_name, "stack": stack, "is_hero": is_hero}

        # Hero cards shown in Ignition format
        hc = re.search(r"\[ME\][^\n]*\[(.+?)\]", text)
        if hc:
            h.hero_cards = hc.group(1)

        h.board_cards = self._extract_board(text)
        h.streets = self._parse_streets_generic(lines, hero, "Ignition")

        pot_m = re.search(r"Total pot\s+(\d+(?:\.\d+)?)", text)
        if pot_m:
            h.pot = float(pot_m.group(1))
        rake_m = re.search(r"Rake\s+(\d+(?:\.\d+)?)", text)
        if rake_m:
            h.rake = float(rake_m.group(1))

        for line in lines:
            wm = re.match(r"(.+?) wins\s+\$?(\d+(?:\.\d+)?)", line.strip())
            if wm:
                h.winners.append({"name": wm.group(1).strip(), "amount": float(wm.group(2))})

        h.hero_won = self._calc_hero_result(h, hero)
        h.hero_position = self._calc_position(h, hero)
        return h

    def _parse_streets_generic(self, lines: List[str], hero: str, site: str) -> List[Dict[str, Any]]:
        """Generic street parser for sites with *** STREET *** format."""
        current_street: Dict[str, Any] = {"name": "Preflop", "cards": [], "actions": []}
        streets = [current_street]
        for line in lines:
            stripped = line.strip()
            if stripped.startswith("*** FLOP ***") or stripped.startswith("** Dealing flop"):
                cards_m = re.search(r"\[(.+?)\]", stripped)
                cards = cards_m.group(1).split() if cards_m else []
                current_street = {"name": "Flop", "cards": cards, "actions": []}
                streets.append(current_street)
            elif stripped.startswith("*** TURN ***") or stripped.startswith("** Dealing turn"):
                cards_m = re.findall(r"\[(.+?)\]", stripped)
                cards = cards_m[-1].split() if cards_m else []
                current_street = {"name": "Turn", "cards": cards, "actions": []}
                streets.append(current_street)
            elif stripped.startswith("*** RIVER ***") or stripped.startswith("** Dealing river"):
                cards_m = re.findall(r"\[(.+?)\]", stripped)
                cards = cards_m[-1].split() if cards_m else []
                current_street = {"name": "River", "cards": cards, "actions": []}
                streets.append(current_street)
            else:
                am = re.match(r"(.+?): (folds|checks|calls|bets|raises)(?: \$?(\d+(?:\.\d+)?))?", stripped)
                if am:
                    try:
                        amt = float(am.group(3) or 0)
                    except (ValueError, TypeError):
                        amt = 0.0
                    current_street["actions"].append({
                        "player": am.group(1), "action": am.group(2), "amount": amt
                    })
        return streets
