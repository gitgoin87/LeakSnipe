"""
Hand parsing logic for multiple poker sites.
Supports CoinPoker, BetACR (WPN), GGPoker, ClubGG, PokerStars, 888poker,
and Ignition/Bovada hand history formats.
"""

import re
import logging
from typing import Optional, Dict, List, Any
from datetime import datetime

from models import Hand


class HandParser:
    """Parses hand history text from various poker sites into Hand objects."""

    def __init__(self, settings: Dict[str, Any]):
        self.settings = settings

    def detect_site(self, text: str) -> Optional[str]:
        """Detect which poker site the hand is from based on text content."""
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
        return None

    def split_hands(self, text: str, site: str) -> List[str]:
        """Split raw text into individual hand texts."""
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

        # Fallback: Try to detect format from content
        if "CoinPoker Hand #" in text:
            return self._parse_coinpoker(text)
        if "Game Hand #" in text:
            return self._parse_acr(text, site_label="BetACR")

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
        dm = re.search(r"(\d{4}/\d{2}/\d{2} \d{2}:\d{2}:\d{2})", header)
        if dm:
            try:
                h.date = datetime.strptime(dm.group(1), "%Y/%m/%d %H:%M:%S")
            except ValueError:
                h.date = datetime.now()
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
        lines = text.split("\n")
        hero_names = self.settings.get("hero_names", {})
        hero = hero_names.get(site_label) or hero_names.get("BetACR", "JohnDaWalka")

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
        dm = re.search(r"(\d{4}/\d{2}/\d{2} \d{2}:\d{2}:\d{2})", header)
        if dm:
            try:
                h.date = datetime.strptime(dm.group(1), "%Y/%m/%d %H:%M:%S")
            except ValueError:
                h.date = datetime.now()
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
            wm = re.match(r"(.+?) collected \$?(\d+(?:\.\d+)?) from", line.strip())
            if wm:
                h.winners.append({"name": wm.group(1), "amount": float(wm.group(2))})

        h.hero_won = self._calc_hero_result(h, hero)
        h.hero_position = self._calc_position(h, hero)
        return h

    def _parse_ggpoker(self, text: str) -> Optional[Hand]:
        """Stub GGPoker parser — returns None until full implementation is added."""
        return None

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
        action_str = action_str.strip().lower()
        if action_str.startswith("fold"):
            return "fold", 0.0
        if action_str.startswith("check"):
            return "check", 0.0
        if action_str.startswith("call"):
            am = re.search(r"(\d+(?:\.\d+)?)", action_str)
            return "call", float(am.group(1)) if am else 0.0
        if action_str.startswith("raise"):
            am = re.search(r"to (\d+(?:\.\d+)?)", action_str)
            if am:
                return "raise", float(am.group(1))
            am = re.search(r"(\d+(?:\.\d+)?)", action_str)
            return "raise", float(am.group(1)) if am else 0.0
        if action_str.startswith("bet"):
            am = re.search(r"(\d+(?:\.\d+)?)", action_str)
            return "bet", float(am.group(1)) if am else 0.0
        if "all-in" in action_str or "allin" in action_str:
            am = re.search(r"(\d+(?:\.\d+)?)", action_str)
            return "raise", float(am.group(1)) if am else 0.0
        if action_str.startswith("posts"):
            am = re.search(r"(\d+(?:\.\d+)?)", action_str)
            return "post", float(am.group(1)) if am else 0.0
        return None, 0.0

    @staticmethod
    def _extract_board(text: str) -> List[str]:
        """Extract community cards from hand text."""
        m = re.search(r"Board \[(.+?)\]", text)
        if m:
            return m.group(1).split()
        return []

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
            ub = re.search(
                r"Uncalled bet \(\$?(\d+(?:\.\d+)?)\) returned to "
                + re.escape(hero),
                raw,
            )
            if ub:
                won += float(ub.group(1))

        invested: float = 0.0
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
                street_total = hero_acts[last_raise_idx][1]
                for a, amt in hero_acts[last_raise_idx + 1:]:
                    if a in ("call", "bet"):
                        street_total += amt
            else:
                street_total = sum(
                    amt for a, amt in hero_acts if a in ("call", "bet", "post")
                )
            invested += street_total

        if won > 0:
            return won - invested
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

        dm = re.search(r"(\d{4}/\d{2}/\d{2} \d{1,2}:\d{2}:\d{2})", header)
        if dm:
            try:
                h.date = datetime.strptime(dm.group(1), "%Y/%m/%d %H:%M:%S")
            except ValueError:
                h.date = datetime.now()
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

        dm = re.search(r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})", text)
        if dm:
            try:
                h.date = datetime.strptime(dm.group(1), "%Y-%m-%d %H:%M:%S")
            except ValueError:
                h.date = datetime.now()
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

        dm = re.search(r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})", text)
        if dm:
            try:
                h.date = datetime.strptime(dm.group(1), "%Y-%m-%d %H:%M:%S")
            except ValueError:
                h.date = datetime.now()
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
