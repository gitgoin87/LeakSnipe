"""
Data models for poker hand tracking.
Defines Hand and HandDatabase classes for storing and retrieving hand data.
"""

import os
import sqlite3
import threading
from typing import Optional, Dict, List, Any
from datetime import datetime
from collections import defaultdict, OrderedDict
import logging


class Hand:
    """Represents a single poker hand with players, actions, and results."""

    def __init__(self):
        self.hand_id: str = ""
        self.site: str = ""
        self.date: Optional[datetime] = None
        self.game_type: str = ""
        self.is_tournament: bool = False
        self.tournament_id: str = ""
        self.buy_in: str = ""
        self.table_name: str = ""
        self.max_seats: int = 0
        self.button_seat: int = 0
        self.players: Dict[int, Dict[str, Any]] = {}
        self.hero_cards: str = ""
        self.board_cards: List[str] = []
        self.streets: List[Dict[str, Any]] = []
        self.pot: float = 0.0
        self.rake: float = 0.0
        self.winners: List[Dict[str, Any]] = []
        self.hero_won: float = 0.0
        self.hero_position: str = ""
        self.hero_player: str = ""
        self.raw_text: str = ""
        self.tags: List[str] = []

    def hero_name(self, settings: Dict[str, Any]) -> str:
        """Get hero player name from settings for this hand's site."""
        from utils import resolve_hand_hero_name
        return resolve_hand_hero_name(
            settings,
            self.site,
            players=self.players,
            raw_text=self.raw_text,
            hero_player=getattr(self, "hero_player", ""),
        )


class HandDatabase:
    """SQLite database for storing poker hands and related data."""

    def __init__(self, db_path: str):
        self.db_path = db_path
        self.lock = threading.Lock()
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        """Create a new database connection."""
        return sqlite3.connect(self.db_path, timeout=10)

    def _init_db(self) -> None:
        """Initialize database schema if tables don't exist."""
        with self.lock:
            conn = self._connect()
            try:
                c = conn.cursor()
                c.executescript("""
                    CREATE TABLE IF NOT EXISTS hands (
                        hand_id TEXT PRIMARY KEY,
                        site TEXT NOT NULL,
                        hand_number TEXT,
                        date TEXT,
                        game_type TEXT,
                        is_tournament INTEGER DEFAULT 0,
                        tournament_id TEXT,
                        buy_in TEXT,
                        table_name TEXT,
                        max_seats INTEGER DEFAULT 0,
                        button_seat INTEGER DEFAULT 0,
                        hero_cards TEXT,
                        board_cards TEXT,
                        pot REAL DEFAULT 0,
                        rake REAL DEFAULT 0,
                        hero_won REAL DEFAULT 0,
                        hero_position TEXT,
                        raw_text TEXT,
                        source_file TEXT,
                        imported_at TEXT
                    );
                    CREATE TABLE IF NOT EXISTS players (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        hand_id TEXT NOT NULL,
                        seat INTEGER,
                        name TEXT,
                        stack REAL DEFAULT 0,
                        is_hero INTEGER DEFAULT 0,
                        FOREIGN KEY (hand_id) REFERENCES hands(hand_id)
                    );
                    CREATE TABLE IF NOT EXISTS actions (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        hand_id TEXT NOT NULL,
                        street TEXT,
                        sequence INTEGER,
                        player TEXT,
                        action TEXT,
                        amount REAL DEFAULT 0,
                        FOREIGN KEY (hand_id) REFERENCES hands(hand_id)
                    );
                    CREATE TABLE IF NOT EXISTS winners (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        hand_id TEXT NOT NULL,
                        player_name TEXT,
                        amount REAL DEFAULT 0,
                        FOREIGN KEY (hand_id) REFERENCES hands(hand_id)
                    );
                    CREATE TABLE IF NOT EXISTS ocr_imports (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        image_path TEXT,
                        ocr_text TEXT,
                        parsed_cards TEXT,
                        parsed_pot REAL,
                        parsed_bets TEXT,
                        parsed_blinds TEXT,
                        notes TEXT,
                        hand_id TEXT,
                        created_at TEXT,
                        FOREIGN KEY (hand_id) REFERENCES hands(hand_id)
                    );
                    CREATE TABLE IF NOT EXISTS hand_tags (
                        hand_id TEXT NOT NULL,
                        tag TEXT NOT NULL,
                        created_at TEXT,
                        PRIMARY KEY (hand_id, tag),
                        FOREIGN KEY (hand_id) REFERENCES hands(hand_id)
                    );
                    CREATE TABLE IF NOT EXISTS player_types (
                        name TEXT PRIMARY KEY,
                        site TEXT DEFAULT '',
                        auto_type TEXT DEFAULT 'Unknown',
                        manual_type TEXT DEFAULT '',
                        hands INTEGER DEFAULT 0,
                        vpip REAL DEFAULT 0,
                        pfr REAL DEFAULT 0,
                        af REAL DEFAULT 0,
                        fold_cbet REAL DEFAULT 0,
                        wtsd REAL DEFAULT 0,
                        updated_at TEXT
                    );
                    CREATE INDEX IF NOT EXISTS idx_hands_date ON hands(date DESC);
                    CREATE INDEX IF NOT EXISTS idx_players_hand_id ON players(hand_id);
                    CREATE INDEX IF NOT EXISTS idx_actions_hand_id_seq ON actions(hand_id, sequence);
                    CREATE INDEX IF NOT EXISTS idx_winners_hand_id ON winners(hand_id);
                    CREATE INDEX IF NOT EXISTS idx_hand_tags_tag ON hand_tags(tag);
                    CREATE INDEX IF NOT EXISTS idx_hand_tags_hand_id ON hand_tags(hand_id);
                """)
                conn.commit()
            finally:
                conn.close()

    @staticmethod
    def _group_rows_by_hand(rows: List, key_name: str = "hand_id") -> Dict:
        """Group database rows by hand_id."""
        grouped = defaultdict(list)
        for row in rows:
            grouped[row[key_name]].append(row)
        return grouped

    def hand_exists(self, hand_id: str) -> bool:
        """Check if a hand exists in the database."""
        with self.lock:
            conn = self._connect()
            try:
                row = conn.execute(
                    "SELECT 1 FROM hands WHERE hand_id = ?", (hand_id,)
                ).fetchone()
                return row is not None
            finally:
                conn.close()

    def hand_needs_hero_backfill(self, hand_id: str) -> bool:
        """True when stored hero cards or position are missing."""
        with self.lock:
            conn = self._connect()
            try:
                row = conn.execute(
                    "SELECT hero_cards, hero_position FROM hands WHERE hand_id = ?",
                    (hand_id,),
                ).fetchone()
                if not row:
                    return False
                cards = (row[0] or "").strip()
                position = (row[1] or "").strip()
                return not cards or not position or position == "?"
            finally:
                conn.close()

    @staticmethod
    def hand_has_hero_fields(hand: Hand) -> bool:
        """True when a parsed hand has usable hero cards and position."""
        cards = (hand.hero_cards or "").strip()
        position = (hand.hero_position or "").strip()
        return bool(cards) and bool(position) and position != "?"

    def save_hand(self, hand: Hand, source_file: str = "") -> None:
        """Save a hand to the database."""
        with self.lock:
            conn = self._connect()
            try:
                c = conn.cursor()
                date_str = hand.date.isoformat() if hand.date else None
                board_str = " ".join(hand.board_cards) if hand.board_cards else ""
                c.execute("""
                    INSERT OR REPLACE INTO hands
                    (hand_id, site, hand_number, date, game_type, is_tournament,
                     tournament_id, buy_in, table_name, max_seats, button_seat,
                     hero_cards, board_cards, pot, rake, hero_won, hero_position,
                     raw_text, source_file, imported_at)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """, (
                    hand.hand_id, hand.site,
                    hand.hand_id.split("_", 1)[-1] if "_" in hand.hand_id else hand.hand_id,
                    date_str, hand.game_type,
                    1 if hand.is_tournament else 0,
                    hand.tournament_id, hand.buy_in, hand.table_name,
                    hand.max_seats, hand.button_seat, hand.hero_cards,
                    board_str, hand.pot, hand.rake, hand.hero_won,
                    hand.hero_position, hand.raw_text, source_file,
                    datetime.now().isoformat(),
                ))
                # Save players
                c.execute("DELETE FROM players WHERE hand_id = ?", (hand.hand_id,))
                for seat, info in hand.players.items():
                    c.execute(
                        "INSERT INTO players (hand_id, seat, name, stack, is_hero) VALUES (?,?,?,?,?)",
                        (hand.hand_id, seat, info["name"], info["stack"],
                         1 if info.get("is_hero") else 0),
                    )
                # Save actions
                c.execute("DELETE FROM actions WHERE hand_id = ?", (hand.hand_id,))
                seq: int = 0
                for street in hand.streets:
                    for act in street.get("actions", []):
                        c.execute(
                            "INSERT INTO actions (hand_id, street, sequence, player, action, amount) "
                            "VALUES (?,?,?,?,?,?)",
                            (hand.hand_id, street["name"], seq,
                             act["player"], act["action"], act["amount"]),
                        )
                        seq += 1
                # Save winners
                c.execute("DELETE FROM winners WHERE hand_id = ?", (hand.hand_id,))
                for w in hand.winners:
                    c.execute(
                        "INSERT INTO winners (hand_id, player_name, amount) VALUES (?,?,?)",
                        (hand.hand_id, w["name"], w["amount"]),
                    )
                conn.commit()
            finally:
                conn.close()

    def reparse_hands_missing_hero(self, parser: "HandParser") -> int:
        """Re-parse stored raw text for hands missing hero cards or position."""
        with self.lock:
            conn = self._connect()
            try:
                conn.row_factory = sqlite3.Row
                rows = conn.execute(
                    "SELECT hand_id, site, raw_text, source_file FROM hands "
                    "WHERE raw_text != '' AND (hero_cards IS NULL OR hero_cards = '' "
                    "OR hero_position IS NULL OR hero_position = '' OR hero_position = '?')"
                ).fetchall()
            finally:
                conn.close()

        updated = 0
        for row in rows:
            site = row["site"] or "BetACR"
            hand = parser._parse_single(row["raw_text"], site)
            if hand and hand.hand_id:
                self.save_hand(hand, source_file=row["source_file"] or "")
                updated += 1
        return updated

    @staticmethod
    def _hydrate_hand(
        row: sqlite3.Row,
        players_by_hand: Dict,
        actions_by_hand: Dict,
        winners_by_hand: Dict,
        tags_by_hand: Dict,
    ) -> Hand:
        """Build a Hand object from a hands row and related grouped rows."""
        h = Hand()
        h.hand_id = row["hand_id"]
        h.site = row["site"] or ""
        if row["date"]:
            try:
                h.date = datetime.fromisoformat(row["date"])
            except (ValueError, TypeError):
                h.date = datetime.now()
        else:
            h.date = datetime.now()
        h.game_type = row["game_type"] or ""
        h.is_tournament = bool(row["is_tournament"])
        h.tournament_id = row["tournament_id"] or ""
        h.buy_in = row["buy_in"] or ""
        h.table_name = row["table_name"] or ""
        h.max_seats = row["max_seats"] or 0
        h.button_seat = row["button_seat"] or 0
        h.hero_cards = row["hero_cards"] or ""
        h.board_cards = row["board_cards"].split() if row["board_cards"] else []
        h.pot = row["pot"] or 0.0
        h.rake = row["rake"] or 0.0
        h.hero_won = row["hero_won"] or 0.0
        h.hero_position = row["hero_position"] or ""
        h.raw_text = row["raw_text"] or ""
        h.tags = list(tags_by_hand.get(h.hand_id, []))

        for pr in players_by_hand.get(h.hand_id, []):
            h.players[pr["seat"]] = {
                "name": pr["name"],
                "stack": pr["stack"],
                "is_hero": bool(pr["is_hero"]),
            }
            if pr["is_hero"]:
                h.hero_player = pr["name"]

        streets_map = OrderedDict()
        for ar in actions_by_hand.get(h.hand_id, []):
            sname = ar["street"]
            if sname not in streets_map:
                streets_map[sname] = {"name": sname, "cards": [], "actions": []}
            streets_map[sname]["actions"].append({
                "player": ar["player"],
                "action": ar["action"],
                "amount": ar["amount"],
            })
        bc = h.board_cards
        _street_order = ["Preflop", "Flop", "Turn", "River"]
        if len(bc) >= 3:
            if "Flop" not in streets_map:
                streets_map["Flop"] = {"name": "Flop", "cards": [], "actions": []}
            streets_map["Flop"]["cards"] = bc[:3]
        if len(bc) >= 4:
            if "Turn" not in streets_map:
                streets_map["Turn"] = {"name": "Turn", "cards": [], "actions": []}
            streets_map["Turn"]["cards"] = [bc[3]]
        if len(bc) >= 5:
            if "River" not in streets_map:
                streets_map["River"] = {"name": "River", "cards": [], "actions": []}
            streets_map["River"]["cards"] = [bc[4]]
        sorted_map = OrderedDict()
        for _sn in _street_order:
            if _sn in streets_map:
                sorted_map[_sn] = streets_map[_sn]
        for _sn in streets_map:
            if _sn not in sorted_map:
                sorted_map[_sn] = streets_map[_sn]
        h.streets = list(sorted_map.values())

        h.winners = [
            {"name": wr["player_name"], "amount": wr["amount"]}
            for wr in winners_by_hand.get(h.hand_id, [])
        ]
        return h

    def _load_related_for_ids(self, c: sqlite3.Cursor, hand_ids: List[str]):
        """Load players, actions, winners, tags grouped by hand_id for given IDs."""
        if not hand_ids:
            return {}, {}, {}, defaultdict(list)
        placeholders = ",".join("?" * len(hand_ids))
        players_by_hand = self._group_rows_by_hand(
            c.execute(
                f"SELECT hand_id, seat, name, stack, is_hero FROM players "
                f"WHERE hand_id IN ({placeholders}) ORDER BY hand_id, seat",
                hand_ids,
            ).fetchall()
        )
        actions_by_hand = self._group_rows_by_hand(
            c.execute(
                f"SELECT hand_id, street, player, action, amount, sequence "
                f"FROM actions WHERE hand_id IN ({placeholders}) ORDER BY hand_id, sequence",
                hand_ids,
            ).fetchall()
        )
        winners_by_hand = self._group_rows_by_hand(
            c.execute(
                f"SELECT hand_id, player_name, amount FROM winners "
                f"WHERE hand_id IN ({placeholders}) ORDER BY hand_id",
                hand_ids,
            ).fetchall()
        )
        tags_by_hand: Dict[str, List[str]] = defaultdict(list)
        for tag_row in c.execute(
            f"SELECT hand_id, tag FROM hand_tags WHERE hand_id IN ({placeholders}) "
            f"ORDER BY hand_id, tag",
            hand_ids,
        ).fetchall():
            tags_by_hand[tag_row["hand_id"]].append(tag_row["tag"])
        return players_by_hand, actions_by_hand, winners_by_hand, tags_by_hand

    def count_hands(self) -> int:
        """Total number of hands in the database."""
        with self.lock:
            conn = self._connect()
            try:
                row = conn.execute("SELECT COUNT(*) FROM hands").fetchone()
                return int(row[0]) if row else 0
            finally:
                conn.close()

    def get_hand_by_id(self, hand_id: str) -> Optional[Hand]:
        """Load a single hand by ID (fast path for replayer/detail)."""
        with self.lock:
            conn = self._connect()
            try:
                conn.row_factory = sqlite3.Row
                c = conn.cursor()
                row = c.execute(
                    "SELECT * FROM hands WHERE hand_id = ?", (hand_id,)
                ).fetchone()
                if not row:
                    return None
                players_by_hand, actions_by_hand, winners_by_hand, tags_by_hand = (
                    self._load_related_for_ids(c, [hand_id])
                )
                return self._hydrate_hand(
                    row, players_by_hand, actions_by_hand, winners_by_hand, tags_by_hand
                )
            finally:
                conn.close()

    def get_hands_page(self, limit: int, offset: int = 0) -> List[Hand]:
        """Load a page of hands ordered by date (does not load entire DB)."""
        with self.lock:
            conn = self._connect()
            try:
                conn.row_factory = sqlite3.Row
                c = conn.cursor()
                rows = c.execute(
                    "SELECT * FROM hands ORDER BY date DESC LIMIT ? OFFSET ?",
                    (limit, offset),
                ).fetchall()
                if not rows:
                    return []
                hand_ids = [row["hand_id"] for row in rows]
                players_by_hand, actions_by_hand, winners_by_hand, tags_by_hand = (
                    self._load_related_for_ids(c, hand_ids)
                )
                return [
                    self._hydrate_hand(
                        row, players_by_hand, actions_by_hand, winners_by_hand, tags_by_hand
                    )
                    for row in rows
                ]
            finally:
                conn.close()

    def get_all_hands(self) -> List[Hand]:
        """Retrieve all hands from the database."""
        with self.lock:
            conn = self._connect()
            try:
                conn.row_factory = sqlite3.Row
                c = conn.cursor()
                rows = c.execute("SELECT * FROM hands ORDER BY date DESC").fetchall()
                if not rows:
                    return []

                hand_ids = [row["hand_id"] for row in rows]
                players_by_hand, actions_by_hand, winners_by_hand, tags_by_hand = (
                    self._load_related_for_ids(c, hand_ids)
                )

                return [
                    self._hydrate_hand(
                        row, players_by_hand, actions_by_hand, winners_by_hand, tags_by_hand
                    )
                    for row in rows
                ]
            finally:
                conn.close()

    def get_hand_count(self) -> Dict[str, int]:
        """Get count of hands per site."""
        with self.lock:
            conn = self._connect()
            try:
                rows = conn.execute(
                    "SELECT site, COUNT(*) as cnt FROM hands GROUP BY site"
                ).fetchall()
                return {r[0]: r[1] for r in rows}
            finally:
                conn.close()

    def save_ocr_import(self, image_path: str, ocr_text: str, elements: Dict, notes: str = "") -> None:
        """Save OCR import data to database."""
        with self.lock:
            conn = self._connect()
            try:
                cards_str = " ".join(elements.get("cards", []))
                pot_val = elements.get("pot") or 0.0
                bets_str = ",".join(str(b) for b in elements.get("bets", []))
                blinds_str = elements.get("blinds") or ""
                conn.execute(
                    "INSERT INTO ocr_imports (image_path, ocr_text, parsed_cards, "
                    "parsed_pot, parsed_bets, parsed_blinds, notes, created_at) "
                    "VALUES (?,?,?,?,?,?,?,?)",
                    (image_path, ocr_text, cards_str, pot_val, bets_str,
                     blinds_str, notes, datetime.now().isoformat()),
                )
                conn.commit()
            finally:
                conn.close()

    def save_ocr_as_hand(self, ocr_id: int, hand: Hand) -> None:
        """Convert OCR import to hand and link them."""
        self.save_hand(hand, source_file="OCR Import")
        with self.lock:
            conn = self._connect()
            try:
                conn.execute(
                    "UPDATE ocr_imports SET hand_id = ? WHERE id = ?",
                    (hand.hand_id, ocr_id),
                )
                conn.commit()
            finally:
                conn.close()

    def get_ocr_imports(self) -> List[Dict]:
        """Retrieve all OCR imports."""
        with self.lock:
            conn = self._connect()
            try:
                conn.row_factory = sqlite3.Row
                rows = conn.execute(
                    "SELECT * FROM ocr_imports ORDER BY created_at DESC"
                ).fetchall()
                return [dict(r) for r in rows]
            finally:
                conn.close()

    def delete_hand(self, hand_id: str) -> None:
        """Delete a hand and all related data."""
        with self.lock:
            conn = self._connect()
            try:
                for tbl in ("players", "actions", "winners"):
                    conn.execute(f"DELETE FROM {tbl} WHERE hand_id = ?", (hand_id,))
                conn.execute("DELETE FROM hands WHERE hand_id = ?", (hand_id,))
                conn.commit()
            finally:
                conn.close()

    def add_tag(self, hand_id: str, tag: str) -> None:
        """Add a tag to a hand."""
        with self.lock:
            conn = self._connect()
            try:
                conn.execute(
                    "INSERT OR IGNORE INTO hand_tags (hand_id, tag, created_at) VALUES (?, ?, ?)",
                    (hand_id, tag.strip(), datetime.now().isoformat()))
                conn.commit()
            finally:
                conn.close()

    def remove_tag(self, hand_id: str, tag: str) -> None:
        """Remove a tag from a hand."""
        with self.lock:
            conn = self._connect()
            try:
                conn.execute("DELETE FROM hand_tags WHERE hand_id = ? AND tag = ?", (hand_id, tag.strip()))
                conn.commit()
            finally:
                conn.close()

    def get_tags(self, hand_id: str) -> List[str]:
        """Get all tags for a hand."""
        with self.lock:
            conn = self._connect()
            try:
                rows = conn.execute("SELECT tag FROM hand_tags WHERE hand_id = ? ORDER BY tag", (hand_id,)).fetchall()
                return [r[0] for r in rows]
            finally:
                conn.close()

    def get_all_tags(self) -> List[str]:
        """Get all unique tags in database."""
        with self.lock:
            conn = self._connect()
            try:
                rows = conn.execute("SELECT DISTINCT tag FROM hand_tags ORDER BY tag").fetchall()
                return [r[0] for r in rows]
            finally:
                conn.close()

    def get_hand_ids_by_tag(self, tag: str) -> set:
        """Get all hand IDs with a specific tag."""
        with self.lock:
            conn = self._connect()
            try:
                rows = conn.execute("SELECT hand_id FROM hand_tags WHERE tag = ?", (tag.strip(),)).fetchall()
                return {r[0] for r in rows}
            finally:
                conn.close()

    def save_player_type(self, name: str, auto_type: str, hands: int, vpip: float,
                        pfr: float, af: float, fold_cbet: float, wtsd: float, site: str = "") -> None:
        """Save player statistics and type."""
        with self.lock:
            conn = self._connect()
            try:
                existing = conn.execute(
                    "SELECT manual_type FROM player_types WHERE name = ?", (name,)).fetchone()
                manual = existing[0] if existing else ""
                conn.execute(
                    "INSERT OR REPLACE INTO player_types "
                    "(name, site, auto_type, manual_type, hands, vpip, pfr, af, fold_cbet, wtsd, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (name, site, auto_type, manual, hands, vpip, pfr, af, fold_cbet, wtsd,
                     datetime.now().isoformat()))
                conn.commit()
            finally:
                conn.close()

    def set_manual_player_type(self, name: str, manual_type: str) -> None:
        """Manually set a player's type."""
        with self.lock:
            conn = self._connect()
            try:
                conn.execute(
                    "UPDATE player_types SET manual_type = ?, updated_at = ? WHERE name = ?",
                    (manual_type, datetime.now().isoformat(), name))
                if conn.total_changes == 0:
                    conn.execute(
                        "INSERT INTO player_types (name, manual_type, updated_at) VALUES (?, ?, ?)",
                        (name, manual_type, datetime.now().isoformat()))
                conn.commit()
            finally:
                conn.close()

    def get_player_type(self, name: str) -> Optional[Dict[str, Any]]:
        """Get player type and statistics."""
        with self.lock:
            conn = self._connect()
            try:
                row = conn.execute(
                    "SELECT auto_type, manual_type, hands, vpip, pfr, af, fold_cbet, wtsd "
                    "FROM player_types WHERE name = ?", (name,)).fetchone()
                if not row:
                    return None
                return {
                    "auto_type": row[0], "manual_type": row[1], "hands": row[2],
                    "vpip": row[3], "pfr": row[4], "af": row[5],
                    "fold_cbet": row[6], "wtsd": row[7],
                    "effective_type": row[1] if row[1] else row[0],
                }
            finally:
                conn.close()

    def get_player_types_batch(self, names: List[str]) -> Dict[str, Dict[str, Any]]:
        """Fetch cached HUD stats for multiple players in one query."""
        if not names:
            return {}
        unique = list(dict.fromkeys(n for n in names if n))
        if not unique:
            return {}
        placeholders = ",".join("?" * len(unique))
        with self.lock:
            conn = self._connect()
            try:
                rows = conn.execute(
                    f"SELECT name, auto_type, manual_type, hands, vpip, pfr, af, fold_cbet, wtsd "
                    f"FROM player_types WHERE name IN ({placeholders})",
                    unique,
                ).fetchall()
                result: Dict[str, Dict[str, Any]] = {}
                for row in rows:
                    result[row[0]] = {
                        "auto_type": row[1], "manual_type": row[2], "hands": row[3],
                        "vpip": row[4], "pfr": row[5], "af": row[6],
                        "fold_cbet": row[7], "wtsd": row[8],
                        "effective_type": row[2] if row[2] else row[1],
                    }
                return result
            finally:
                conn.close()

    def count_player_types(self) -> int:
        with self.lock:
            conn = self._connect()
            try:
                row = conn.execute(
                    "SELECT COUNT(*) FROM player_types WHERE hands > 0"
                ).fetchone()
                return int(row[0]) if row else 0
            finally:
                conn.close()

    def get_player_position_stats(self, name: str) -> Dict[str, Dict[str, float]]:
        """Get per-position VPIP/PFR statistics for a player."""
        POSITIONS = ["UTG", "UTG+1", "UTG+2", "MP", "HJ", "CO", "BTN", "SB", "BB"]
        result = {p: {"pfr": 0.0, "vpip": 0.0, "hands": 0} for p in POSITIONS}
        with self.lock:
            conn = self._connect()
            try:
                rows = conn.execute("""
                    SELECT p.hand_id, p.seat, h.button_seat
                    FROM players p JOIN hands h ON p.hand_id = h.hand_id
                    WHERE p.name = ? AND p.is_hero = 0
                """, (name,)).fetchall()
                for row in rows:
                    hand_id, seat, button_seat = row[0], row[1], row[2]
                    all_seats = [r[0] for r in conn.execute(
                        "SELECT seat FROM players WHERE hand_id=? ORDER BY seat", (hand_id,)).fetchall()]
                    if not all_seats or button_seat not in all_seats or seat not in all_seats:
                        continue
                    n = len(all_seats)
                    dist = (all_seats.index(seat) - all_seats.index(button_seat)) % n
                    if   dist == 0:                       pos = "BTN"
                    elif dist == 1:                       pos = "SB"
                    elif dist == 2:                       pos = "BB"
                    elif dist == n - 1:                   pos = "CO"
                    elif n >= 5 and dist == n - 2:        pos = "HJ"
                    elif n >= 6 and dist == n - 3:        pos = "MP"
                    elif n >= 7 and dist == n - 4:        pos = "UTG+2"
                    elif n >= 8 and dist == n - 5:        pos = "UTG+1"
                    else:                                 pos = "UTG"
                    result[pos]["hands"] += 1
                    if conn.execute("SELECT 1 FROM actions WHERE hand_id=? AND player=? AND street='preflop' AND action IN ('call','raise','bet') LIMIT 1", (hand_id, name)).fetchone():
                        result[pos]["vpip"] += 1
                    if conn.execute("SELECT 1 FROM actions WHERE hand_id=? AND player=? AND street='preflop' AND action IN ('raise','bet') LIMIT 1", (hand_id, name)).fetchone():
                        result[pos]["pfr"] += 1
            finally:
                conn.close()
        for pos in POSITIONS:
            h = result[pos]["hands"]
            if h > 0:
                result[pos]["vpip"] = round(result[pos]["vpip"] / h * 100, 1)
                result[pos]["pfr"]  = round(result[pos]["pfr"]  / h * 100, 1)
        return result

    def get_all_player_types(self) -> List[Dict[str, Any]]:
        """Get all player types with statistics."""
        with self.lock:
            conn = self._connect()
            try:
                rows = conn.execute(
                    "SELECT name, auto_type, manual_type, hands, vpip, pfr, af, fold_cbet, wtsd "
                    "FROM player_types ORDER BY hands DESC").fetchall()
                results = []
                for r in rows:
                    results.append({
                        "name": r[0], "auto_type": r[1], "manual_type": r[2],
                        "hands": r[3], "vpip": r[4], "pfr": r[5], "af": r[6],
                        "fold_cbet": r[7], "wtsd": r[8],
                        "effective_type": r[2] if r[2] else r[1],
                    })
                return results
            finally:
                conn.close()

    def get_players_by_type(self, player_type: str) -> set:
        """Get all players of a specific type."""
        with self.lock:
            conn = self._connect()
            try:
                rows = conn.execute(
                    "SELECT name FROM player_types "
                    "WHERE (manual_type = ? AND manual_type != '') OR (manual_type = '' AND auto_type = ?)",
                    (player_type, player_type)).fetchall()
                return {r[0] for r in rows}
            finally:
                conn.close()
