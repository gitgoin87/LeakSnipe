#!/usr/bin/env python3
"""
♠ Poker Hand Tracker ♥ — Multi-site poker hand tracker with dark GUI.
Supports CoinPoker, BetACR/WPN, and GGPoker hand history formats.
"""

import os
import sys
import re

# Ensure working directory is always the folder containing the exe/script,
# so pinned taskbar launches and shortcuts find their files correctly.
if getattr(sys, 'frozen', False):
    _BASE_DIR = os.path.dirname(sys.executable)
else:
    _BASE_DIR = os.path.dirname(os.path.abspath(__file__))
os.chdir(_BASE_DIR)
import json
import glob
import time
import base64
import subprocess
import threading
import tempfile
import sqlite3
from typing import Any, Dict, List, Optional
from datetime import datetime
from collections import defaultdict, OrderedDict
import hashlib
import math
import customtkinter as ctk
import tkinter as tk
from tkinter import filedialog
from PIL import Image, ImageTk, ImageEnhance, ImageFilter
import matplotlib
matplotlib.use("Agg")
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib.figure import Figure

import logging

LOG_PATH = os.path.join(tempfile.gettempdir(), "poker_debug.log")
HUD_LOG_PATH = os.path.join(tempfile.gettempdir(), "leaksnipe_python_hud.log")
HUD_PID_PATH = os.path.join(tempfile.gettempdir(), "leaksnipe_python_hud.pid")


def _write_hud_pid():
    try:
        with open(HUD_PID_PATH, "w", encoding="utf-8") as fh:
            fh.write(str(os.getpid()))
    except OSError:
        pass


def _remove_hud_pid():
    try:
        if os.path.isfile(HUD_PID_PATH):
            os.remove(HUD_PID_PATH)
    except OSError:
        pass

# Configure logging without blocking startup if the file path is unusable.
try:
    logging.basicConfig(
        filename=LOG_PATH,
        level=logging.DEBUG,
        format='%(asctime)s - %(levelname)s - %(message)s'
    )
except OSError:
    logging.basicConfig(
        level=logging.DEBUG,
        format='%(asctime)s - %(levelname)s - %(message)s'
    )

try:
    import pytesseract
    pytesseract.pytesseract.tesseract_cmd = r'C:\Program Files\Tesseract-OCR\tesseract.exe'
    HAS_TESSERACT = True
except ImportError:
    HAS_TESSERACT = False

try:
    from ai_processor import AIProcessor
    HAS_AI_ENGINE = True
except ImportError:
    HAS_AI_ENGINE = False

try:
    import win32gui
    import win32con
    import win32process
    HAS_WIN32 = True
except ImportError:
    HAS_WIN32 = False
    win32process = None  # type: ignore

# Import refactored modules
from themes import THEMES, lighten as _lighten, darken as _darken, blend as _blend
from models import Hand, HandDatabase
from parsers import HandParser
from analysis import LeakEngine, SummaryGenerator
from importing import HandImporter, discover_scan_dirs, merge_scan_dirs
from ocr_capture import OCRCaptureBridge, ReplayWindowCapture
from utils import (
    font_style as _font_style,
    canonical_path as _canonical_path,
    format_hero_result,
    hero_aliases_from_settings,
)

# ── Global font system ────────────────────────────────────────────────────────
# UI chrome (labels, buttons, tabs) uses Segoe UI — clean, proportional.
# Data / hand history uses Consolas — monospace, aligns numbers neatly.
_FF = "Segoe UI"    # proportional UI font
_FM = "Consolas"    # monospace data font

_F_CAPTION     = (_FF, 9,  "normal")   # tiny hints, badges
_F_CAPTION_I   = (_FF, 9,  "italic")   # italic captions / placeholders
_F_BODY        = (_FF, 11, "normal")   # standard labels
_F_BODY_I      = (_FF, 11, "italic")   # subtitles, secondary info
_F_SEMIBOLD    = (_FF, 11, "bold")     # medium-weight labels
_F_LABEL       = (_FF, 12, "bold")     # section / panel headings
_F_TITLE       = (_FF, 14, "bold")     # tab-level titles
_F_HEADER      = (_FF, 16, "bold")     # app header
_F_DATA        = (_FM, 10, "normal")   # inline data values
_F_DATA_MD     = (_FM, 11, "normal")   # medium data text (hand lists, stats)
_F_DATA_BOLD   = (_FM, 11, "bold")     # prominent data
_F_DATA_LG     = (_FM, 13, "bold")     # larger stat display
_F_KPI         = (_FM, 28, "bold")     # dashboard KPI numbers
# ─────────────────────────────────────────────────────────────────────────────

# ── Hand Tag Presets ─────────────────────────────────────────────────────────
HAND_TAG_PRESETS = [
    # (display_label, tag_key, hex_color, category)
    ("⭐ For Review",  "For Review",  "#E8A838", "Review"),
    ("📚 Study Later", "Study Later", "#7B9DD9", "Review"),
    ("🎯 Hero Call",   "Hero Call",   "#5BBF6A", "Decision"),
    ("🃏 Bluff",       "Bluff",       "#CF7ADB", "Decision"),
    ("💰 Value Bet",   "Value Bet",   "#4FC3A1", "Decision"),
    ("✅ Check Raise", "Check Raise", "#5097D9", "Decision"),
    ("💥 Bad Beat",    "Bad Beat",    "#E85D5D", "Situation"),
    ("🧊 Cooler",      "Cooler",      "#7BB8D9", "Situation"),
    ("💣 Big Pot",     "Big Pot",     "#D9A650", "Situation"),
    ("🔑 Key Hand",    "Key Hand",    "#A890D9", "Situation"),
    ("❌ Misplay",     "Misplay",     "#E05050", "Mistake"),
    ("📉 Bad Fold",    "Bad Fold",    "#E87040", "Mistake"),
    ("📈 Bad Call",    "Bad Call",    "#DB6B6B", "Mistake"),
    ("🏆 Tournament",  "Tournament",  "#9BD97A", "Other"),
]
# Quick color lookup by tag key
HAND_TAG_COLORS = {entry[1]: entry[2] for entry in HAND_TAG_PRESETS}
# ─────────────────────────────────────────────────────────────────────────────

# Legacy globals— kept for backward compat during transition, driven by active theme
_active_theme = THEMES["Slate Blue"]
BG_DARK   = _active_theme["bg_base"]
BG_PANEL  = _active_theme["bg_panel"]
BG_ACCENT = _active_theme["bg_accent"]
GREEN     = _active_theme["green"]
RED       = _active_theme["red"]
YELLOW    = _active_theme["yellow"]
TEXT      = _active_theme["text"]
TEXT_DIM  = _active_theme["text_dim"]
GOLD      = _active_theme["gold"]
ORANGE    = _active_theme["orange"]

if getattr(sys, 'frozen', False):
    # Running as compiled executable
    BASE_DIR = os.path.dirname(sys.executable)
else:
    # Running as script
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))

SETTINGS_PATH = os.path.join(BASE_DIR, "settings.json")
# Fallback: look in parent directory if not found in current (useful for dist/ structure)
if not os.path.exists(SETTINGS_PATH):
    PARENT_DIR = os.path.dirname(BASE_DIR)
    PARENT_SETTINGS = os.path.join(PARENT_DIR, "settings.json")
    if os.path.exists(PARENT_SETTINGS):
        BASE_DIR = PARENT_DIR
        SETTINGS_PATH = PARENT_SETTINGS

DEFAULT_SETTINGS = {
    "hero_names": {"CoinPoker": "jdwalka", "BetACR": "GBOSS101,JohnDaWalka", "GGPoker": "JohnDaWalka", "ReplayPoker": ""},
    "scan_dirs": [
        {"path": r"D:\Hand2Note4Hh\CoinPoker", "site": "CoinPoker"},
        # BetACR live hand histories (WPN skin — written by ACR Poker client)
        {"path": r"C:\ACR Poker\handHistory\JohnDaWalka", "site": "BetACR"},
        {"path": r"C:\ACR Poker\handHistory\JohnDaWalka - Copy", "site": "BetACR"},
        {"path": r"C:\ACR Poker\TournamentSummary\JohnDaWalka", "site": "BetACR"},
        {"path": r"C:\HM3Archive\Winning Poker Network", "site": "BetACR"},
        # BetACR.eu — archived hand histories via Hand2Note
        {"path": r"C:\Hand2Note4Hh\MyHandsArchive_H2N4\WinningPokerNetwork", "site": "BetACR"},
    ],
    "auto_refresh": True,
    "refresh_interval": 5,
    "theme": "Slate Blue",
    "advanced_mode": False,
    "live_hud_enabled": False,
    "hud_opacity": 0.9,
    "hud_seat_layout": "auto",
    "hud_density": "compact",
    "hud_site_preset": "auto",
    "hud_anchor": "top-left",
    "hud_offset_x": 0,
    "hud_offset_y": 0,
    "hud_edge_margin_pct": 0.12,
    "hud_badge_scale": 1.5,
    "hud_locked": True,
    "hud_slot_positions": {},
    "hud_site_profiles": {},
}

HUD_DENSITY_OPTIONS = ("mini", "compact", "standard", "expanded")
HUD_ANCHOR_OPTIONS = ("top-left", "top-right", "bottom-left", "bottom-right")
HUD_SITE_PRESET_OPTIONS = ("auto", "off", "CoinPoker", "BetACR", "GGPoker", "ReplayPoker")
HUD_PROFILE_SITES = ("CoinPoker", "BetACR", "GGPoker", "ReplayPoker")


def _normalize_hud_slot_offset(raw_offset):
    """Normalize a per-slot HUD position (pixel nudge and/or fx/fy fractions)."""
    if not isinstance(raw_offset, dict):
        return None
    entry = {}
    if "fx" in raw_offset or "fy" in raw_offset:
        try:
            entry["fx"] = round(max(0.0, min(1.0, float(raw_offset.get("fx", 0)))), 4)
        except (TypeError, ValueError):
            entry["fx"] = 0.0
        try:
            entry["fy"] = round(max(0.0, min(1.0, float(raw_offset.get("fy", 0)))), 4)
        except (TypeError, ValueError):
            entry["fy"] = 0.0
    if "x" in raw_offset or "y" in raw_offset:
        try:
            entry["x"] = int(raw_offset.get("x", 0))
        except (TypeError, ValueError):
            entry["x"] = 0
        try:
            entry["y"] = int(raw_offset.get("y", 0))
        except (TypeError, ValueError):
            entry["y"] = 0
    return entry or None


def normalize_hud_slot_positions(raw_positions):
    """Normalize layout-slot positions keyed by slot index 1-9."""
    normalized = {}
    if not isinstance(raw_positions, dict):
        return normalized
    for raw_slot, raw_offset in raw_positions.items():
        try:
            slot = int(raw_slot)
        except (TypeError, ValueError):
            continue
        if slot < 1 or slot > 9:
            continue
        entry = _normalize_hud_slot_offset(raw_offset)
        if entry:
            normalized[str(slot)] = entry
    return normalized


def normalize_hud_site_profiles(raw_profiles):
    normalized = {}
    if not isinstance(raw_profiles, dict):
        return normalized

    for site, profile in raw_profiles.items():
        if site not in HUD_PROFILE_SITES or not isinstance(profile, dict):
            continue
        anchor = str(profile.get("anchor", "top-left")).lower()
        density = str(profile.get("density", "standard")).lower()
        seat_layout = str(profile.get("seat_layout", "auto")).lower()
        try:
            offset_x = int(profile.get("offset_x", 0))
        except (TypeError, ValueError):
            offset_x = 0
        try:
            offset_y = int(profile.get("offset_y", 0))
        except (TypeError, ValueError):
            offset_y = 0
        badge_offsets = normalize_hud_slot_positions(profile.get("badge_offsets", {}))

        normalized[site] = {
            "anchor": anchor if anchor in HUD_ANCHOR_OPTIONS else "top-left",
            "density": density if density in HUD_DENSITY_OPTIONS else "standard",
            "seat_layout": seat_layout if seat_layout in {"auto", "2max", "6max", "9max"} else "auto",
            "offset_x": offset_x,
            "offset_y": offset_y,
            "badge_offsets": badge_offsets,
        }
    return normalized


# ─── Hand Data Model ──────────────────────────────────────────────────────────
class Hand:
    def __init__(self):
        self.hand_id = ""
        self.site = ""
        self.date: datetime | None = None
        self.game_type = ""
        self.is_tournament = False
        self.tournament_id = ""
        self.buy_in = ""
        self.table_name = ""
        self.max_seats = 0
        self.button_seat = 0
        self.players: dict = {}
        self.hero_cards = ""
        self.board_cards: list[str] = []
        self.streets: list[dict] = []
        self.pot = 0.0
        self.rake = 0.0
        self.winners: list[dict] = []
        self.hero_won = 0.0
        self.hero_position = ""
        self.hero_player = ""
        self.raw_text = ""

    def hero_name(self, settings):
        from utils import resolve_hand_hero_name
        return resolve_hand_hero_name(
            settings,
            self.site,
            players=self.players,
            raw_text=self.raw_text,
            hero_player=getattr(self, "hero_player", ""),
        )


# ─── Hand Database (SQLite) ───────────────────────────────────────────────────
_DEFAULT_DB_PATH = os.path.join(BASE_DIR, "poker_hands.db")

def _resolve_db_path(settings: dict = None) -> str:
    """Return DB path from settings, env var, or repo-local default."""
    if settings is None and os.path.exists(SETTINGS_PATH):
        try:
            with open(SETTINGS_PATH, "r", encoding="utf-8") as f:
                settings = json.load(f)
        except Exception:
            settings = None
    if settings:
        p = str(settings.get("db_path", "")).strip()
        if p:
            if not os.path.isabs(p):
                p = os.path.join(BASE_DIR, p)
            parent = os.path.dirname(p)
            if parent:
                os.makedirs(parent, exist_ok=True)
            return p
    env = os.environ.get("LEAKSNIPE_DB_PATH", "").strip()
    if env:
        return env
    parent = os.path.dirname(_DEFAULT_DB_PATH)
    if parent:
        os.makedirs(parent, exist_ok=True)
    return _DEFAULT_DB_PATH

DB_PATH = _resolve_db_path()


class HandDatabase:
    def __init__(self, db_path=DB_PATH):
        self.db_path = db_path
        self.lock = threading.Lock()
        self._init_db()

    def _connect(self):
        return sqlite3.connect(self.db_path, timeout=10)

    def _init_db(self):
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
    def _group_rows_by_hand(rows, key_name="hand_id"):
        grouped = defaultdict(list)
        for row in rows:
            grouped[row[key_name]].append(row)
        return grouped

    def hand_exists(self, hand_id):
        with self.lock:
            conn = self._connect()
            try:
                row = conn.execute(
                    "SELECT 1 FROM hands WHERE hand_id = ?", (hand_id,)
                ).fetchone()
                return row is not None
            finally:
                conn.close()

    def save_hand(self, hand, source_file=""):
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
                # players
                c.execute("DELETE FROM players WHERE hand_id = ?", (hand.hand_id,))
                for seat, info in hand.players.items():
                    c.execute(
                        "INSERT INTO players (hand_id, seat, name, stack, is_hero) VALUES (?,?,?,?,?)",
                        (hand.hand_id, seat, info["name"], info["stack"],
                         1 if info.get("is_hero") else 0),
                    )
                # actions
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
                # winners
                c.execute("DELETE FROM winners WHERE hand_id = ?", (hand.hand_id,))
                for w in hand.winners:
                    c.execute(
                        "INSERT INTO winners (hand_id, player_name, amount) VALUES (?,?,?)",
                        (hand.hand_id, w["name"], w["amount"]),
                    )
                conn.commit()
            finally:
                conn.close()

    def reparse_hands_missing_hero(self, parser):
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

    def get_all_hands(self):
        with self.lock:
            conn = self._connect()
            try:
                conn.row_factory = sqlite3.Row
                c = conn.cursor()
                rows = c.execute("SELECT * FROM hands ORDER BY date DESC").fetchall()
                if not rows:
                    return []

                players_by_hand = self._group_rows_by_hand(
                    c.execute(
                        "SELECT hand_id, seat, name, stack, is_hero FROM players ORDER BY hand_id, seat"
                    ).fetchall()
                )
                actions_by_hand = self._group_rows_by_hand(
                    c.execute(
                        "SELECT hand_id, street, player, action, amount, sequence "
                        "FROM actions ORDER BY hand_id, sequence"
                    ).fetchall()
                )
                winners_by_hand = self._group_rows_by_hand(
                    c.execute(
                        "SELECT hand_id, player_name, amount FROM winners ORDER BY hand_id"
                    ).fetchall()
                )
                tags_by_hand = defaultdict(list)
                for tag_row in c.execute(
                    "SELECT hand_id, tag FROM hand_tags ORDER BY hand_id, tag"
                ).fetchall():
                    tags_by_hand[tag_row["hand_id"]].append(tag_row["tag"])

                hands = []
                for row in rows:
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

                    players_rows = players_by_hand.get(h.hand_id, [])
                    for pr in players_rows:
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
                    # Always inject Flop/Turn/River from board_cards (even if no actions,
                    # e.g. both players all-in pre-flop — streets have no action rows)
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
                    # Re-sort streets in natural poker order
                    sorted_map = OrderedDict()
                    for _sn in _street_order:
                        if _sn in streets_map:
                            sorted_map[_sn] = streets_map[_sn]
                    for _sn in streets_map:
                        if _sn not in sorted_map:
                            sorted_map[_sn] = streets_map[_sn]
                    h.streets = list(sorted_map.values())

                    winner_rows = winners_by_hand.get(h.hand_id, [])
                    h.winners = [{"name": wr["player_name"], "amount": wr["amount"]}
                                 for wr in winner_rows]

                    hands.append(h)
                return hands
            finally:
                conn.close()

    def get_hand_count(self):
        with self.lock:
            conn = self._connect()
            try:
                rows = conn.execute(
                    "SELECT site, COUNT(*) as cnt FROM hands GROUP BY site"
                ).fetchall()
                return {r[0]: r[1] for r in rows}
            finally:
                conn.close()

    def save_ocr_import(self, image_path, ocr_text, elements, notes=""):
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

    def save_ocr_as_hand(self, ocr_id, hand):
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

    def get_ocr_imports(self):
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

    def delete_hand(self, hand_id):
        with self.lock:
            conn = self._connect()
            try:
                for tbl in ("players", "actions", "winners"):
                    conn.execute(f"DELETE FROM {tbl} WHERE hand_id = ?", (hand_id,))
                conn.execute("DELETE FROM hands WHERE hand_id = ?", (hand_id,))
                conn.commit()
            finally:
                conn.close()

    def add_tag(self, hand_id, tag):
        with self.lock:
            conn = self._connect()
            try:
                conn.execute(
                    "INSERT OR IGNORE INTO hand_tags (hand_id, tag, created_at) VALUES (?, ?, ?)",
                    (hand_id, tag.strip(), datetime.now().isoformat()))
                conn.commit()
            finally:
                conn.close()

    def remove_tag(self, hand_id, tag):
        with self.lock:
            conn = self._connect()
            try:
                conn.execute("DELETE FROM hand_tags WHERE hand_id = ? AND tag = ?", (hand_id, tag.strip()))
                conn.commit()
            finally:
                conn.close()

    def get_tags(self, hand_id):
        with self.lock:
            conn = self._connect()
            try:
                rows = conn.execute("SELECT tag FROM hand_tags WHERE hand_id = ? ORDER BY tag", (hand_id,)).fetchall()
                return [r[0] for r in rows]
            finally:
                conn.close()

    def get_all_tags(self):
        with self.lock:
            conn = self._connect()
            try:
                rows = conn.execute("SELECT DISTINCT tag FROM hand_tags ORDER BY tag").fetchall()
                return [r[0] for r in rows]
            finally:
                conn.close()

    def get_hand_ids_by_tag(self, tag):
        with self.lock:
            conn = self._connect()
            try:
                rows = conn.execute("SELECT hand_id FROM hand_tags WHERE tag = ?", (tag.strip(),)).fetchall()
                return {r[0] for r in rows}
            finally:
                conn.close()


    def save_player_type(self, name, auto_type, hands, vpip, pfr, af, fold_cbet, wtsd, site=""):
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

    def set_manual_player_type(self, name, manual_type):
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

    def get_player_type(self, name):
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

    def get_player_types_batch(self, names):
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
                result = {}
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

    def count_player_types(self):
        with self.lock:
            conn = self._connect()
            try:
                row = conn.execute(
                    "SELECT COUNT(*) FROM player_types WHERE hands > 0"
                ).fetchone()
                return int(row[0]) if row else 0
            finally:
                conn.close()

    def get_player_position_stats(self, name: str) -> dict:
        """Per-position VPIP/PFR for a player across up to 9-max tables."""
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

    def get_all_player_types(self):
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

    def get_players_by_type(self, player_type):
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


# ─── Unified Parser ───────────────────────────────────────────────────────────
class HandParser:
    def __init__(self, settings):
        self.settings = settings

    def detect_site(self, text):
        for line in text.split("\n"):
            stripped = line.strip()
            if stripped.startswith("CoinPoker Hand #"):
                return "CoinPoker"
            if stripped.startswith("Game Hand #") or stripped.startswith("Hand #"):
                return "BetACR"
            if "GG Poker" in stripped or "GGPoker" in stripped or stripped.startswith("Poker Hand #PT"):
                return "GGPoker"
            if (
                stripped.startswith("Replay Poker Hand #")
                or stripped.startswith("***** Replay Poker Hand History for Game")
                or ("Replay Poker" in stripped and ("Hand" in stripped or "Game" in stripped))
            ):
                return "ReplayPoker"
        return None

    def split_hands(self, text, site):
        hands = []
        current: list[str] = []
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

    def parse_file(self, filepath, site):
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
                    # All WPN hands are BetACR regardless of detect_site result
                    if h.site in ("ACR", "BetACR"):
                        h.site = "BetACR"
                    h.raw_text = raw.strip()
                    results.append(h)
            except Exception as e:
                logging.error(f"Error parsing hand from {filepath}: {e}, content start: {str(raw.strip())[:100]}")
                continue
        return results

    def _parse_single(self, text, site):
        if site == "CoinPoker":
            return self._parse_coinpoker(text)
        elif site in ("ACR", "BetACR"):
            return self._parse_acr(text, site_label="BetACR")
        elif site == "GGPoker":
            return self._parse_ggpoker(text)
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

    # ── CoinPoker parser ──────────────────────────────────────────────────
    def _parse_coinpoker(self, text):
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

    def _parse_streets_coinpoker(self, lines, hero):
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

    # ── ACR / BetACR (WPN) parser ──────────────────────────────────────────
    def _parse_acr(self, text, site_label="BetACR"):
        h = Hand()
        h.site = site_label
        h.raw_text = text
        lines = text.split("\n")
        hero_names = self.settings.get("hero_names", {})
        hero = hero_names.get(site_label) or hero_names.get("BetACR", "JohnDaWalka")

        header = lines[0] if lines else ""
        m = re.search(r"(?:Game )?Hand #(\d+)", header)
        if not m:
            return None
        prefix = "ACR"  # All WPN-format hands (BetACR.eu / ACR) use ACR_ prefix
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
            # BetACR format: "Eton 6-max Seat #2 is the button" (no Table '...')
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

    # ── GGPoker parser (stub) ─────────────────────────────────────────────
    def _parse_ggpoker(self, text):
        """Stub GGPoker parser — returns None until full implementation is added."""
        return None

    def _parse_replaypoker(self, text):
        h = Hand()
        h.site = "ReplayPoker"
        lines = text.split("\n")
        hero = self.settings.get("hero_names", {}).get("ReplayPoker", "")

        hand_id = None
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
            for fmt in ("%Y-%m-%d %H:%M:%S", "%Y/%m/%d %H:%M:%S"):
                try:
                    h.date = datetime.strptime(dm.group(1), fmt)
                    break
                except ValueError:
                    continue
            else:
                h.date = datetime.now()
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
                h.winners.append({"name": collected_m.group(1), "amount": self._parse_amount(collected_m.group(2))})
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

    def _parse_streets_replaypoker(self, lines):
        current_street = {"name": "Preflop", "cards": [], "actions": []}
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

    def _parse_streets_acr(self, lines, hero):
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

    # ── Shared helpers ────────────────────────────────────────────────────
    def _parse_action(self, action_str):
        def _find_amount(pattern=r"(\d[\d,]*(?:\.\d+)?)"):
            match = re.search(pattern, action_str)
            return self._parse_amount(match.group(1)) if match else 0.0

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

    def _parse_amount(self, value):
        cleaned = re.sub(r"[^\d.]", "", value or "")
        return float(cleaned) if cleaned else 0.0

    def _extract_board(self, text):
        m = re.search(r"Board \[(.+?)\]", text)
        if m:
            return m.group(1).split()
        return []

    def _collect_board_from_streets(self, streets):
        board = []
        for street in streets:
            board.extend(street.get("cards", []))
        return board

    def _calc_hero_result(self, h, hero):
        won: float = 0.0
        for w in h.winners:
            if w.get("name") == hero:
                won += float(w.get("amount", 0.0))

        # Bug fix #1: also credit any uncalled bet returned to hero.
        # Hand histories emit "Uncalled bet ($X) returned to <name>" when the
        # hero raised and was not called — this money was never truly "invested"
        # and must be subtracted from the gross invested figure.  The cleanest
        # approach is to add it to `won` so the net formula (won − invested)
        # stays correct.
        raw = getattr(h, "raw_text", "") or ""
        if raw and hero:
            import re as _re
            for ub in _re.finditer(
                r"Uncalled bet \(\$?(\d+(?:\.\d+)?)\) returned to "
                + _re.escape(hero),
                raw,
            ):
                won += float(ub.group(1))

        invested: float = 0.0
        preflop_raised = False
        for street in h.streets:
            # Bug fix #2: a "raises to $X" line stores the player's *total*
            # street commitment up to that raise.  Any prior "post" (blind) on
            # the same street is already included in that raise-to amount, so
            # naively summing post + raise double-counts the blind chips.
            #
            # Correct algorithm: if the hero raised on this street, the
            # raise-to amount IS their commitment up to that point; only add
            # subsequent calls (after an opponent re-raise) on top of it.
            # If they never raised, sum posts + calls + bets normally.
            hero_acts = [
                (act.get("action", ""), float(act.get("amount", 0.0)))
                for act in street.get("actions", [])
                if act.get("player") == hero
            ]
            last_raise_idx: int | None = None
            for i, (a, _) in enumerate(hero_acts):
                if a == "raise":
                    last_raise_idx = i
            if last_raise_idx is not None:
                if street.get("name") == "Preflop":
                    preflop_raised = True
                # Raise-to total covers everything up to this raise.
                street_total = hero_acts[last_raise_idx][1]
                # Add only actions that come *after* the raise (opponent
                # re-raised and hero called/bet again).
                for a, amt in hero_acts[last_raise_idx + 1:]:
                    if a in ("call", "bet"):
                        street_total += amt
            else:
                # No raise on this street: simple sum.
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

    def _calc_position(self, h, hero):
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


# ─── Leak Detection Engine ────────────────────────────────────────────────────
class LeakEngine:
    def __init__(self, settings):
        self.settings = settings

    def analyze(self, hands):
        stats: dict = {
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

    def _compute_final(self, s):
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

    def _generate_alerts(self, r):
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


# ─── AI Summary Generator ────────────────────────────────────────────────────
class SummaryGenerator:
    def generate(self, stats, hands):
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


def _is_drive_root(path: str) -> bool:
    """Return True if *path* is the root of a drive (e.g. 'C:\\'), to prevent
    accidentally scanning an entire drive."""
    p = os.path.normpath(path)
    return p == os.path.splitdrive(p)[0] + os.sep


# ─── File Watcher / Importer ──────────────────────────────────────────────────
class HandImporter:
    def __init__(self, settings, db=None):
        self.settings = settings
        self.parser = HandParser(settings)
        self.db = db
        self.hands = []
        self.files_scanned = set()
        self.file_mtimes = {}
        self.file_signatures = {}
        self.lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = None

    def update_settings(self, settings):
        with self.lock:
            self.settings = settings
            self.parser = HandParser(settings)

    def _save_hand_if_new(self, hand, source_file):
        if self.db:
            if self.db.hand_exists(hand.hand_id):
                return False
            self.db.save_hand(hand, source_file=source_file)
            return True

        with self.lock:
            existing_ids = {hh.hand_id for hh in self.hands}
            if hand.hand_id in existing_ids:
                return False
            self.hands.append(hand)
            return True

    def _get_file_signature(self, fpath):
        try:
            stat = os.stat(fpath)
        except OSError as exc:
            logging.warning("Failed to stat hand history %s: %s", fpath, exc)
            return None

        mtime_ns = getattr(stat, "st_mtime_ns", int(stat.st_mtime * 1_000_000_000))
        size = stat.st_size

        try:
            with open(fpath, "rb") as fh:
                if size > 4096:
                    fh.seek(-4096, os.SEEK_END)
                tail_hash = hashlib.sha1(fh.read()).hexdigest()
        except OSError as exc:
            logging.warning("Failed to read hand history tail %s: %s", fpath, exc)
            return None

        return (mtime_ns, size, tail_hash)

    def full_scan(self):
        saved = 0
        files_count = 0
        for entry in self.settings.get("scan_dirs", []):
            path = os.path.normpath(entry["path"])
            site = entry["site"]
            if _is_drive_root(path):
                continue
            if not os.path.isdir(path):
                continue
            for root, dirs, files in os.walk(path):
                for fname in files:
                    if not fname.lower().endswith(".txt"):
                        continue
                    fpath = os.path.join(root, fname)
                    signature = self._get_file_signature(fpath)
                    if signature is None:
                        continue
                    if self.file_signatures.get(fpath) == signature:
                        continue
                    self.file_signatures[fpath] = signature
                    self.file_mtimes[fpath] = signature[0]
                    try:
                        parsed = self.parser.parse_file(fpath, site)
                    except Exception as exc:
                        logging.error("Failed to parse hand history %s: %s", fpath, exc, exc_info=True)
                        continue
                    for h in parsed:
                        if self._save_hand_if_new(h, fpath):
                            saved += 1
                    files_count += 1
                    self.files_scanned.add(fpath)
        return saved, files_count

    def import_files(self, file_paths):
        """Import hands from explicit file paths (manual import)."""
        new_hands = []
        files_count = 0
        for fpath in file_paths:
            if not os.path.isfile(fpath):
                continue
            try:
                with open(fpath, "r", encoding="utf-8", errors="replace") as f:
                    content = f.read()
            except Exception:
                continue
            detected = self.parser.detect_site(content)
            if detected is None:
                continue
            parsed = self.parser.parse_file(fpath, detected)
            for h in parsed:
                if self.db and self.db.hand_exists(h.hand_id):
                    continue
                new_hands.append((h, fpath))
            files_count += 1
            signature = self._get_file_signature(fpath)
            if signature is not None:
                self.file_signatures[fpath] = signature
                self.file_mtimes[fpath] = signature[0]
            self.files_scanned.add(fpath)
        saved = 0
        for h, fpath in new_hands:
            if self.db:
                self.db.save_hand(h, source_file=fpath)
                saved += 1
            else:
                with self.lock:
                    existing_ids = {hh.hand_id for hh in self.hands}
                    if h.hand_id not in existing_ids:
                        self.hands.append(h)
                        saved += 1
        return saved, files_count

    def start_watcher(self, callback=None):
        self._stop.clear()
        self._thread = threading.Thread(target=self._watch_loop, args=(callback,), daemon=True)
        self._thread.start()

    def stop_watcher(self):
        self._stop.set()

    def _watch_loop(self, callback):
        while not self._stop.is_set():
            try:
                new_count, file_count = self.full_scan()
                if callback and new_count > 0:
                    callback(new_count, file_count)
            except Exception as e:
                logging.error(f"Error in watch loop: {e}", exc_info=True)
            interval = self.settings.get("refresh_interval", 5)
            self._stop.wait(interval)

    def get_hands(self):
        if self.db:
            return self.db.get_all_hands()
        with self.lock:
            return list(self.hands)

    def get_stats_text(self):
        if self.db:
            counts = self.db.get_hand_count()
            total = sum(counts.values())
            parts = [f"{site}: {count}" for site, count in counts.items() if count > 0]
            fcount = len(self.files_scanned)
            return f"{total} hands imported from {fcount} files ({', '.join(parts)})"
        with self.lock:
            total = len(self.hands)
            # Dynamic count for in-memory mode too
            counts = defaultdict(int)
            for h in self.hands:
                counts[h.site] += 1
            parts = [f"{site}: {count}" for site, count in counts.items()]
            fcount = len(self.files_scanned)
        return f"{total} hands imported from {fcount} files ({', '.join(parts)})"


def normalize_scan_dirs(scan_dirs):
    if scan_dirs is None:
        scan_dirs = DEFAULT_SETTINGS.get("scan_dirs", [])
    if not isinstance(scan_dirs, list):
        scan_dirs = DEFAULT_SETTINGS.get("scan_dirs", [])

    normalized = []
    seen = set()
    for entry in scan_dirs:
        if not isinstance(entry, dict):
            continue
        raw_path = str(entry.get("path", "")).strip()
        if not raw_path:
            continue
        path = os.path.normpath(raw_path)
        if _is_drive_root(path):
            continue
        site = str(entry.get("site", "")).strip() or "CoinPoker"
        key = (site, os.path.normcase(path))
        if key in seen:
            continue
        seen.add(key)
        normalized.append({"path": path, "site": site})
    return normalized


def normalize_settings(raw_settings):
    settings = dict(DEFAULT_SETTINGS)
    settings["hero_names"] = dict(DEFAULT_SETTINGS.get("hero_names", {}))
    settings["scan_dirs"] = [dict(entry) for entry in DEFAULT_SETTINGS.get("scan_dirs", [])]

    if isinstance(raw_settings, dict):
        settings.update(raw_settings)
        hero_names = raw_settings.get("hero_names")
        if isinstance(hero_names, dict):
            settings["hero_names"].update(hero_names)
        settings["scan_dirs"] = normalize_scan_dirs(raw_settings.get("scan_dirs"))
    else:
        settings["scan_dirs"] = normalize_scan_dirs(None)

    density = str(settings.get("hud_density", "standard")).lower()
    settings["hud_density"] = density if density in HUD_DENSITY_OPTIONS else "standard"

    site_preset = str(settings.get("hud_site_preset", "auto"))
    settings["hud_site_preset"] = site_preset if site_preset in HUD_SITE_PRESET_OPTIONS else "auto"

    anchor = str(settings.get("hud_anchor", "top-left")).lower()
    settings["hud_anchor"] = anchor if anchor in HUD_ANCHOR_OPTIONS else "top-left"

    try:
        settings["hud_offset_x"] = int(settings.get("hud_offset_x", 0))
    except (TypeError, ValueError):
        settings["hud_offset_x"] = 0
    try:
        settings["hud_offset_y"] = int(settings.get("hud_offset_y", 0))
    except (TypeError, ValueError):
        settings["hud_offset_y"] = 0
    settings["hud_site_profiles"] = normalize_hud_site_profiles(settings.get("hud_site_profiles"))
    settings["hud_slot_positions"] = normalize_hud_slot_positions(settings.get("hud_slot_positions"))
    settings["hud_locked"] = bool(settings.get("hud_locked", True))
    try:
        settings["hud_edge_margin_pct"] = float(settings.get("hud_edge_margin_pct", 0.12))
    except (TypeError, ValueError):
        settings["hud_edge_margin_pct"] = 0.12
    settings["hud_edge_margin_pct"] = max(0.05, min(0.25, settings["hud_edge_margin_pct"]))
    try:
        settings["hud_badge_scale"] = float(settings.get("hud_badge_scale", 1.5))
    except (TypeError, ValueError):
        settings["hud_badge_scale"] = 1.5
    settings["hud_badge_scale"] = max(0.8, min(2.5, settings["hud_badge_scale"]))
    return settings


# ─── Settings I/O ─────────────────────────────────────────────────────────────
def load_settings():
    raw_settings = None
    if os.path.exists(SETTINGS_PATH):
        try:
            with open(SETTINGS_PATH, "r", encoding="utf-8") as f:
                raw_settings = json.load(f)
        except Exception:
            pass
    settings = normalize_settings(raw_settings)
    if settings != raw_settings:
        save_settings(settings)
    return settings


def save_settings(settings):
    try:
        with open(SETTINGS_PATH, "w", encoding="utf-8") as f:
            json.dump(settings, f, indent=2)
    except Exception:
        pass


# ─── OCR Engine (Windows built-in OCR) ────────────────────────────────────────
class PokerOCR:
    """Uses Windows 10 built-in OCR (Windows.Media.Ocr) — no external binaries."""

    PS_SCRIPT = r'''
Add-Type -AssemblyName System.Runtime.WindowsRuntime
[Windows.Media.Ocr.OcrEngine,Windows.Foundation,ContentType=WindowsRuntime] | Out-Null
[Windows.Graphics.Imaging.BitmapDecoder,Windows.Foundation,ContentType=WindowsRuntime] | Out-Null
[Windows.Storage.StorageFile,Windows.Foundation,ContentType=WindowsRuntime] | Out-Null
[Windows.Storage.Streams.RandomAccessStream,Windows.Foundation,ContentType=WindowsRuntime] | Out-Null

$asTaskGeneric = ([System.WindowsRuntimeSystemExtensions].GetMethods() |
    Where-Object { $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and
    $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1' })[0]

Function Await($WinRtTask, $ResultType) {
    $asTask = $asTaskGeneric.MakeGenericMethod($ResultType)
    $netTask = $asTask.Invoke($null, @($WinRtTask))
    $netTask.Wait(-1) | Out-Null
    $netTask.Result
}

$imagePath = $args[0]
$file = Await ([Windows.Storage.StorageFile]::GetFileFromPathAsync($imagePath)) ([Windows.Storage.StorageFile])
$stream = Await ($file.OpenAsync([Windows.Storage.FileAccessMode]::Read)) ([Windows.Storage.Streams.IRandomAccessStream])
$decoder = Await ([Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($stream)) ([Windows.Graphics.Imaging.BitmapDecoder])
$bitmap = Await ($decoder.GetSoftwareBitmapAsync()) ([Windows.Graphics.Imaging.SoftwareBitmap])

$ocrEngine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromUserProfileLanguages()
if (-not $ocrEngine) { Write-Error "No OCR engine"; exit 1 }
$ocrResult = Await ($ocrEngine.RecognizeAsync($bitmap)) ([Windows.Media.Ocr.OcrResult])

foreach ($line in $ocrResult.Lines) {
    Write-Output $line.Text
}
$stream.Dispose()
'''

    def __init__(self):
        self._script_path = os.path.join(BASE_DIR, "poker_ocr_bridge.ps1")
        try:
            with open(self._script_path, "w", encoding="utf-8") as f:
                f.write(self.PS_SCRIPT)
        except Exception as e:
            print(f"Warning: Could not write OCR script to {self._script_path}: {e}")

    def preprocess_image(self, image_path):
        """Enhance image for better OCR: grayscale, contrast, sharpen, upscale."""
        img = Image.open(image_path)
        if img.mode == "RGBA":
            bg = Image.new("RGB", img.size, (255, 255, 255))
            bg.paste(img, mask=img.split()[3])
            img = bg
        elif img.mode != "RGB":
            img = img.convert("RGB")
        w, h = img.size
        if max(w, h) < 1500:
            scale = 1500 / max(w, h)
            img = img.resize((int(w * scale), int(h * scale)), Image.LANCZOS)
        img = ImageEnhance.Contrast(img).enhance(1.8)
        img = ImageEnhance.Sharpness(img).enhance(2.0)
        img = img.convert("L")
        img = img.point(lambda x: 0 if x < 140 else 255)
        tmp = os.path.join(tempfile.gettempdir(), "poker_ocr_preprocessed.png")
        img.save(tmp, "PNG")
        return tmp

    def ocr_image(self, image_path):
        """Run OCR: try Tesseract first, fall back to Windows built-in OCR."""
        preprocessed = self.preprocess_image(image_path)
        if HAS_TESSERACT:
            try:
                from PIL import Image as PILImage
                img = PILImage.open(preprocessed)
                text = pytesseract.image_to_string(img, config='--psm 6')
                if text.strip():
                    return text.strip()
            except Exception:
                pass
        return self._windows_ocr(preprocessed)

    def _windows_ocr(self, preprocessed):
        """Fallback: Windows 10 built-in OCR via PowerShell."""
        try:
            result = subprocess.run(
                ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
                 "-File", self._script_path, preprocessed],
                capture_output=True, text=True, timeout=30, encoding="utf-8"
            )
            if result.returncode != 0:
                return f"[OCR Error] {result.stderr.strip()}"
            return result.stdout.strip()
        except subprocess.TimeoutExpired:
            return "[OCR Error] Timed out after 30 seconds"
        except Exception as e:
            return f"[OCR Error] {e}"

    def parse_poker_elements(self, text):
        """Extract poker-specific elements from OCR text."""
        elements = {
            "cards": [], "bets": [], "pot": None,
            "players": [], "board": [], "blinds": None,
            "hand_number": None, "raw_text": text,
            "actions": [], "players_detected": [],
        }
        lines = text.split("\n")
        current_street = "preflop"
        players_seen = set()
        for line in lines:
            line = line.strip()
            if not line:
                continue

            # Track current street
            street_m = re.search(r'\*\*\*\s*(FLOP|TURN|RIVER|PREFLOP|HOLE\s*CARDS)', line, re.IGNORECASE)
            if street_m:
                sname = street_m.group(1).lower().replace("hole cards", "preflop").strip()
                current_street = sname
            elif re.search(r'\b(FLOP)\b', line, re.IGNORECASE) and "fold" not in line.lower():
                current_street = "flop"
            elif re.search(r'\b(TURN)\b', line, re.IGNORECASE):
                current_street = "turn"
            elif re.search(r'\b(RIVER)\b', line, re.IGNORECASE):
                current_street = "river"

            # Extract player names from "Seat N: PlayerName (stack)" patterns
            seat_m = re.match(r'Seat\s+\d+:\s+(\S+(?:\s+\S+)?)\s*\(', line)
            if seat_m:
                pname = seat_m.group(1).strip()
                if pname not in players_seen:
                    players_seen.add(pname)
                    elements["players_detected"].append(pname)

            # Parse betting actions: "PlayerName: action [amount]"
            action_m = re.match(
                r'(.+?):\s+(folds?|checks?|calls?|bets?|raises?|all-in|all\s*in)'
                r'(?:\s+(?:to\s+)?([\d,]+(?:\.\d+)?))?',
                line, re.IGNORECASE,
            )
            if action_m:
                pname = action_m.group(1).strip()
                raw_act = action_m.group(2).lower().rstrip("s")
                amt_str = action_m.group(3)
                amt = 0.0
                if amt_str:
                    try:
                        amt = float(amt_str.replace(",", ""))
                    except ValueError:
                        pass
                # For raise lines like "raises 500 to 1000", prefer the "to" amount
                raise_to = re.search(r'to\s+([\d,]+(?:\.\d+)?)', line, re.IGNORECASE)
                if raw_act == "raise" and raise_to:
                    try:
                        amt = float(raise_to.group(1).replace(",", ""))
                    except ValueError:
                        pass
                act_name = raw_act.replace(" ", "-")
                if act_name == "fold":
                    act_name = "fold"
                elif act_name == "check":
                    act_name = "check"
                elif act_name == "call":
                    act_name = "call"
                elif act_name == "bet":
                    act_name = "bet"
                elif act_name == "raise":
                    act_name = "raise"
                elif "all" in act_name:
                    act_name = "all-in"
                elements["actions"].append({
                    "player": pname, "action": act_name,
                    "amount": amt, "street": current_street,
                })
                if pname not in players_seen:
                    players_seen.add(pname)
                    elements["players_detected"].append(pname)

            # Also detect "PlayerName is all-in 5000" style
            allin_m = re.match(r'(.+?)\s+is\s+all[- ]?in\s+([\d,]+(?:\.\d+)?)', line, re.IGNORECASE)
            if allin_m and not action_m:
                pname = allin_m.group(1).strip()
                try:
                    amt = float(allin_m.group(2).replace(",", ""))
                except ValueError:
                    amt = 0.0
                elements["actions"].append({
                    "player": pname, "action": "all-in",
                    "amount": amt, "street": current_street,
                })
                if pname not in players_seen:
                    players_seen.add(pname)
                    elements["players_detected"].append(pname)

            cards = re.findall(
                r'\b([2-9TJQKA][shdcSHDC])\b'
                r'|([2-9TJQKA]\s*(?:of\s+)?(?:spades?|hearts?|diamonds?|clubs?))',
                line, re.IGNORECASE
            )
            for match in cards:
                card = match[0] if match[0] else match[1]
                card = card.strip()
                if len(card) == 2:
                    elements["cards"].append(card[0].upper() + card[1].lower())

            unicode_cards = re.findall(r'[♠♥♦♣]\s*[2-9TJQKA]|[2-9TJQKA]\s*[♠♥♦♣]', line)
            suit_map = {"♠": "s", "♥": "h", "♦": "d", "♣": "c"}
            for uc in unicode_cards:
                uc = uc.replace(" ", "")
                if uc[0] in suit_map:
                    elements["cards"].append(uc[1].upper() + suit_map[uc[0]])
                elif uc[-1] in suit_map:
                    elements["cards"].append(uc[0].upper() + suit_map[uc[-1]])

            bets = re.findall(r'(?:bet|raise|call|all.?in|pot)\D{0,3}([\d,]+(?:\.\d+)?)', line, re.IGNORECASE)
            for b in bets:
                try:
                    elements["bets"].append(float(b.replace(",", "")))
                except ValueError:
                    pass

            pot_m = re.search(r'\bpot\b\D{0,5}([\d,]+(?:\.\d+)?)', line, re.IGNORECASE)
            if pot_m and not elements["pot"]:
                try:
                    elements["pot"] = float(pot_m.group(1).replace(",", ""))
                except ValueError:
                    pass

            blind_m = re.search(r'(\d+(?:\.\d+)?)\s*/\s*(\d+(?:\.\d+)?)', line)
            if blind_m and not elements["blinds"]:
                elements["blinds"] = f"{blind_m.group(1)}/{blind_m.group(2)}"

            hand_m = re.search(r'(?:Hand|Game)\s*#?\s*(\d{6,})', line, re.IGNORECASE)
            if hand_m:
                elements["hand_number"] = hand_m.group(1)

            board_m = re.search(r'(?:board|flop|community)\D{0,5}((?:[2-9TJQKA][shdcSHDC]\s*){3,5})', line, re.IGNORECASE)
            if board_m:
                elements["board"] = re.findall(r'[2-9TJQKA][shdcSHDC]', board_m.group(1), re.IGNORECASE)

        elements["cards"] = list(dict.fromkeys(elements["cards"]))
        return elements

    def format_analysis(self, elements):
        """Format parsed poker elements into readable analysis."""
        lines = []
        lines.append("=" * 50)
        lines.append("  POKER TABLE OCR ANALYSIS")
        lines.append("=" * 50)

        if elements.get("hand_number"):
            lines.append(f"\n  Hand #: {elements['hand_number']}")
        if elements.get("blinds"):
            lines.append(f"  Blinds: {elements['blinds']}")
        if elements.get("pot"):
            lines.append(f"  Pot: {elements['pot']:,.0f}")

        if elements.get("cards"):
            lines.append(f"\n  Cards detected: {' '.join(elements['cards'])}")
            if len(elements["cards"]) >= 2:
                lines.append(f"  Likely hole cards: {elements['cards'][0]} {elements['cards'][1]}")
            if len(elements["cards"]) >= 5:
                lines.append(f"  Likely board: {' '.join(elements['cards'][2:7])}")

        if elements.get("board"):
            lines.append(f"  Board: {' '.join(elements['board'])}")

        if elements.get("bets"):
            lines.append(f"\n  Bet amounts detected: {', '.join(f'{b:,.0f}' for b in elements['bets'])}")

        if elements.get("actions"):
            lines.append(f"\n  BETTING ACTIONS ({len(elements['actions'])} detected):")
            lines.append("  " + "-" * 46)
            current_street = ""
            for act in elements["actions"]:
                if act.get("street") and act["street"] != current_street:
                    current_street = act["street"]
                    lines.append(f"\n  [{current_street.upper()}]")
                amt = f" {act['amount']:,.0f}" if act["amount"] else ""
                lines.append(f"    {act['player']}: {act['action']}{amt}")

        if elements.get("players_detected"):
            lines.append(f"\n  Players: {', '.join(elements['players_detected'])}")

        lines.append("\n" + "=" * 50)
        lines.append("  RAW OCR TEXT:")
        lines.append("-" * 50)
        lines.append(elements.get("raw_text", "(no text)"))
        lines.append("=" * 50)
        return "\n".join(lines)


# ─── Station Detector (Player Classification) ────────────────────────────────
class StationDetector:
    """Analyze all opponents across all hands and classify player types."""

    def __init__(self, settings):
        self.settings = settings

    def analyze_players(self, hands):
        player_data = defaultdict(lambda: {
            "total_hands": 0, "vpip_hands": 0, "pfr_hands": 0,
            "bets_raises": 0, "calls": 0, "folds_to_cbet": 0,
            "cbet_faced": 0, "saw_flop": 0, "went_to_sd": 0,
        })

        for h in hands:
            hero = h.hero_name(self.settings)
            player_names = {info["name"] for info in h.players.values()}
            preflop = h.streets[0] if h.streets else None

            # Determine preflop raiser (last raiser)
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

                # Fold to C-Bet
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

        results = []
        for pname, d in player_data.items():
            t = d["total_hands"] or 1
            sf = d["saw_flop"] or 1
            vpip = round(100 * d["vpip_hands"] / t, 1)
            pfr = round(100 * d["pfr_hands"] / t, 1)
            af = round(d["bets_raises"] / max(d["calls"], 1), 2)
            fold_cbet = round(100 * d["folds_to_cbet"] / max(d["cbet_faced"], 1), 1)
            wtsd = round(100 * d["went_to_sd"] / sf, 1)
            classification = self._classify(vpip, pfr, af, d["total_hands"])
            results.append({
                "name": pname, "hands": d["total_hands"],
                "vpip": vpip, "pfr": pfr, "af": af,
                "fold_cbet": fold_cbet, "wtsd": wtsd,
                "auto_type": classification,
                "manual_type": "",
                "classification": classification,
            })
        results.sort(key=lambda x: x["hands"], reverse=True)
        return results

    def apply_manual_overrides(self, results, db):
        """Apply manual type overrides from database."""
        for p in results:
            try:
                db_info = db.get_player_type(p["name"])
                if db_info and db_info["manual_type"]:
                    p["manual_type"] = db_info["manual_type"]
                    p["classification"] = db_info["manual_type"]
            except Exception:
                pass
        return results

    def _classify(self, vpip, pfr, af, hands):
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


# ─── EV Calculator ────────────────────────────────────────────────────────────
class EVCalculator:
    """Simplified Expected Value analysis per hand."""

    HAND_STRENGTH = {
        "AA": 100, "KK": 95, "QQ": 90, "JJ": 85, "TT": 78,
        "99": 72, "88": 68, "77": 62, "66": 58, "55": 54,
        "44": 50, "33": 46, "22": 42,
        "AKs": 88, "AKo": 82, "AQs": 80, "AQo": 75,
        "AJs": 76, "AJo": 71, "ATs": 70, "ATo": 65,
        "A9s": 60, "A8s": 58, "A7s": 56, "A6s": 54,
        "A5s": 56, "A4s": 54, "A3s": 52, "A2s": 50,
        "KQs": 74, "KQo": 69, "KJs": 68, "KJo": 63,
        "KTs": 64, "K9s": 58, "K8s": 52, "K7s": 50,
        "K6s": 48, "K5s": 46, "K4s": 44, "K3s": 42, "K2s": 40,
        "QJs": 66, "QTs": 62, "Q9s": 54, "Q8s": 48,
        "JTs": 64, "J9s": 52, "J8s": 46,
        "T9s": 56, "T8s": 50, "T7s": 44,
        "98s": 54, "97s": 48, "96s": 42,
        "87s": 52, "86s": 46, "85s": 40,
        "76s": 50, "75s": 44, "74s": 38,
        "65s": 48, "64s": 42, "63s": 36,
        "54s": 46, "53s": 40, "52s": 34,
        "43s": 38, "42s": 32,
    }

    POSITION_MULT = {
        "BTN": 1.15, "CO": 1.10, "MP": 1.0, "EP": 0.90,
        "SB": 0.85, "BB": 0.90, "?": 1.0,
    }

    def get_hand_strength(self, hero_cards):
        if not hero_cards or len(hero_cards.split()) < 2:
            return 0
        parts = hero_cards.split()
        c1, c2 = parts[0], parts[1]
        if len(c1) < 2 or len(c2) < 2:
            return 0
        r1, s1 = c1[0].upper(), c1[1].lower()
        r2, s2 = c2[0].upper(), c2[1].lower()
        suited = s1 == s2
        rank_order = "23456789TJQKA"
        r1_idx = rank_order.index(r1) if r1 in rank_order else -1
        r2_idx = rank_order.index(r2) if r2 in rank_order else -1
        if r1_idx < 0 or r2_idx < 0:
            return 0
        if r2_idx > r1_idx:
            r1, r2 = r2, r1
            r1_idx, r2_idx = r2_idx, r1_idx
        if r1 == r2:
            key = r1 + r2
        else:
            key = r1 + r2 + ("s" if suited else "o")
        if key in self.HAND_STRENGTH:
            return self.HAND_STRENGTH[key]
        # Fallback for hands not in table
        if r1 == r2:
            return min(100, 35 + r1_idx * 5)
        if suited:
            return min(90, 20 + r1_idx * 3 + r2_idx * 2)
        return min(80, 15 + r1_idx * 3 + r2_idx)

    def calc_ev_diff(self, hand, settings):
        strength = self.get_hand_strength(hand.hero_cards)
        if strength == 0 or hand.pot <= 0:
            return 0.0
        pos = hand.hero_position or "MP"
        pos_mult = self.POSITION_MULT.get(pos, 1.0)
        adj_strength = min(100, strength * pos_mult)
        winrate = adj_strength / 100.0
        expected_result = hand.pot * (2 * winrate - 1)
        ev_diff = hand.hero_won - expected_result
        return round(ev_diff, 1)


# ─── Session Tilt Meter ──────────────────────────────────────────────────────
class TiltMeter:
    """Analyzes hero's recent play patterns to detect tilt."""

    def __init__(self, settings, window_size=20):
        self.settings = settings
        self.window_size = window_size

    def analyze(self, hands):
        if not hands or len(hands) < 5:
            return {"score": 0, "label": "Cool", "emoji": "COOL",
                    "color": GREEN, "indicators": [],
                    "advice": "Not enough data to analyze tilt."}

        sorted_hands = sorted(hands, key=lambda h: h.date or datetime.min, reverse=True)
        recent = sorted_hands[:self.window_size]
        baseline = sorted_hands

        base_vpip = self._calc_vpip(baseline)
        base_pfr = self._calc_pfr(baseline)
        base_af = self._calc_af(baseline)
        base_avg_pot = self._avg_pot(baseline)

        rec_vpip = self._calc_vpip(recent)
        rec_pfr = self._calc_pfr(recent)
        rec_af = self._calc_af(recent)
        rec_avg_pot = self._avg_pot(recent)
        rec_net = sum(h.hero_won for h in recent)

        rec_ep = sum(1 for h in recent if h.hero_position in ("EP", "MP")) / max(len(recent), 1)
        base_ep = sum(1 for h in baseline if h.hero_position in ("EP", "MP")) / max(len(baseline), 1)

        score = 0
        indicators = []

        vpip_diff = rec_vpip - base_vpip
        if vpip_diff > 10:
            score += 25
            indicators.append(f"VPIP spiked +{vpip_diff:.0f}% vs baseline")
        elif vpip_diff > 5:
            score += 12
            indicators.append(f"VPIP up +{vpip_diff:.0f}% vs baseline")

        pfr_diff = base_pfr - rec_pfr
        if pfr_diff > 8:
            score += 20
            indicators.append(f"PFR dropped {pfr_diff:.0f}% (passive)")
        elif pfr_diff > 4:
            score += 10
            indicators.append(f"PFR down {pfr_diff:.0f}%")

        af_diff = base_af - rec_af
        if af_diff > 1.0:
            score += 15
            indicators.append(f"AF dropped {af_diff:.1f} (calling more)")
        elif af_diff > 0.5:
            score += 8
            indicators.append(f"AF down {af_diff:.1f}")

        if rec_net < 0:
            loss_severity = min(abs(rec_net) / max(base_avg_pot * 10, 1) * 15, 20)
            score += int(loss_severity)
            indicators.append(f"Recent net: {rec_net:+.0f} (losing)")

        if base_avg_pot > 0:
            pot_ratio = rec_avg_pot / base_avg_pot
            if pot_ratio > 1.5:
                score += 15
                indicators.append(f"Avg pot {pot_ratio:.1f}x bigger (chasing)")
            elif pot_ratio > 1.2:
                score += 8
                indicators.append(f"Avg pot {pot_ratio:.1f}x larger")

        ep_diff = rec_ep - base_ep
        if ep_diff > 0.15:
            score += 10
            indicators.append("Playing more hands from early position")

        score = min(100, max(0, score))

        if score <= 25:
            label, emoji, color = "Cool", "COOL", GREEN
            advice = "You are playing your A-game. Stay focused!"
        elif score <= 50:
            label, emoji, color = "Warm", "WARM", YELLOW
            advice = "Some tilt indicators detected. Take a short break if needed."
        elif score <= 75:
            label, emoji, color = "Heated", "HOT!", ORANGE
            advice = "Significant tilt detected! Consider stopping or taking a 15-min break."
        else:
            label, emoji, color = "Tilting", "TILT", RED
            advice = "STOP PLAYING! You are on heavy tilt. Walk away and come back later."

        return {"score": score, "label": label, "emoji": emoji, "color": color,
                "indicators": indicators, "advice": advice}

    def _calc_vpip(self, hands):
        t: int = 0
        v: int = 0
        for h in hands:
            hero = h.hero_name(self.settings)
            if not hero:
                continue
            t += 1
            pf = h.streets[0] if h.streets else None
            if pf:
                for act in pf["actions"]:
                    if act["player"] == hero and act["action"] in ("call", "raise", "bet"):
                        v += 1
                        break
        return 100 * v / max(t, 1)

    def _calc_pfr(self, hands):
        t: int = 0
        p: int = 0
        for h in hands:
            hero = h.hero_name(self.settings)
            if not hero:
                continue
            t += 1
            pf = h.streets[0] if h.streets else None
            if pf:
                for act in pf["actions"]:
                    if act["player"] == hero and act["action"] in ("raise", "bet"):
                        p += 1
                        break
        return 100 * p / max(t, 1)

    def _calc_af(self, hands):
        br: int = 0
        ca: int = 0
        for h in hands:
            hero = h.hero_name(self.settings)
            if not hero:
                continue
            for street in h.streets:
                for act in street["actions"]:
                    if act["player"] == hero:
                        if act["action"] in ("bet", "raise"):
                            br += 1
                        if act["action"] == "call":
                            ca += 1
        return br / max(ca, 1)

    def _avg_pot(self, hands):
        if not hands:
            return 0
        return sum(h.pot for h in hands) / len(hands)


# ─── Live HUD — Seat Layouts ─────────────────────────────────────────────────
# Positions as (x_pct, y_pct) relative to the poker window size.
# x=0 is left edge, y=0 is top edge. Side seats stay inset (~12%+) from left/right
# margins so badges do not cover BetACR action buttons or side info panels.
SEAT_POSITIONS = {
    2: {
        1: (0.50, 0.82),
        2: (0.50, 0.12),
    },
    6: {
        1: (0.50, 0.88),   # hero / bottom center
        2: (0.76, 0.74),   # CO — inset from right
        3: (0.78, 0.34),   # MP — inset from right
        4: (0.62, 0.12),   # HJ / top-right arc
        5: (0.38, 0.12),   # UTG / top-left arc
        6: (0.22, 0.34),   # BB — inset from left
    },
    9: {
        1: (0.50, 0.88),   # hero / bottom center
        2: (0.72, 0.82),
        3: (0.78, 0.60),
        4: (0.72, 0.18),
        5: (0.62, 0.10),
        6: (0.38, 0.10),
        7: (0.28, 0.18),
        8: (0.22, 0.60),
        9: (0.28, 0.82),
    },
}


def build_hero_anchored_seat_slots(seat_map, layout_key):
    """Map hand-history seat numbers to layout slots with hero anchored at slot 1 (bottom)."""
    layout = SEAT_POSITIONS.get(layout_key, {})
    layout_slots = sorted(layout.keys())
    if not layout_slots or not seat_map:
        return {}

    seats_sorted = sorted(seat_map.keys())
    n = len(seats_sorted)
    hero_seat = next(
        (seat for seat, info in seat_map.items() if info.get("is_hero")),
        seats_sorted[0],
    )

    hero_idx = seats_sorted.index(hero_seat)
    slot_count = min(n, len(layout_slots))
    return {
        seat: layout_slots[(seats_sorted.index(seat) - hero_idx) % slot_count]
        for seat in seat_map
    }


def tag_hero_seats(seat_map, settings, site):
    """Mark hero seat using settings aliases when DB is_hero flag is missing."""
    if any(info.get("is_hero") for info in seat_map.values()):
        return seat_map
    aliases = set(hero_aliases_from_settings(settings, site))
    if not aliases:
        return seat_map
    tagged = {seat: dict(info) for seat, info in seat_map.items()}
    for seat, info in tagged.items():
        if info.get("name") in aliases:
            info["is_hero"] = True
            break
    return tagged


EXPLOIT_TIPS = {
    "Calling Station": "value bet thin, no bluffs",
    "Fish":            "bet all streets for value",
    "Nit":             "steal blinds, fold to 3bets",
    "LAG":             "trap, call down wide",
    "Maniac":          "pot control, let them bluff",
    "TAG":             "balanced, respect 3bets",
    "Regular":         "stay unexploitable",
    "Whale":           "bet large for value",
    "Rec":             "value bet, avoid big bluffs",
    "Unknown":         "not enough data",
}

TYPE_COLORS = {
    "Calling Station": "red", "Fish": "red", "Maniac": "red",
    "Nit": "text_dim", "Unknown": "text_dim",
    "TAG": "green", "Regular": "text",
    "LAG": "yellow", "Whale": "gold", "Rec": "orange",
}

# DriveHUD2-style presets — grid layout, 4 stat columns, 2 stat rows
HUD_DENSITY_PRESETS = {
    # mini: name + 4 primary stats in one row only
    "mini": {
        "width": 118, "height": 40, "corner": 9,
        "rows": 1,
        "name_font":  ("Consolas", 9,  "bold"),
        "label_font": ("Consolas", 7,  ""),
        "value_font": ("Consolas", 9,  "bold"),
        "type_font":  ("Consolas", 7,  "bold"),
    },
    # compact: name + 4 primary stats + 2 secondary
    "compact": {
        "width": 142, "height": 60, "corner": 11,
        "rows": 2,
        "name_font":  ("Consolas", 9,  "bold"),
        "label_font": ("Consolas", 8,  ""),
        "value_font": ("Consolas", 10, "bold"),
        "type_font":  ("Consolas", 8,  "bold"),
    },
    # standard: full DH2 layout — 4+4 stats + type footer
    "standard": {
        "width": 166, "height": 78, "corner": 13,
        "rows": 2,
        "name_font":  ("Consolas", 10, "bold"),
        "label_font": ("Consolas", 8,  ""),
        "value_font": ("Consolas", 11, "bold"),
        "type_font":  ("Consolas", 8,  "bold"),
    },
    # expanded: larger version with exploit tip
    "expanded": {
        "width": 202, "height": 94, "corner": 15,
        "rows": 2,
        "name_font":  ("Consolas", 11, "bold"),
        "label_font": ("Consolas", 9,  ""),
        "value_font": ("Consolas", 12, "bold"),
        "type_font":  ("Consolas", 9,  "bold"),
    },
}


def hud_edge_margin_pct(settings):
    try:
        pct = float(settings.get("hud_edge_margin_pct", 0.12))
    except (TypeError, ValueError):
        pct = 0.12
    return max(0.05, min(0.25, pct))


def hud_badge_scale(settings):
    try:
        scale = float(settings.get("hud_badge_scale", 1.5))
    except (TypeError, ValueError):
        scale = 1.5
    return max(0.8, min(2.5, scale))


def scaled_hud_density_preset(density, scale=1.0):
    base = HUD_DENSITY_PRESETS.get(str(density or "standard").lower(),
                                   HUD_DENSITY_PRESETS["standard"])
    if scale == 1.0:
        return base
    scaled = dict(base)
    scaled["width"] = int(round(base["width"] * scale))
    scaled["height"] = int(round(base["height"] * scale))
    scaled["corner"] = max(6, int(round(base["corner"] * scale)))
    for key in ("name_font", "label_font", "value_font", "type_font"):
        fam, size, *rest = base[key]
        scaled[key] = (fam, max(7, int(round(size * scale))), *rest)
    return scaled


def clamp_badge_position(px, py, badge_w, badge_h, win_w, win_h, edge_margin_pct=0.12):
    """Keep badge x inside a safe horizontal band; y inside the overlay window."""
    margin_x = int(win_w * edge_margin_pct)
    min_x = max((badge_w // 2) + 6, margin_x)
    max_x = min(win_w - (badge_w // 2) - 6, win_w - margin_x)
    px = max(min_x, min(max_x, px))
    py = max(6, min(win_h - badge_h - 6, py))
    return px, py


HUD_SITE_PRESETS = {
    "CoinPoker": {"anchor": "top-left", "summary_offset": (0, 0), "badge_offset": (0, 0)},
    "ACR":       {"anchor": "top-right", "summary_offset": (-6, 0), "badge_offset": (-18, 0)},
    "BetACR":    {"anchor": "top-right", "summary_offset": (-6, 0), "badge_offset": (-18, 0)},
    "GGPoker":   {"anchor": "top-left", "summary_offset": (6, 0), "badge_offset": (18, 0)},
    "ReplayPoker": {"anchor": "top-left", "summary_offset": (0, 0), "badge_offset": (0, 0)},
    "Unknown":   {"anchor": "top-left", "summary_offset": (0, 0), "badge_offset": (0, 0)},
}

# Magenta punch-through colour — must not appear in any theme palette.
# Windows renders pixels of this exact colour as fully transparent (colorkey).
_HUD_COLORKEY    = "#ff00ff"
_HUD_COLORKEY_BGR = 0x00FF00FF  # COLORREF is BGR; magenta is symmetric


def _setup_hud_interactive_host(toplevel):
    """Layered colorkey host that always receives mouse input (never click-through)."""
    try:
        import ctypes
        toplevel.update_idletasks()
        inner = toplevel.winfo_id()
        _ga = ctypes.windll.user32.GetAncestor
        _ga.restype = ctypes.c_size_t
        root_hwnd = int(_ga(inner, 2)) or inner
        GWL_EXSTYLE = -20
        WS_EX_LAYERED = 0x00080000
        WS_EX_NOACTIVATE = 0x08000000
        WS_EX_TRANSPARENT = 0x00000020
        style = ctypes.windll.user32.GetWindowLongW(root_hwnd, GWL_EXSTYLE)
        style |= WS_EX_LAYERED | WS_EX_NOACTIVATE
        style &= ~WS_EX_TRANSPARENT
        ctypes.windll.user32.SetWindowLongW(root_hwnd, GWL_EXSTYLE, style)
        ctypes.windll.user32.SetLayeredWindowAttributes(root_hwnd, _HUD_COLORKEY_BGR, 0, 1)
    except Exception:
        pass


# ─── Live HUD — Table Detector ───────────────────────────────────────────────
# BetACR / ACR (WPN) tournament tables only — never anchor to LeakSnipe, Cursor, etc.
_HUD_OWN_PID = os.getpid()

_HUD_WINDOW_BLACKLIST = (
    "leaksnipe",
    "leak snipe",
    "poker tracker",
    "cursor",
    "visual studio",
    "vscode",
    "code - ",
    "poker_gui",
    "customtkinter",
    "electron",
    "tauri",
    "chrome",
    "mozilla firefox",
    "microsoft edge",
    "msedge",
    "discord",
    "slack",
    "notepad",
    "powershell",
    "windows terminal",
    "cmd.exe",
    "task manager",
)
_ACR_LOBBY_PATTERNS = (
    "acr poker lobby",
    "americas cardroom lobby",
    "winning poker lobby",
    "coinpoker lobby",
    "ggpoker lobby",
    "pokerstars lobby",
)
_ACR_GAME_HINTS = ("hold'em", "holdem", "omaha", "stud", "razz")
_ACR_TABLE_HINTS = (
    "table", " - no limit", " - pot limit", " - omaha", " - stud",
    "tournament", " gtd", " pko", " bounty", "turbo", " hyper",
    "sit & go", "sit&go", "sng",
)


def _hud_is_own_window(hwnd) -> bool:
    if not HAS_WIN32 or win32process is None:
        return False
    try:
        _, pid = win32process.GetWindowThreadProcessId(hwnd)
        return pid == _HUD_OWN_PID
    except Exception:
        return False


def _hud_title_blacklisted(title: str) -> bool:
    tl = (title or "").lower()
    return any(b in tl for b in _HUD_WINDOW_BLACKLIST)


def _is_acr_table_window(title: str, hwnd=None) -> bool:
    """True for visible ACR/WPN table windows (not lobbies, IDE, or LeakSnipe)."""
    if hwnd is not None and _hud_is_own_window(hwnd):
        return False
    if not title or _hud_title_blacklisted(title):
        return False
    tl = title.lower()
    if any(lp in tl for lp in _ACR_LOBBY_PATTERNS):
        return False
    if "lobby" in tl and not any(g in tl for g in _ACR_GAME_HINTS):
        return False
    has_game = any(g in tl for g in _ACR_GAME_HINTS)
    has_table = any(t in tl for t in _ACR_TABLE_HINTS)
    return has_game and has_table


def _extract_table_key_from_title(title: str) -> str:
    """Normalized table identifier from an ACR/WPN window title."""
    if not title:
        return ""
    m = re.search(r"Table\s+'([^']+)'", title, re.I)
    if m:
        return m.group(1).strip().lower()
    m = re.search(r"Table\s+(\d+)\b", title, re.I)
    if m:
        return m.group(1).strip().lower()
    m = re.search(r"Tournament\s+#(\d+)", title, re.I)
    if m:
        return f"t{m.group(1)}"
    return ""


def _table_keys_match(window_key: str, hand_table_name: str) -> bool:
    """True when a window table key matches a hand-history table name."""
    if not window_key:
        return True
    ht = (hand_table_name or "").strip().lower()
    if not ht:
        return False
    wk = window_key.lower()
    if wk == ht:
        return True
    if wk.isdigit():
        if re.search(rf"\btable\s+{re.escape(wk)}\b", ht):
            return True
        if ht == wk or ht.endswith(wk):
            return True
    if wk.startswith("t") and wk[1:].isdigit():
        return wk[1:] in ht
    return wk in ht or ht in wk


def _seat_map_signature(seat_map) -> tuple:
    """Stable roster signature for diffing seat maps."""
    items = []
    for seat, info in seat_map.items():
        items.append((int(seat), str(info.get("name") or "").strip(), bool(info.get("is_hero"))))
    return tuple(sorted(items))


class TableDetector:
    """Finds the active poker client window using win32gui and tracks its screen rect."""

    WINDOW_TITLES = []  # legacy — matching uses _is_acr_table_window()

    # Lobby-only patterns — never used as HUD anchors
    _LOBBY_PATTERNS = set(_ACR_LOBBY_PATTERNS)

    def __init__(self, on_rect_change=None, poll_interval=1.5):
        self.on_rect_change = on_rect_change
        self.poll_interval = poll_interval
        self._stop = threading.Event()
        self._thread = None
        self._last_rect = None

    def _find_window(self):
        """Return (hwnd, x, y, w, h) for the best matching poker window, or None."""
        if not HAS_WIN32:
            return None
        tables = []

        def _cb(hwnd, _):
            if not win32gui.IsWindowVisible(hwnd) or _hud_is_own_window(hwnd):
                return
            title = win32gui.GetWindowText(hwnd)
            if not _is_acr_table_window(title, hwnd):
                return
            try:
                r = win32gui.GetWindowRect(hwnd)
                x, y, x2, y2 = r
                w, h = x2 - x, y2 - y
                if w > 200 and h > 150:
                    tables.append((hwnd, x, y, w, h))
            except Exception:
                pass

        win32gui.EnumWindows(_cb, None)
        result = tables[0] if tables else None
        logging.debug(f"TableDetector: acr_tables={len(tables)} result={result}")
        return result

    def get_rect(self):
        """Return current (x, y, w, h) of the poker window, or None."""
        found = self._find_window()
        return found[1:] if found else None

    def start(self):
        self._stop.clear()
        self._thread = threading.Thread(target=self._poll, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()

    def _poll(self):
        while not self._stop.is_set():
            rect = self.get_rect()
            if rect != self._last_rect:
                self._last_rect = rect
                if self.on_rect_change:
                    self.on_rect_change(rect)
            self._stop.wait(self.poll_interval)


class MultiTableDetector:
    """Finds ALL active BetACR/ACR table windows and fires add/remove/move callbacks."""

    WINDOW_TITLES = []  # legacy — matching uses _is_acr_table_window()
    _LOBBY_PATTERNS = set(_ACR_LOBBY_PATTERNS)
    MIN_W, MIN_H = 150, 100  # catch small tables too

    def __init__(
        self,
        on_table_added=None,
        on_table_removed=None,
        on_table_moved=None,
        on_table_switched=None,
        poll_interval=1.5,
    ):
        self.on_table_added   = on_table_added
        self.on_table_removed = on_table_removed
        self.on_table_moved   = on_table_moved
        self.on_table_switched = on_table_switched
        self.poll_interval    = poll_interval
        self._stop   = threading.Event()
        self._thread = None
        self._tables: dict = {}  # hwnd → (x, y, w, h)
        self._titles: dict = {}  # hwnd → window title

    def find_all_windows(self):
        """Return {hwnd: (x, y, w, h, title, is_lobby)} for all matching visible windows."""
        if not HAS_WIN32:
            return {}
        found = {}
        def _cb(hwnd, _):
            if not win32gui.IsWindowVisible(hwnd) or _hud_is_own_window(hwnd):
                return
            title = win32gui.GetWindowText(hwnd)
            if not _is_acr_table_window(title, hwnd):
                return
            try:
                r = win32gui.GetWindowRect(hwnd)
                x, y, x2, y2 = r
                w, h = x2 - x, y2 - y
                if w >= self.MIN_W and h >= self.MIN_H:
                    found[hwnd] = (x, y, w, h, title, False)
            except Exception:
                pass
        win32gui.EnumWindows(_cb, None)
        logging.debug(f"MultiTableDetector: {len(found)} ACR table(s) found")
        return found

    def get_window_title(self, hwnd):
        try:
            return win32gui.GetWindowText(hwnd)
        except Exception:
            return ""

    def get_all_rects(self):
        return {h: v[:4] for h, v in self.find_all_windows().items()}

    def start(self):
        self._stop.clear()
        self._tables = {}
        self._titles = {}
        self._thread = threading.Thread(target=self._poll, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()

    def _poll(self):
        while not self._stop.is_set():
            try:
                self._check()
            except Exception as e:
                logging.debug(f"MultiTableDetector poll error: {e}")
            self._stop.wait(self.poll_interval)

    def _check(self):
        current_full = self.find_all_windows()
        current = {h: v[:4] for h, v in current_full.items()}
        current_titles = {h: v[4] for h, v in current_full.items()}
        prev = self._tables
        prev_set = set(prev.keys())
        current_set = set(current.keys())
        for hwnd in prev_set - current_set:
            logging.info(f"Table removed: hwnd={hwnd}")
            self._titles.pop(hwnd, None)
            if self.on_table_removed:
                self.on_table_removed(hwnd)
        for hwnd in current_set - prev_set:
            title = current_titles.get(hwnd, "")
            logging.info(f"Table added: hwnd={hwnd} rect={current[hwnd]} title={title!r}")
            if self.on_table_added:
                self.on_table_added(hwnd, current[hwnd], title)
        for hwnd in current_set & prev_set:
            old_title = self._titles.get(hwnd, "")
            new_title = current_titles.get(hwnd, "")
            if new_title and new_title != old_title:
                logging.info(f"Table switched: hwnd={hwnd} {old_title!r} -> {new_title!r}")
                if self.on_table_switched:
                    self.on_table_switched(hwnd, old_title, new_title, current[hwnd])
            elif current[hwnd] != prev[hwnd]:
                if self.on_table_moved:
                    self.on_table_moved(hwnd, current[hwnd], new_title)
        self._tables = dict(current)
        self._titles = dict(current_titles)


# ─── Live HUD — Current Hand Monitor ────────────────────────────────────────
class CurrentHandMonitor:
    """Polls poker_hands.db for the latest hand and emits a callback when it changes."""

    def __init__(self, db, settings, on_new_hand=None, poll_interval=2.0):
        self.db = db
        self.settings = settings
        self.on_new_hand = on_new_hand
        self.poll_interval = poll_interval
        self._stop = threading.Event()
        self._thread = None
        self._last_hand_id = None

    def start(self):
        self._stop.clear()
        self._thread = threading.Thread(target=self._poll, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()

    def _poll(self):
        while not self._stop.is_set():
            try:
                self._check()
            except Exception:
                pass
            self._stop.wait(self.poll_interval)

    def _check(self):
        with self.db.lock:
            conn = self.db._connect()
            try:
                conn.row_factory = sqlite3.Row
                row = conn.execute(
                    "SELECT hand_id, max_seats, site FROM hands ORDER BY imported_at DESC LIMIT 1"
                ).fetchone()
                if not row:
                    return
                hand_id = row["hand_id"]
                max_seats = row["max_seats"] or 6
                site = row["site"] or "Unknown"
                if hand_id == self._last_hand_id:
                    return
                self._last_hand_id = hand_id
                players = conn.execute(
                    "SELECT seat, name, is_hero FROM players WHERE hand_id = ?", (hand_id,)
                ).fetchall()
                seat_map = {
                    p["seat"]: {"name": p["name"], "is_hero": bool(p["is_hero"])}
                    for p in players
                }
            finally:
                conn.close()

        if self.on_new_hand:
            self.on_new_hand(hand_id, seat_map, max_seats, site)


class MultiHandMonitor:
    """Polls poker_hands.db per ACR table window and emits roster/hand updates."""

    def __init__(self, db, settings, on_hand_update=None, poll_interval=2.0):
        self.db = db
        self.settings = settings
        self.on_hand_update = on_hand_update
        self.poll_interval = poll_interval
        self._stop = threading.Event()
        self._thread = None
        self._windows: Dict[int, str] = {}  # hwnd → table_key from window title
        self._last_sig: Dict[int, tuple] = {}  # hwnd → (hand_id, roster_signature)
        self._check_lock = threading.Lock()

    def register_window(self, hwnd, title: str = ""):
        key = _extract_table_key_from_title(title)
        with self._check_lock:
            prev = self._windows.get(hwnd)
            self._windows[hwnd] = key
            if prev != key:
                self._last_sig.pop(hwnd, None)

    def unregister_window(self, hwnd):
        with self._check_lock:
            self._windows.pop(hwnd, None)
            self._last_sig.pop(hwnd, None)

    def check_now(self):
        """Immediate poll — call after HH import or table switch."""
        try:
            self._check()
        except Exception:
            logging.exception("Live hand monitor check_now failed")

    def start(self):
        self._stop.clear()
        self._thread = threading.Thread(target=self._poll, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()

    def _poll(self):
        while not self._stop.is_set():
            try:
                self._check()
            except Exception:
                pass
            self._stop.wait(self.poll_interval)

    def _load_recent_hands(self, conn):
        conn.row_factory = sqlite3.Row
        return conn.execute(
            "SELECT hand_id, max_seats, site, table_name FROM hands "
            "ORDER BY imported_at DESC LIMIT 80"
        ).fetchall()

    def _load_seat_map(self, conn, hand_id):
        players = conn.execute(
            "SELECT seat, name, is_hero FROM players WHERE hand_id = ?", (hand_id,)
        ).fetchall()
        return {
            p["seat"]: {"name": p["name"], "is_hero": bool(p["is_hero"])}
            for p in players
        }

    def _pick_hand_for_table(self, rows, table_key: str):
        if table_key:
            for row in rows:
                if _table_keys_match(table_key, row["table_name"] or ""):
                    return row
        return None

    def _check(self):
        with self._check_lock:
            windows = dict(self._windows)
        if not windows:
            return

        with self.db.lock:
            conn = self.db._connect()
            try:
                rows = self._load_recent_hands(conn)
                if not rows:
                    return
                fallback_row = rows[0]
                updates = []
                for hwnd, table_key in windows.items():
                    row = self._pick_hand_for_table(rows, table_key)
                    if row is None and len(windows) == 1:
                        row = fallback_row
                    if row is None:
                        continue
                    hand_id = row["hand_id"]
                    seat_map = self._load_seat_map(conn, hand_id)
                    sig = (hand_id, _seat_map_signature(seat_map))
                    if self._last_sig.get(hwnd) == sig:
                        continue
                    self._last_sig[hwnd] = sig
                    updates.append((
                        hwnd,
                        hand_id,
                        seat_map,
                        row["max_seats"] or 6,
                        row["site"] or "BetACR",
                        row["table_name"] or "",
                    ))
            finally:
                conn.close()

        if self.on_hand_update:
            for payload in updates:
                self.on_hand_update(*payload)


# ─── Live HUD — Stat Tooltip ─────────────────────────────────────────────────
class HUDStatTooltip(tk.Toplevel):
    """DriveHUD2-style hover popup: overall stats + per-position VPIP/PFR (9-max)."""

    W, H = 260, 285

    def __init__(self, parent_widget, theme, player_name: str, stat: dict, db):
        super().__init__(parent_widget.winfo_toplevel())
        self.overrideredirect(True)
        self.attributes("-topmost", True)
        self.configure(bg=theme["bg_base"])
        t = theme
        try:
            bx = parent_widget.winfo_rootx()
            by = parent_widget.winfo_rooty()
            bw = parent_widget.winfo_width()
        except Exception:
            bx, by, bw = 0, 0, 0
        tx = bx + bw + 4
        if tx + self.W > self.winfo_screenwidth():
            tx = bx - self.W - 4
        self.geometry(f"{self.W}x{self.H}+{tx}+{by}")
        self._canvas = tk.Canvas(self, width=self.W, height=self.H,
                                 bg=t["bg_base"], highlightthickness=0)
        self._canvas.pack()
        self._draw_loading(t, player_name)
        import threading
        def _fetch():
            pos_stats = db.get_player_position_stats(player_name)
            try:
                self.after(0, lambda: self._draw_full(t, player_name, stat, pos_stats))
            except Exception:
                pass
        threading.Thread(target=_fetch, daemon=True).start()

    def _rr(self, x1, y1, x2, y2, r, **kw):
        return self._canvas.create_polygon(
            [x1+r,y1, x2-r,y1, x2,y1, x2,y1+r, x2,y2-r,
             x2,y2, x2-r,y2, x1+r,y2, x1,y2, x1,y2-r, x1,y1+r, x1,y1],
            smooth=True, **kw)

    def _draw_loading(self, t, player_name):
        c = self._canvas; c.delete("all")
        self._rr(4, 4, self.W-4, self.H-4, 12, fill=_lighten(t["bg_card"], 0.04), outline=t["border"], width=1)
        c.create_text(14, 20, text=player_name[:22], anchor="w", fill=t["text"], font=("Consolas", 11, "bold"))
        c.create_text(14, 42, text="Loading position stats…", anchor="w", fill=t["text_dim"], font=("Consolas", 9))

    def _draw_full(self, t, player_name, stat, pos_stats):
        if not self.winfo_exists():
            return
        c = self._canvas; c.delete("all")
        self._rr(4, 4, self.W-4, self.H-4, 12, fill=_lighten(t["bg_card"], 0.04), outline=t["border"], width=1)
        self._rr(4, 4, self.W-4, 36, 12, fill=_darken(t["bg_card"], 0.06), outline="")
        c.create_text(14, 20, text=player_name[:22], anchor="w", fill=t["text"], font=("Consolas", 11, "bold"))
        # Overall row
        overall = [
            ("VPIP", f"{stat['vpip']:.0f}%" if stat and stat.get("vpip") else "–"),
            ("PFR",  f"{stat['pfr']:.0f}%"  if stat and stat.get("pfr")  else "–"),
            ("AF",   f"{stat['af']:.1f}"     if stat and stat.get("af")   else "–"),
            ("WSD",  f"{stat['wtsd']:.0f}%"  if stat and stat.get("wtsd") else "–"),
        ]
        cw = (self.W - 20) // 4
        for i, (lbl, val) in enumerate(overall):
            cx = 14 + i * cw + cw // 2
            c.create_text(cx, 46, text=lbl, anchor="center", fill=t["text_dim"], font=("Consolas", 8))
            c.create_text(cx, 60, text=val, anchor="center", fill=t["text"],     font=("Consolas", 10, "bold"))
        # Divider
        c.create_line(14, 78, self.W-14, 78, fill=t["border"])
        # Column headers
        c.create_text(14,  90, text="Pos",   anchor="w", fill=t["text_dim"], font=("Consolas", 8, "bold"))
        c.create_text(90,  90, text="VPIP",  anchor="w", fill=t["text_dim"], font=("Consolas", 8, "bold"))
        c.create_text(150, 90, text="PFR",   anchor="w", fill=t["text_dim"], font=("Consolas", 8, "bold"))
        c.create_text(210, 90, text="Hands", anchor="w", fill=t["text_dim"], font=("Consolas", 8, "bold"))
        # 9 position rows
        for i, pos in enumerate(["UTG", "UTG+1", "UTG+2", "MP", "HJ", "CO", "BTN", "SB", "BB"]):
            ry = 106 + i * 18
            ps = pos_stats.get(pos, {})
            n  = ps.get("hands", 0)
            if pos == "BTN":
                c.create_rectangle(12, ry-7, self.W-12, ry+9, fill=_lighten(t["bg_panel"], 0.06), outline="")
            c.create_text(14,  ry, text=pos,
                          anchor="w", fill=t["gold"] if pos == "BTN" else t["text"], font=("Consolas", 9, "bold"))
            c.create_text(90,  ry, text=f"{ps['vpip']:.0f}%" if n else "–", anchor="w", fill=t["text"],     font=("Consolas", 9))
            c.create_text(150, ry, text=f"{ps['pfr']:.0f}%"  if n else "–", anchor="w",
                          fill=t["green"] if n else t["text_dim"], font=("Consolas", 9))
            c.create_text(210, ry, text=str(n) if n else "–", anchor="w", fill=t["text_dim"], font=("Consolas", 8))


# ─── Live HUD — Seat Badge ───────────────────────────────────────────────────
def _hud_stat_color(t, stat_name, value):
    """DriveHUD2-style colour coding for HUD stat values."""
    if not value:
        return t.get("text_dim", "#888")
    if stat_name == "vpip":
        if 15 <= value <= 28: return t.get("green",  "#4CAF50")
        if 10 <= value < 15 or 28 < value <= 35: return t.get("yellow", "#FFC107")
        return t.get("red", "#F44336")
    if stat_name == "pfr":
        if 10 <= value <= 22: return t.get("green",  "#4CAF50")
        if  8 <= value < 10 or 22 < value <= 30: return t.get("yellow", "#FFC107")
        return t.get("red", "#F44336")
    if stat_name == "af":
        if 1.5 <= value <= 4.0: return t.get("green",  "#4CAF50")
        if 1.0 <= value < 1.5 or 4.0 < value <= 6.0: return t.get("yellow", "#FFC107")
        return t.get("red", "#F44336")
    if stat_name == "wtsd":
        if 25 <= value <= 35: return t.get("green",  "#4CAF50")
        if 20 <= value < 25 or 35 < value <= 45: return t.get("yellow", "#FFC107")
        return t.get("red", "#F44336")
    if stat_name == "fold_cbet":
        if value >= 55: return t.get("green",  "#4CAF50")
        if value >= 30: return t.get("yellow", "#FFC107")
        return t.get("red", "#F44336")
    return t.get("text", "#DDD")


# ─── Live HUD — Seat Badge (DriveHUD2-style) ─────────────────────────────────
class SeatBadge(tk.Canvas):
    """DriveHUD2-style seat badge: floating name label + dark grid card with
    colour-coded stats (VPIP/PFR/AF/WSD row-1; FCBet/type row-2)."""

    W, H = 166, 96   # class-level defaults; overridden per preset

    # Primary stats: 4 columns, always shown
    _ROW1 = [
        ("VPIP",  "vpip",      "%"),
        ("PFR",   "pfr",       "%"),
        ("AF",    "af",        ""),
        ("H",     "hands",     ""),
    ]
    # Secondary stats: shown when rows==2
    _ROW2 = [
        ("WSD",   "wtsd",      "%"),
        ("FCBet", "fold_cbet", "%"),
        (None, None, None),   # spacer
        (None, None, None),   # spacer
    ]

    def __init__(self, parent, theme, player_info, stat, density="standard", db=None, loading=False,
                 badge_scale=1.0, **kwargs):
        t = theme
        self._theme = t
        self._stat  = stat
        self._db    = db
        self._loading = loading

        p = scaled_hud_density_preset(density, badge_scale)
        LABEL_H = max(16, int(round(20 * badge_scale)))
        W  = p["width"]
        CH = p["height"]      # card height
        H  = CH + LABEL_H     # total canvas height
        self.W, self.H = W, H

        super().__init__(parent, width=W, height=H,
                         bg=_HUD_COLORKEY, highlightthickness=0, **kwargs)

        name           = player_info.get("name", "?")
        self._player_name = name
        if loading:
            classification = "..."
        else:
            classification = (stat.get("effective_type") or "Unknown") if stat else "Unknown"

        # ── Floating name label (no background — transparent to table) ────────
        self.create_text(W // 2, LABEL_H // 2, text=name[:22], anchor="center",
                         fill="#FFFFFF", font=p["name_font"])

        # ── Card background ───────────────────────────────────────────────────
        r   = p["corner"]
        BG  = _darken(t.get("bg_card", "#1a1a2e"), 0.05)
        BDR = t.get("border", "#333355")
        # drop-shadow
        self._rrect(5, LABEL_H + 5, W - 1, H - 1, r, fill=_darken(BG, 0.5), outline="")
        # card body
        self._rrect(0, LABEL_H, W - 6, H - 6, r, fill=BG, outline=BDR, width=1)

        # ── Header strip: type pill (left)  H:nnn (right) ────────────────────
        HEADER_H = max(14, int(round(18 * badge_scale)))
        HDR_BG   = _darken(BG, 0.08)
        self._rrect(0, LABEL_H, W - 6, LABEL_H + HEADER_H, r, fill=HDR_BG, outline="")
        # flatten bottom of header (re-draw lower half as plain rect)
        self.create_rectangle(0, LABEL_H + HEADER_H // 2,
                               W - 6, LABEL_H + HEADER_H,
                               fill=HDR_BG, outline="")

        type_color_key = TYPE_COLORS.get(classification, "text")
        accent = t.get(type_color_key, t.get("text", "#DDD"))
        # left accent bar
        self.create_rectangle(0, LABEL_H, 4, H - 6, fill=accent, outline="")
        # type label (left)
        self.create_text(10, LABEL_H + HEADER_H // 2,
                         text=classification[:14], anchor="w",
                         fill=accent, font=p["type_font"])
        # hands count (right)
        h_val = stat.get("hands", 0) if stat else 0
        self.create_text(W - 10, LABEL_H + HEADER_H // 2,
                         text=f"H:{h_val}" if h_val else "H:–", anchor="e",
                         fill=t.get("text_dim", "#888"), font=p["label_font"])

        # ── Stat grid ─────────────────────────────────────────────────────────
        COLS   = 4
        col_w  = (W - 6) / COLS
        rows   = p.get("rows", 2)
        # vertical space for stats inside card (below header, above bottom padding)
        stats_top    = LABEL_H + HEADER_H + 2
        stats_bottom = H - 8
        row_h        = (stats_bottom - stats_top) / rows

        def _draw_stat_cell(col_idx, row_idx, label, key, suffix):
            if label is None:
                return
            cx     = col_w * col_idx + col_w / 2
            cell_y = stats_top + row_h * row_idx
            lbl_y  = cell_y + 5
            val_y  = cell_y + row_h - 6

            if self._loading:
                val_str = "..."
                val_col = t.get("text_dim", "#888")
            else:
                # raw value
                raw = stat.get(key, 0) if stat else 0
                if key == "hands":
                    val_str = str(int(raw)) if raw else "–"
                    val_col = t.get("gold", "#FFD700")
                elif key == "af":
                    val_str = f"{raw:.1f}" if raw else "–"
                    val_col = _hud_stat_color(t, key, raw)
                else:
                    val_str = (f"{raw:.0f}{suffix}" if raw else "–")
                    val_col = _hud_stat_color(t, key, raw)

            # column divider (skip leftmost)
            if col_idx > 0:
                div_x = int(col_w * col_idx)
                self.create_line(div_x, stats_top + 2,
                                 div_x, stats_bottom - 2,
                                 fill=BDR)

            self.create_text(cx, lbl_y, text=label, anchor="center",
                             fill=t.get("text_dim", "#888"), font=p["label_font"])
            self.create_text(cx, val_y, text=val_str, anchor="center",
                             fill=val_col, font=p["value_font"])

        for ci, (lbl, key, sfx) in enumerate(self._ROW1):
            _draw_stat_cell(ci, 0, lbl, key, sfx)

        if rows >= 2:
            # horizontal divider between rows
            mid_y = int(stats_top + row_h)
            self.create_line(4, mid_y, W - 10, mid_y, fill=BDR)
            for ci, (lbl, key, sfx) in enumerate(self._ROW2):
                if lbl:
                    _draw_stat_cell(ci, 1, lbl, key, sfx)
                elif ci == 2 and rows >= 2:
                    # cols 2-3: exploit tip
                    tip = EXPLOIT_TIPS.get(classification, "")
                    if tip:
                        tip_cx = col_w * 2.5
                        tip_y  = stats_top + row_h * 1.5
                        self.create_text(tip_cx, tip_y - 6,
                                         text="Exploit", anchor="center",
                                         fill=t.get("text_dim", "#888"),
                                         font=p["label_font"])
                        self.create_text(tip_cx, tip_y + 6,
                                         text=tip[:22], anchor="center",
                                         fill=t.get("yellow", "#FFC107"),
                                         font=p["label_font"],
                                         width=int(col_w * 2 - 4))
                    break

        # Hover / click tooltip bindings
        self._tooltip = None
        self._pinned = False
        self._drag_moved = False
        self.bind("<Enter>", self._show_tooltip)
        self.bind("<Leave>", self._hide_tooltip)

    def _show_tooltip(self, event=None):
        if self._tooltip or not self._db:
            return
        try:
            self._tooltip = HUDStatTooltip(self, self._theme, self._player_name,
                                            self._stat, self._db)
        except Exception:
            self._tooltip = None

    def _hide_tooltip(self, event=None, *, force=False):
        if self._pinned and not force:
            return
        if self._tooltip:
            try:
                self._tooltip.destroy()
            except Exception:
                pass
            self._tooltip = None

    def toggle_pinned_stats(self):
        """Pin or unpin the full-stats tooltip (click when HUD is locked)."""
        self._pinned = not self._pinned
        if self._pinned:
            self._show_tooltip()
        else:
            self._hide_tooltip(force=True)

    def _rrect(self, x1, y1, x2, y2, r, **kw):
        pts = [x1+r,y1, x2-r,y1, x2-r,y1, x2,y1, x2,y1+r,
               x2,y2-r, x2,y2, x2-r,y2, x1+r,y2, x1,y2,
               x1,y2-r, x1,y1+r, x1,y1]
        return self.create_polygon(pts, smooth=True, **kw)


class HUDSummaryPanel(tk.Canvas):
    """Compact overlay summary card anchored to the table edge."""

    W, H = 226, 86

    def __init__(self, parent, theme, **kwargs):
        self.theme = theme
        super().__init__(parent, width=self.W, height=self.H,
                         bg=theme["bg_base"], highlightthickness=0, **kwargs)
        self.render(layout_key=6, opponent_count=0, forced=False, opacity=0.85, site="Unknown", density="standard", layout_mode=False)

    def render(self, *, layout_key, opponent_count, forced, opacity, site, density, layout_mode):
        t = self.theme
        accent = t["orange"] if layout_mode else (t["gold"] if forced else t["green"])
        self.delete("all")
        self._rrect(8, 8, self.W - 1, self.H - 1, 18, fill=_darken(t["bg_base"], 0.6), outline="")
        self._rrect(0, 0, self.W - 10, self.H - 10, 18, fill=_lighten(t["bg_card"], 0.02), outline=t["border"], width=1)
        title = "DRAG MODE" if layout_mode else "LIVE HUD"
        self.create_text(18, 18, text=title, anchor="w", fill=t["text"], font=("Consolas", 11, "bold"))
        self.create_oval(self.W - 44, 14, self.W - 30, 28, fill=accent, outline="")
        self.create_text(18, 38, text=f"Site  {site}", anchor="w", fill=t["text_dim"], font=("Consolas", 9))
        self.create_text(18, 56, text=f"Layout  {layout_key}-Max  •  Opponents {opponent_count}", anchor="w", fill=t["text_dim"], font=("Consolas", 9))
        self._pill(118, 38, 82, 18, _lighten(t["bg_panel"], 0.08), t["text"], density.title())
        self._pill(118, 60, 82, 18, _lighten(t["bg_panel"], 0.08), t["gold"], "Drag" if layout_mode else ("Locked" if forced else f"Alpha {int(opacity * 100)}%"))

    def _rrect(self, x1, y1, x2, y2, r, **kw):
        return self.create_polygon(
            [x1+r,y1, x2-r,y1, x2-r,y1, x2,y1, x2,y1+r,
             x2,y2-r, x2,y2, x2-r,y2, x1+r,y2, x1,y2,
             x1,y2-r, x1,y1+r, x1,y1],
            smooth=True, **kw
        )

    def _pill(self, x, y, w, h, fill, text_color, text):
        self._rrect(x, y, x + w, y + h, 9, fill=fill, outline="")
        self.create_text(x + (w / 2), y + (h / 2), text=text, fill=text_color, font=("Consolas", 8, "bold"))


class HUDSeatGuide(tk.Canvas):
    """Small seat anchor marker used during layout mode."""

    W, H = 38, 38

    def __init__(self, parent, theme, seat, has_custom_offset=False, **kwargs):
        self.theme = theme
        super().__init__(
            parent,
            width=self.W,
            height=self.H,
            bg=theme["bg_base"],
            highlightthickness=0,
            **kwargs,
        )
        self.render(seat, has_custom_offset)

    def render(self, seat, has_custom_offset=False):
        t = self.theme
        self.delete("all")
        accent = t["orange"] if has_custom_offset else t["text_dim"]
        fill = _lighten(t["bg_panel"], 0.08 if has_custom_offset else 0.03)
        self.create_oval(4, 4, self.W - 4, self.H - 4, fill=fill, outline=accent, width=2)
        self.create_text(self.W / 2, self.H / 2, text=str(seat), fill=t["text"], font=("Consolas", 9, "bold"))
        if has_custom_offset:
            self.create_text(self.W / 2, self.H - 7, text="R", fill=t["gold"], font=("Consolas", 7, "bold"))


class HUDLayoutHintPanel(tk.Canvas):
    """Compact shortcut strip shown while layout mode is active."""

    W, H = 332, 34

    def __init__(self, parent, theme, **kwargs):
        self.theme = theme
        super().__init__(
            parent,
            width=self.W,
            height=self.H,
            bg=theme["bg_base"],
            highlightthickness=0,
            **kwargs,
        )
        self.render("Summary")

    def render(self, target_label):
        t = self.theme
        self.delete("all")
        self.create_rectangle(5, 7, self.W - 1, self.H - 1, fill=_darken(t["bg_base"], 0.5), outline="")
        self.create_rectangle(0, 0, self.W - 6, self.H - 6, fill=_lighten(t["bg_panel"], 0.02), outline=t["border"], width=1)
        text = f"DRAG MODE — {target_label}  •  drag badges  •  Tab cycle  •  Arrows nudge  •  Esc lock"
        self.create_text(12, self.H / 2 - 3, text=text, anchor="w", fill=t["text_dim"], font=("Consolas", 8, "bold"))


class HUDCloseButton(tk.Canvas):
    """Small close control on the HUD toolbar."""

    W, H = 22, 22

    def __init__(self, parent, theme, on_click):
        super().__init__(parent, width=self.W, height=self.H,
                         bg=_HUD_COLORKEY, highlightthickness=0)
        self._theme = theme
        self._on_click = on_click
        self._draw()
        self.bind("<ButtonPress-1>", lambda _e: self._on_click())

    def _draw(self):
        self.delete("all")
        t = self._theme
        bg = _lighten(t.get("bg_panel", "#222"), 0.08)
        self.create_rectangle(0, 0, self.W, self.H, fill=bg, outline=t.get("border", "#444"))
        self.create_text(self.W / 2, self.H / 2, text="✕", fill=t.get("orange", "#ff8c00"),
                         font=("Consolas", 9, "bold"), anchor="center")


class HUDLayoutToggle(tk.Canvas):
    """Lock / unlock HUD — unlocked = drag mode for seat badges."""

    W, H = 148, 22
    RESET_W = 68
    CLOSE_W = HUDCloseButton.W
    TOOLBAR_GAP = 4
    TOOLBAR_W = W + TOOLBAR_GAP + RESET_W + TOOLBAR_GAP + CLOSE_W
    TOOLBAR_H = max(H, HUDCloseButton.H)

    def __init__(self, parent, theme, on_click):
        super().__init__(parent, width=self.W, height=self.H,
                         bg=_HUD_COLORKEY, highlightthickness=0)
        self._theme = theme
        self._on_click = on_click
        self._layout_mode = False
        self._draw()
        self.bind("<ButtonPress-1>", self._click)
        self.tag_hud_layout_toggle = True  # marker so _apply_window_interaction can find it

    def _draw(self):
        self.delete("all")
        t = self._theme
        bg = t.get("accent", t.get("orange", "#ff8c00")) if self._layout_mode else _lighten(t["bg_panel"], 0.15)
        fg = "#ffffff" if self._layout_mode else t.get("text", "#ddd")
        r = 8
        W, H = self.W, self.H
        self.create_arc(0, 0, 2*r, 2*r, start=90, extent=90, fill=bg, outline="")
        self.create_arc(W-2*r, 0, W, 2*r, start=0, extent=90, fill=bg, outline="")
        self.create_arc(0, H-2*r, 2*r, H, start=180, extent=90, fill=bg, outline="")
        self.create_arc(W-2*r, H-2*r, W, H, start=270, extent=90, fill=bg, outline="")
        self.create_rectangle(r, 0, W-r, H, fill=bg, outline="")
        self.create_rectangle(0, r, W, H-r, fill=bg, outline="")
        if self._layout_mode:
            label = "DRAG MODE — Lock [H]"
        else:
            label = "Unlock HUD [Ctrl+Shift+H]"
        self.create_text(W//2, H//2, text=label, fill=fg,
                         font=("Consolas", 6, "bold"), anchor="center")

    def set_layout_mode(self, enabled):
        self._layout_mode = bool(enabled)
        self._draw()

    def _click(self, event):
        self._on_click()


class HUDResetSeatsButton(tk.Canvas):
    """Restore default SEAT_POSITIONS for all layout slots."""

    W, H = 68, 22

    def __init__(self, parent, theme, on_click):
        super().__init__(parent, width=self.W, height=self.H,
                         bg=_HUD_COLORKEY, highlightthickness=0)
        self._theme = theme
        self._on_click = on_click
        self._draw()
        self.bind("<ButtonPress-1>", lambda _e: self._on_click())

    def _draw(self):
        self.delete("all")
        t = self._theme
        bg = _lighten(t.get("bg_panel", "#222"), 0.08)
        self.create_rectangle(0, 0, self.W, self.H, fill=bg, outline=t.get("border", "#444"))
        self.create_text(self.W / 2, self.H / 2, text="↺ Reset seats",
                         fill=t.get("gold", "#ffd700"), font=("Consolas", 7, "bold"), anchor="center")


# ─── Live HUD — Player stats cache ───────────────────────────────────────────
class PlayerStatsCache:
    """In-memory TTL cache backed by player_types SQLite reads."""

    def __init__(self, db, ttl=45.0):
        self.db = db
        self.ttl = ttl
        self._entries = {}
        self._lock = threading.Lock()

    def get_batch(self, names):
        now = time.time()
        result = {}
        missing = []
        with self._lock:
            for name in names:
                entry = self._entries.get(name)
                if entry and (now - entry[1]) < self.ttl:
                    result[name] = entry[0]
                else:
                    missing.append(name)
        if missing:
            batch = self.db.get_player_types_batch(missing)
            with self._lock:
                for name in missing:
                    stat = batch.get(name)
                    if stat:
                        self._entries[name] = (stat, now)
                        result[name] = stat
        return result

    def invalidate(self, names=None):
        with self._lock:
            if names is None:
                self._entries.clear()
            else:
                for name in names:
                    self._entries.pop(name, None)


def _show_hud_error(title, message):
    """Surface HUD startup failures to the user (console + messagebox + log)."""
    logging.error("%s: %s", title, message)
    try:
        with open(HUD_LOG_PATH, "a", encoding="utf-8") as fh:
            fh.write(f"{datetime.now().isoformat()} ERROR {title}: {message}\n")
    except OSError:
        pass
    try:
        from tkinter import messagebox
        messagebox.showerror(title, f"{message}\n\nLog: {HUD_LOG_PATH}")
    except Exception:
        pass


# ─── Live HUD — Overlay Window ──────────────────────────────────────────────
class LiveHUDOverlay:
    """Borderless always-on-top transparent window that overlays the poker client."""

    def __init__(self, root, theme, db, settings, on_profile_changed=None, on_quit=None, on_lock_changed=None):
        self.root = root
        self.theme = theme
        self.db = db
        self.settings = settings
        self.on_profile_changed = on_profile_changed
        self.on_quit = on_quit
        self.on_lock_changed = on_lock_changed
        self._win = None
        self._badges = {}
        self._badge_hosts = {}
        self._seat_guides = {}
        self._summary_panel = None
        self._layout_hint_panel = None
        self._current_rect = None
        self._current_site = "Unknown"
        self._opacity = float(settings.get("hud_opacity", 0.85))
        self._layout_mode = not bool(settings.get("hud_locked", True))
        self._drag_origin = None
        self._summary_origin = None
        self._badge_drag_state = None
        self._selected_target = "summary"
        self._last_seat_map = {}
        self._last_seat_to_slot = {}
        self._last_max_seats = 6
        self._last_hand_id = None
        self._bound_table_key = ""
        self._window_title = ""
        self._layout_toggle = None
        self._reset_btn = None
        self._toggle_win = None
        self._hotkey_thread = None
        self._hotkey_id = 1  # arbitrary ID for RegisterHotKey
        self._resize_job = None          # debounce handle for _on_win_configure
        self._stats_cache = PlayerStatsCache(db, ttl=45.0)
        self._stats_load_generation = 0
        self._last_hand_signature = None
        self._start_hotkey_listener()

    def _clear_badges(self):
        for badge in self._badges.values():
            if badge.winfo_exists():
                badge.destroy()
        self._badges.clear()
        for host in self._badge_hosts.values():
            if host.winfo_exists():
                host.destroy()
        self._badge_hosts.clear()

    def _remove_badge(self, seat):
        badge = self._badges.pop(seat, None)
        if badge is not None and badge.winfo_exists():
            badge.destroy()
        host = self._badge_hosts.pop(seat, None)
        if host is not None and host.winfo_exists():
            host.destroy()

    def bind_table(self, window_title: str = ""):
        """Track ACR window title; reset roster state when table identity changes."""
        title = window_title or self._window_title
        self._window_title = title
        table_key = _extract_table_key_from_title(title)
        if table_key and table_key != self._bound_table_key:
            logging.info(f"HUD table bind: {self._bound_table_key!r} -> {table_key!r}")
            self.reset_for_table_switch(table_key)
        elif not table_key:
            self._bound_table_key = ""

    def reset_for_table_switch(self, table_key: str = ""):
        """Clear badges and cached roster when moving to a new tournament table."""
        self._bound_table_key = table_key or _extract_table_key_from_title(self._window_title)
        self._last_hand_id = None
        self._last_hand_signature = None
        self._last_seat_map = {}
        self._last_seat_to_slot = {}
        self._clear_badges()
        if self._summary_panel is not None and self._summary_panel.winfo_exists():
            self._summary_panel.render(
                layout_key=6,
                opponent_count=0,
                forced=False,
                opacity=self._opacity,
                site=self._current_site,
                density=str(self.settings.get("hud_density", "standard")).lower(),
                layout_mode=self._layout_mode,
            )

    def _create_badge_host(self, seat):
        host = tk.Toplevel(self.root)
        host.overrideredirect(True)
        host.attributes("-topmost", True)
        host.configure(bg=_HUD_COLORKEY)
        self._badge_hosts[seat] = host
        return host

    def _badge_host_screen_xy(self, px, py, badge_w):
        tx, ty, _, _ = self._current_rect
        return tx + px - (badge_w // 2), ty + py

    def _position_badge_host(self, seat, px, py, badge):
        host = self._badge_hosts.get(seat)
        if host is None or not host.winfo_exists():
            return
        sx, sy = self._badge_host_screen_xy(px, py, badge.W)
        host.geometry(f"{badge.W}x{badge.H}+{sx}+{sy}")
        badge.place(x=badge.W // 2, y=0, anchor="n")
        _setup_hud_interactive_host(host)
        host.lift()

    def _overlay_xy_from_host(self, host, badge):
        tx, ty, _, _ = self._current_rect
        px = host.winfo_x() - tx + (badge.W // 2)
        py = host.winfo_y() - ty
        return px, py

    def invalidate_stats_cache(self, names=None):
        self._stats_cache.invalidate(names)

    def _edge_margin_pct(self):
        return hud_edge_margin_pct(self.settings)

    def _badge_scale(self):
        return hud_badge_scale(self.settings)

    def _slot_offset(self, badge_offsets, seat_to_slot, seat):
        slot = seat_to_slot.get(seat)
        if slot is None:
            return {}
        return badge_offsets.get(str(slot), {})

    def _resolve_badge_xy(self, pos, seat_offset, badge_dx, badge_dy, w, h, badge_w, badge_h):
        if "fx" in seat_offset and "fy" in seat_offset:
            px = int(seat_offset["fx"] * w)
            py = int(seat_offset["fy"] * h)
        else:
            px = int(pos[0] * w) + badge_dx + int(seat_offset.get("x", 0))
            py = int(pos[1] * h) + badge_dy + int(seat_offset.get("y", 0))
        return clamp_badge_position(
            px, py, badge_w, badge_h, w, h, self._edge_margin_pct(),
        )

    def _layout_slot_for_seat(self, seat):
        return self._last_seat_to_slot.get(seat)

    def _persist_badge_offsets(self, profile, badge_offsets):
        profile_site = profile.get("profile_site")
        if profile_site:
            existing_profile = dict(self.settings.setdefault("hud_site_profiles", {}).get(profile_site, {}))
            summary_offset = profile.get("summary_offset", (0, 0))
            saved_profile = {
                "anchor": existing_profile.get("anchor", profile.get("anchor", self.settings.get("hud_anchor", "top-left"))),
                "offset_x": existing_profile.get("offset_x", summary_offset[0]),
                "offset_y": existing_profile.get("offset_y", summary_offset[1]),
                "density": existing_profile.get("density", profile.get("density", self.settings.get("hud_density", "standard"))),
                "seat_layout": existing_profile.get("seat_layout", profile.get("seat_layout", self.settings.get("hud_seat_layout", "auto"))),
                "badge_offsets": badge_offsets,
            }
            self.settings.setdefault("hud_site_profiles", {})[profile_site] = saved_profile
            if self.on_profile_changed:
                self.on_profile_changed(profile_site, saved_profile)
        else:
            self.settings["hud_slot_positions"] = badge_offsets
            if self.on_lock_changed:
                self.on_lock_changed(self.settings)
        if self._last_seat_map:
            self.update_hand(self._last_seat_map, self._last_max_seats, self._current_site)

    # ── Global hotkey: Ctrl+Shift+H toggles layout mode ─────────────────────
    def _start_hotkey_listener(self):
        """Register Ctrl+Shift+H as a global hotkey and poll for it in a daemon thread."""
        import threading
        def _listen():
            try:
                import ctypes, ctypes.wintypes
                MOD_CONTROL = 0x0002
                MOD_SHIFT   = 0x0004
                VK_H        = 0x48
                WM_HOTKEY   = 0x0312
                ctypes.windll.user32.RegisterHotKey(None, self._hotkey_id, MOD_CONTROL | MOD_SHIFT, VK_H)
                msg = ctypes.wintypes.MSG()
                while True:
                    ret = ctypes.windll.user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
                    if ret <= 0:
                        break
                    if msg.message == WM_HOTKEY and msg.wParam == self._hotkey_id:
                        self.root.after(0, lambda: self.set_layout_mode(not self._layout_mode))
                    ctypes.windll.user32.TranslateMessage(ctypes.byref(msg))
                    ctypes.windll.user32.DispatchMessageW(ctypes.byref(msg))
            except Exception:
                pass
            finally:
                try:
                    ctypes.windll.user32.UnregisterHotKey(None, self._hotkey_id)
                except Exception:
                    pass
        self._hotkey_thread = threading.Thread(target=_listen, daemon=True)
        self._hotkey_thread.start()

    def _create_window(self, rect):
        x, y, w, h = rect
        self._win = tk.Toplevel(self.root)
        self._win.overrideredirect(True)
        self._win.attributes("-topmost", True)
        self._win.configure(bg=_HUD_COLORKEY)  # colorkey = fully transparent background
        self._win.geometry(f"{w}x{h}+{x}+{y}")
        self._add_resize_grip()
        self._bind_layout_hotkeys()
        self._setup_layered_transparency()
        # Delay so tkinter has finished creating all child HWNDs before we walk them
        self._win.after(100, self._apply_window_interaction)
        self._win.bind("<Configure>", self._on_win_configure)

    def _add_resize_grip(self):
        """Add a semi-transparent corner drag handle for manual HUD resize."""
        GRIP_SIZE = 18
        grip_bg   = "#444466"
        self._grip = tk.Canvas(
            self._win,
            width=GRIP_SIZE,
            height=GRIP_SIZE,
            bg=grip_bg,
            highlightthickness=0,
            cursor="size_nw_se",
        )
        # Draw grip dots
        for i in range(3):
            for j in range(3):
                if i + j >= 2:
                    self._grip.create_oval(
                        3 + i*5, 3 + j*5,
                        6 + i*5, 6 + j*5,
                        fill="#aaaacc", outline=""
                    )
        self._grip.place(relx=1.0, rely=1.0, anchor="se")
        self._grip_drag = None
        self._grip.bind("<ButtonPress-1>",   self._grip_press)
        self._grip.bind("<B1-Motion>",       self._grip_drag_move)
        self._grip.bind("<ButtonRelease-1>", self._grip_release)

    def _grip_press(self, event):
        self._grip_drag = (event.x_root, event.y_root,
                           self._win.winfo_width(), self._win.winfo_height(),
                           self._win.winfo_x(), self._win.winfo_y())

    def _grip_drag_move(self, event):
        if not self._grip_drag:
            return
        ox, oy, ow, oh, wx, wy = self._grip_drag
        dx = event.x_root - ox
        dy = event.y_root - oy
        nw = max(200, ow + dx)
        nh = max(120, oh + dy)
        self._win.geometry(f"{nw}x{nh}+{wx}+{wy}")
        self._current_rect = (wx, wy, nw, nh)
        self._reposition_badges()

    def _grip_release(self, event):
        self._grip_drag = None

    def update_rect(self, rect):
        """Reposition/resize overlay to match the poker window. Call from main thread."""
        if rect is None:
            self.hide()
            return
        if self._win is None or not self._win.winfo_exists():
            self._create_window(rect)
        else:
            x, y, w, h = rect
            self._win.geometry(f"{w}x{h}+{x}+{y}")
        self.show()
        self._current_rect = rect
        self._ensure_summary_panel()
        self._ensure_layout_hint_panel()
        self._ensure_layout_toggle()
        self._place_summary_panel(self._current_site)
        self._place_layout_hint_panel()
        self._reposition_badges()
        self._apply_window_interaction()

    def _on_win_configure(self, event):
        """Sync _current_rect on resize/move, then debounce badge repositioning.

        Firing _reposition_badges() on every pixel of a drag causes an expensive
        ctypes walk on every event.  We buffer calls with a 120 ms after() timer
        so the heavy work only runs once the user stops dragging/resizing.
        """
        if (event.widget is self._win
                and event.width > 1 and event.height > 1):
            self._current_rect = (
                self._win.winfo_x(),
                self._win.winfo_y(),
                event.width,
                event.height,
            )
        # Cancel any pending debounce timer before starting a new one
        if self._resize_job is not None:
            try:
                self._win.after_cancel(self._resize_job)
            except Exception:
                pass
        self._resize_job = self._win.after(120, self._on_resize_settled)

    def _on_resize_settled(self):
        """Called 120 ms after the last Configure event — safe to do heavy work."""
        self._resize_job = None
        self._reposition_badges()

    def _reposition_badges(self):
        """Re-place all existing seat badges after the overlay window has been resized."""
        if not self._badges or self._current_rect is None:
            return
        if self._win is None or not self._win.winfo_exists():
            return
        _, _, w, h = self._current_rect
        profile = self._get_site_profile(self._current_site)
        layout_pref = str(profile.get("seat_layout", self.settings.get("hud_seat_layout", "auto"))).lower()
        forced_layout = {"2max": 2, "6max": 6, "9max": 9}.get(layout_pref)
        layout_key = forced_layout or min(SEAT_POSITIONS.keys(), key=lambda k: abs(k - self._last_max_seats))
        layout = SEAT_POSITIONS[layout_key]
        seat_to_slot = self._last_seat_to_slot or build_hero_anchored_seat_slots(self._last_seat_map, layout_key)
        badge_dx, badge_dy = profile["badge_offset"]
        badge_offsets = profile.get("badge_offsets", {})

        for seat, badge in self._badges.items():
            if not badge.winfo_exists():
                continue
            pos = layout.get(seat_to_slot.get(seat))
            if pos is None:
                continue
            seat_offset = self._slot_offset(badge_offsets, seat_to_slot, seat)
            if "fx" in seat_offset and "fy" in seat_offset:
                px = int(seat_offset["fx"] * w)
                py = int(seat_offset["fy"] * h)
            else:
                px = int(pos[0] * w) + badge_dx + int(seat_offset.get("x", 0))
                py = int(pos[1] * h) + badge_dy + int(seat_offset.get("y", 0))
            px, py = clamp_badge_position(
                px, py, badge.W, badge.H, w, h, self._edge_margin_pct(),
            )
            self._position_badge_host(seat, px, py, badge)

        if self._layout_mode:
            self._render_seat_guides(layout, self._last_seat_map, seat_to_slot, badge_offsets)

    def update_hand(self, seat_map, max_seats, site="Unknown", hand_id=None):
        """Refresh seat badges for a new/changed roster. Must be called from main thread."""
        if self._win is None or not self._win.winfo_exists():
            return
        if self._current_rect is None:
            return

        seat_map = tag_hero_seats(seat_map, self.settings, site)
        if hand_id and hand_id != self._last_hand_id:
            self._last_hand_id = hand_id
        villain_names = [
            info["name"] for info in seat_map.values()
            if not info.get("is_hero") and info.get("name")
        ]
        signature = (_seat_map_signature(seat_map), max_seats, site)
        self._last_hand_signature = signature

        self._last_seat_map = dict(seat_map)
        self._last_max_seats = max_seats
        _, _, w, h = self._current_rect
        self._current_site = site or "Unknown"
        profile = self._get_site_profile(self._current_site)
        layout_pref = str(profile.get("seat_layout", self.settings.get("hud_seat_layout", "auto"))).lower()
        forced_layout = {"2max": 2, "6max": 6, "9max": 9}.get(layout_pref)
        layout_key = forced_layout or min(SEAT_POSITIONS.keys(), key=lambda k: abs(k - max_seats))
        layout = SEAT_POSITIONS[layout_key]
        seat_to_slot = build_hero_anchored_seat_slots(seat_map, layout_key)
        self._last_seat_to_slot = seat_to_slot
        villain_count = sum(1 for info in seat_map.values() if not info.get("is_hero") and info.get("name"))
        density = str(profile.get("density", self.settings.get("hud_density", "standard"))).lower()
        badge_dx, badge_dy = profile["badge_offset"]
        badge_offsets = profile.get("badge_offsets", {})

        self._ensure_summary_panel()
        self._ensure_layout_hint_panel()
        self._place_summary_panel(self._current_site)
        self._place_layout_hint_panel()
        if self._summary_panel is not None:
            self._summary_panel.render(
                layout_key=layout_key,
                opponent_count=villain_count,
                forced=forced_layout is not None,
                opacity=self._opacity,
                site=self._current_site,
                density=density,
                layout_mode=self._layout_mode,
            )
        self._refresh_selection_highlights()
        self._render_seat_guides(layout, seat_map, seat_to_slot, badge_offsets)

        cached_stats = self._stats_cache.get_batch(villain_names) if villain_names else {}
        needs_async = any(name not in cached_stats for name in villain_names)

        self._sync_seat_badges(
            seat_map, layout, seat_to_slot, density, badge_dx, badge_dy,
            badge_offsets, w, h, cached_stats, loading=needs_async,
        )

        if not needs_async:
            return

        generation = self._stats_load_generation + 1
        self._stats_load_generation = generation

        def _load_stats(gen=generation, sm=seat_map, ms=max_seats, st=site,
                        hid=hand_id, names=list(villain_names), lay=layout, sts=seat_to_slot,
                        den=density, bdx=badge_dx, bdy=badge_dy, boff=badge_offsets,
                        width=w, height=h):
            try:
                stats = self._stats_cache.get_batch(names)
            except Exception:
                logging.exception("HUD stats batch load failed")
                stats = {}
            if gen != self._stats_load_generation:
                return
            try:
                self.root.after(
                    0,
                    lambda: self._apply_loaded_stats(
                        gen, sm, ms, st, hid, lay, sts, den, bdx, bdy, boff, width, height, stats,
                    ),
                )
            except Exception:
                pass

        threading.Thread(target=_load_stats, daemon=True).start()

    def _desired_villain_seats(self, seat_map):
        """Seat numbers that should show opponent badges."""
        desired = {}
        seen_names = set()
        for seat, info in seat_map.items():
            if info.get("is_hero"):
                continue
            pname = (info.get("name") or "").strip()
            if not pname or pname in seen_names:
                continue
            seen_names.add(pname)
            desired[seat] = pname
        return desired

    def _sync_seat_badges(
        self, seat_map, layout, seat_to_slot, density, badge_dx, badge_dy,
        badge_offsets, w, h, stats_by_name, loading=False,
    ):
        """Add/update/remove badges to match the current seat roster."""
        desired = self._desired_villain_seats(seat_map)
        for seat in list(self._badges.keys()):
            badge = self._badges.get(seat)
            expected_name = desired.get(seat)
            if expected_name is None:
                self._remove_badge(seat)
                continue
            if badge is not None and getattr(badge, "_player_name", None) != expected_name:
                self._remove_badge(seat)

        for seat, pname in desired.items():
            if seat in self._badges and self._badges[seat].winfo_exists():
                badge = self._badges[seat]
                pos = layout.get(seat_to_slot.get(seat))
                if pos is None:
                    continue
                seat_offset = self._slot_offset(badge_offsets, seat_to_slot, seat)
                px, py = self._resolve_badge_xy(
                    pos, seat_offset, badge_dx, badge_dy, w, h, badge.W, badge.H,
                )
                self._position_badge_host(seat, px, py, badge)
                continue

            info = seat_map.get(seat, {"name": pname})
            pos = layout.get(seat_to_slot.get(seat))
            if pos is None:
                continue
            stat = stats_by_name.get(pname)
            badge_loading = loading and stat is None
            host = self._create_badge_host(seat)
            badge = SeatBadge(
                host, self.theme, info, stat,
                density=density, db=self.db, loading=badge_loading,
                badge_scale=self._badge_scale(),
            )
            seat_offset = self._slot_offset(badge_offsets, seat_to_slot, seat)
            px, py = self._resolve_badge_xy(
                pos, seat_offset, badge_dx, badge_dy, w, h, badge.W, badge.H,
            )
            self._position_badge_host(seat, px, py, badge)
            self._bind_badge_interaction(badge, seat)
            self._badges[seat] = badge

        if self._win and self._win.winfo_exists():
            self._win.after(50, self._apply_window_interaction)

    def _place_seat_badges(
        self, seat_map, layout, seat_to_slot, density, badge_dx, badge_dy,
        badge_offsets, w, h, stats_by_name, loading=False,
    ):
        seen_names = set()
        for seat, info in seat_map.items():
            if info.get("is_hero"):
                continue
            pname = (info.get("name") or "").strip()
            if not pname or pname in seen_names:
                continue
            seen_names.add(pname)
            pos = layout.get(seat_to_slot.get(seat))
            if pos is None:
                continue
            stat = stats_by_name.get(pname)
            badge_loading = loading and stat is None
            host = self._create_badge_host(seat)
            badge = SeatBadge(
                host, self.theme, info, stat,
                density=density, db=self.db, loading=badge_loading,
                badge_scale=self._badge_scale(),
            )
            seat_offset = self._slot_offset(badge_offsets, seat_to_slot, seat)
            px, py = self._resolve_badge_xy(
                pos, seat_offset, badge_dx, badge_dy, w, h, badge.W, badge.H,
            )
            self._position_badge_host(seat, px, py, badge)
            self._bind_badge_interaction(badge, seat)
            self._badges[seat] = badge
        self._win.after(50, self._apply_window_interaction)

    def _apply_loaded_stats(
        self, generation, seat_map, max_seats, site, hand_id, layout, seat_to_slot,
        density, badge_dx, badge_dy, badge_offsets, w, h, stats_by_name,
    ):
        if generation != self._stats_load_generation:
            return
        if self._win is None or not self._win.winfo_exists():
            return
        self._sync_seat_badges(
            seat_map, layout, seat_to_slot, density, badge_dx, badge_dy,
            badge_offsets, w, h, stats_by_name, loading=False,
        )

    def refresh_stats_only(self):
        """Re-read cached player_types for current seats without rebuilding layout."""
        if not self._last_seat_map:
            return
        self._stats_cache.invalidate()
        self.update_hand(self._last_seat_map, self._last_max_seats, self._current_site)

    def hide(self):
        if self._win and self._win.winfo_exists():
            self._win.withdraw()
        if self._toggle_win is not None and self._toggle_win.winfo_exists():
            self._toggle_win.withdraw()
        for host in self._badge_hosts.values():
            if host.winfo_exists():
                host.withdraw()

    def show(self):
        if self._win and self._win.winfo_exists():
            self._win.deiconify()
            self._apply_window_interaction()
        if self._toggle_win is not None and self._toggle_win.winfo_exists():
            self._toggle_win.deiconify()
            self._toggle_win.lift()
        for host in self._badge_hosts.values():
            if host.winfo_exists():
                host.deiconify()
                host.lift()

    def destroy(self):
        self._clear_seat_guides()
        self._clear_badges()
        if self._win and self._win.winfo_exists():
            self._win.destroy()
        self._win = None
        self._summary_panel = None
        self._layout_hint_panel = None
        self._layout_toggle = None
        self._reset_btn = None
        if self._toggle_win is not None and self._toggle_win.winfo_exists():
            self._toggle_win.destroy()
        self._toggle_win = None

    def _ensure_summary_panel(self):
        if self._win is None or not self._win.winfo_exists():
            return
        if self._summary_panel is None or not self._summary_panel.winfo_exists():
            self._summary_panel = HUDSummaryPanel(self._win, self.theme)
            self._summary_panel.bind("<ButtonPress-1>", self._drag_start)
            self._summary_panel.bind("<B1-Motion>", self._drag_move)
            self._summary_panel.bind("<ButtonRelease-1>", self._drag_end)

    def _ensure_layout_hint_panel(self):
        if self._win is None or not self._win.winfo_exists():
            return
        if self._layout_hint_panel is None or not self._layout_hint_panel.winfo_exists():
            self._layout_hint_panel = HUDLayoutHintPanel(self._win, self.theme)

    def _create_toggle_window(self):
        """Create a SEPARATE always-on-top toggle window (not a child of the overlay).
        This window is NOT WS_EX_TRANSPARENT so it always receives clicks regardless of overlay state."""
        if self._toggle_win is not None and self._toggle_win.winfo_exists():
            return
        self._toggle_win = tk.Toplevel(self.root)
        self._toggle_win.overrideredirect(True)
        self._toggle_win.attributes("-topmost", True)
        self._toggle_win.configure(bg=_HUD_COLORKEY)
        toggle_w, toggle_h = HUDLayoutToggle.TOOLBAR_W, HUDLayoutToggle.TOOLBAR_H
        self._toggle_win.geometry(f"{toggle_w}x{toggle_h}+0+0")
        self._toggle_win.attributes("-alpha", 0.92)
        _setup_hud_interactive_host(self._toggle_win)
        # Create the HUDLayoutToggle inside this separate window
        if self._layout_toggle is None or not self._layout_toggle.winfo_exists():
            self._layout_toggle = HUDLayoutToggle(
                self._toggle_win, self.theme,
                on_click=lambda: self.set_layout_mode(not self._layout_mode),
            )
        self._layout_toggle.place(x=0, y=0, anchor="nw")
        self._layout_toggle.set_layout_mode(self._layout_mode)
        reset_x = HUDLayoutToggle.W + HUDLayoutToggle.TOOLBAR_GAP
        if self._reset_btn is None or not self._reset_btn.winfo_exists():
            self._reset_btn = HUDResetSeatsButton(
                self._toggle_win, self.theme,
                on_click=self._reset_all_seat_positions,
            )
        self._reset_btn.place(x=reset_x, y=0, anchor="nw")
        if self.on_quit:
            if not getattr(self, "_close_btn", None) or not self._close_btn.winfo_exists():
                self._close_btn = HUDCloseButton(
                    self._toggle_win, self.theme, on_click=self.on_quit,
                )
            close_x = reset_x + HUDResetSeatsButton.W + HUDLayoutToggle.TOOLBAR_GAP
            self._close_btn.place(x=close_x, y=0, anchor="nw")
        self._toggle_win.lift()

    def _reposition_toggle(self):
        """Move the toggle window to the top-left of the current table rect."""
        if self._toggle_win is None or not self._toggle_win.winfo_exists():
            return
        if self._current_rect is None:
            self._toggle_win.withdraw()
            return
        x, y, w, h = self._current_rect
        toggle_w = HUDLayoutToggle.TOOLBAR_W if self.on_quit else (
            HUDLayoutToggle.W + HUDLayoutToggle.TOOLBAR_GAP + HUDResetSeatsButton.W
        )
        toggle_h = HUDLayoutToggle.TOOLBAR_H
        # Place at top-left corner of poker table window, offset by 6px
        self._toggle_win.geometry(f"{toggle_w}x{toggle_h}+{x+6}+{y+6}")
        self._toggle_win.deiconify()
        self._toggle_win.lift()

    def _ensure_layout_toggle(self):
        """Create or refresh the floating layout toggle window."""
        if self._win is None or not self._win.winfo_exists():
            return
        self._create_toggle_window()
        if self._layout_toggle is not None and self._layout_toggle.winfo_exists():
            self._layout_toggle.set_layout_mode(self._layout_mode)
        self._reposition_toggle()

    def _place_layout_hint_panel(self):
        if self._layout_hint_panel is None or self._current_rect is None:
            return
        if not self._layout_mode:
            self._layout_hint_panel.place_forget()
            return
        _, _, w, h = self._current_rect
        x = max(8, (w - HUDLayoutHintPanel.W) // 2)
        y = max(8, h - HUDLayoutHintPanel.H - 10)
        self._layout_hint_panel.place(x=x, y=y, anchor="nw")
        self._layout_hint_panel.render(self._selected_target_label())

    def _bind_layout_hotkeys(self):
        if self._win is None or not self._win.winfo_exists():
            return
        self._win.bind("<Escape>", lambda event: self.set_layout_mode(False))
        self._win.bind("<Tab>", self._handle_cycle_next)
        self._win.bind("<Shift-Tab>", self._handle_cycle_previous)
        self._win.bind("<ISO_Left_Tab>", self._handle_cycle_previous)
        self._win.bind("<Home>", self._handle_select_summary)
        self._win.bind("<BackSpace>", lambda event: self._reset_selected_target())
        self._win.bind("<Delete>", lambda event: self._reset_selected_target())
        self._win.bind("<Left>", lambda event: self._nudge_selected_target(-1, 0))
        self._win.bind("<Right>", lambda event: self._nudge_selected_target(1, 0))
        self._win.bind("<Up>", lambda event: self._nudge_selected_target(0, -1))
        self._win.bind("<Down>", lambda event: self._nudge_selected_target(0, 1))
        self._win.bind("<Shift-Left>", lambda event: self._nudge_selected_target(-10, 0))
        self._win.bind("<Shift-Right>", lambda event: self._nudge_selected_target(10, 0))
        self._win.bind("<Shift-Up>", lambda event: self._nudge_selected_target(0, -10))
        self._win.bind("<Shift-Down>", lambda event: self._nudge_selected_target(0, 10))

    def _selected_target_label(self):
        if self._selected_target == "summary":
            return "Summary"
        if isinstance(self._selected_target, tuple) and len(self._selected_target) == 2:
            return f"Seat {self._selected_target[1]}"
        return "Summary"

    def _select_target(self, target):
        self._selected_target = target
        self._refresh_selection_highlights()

    def _available_selection_targets(self):
        targets = ["summary"]
        for seat in sorted(self._badges.keys()):
            badge = self._badges.get(seat)
            if badge is not None and badge.winfo_exists():
                targets.append(("seat", seat))
        return targets

    def _normalize_selected_target(self):
        targets = self._available_selection_targets()
        if self._selected_target in targets:
            return
        self._selected_target = targets[0] if targets else "summary"

    def _cycle_selected_target(self, step):
        targets = self._available_selection_targets()
        if not targets:
            self._select_target("summary")
            return
        try:
            current_index = targets.index(self._selected_target)
        except ValueError:
            current_index = 0
        next_index = (current_index + step) % len(targets)
        self._select_target(targets[next_index])

    def _handle_cycle_next(self, event=None):
        if self._layout_mode:
            self._cycle_selected_target(1)
            return "break"

    def _handle_cycle_previous(self, event=None):
        if self._layout_mode:
            self._cycle_selected_target(-1)
            return "break"

    def _handle_select_summary(self, event=None):
        if self._layout_mode:
            self._select_target("summary")
            return "break"

    def _refresh_selection_highlights(self):
        self._normalize_selected_target()
        if self._summary_panel is not None and self._summary_panel.winfo_exists():
            if self._layout_mode and self._selected_target == "summary":
                self._summary_panel.configure(highlightthickness=2, highlightbackground=self.theme["orange"], highlightcolor=self.theme["orange"])
            else:
                self._summary_panel.configure(highlightthickness=0)
        for seat, badge in self._badges.items():
            if badge is None or not badge.winfo_exists():
                continue
            if self._layout_mode and self._selected_target == ("seat", seat):
                badge.configure(highlightthickness=2, highlightbackground=self.theme["orange"], highlightcolor=self.theme["orange"])
            else:
                badge.configure(highlightthickness=0)
        if self._layout_hint_panel is not None and self._layout_hint_panel.winfo_exists():
            if self._layout_mode:
                self._layout_hint_panel.render(self._selected_target_label())
                self._place_layout_hint_panel()
            else:
                self._layout_hint_panel.place_forget()

    def _clear_seat_guides(self):
        for guide in self._seat_guides.values():
            if guide and guide.winfo_exists():
                guide.destroy()
        self._seat_guides.clear()

    def _render_seat_guides(self, layout, seat_map, seat_to_slot, badge_offsets):
        self._clear_seat_guides()
        if not self._layout_mode or self._current_rect is None or self._win is None or not self._win.winfo_exists():
            return
        _, _, w, h = self._current_rect
        profile = self._get_site_profile(self._current_site)
        badge_dx, badge_dy = profile.get("badge_offset", (0, 0))
        for seat, info in seat_map.items():
            if info.get("is_hero"):
                continue
            slot = seat_to_slot.get(seat)
            pos = layout.get(slot)
            if pos is None:
                continue
            guide = HUDSeatGuide(
                self._win, self.theme, seat,
                has_custom_offset=slot is not None and str(slot) in badge_offsets,
            )
            base_x = int(pos[0] * w) + badge_dx
            base_y = int(pos[1] * h) + badge_dy
            base_x = max((guide.W // 2) + 6, min(w - (guide.W // 2) - 6, base_x))
            base_y = max((guide.H // 2) + 6, min(h - (guide.H // 2) - 6, base_y))
            guide.place(x=base_x, y=base_y, anchor="center")
            guide.bind("<ButtonPress-1>", lambda event, seat_no=seat: self._reset_badge_offset(seat_no))
            self._seat_guides[seat] = guide

    def _place_summary_panel(self, site):
        if self._summary_panel is None or self._current_rect is None:
            return
        _, _, w, h = self._current_rect
        profile = self._get_site_profile(site)
        anchor = profile["anchor"]
        margin = 18
        panel_w = HUDSummaryPanel.W
        panel_h = HUDSummaryPanel.H
        positions = {
            "top-left": (margin, margin),
            "top-right": (max(margin, w - panel_w - margin), margin),
            "bottom-left": (margin, max(margin, h - panel_h - margin)),
            "bottom-right": (max(margin, w - panel_w - margin), max(margin, h - panel_h - margin)),
        }
        x, y = positions.get(anchor, positions["top-left"])
        sx, sy = profile["summary_offset"]
        x = max(6, min(max(6, w - panel_w - 6), x + sx))
        y = max(6, min(max(6, h - panel_h - 6), y + sy))
        self._summary_panel.place(x=x, y=y, anchor="nw")

    def _get_site_profile(self, site):
        site_name = site or "Unknown"
        preset_choice = str(self.settings.get("hud_site_preset", "auto"))
        manual_anchor = str(self.settings.get("hud_anchor", "top-left")).lower()
        offset_x = int(self.settings.get("hud_offset_x", 0))
        offset_y = int(self.settings.get("hud_offset_y", 0))
        global_density = str(self.settings.get("hud_density", "standard")).lower()
        global_layout = str(self.settings.get("hud_seat_layout", "auto")).lower()

        if preset_choice == "off":
            return {
                "profile_site": site_name if site_name in HUD_PROFILE_SITES else None,
                "anchor": manual_anchor,
                "summary_offset": (offset_x, offset_y),
                "badge_offset": (offset_x, offset_y),
                "density": global_density,
                "seat_layout": global_layout,
                "badge_offsets": dict(self.settings.get("hud_slot_positions", {})),
            }

        effective_site = site_name if preset_choice == "auto" else preset_choice
        site_profiles = self.settings.get("hud_site_profiles", {})
        saved_profile = site_profiles.get(effective_site)

        if saved_profile:
            return {
                "profile_site": effective_site,
                "anchor": saved_profile.get("anchor", manual_anchor),
                "summary_offset": (saved_profile.get("offset_x", 0), saved_profile.get("offset_y", 0)),
                "badge_offset": (saved_profile.get("offset_x", 0), saved_profile.get("offset_y", 0)),
                "density": saved_profile.get("density", global_density),
                "seat_layout": saved_profile.get("seat_layout", global_layout),
                "badge_offsets": dict(saved_profile.get("badge_offsets", {})),
            }

        base = HUD_SITE_PRESETS.get(effective_site, HUD_SITE_PRESETS["Unknown"])
        summary_offset = base.get("summary_offset", (0, 0))
        badge_offset = base.get("badge_offset", (0, 0))
        return {
            "profile_site": effective_site if effective_site in HUD_PROFILE_SITES else None,
            "anchor": base.get("anchor", manual_anchor),
            "summary_offset": (summary_offset[0] + offset_x, summary_offset[1] + offset_y),
            "badge_offset": (badge_offset[0] + offset_x, badge_offset[1] + offset_y),
            "density": global_density,
            "seat_layout": global_layout,
            "badge_offsets": {},
        }

    def set_layout_mode(self, enabled):
        self._layout_mode = bool(enabled)
        self.settings["hud_locked"] = not self._layout_mode
        if self.on_lock_changed:
            self.on_lock_changed(self.settings)
        if self.on_quit and hasattr(self.root, "_hud_layout_mode"):
            self.root._hud_layout_mode = self._layout_mode
        self._apply_window_interaction()
        if not self._layout_mode:
            self._clear_seat_guides()
            if self._layout_hint_panel is not None and self._layout_hint_panel.winfo_exists():
                self._layout_hint_panel.place_forget()
            self._selected_target = "summary"
            self.root.focus_force()
        elif self._win is not None and self._win.winfo_exists():
            self._win.focus_force()
        if self._summary_panel is not None:
            self._summary_panel.render(
                layout_key=min(SEAT_POSITIONS.keys(), key=lambda k: abs(k - self._last_max_seats)),
                opponent_count=sum(1 for info in self._last_seat_map.values() if not info.get("is_hero")),
                forced=str(self.settings.get("hud_seat_layout", "auto")).lower() in {"2max", "6max", "9max"},
                opacity=self._opacity,
                site=self._current_site,
                density=self._get_site_profile(self._current_site).get("density", self.settings.get("hud_density", "standard")),
                layout_mode=self._layout_mode,
            )
        self._refresh_selection_highlights()
        self._ensure_layout_toggle()
        if self._last_seat_map and self._current_rect is not None:
            self.update_hand(self._last_seat_map, self._last_max_seats, self._current_site)

    def _root_hwnd(self):
        """Return the real Win32 top-level HWND (not tkinter's inner container HWND).

        tkinter's winfo_id() returns the handle of the inner container window that
        tkinter creates as a child of the real top-level.  Win32 flags like
        WS_EX_LAYERED and WS_EX_TRANSPARENT must be applied to the actual top-level
        (GA_ROOT) for colorkey transparency and click-through to work correctly.
        """
        try:
            import ctypes
            inner = self._win.winfo_id()
            _ga = ctypes.windll.user32.GetAncestor
            _ga.restype = ctypes.c_size_t
            root = _ga(inner, 2)  # GA_ROOT = 2
            return int(root) if root else inner
        except Exception:
            return self._win.winfo_id()

    def _setup_layered_transparency(self):
        """Apply WS_EX_LAYERED + colorkey on the ROOT HWND so magenta pixels are invisible.

        Must target the real top-level HWND (GetAncestor GA_ROOT), not the inner
        container returned by winfo_id(), otherwise SetLayeredWindowAttributes has
        no effect on the visible window.
        """
        if self._win is None or not self._win.winfo_exists():
            return
        try:
            import ctypes
            self._win.update_idletasks()
            hwnd = self._root_hwnd()  # use GA_ROOT, not inner container
            GWL_EXSTYLE = -20
            style = ctypes.windll.user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
            style |= 0x00080000  # WS_EX_LAYERED
            ctypes.windll.user32.SetWindowLongW(hwnd, GWL_EXSTYLE, style)
            # LWA_COLORKEY=1 — make every magenta pixel fully invisible
            ctypes.windll.user32.SetLayeredWindowAttributes(
                hwnd, _HUD_COLORKEY_BGR, 0, 1
            )
        except Exception:
            pass

    def _apply_window_interaction(self):
        if self._win is None or not self._win.winfo_exists():
            return
        try:
            import ctypes
            self._win.update_idletasks()
            GWL_EXSTYLE       = -20
            WS_EX_LAYERED     = 0x00080000
            WS_EX_TRANSPARENT = 0x00000020
            WS_EX_NOACTIVATE  = 0x08000000
            SWP_FLAGS = 0x0001 | 0x0002 | 0x0004 | 0x0010 | 0x0020  # NOMOVE|NOSIZE|NOZORDER|NOACTIVATE|FRAMECHANGED

            root = self._root_hwnd()

            style = ctypes.windll.user32.GetWindowLongW(root, GWL_EXSTYLE)
            style |= WS_EX_LAYERED | WS_EX_NOACTIVATE
            if self._layout_mode:
                style &= ~WS_EX_TRANSPARENT
            else:
                style |= WS_EX_TRANSPARENT
            ctypes.windll.user32.SetWindowLongW(root, GWL_EXSTYLE, style)
            ctypes.windll.user32.SetWindowPos(root, 0, 0, 0, 0, 0, SWP_FLAGS)
            ctypes.windll.user32.SetLayeredWindowAttributes(root, _HUD_COLORKEY_BGR, 0, 1)

            # Apply to all child HWNDs too (belt-and-suspenders for play mode)
            GW_CHILD    = 5
            GW_HWNDNEXT = 2

            def _walk(parent_hwnd):
                child = ctypes.windll.user32.GetWindow(parent_hwnd, GW_CHILD)
                while child:
                    c_style = ctypes.windll.user32.GetWindowLongW(child, GWL_EXSTYLE)
                    if self._layout_mode:
                        c_style &= ~WS_EX_TRANSPARENT
                    else:
                        c_style |= WS_EX_TRANSPARENT
                    ctypes.windll.user32.SetWindowLongW(child, GWL_EXSTYLE, c_style)
                    ctypes.windll.user32.SetWindowPos(child, 0, 0, 0, 0, 0, SWP_FLAGS)
                    _walk(child)
                    child = ctypes.windll.user32.GetWindow(child, GW_HWNDNEXT)

            _walk(root)
        except Exception:
            pass

    def _drag_start(self, event):
        if not self._layout_mode or self._summary_panel is None:
            return
        self._select_target("summary")
        self._drag_origin = (event.x_root, event.y_root)
        info = self._summary_panel.place_info()
        self._summary_origin = (int(float(info.get("x", 0))), int(float(info.get("y", 0))))

    def _drag_move(self, event):
        if not self._layout_mode or self._summary_panel is None or self._drag_origin is None or self._summary_origin is None:
            return
        dx = event.x_root - self._drag_origin[0]
        dy = event.y_root - self._drag_origin[1]
        x = self._summary_origin[0] + dx
        y = self._summary_origin[1] + dy
        if self._current_rect is not None:
            _, _, w, h = self._current_rect
            x = max(6, min(max(6, w - HUDSummaryPanel.W - 6), x))
            y = max(6, min(max(6, h - HUDSummaryPanel.H - 6), y))
        self._summary_panel.place(x=x, y=y, anchor="nw")

    def _drag_end(self, event):
        if not self._layout_mode or self._summary_panel is None:
            return
        self._drag_move(event)
        self._drag_origin = None
        self._summary_origin = None
        self._persist_dragged_profile()

    def _bind_badge_interaction(self, badge, seat):
        badge.bind("<ButtonPress-1>", lambda event, seat_no=seat: self._badge_press(event, seat_no))
        badge.bind("<B1-Motion>", self._badge_motion)
        badge.bind("<ButtonRelease-1>", lambda event, seat_no=seat: self._badge_release(event, seat_no))
        badge.bind("<Double-Button-1>", lambda event, seat_no=seat: self._reset_badge_offset(seat_no))

    def _badge_press(self, event, seat):
        badge = self._badges.get(seat)
        if badge is not None:
            badge._drag_moved = False
        if self._layout_mode:
            self._badge_drag_start(event, seat)

    def _badge_motion(self, event):
        badge = None
        if self._badge_drag_state:
            badge = self._badges.get(self._badge_drag_state.get("seat"))
        if badge is not None:
            badge._drag_moved = True
        if self._layout_mode:
            self._badge_drag_move(event)

    def _badge_release(self, event, seat):
        badge = self._badges.get(seat)
        if self._layout_mode:
            self._badge_drag_end(event, seat)
            if badge is not None and not badge._drag_moved:
                badge.toggle_pinned_stats()
        elif badge is not None:
            badge.toggle_pinned_stats()

    def _badge_drag_start(self, event, seat):
        if not self._layout_mode:
            return
        badge = self._badges.get(seat)
        host = self._badge_hosts.get(seat)
        if badge is None or host is None:
            return
        self._select_target(("seat", seat))
        self._badge_drag_state = {
            "seat": seat,
            "origin": (event.x_root, event.y_root),
            "position": (host.winfo_x(), host.winfo_y()),
        }
        host.lift()

    def _badge_drag_move(self, event):
        if not self._layout_mode or not self._badge_drag_state or self._current_rect is None:
            return
        seat = self._badge_drag_state["seat"]
        badge = self._badges.get(seat)
        host = self._badge_hosts.get(seat)
        if badge is None or host is None:
            return
        dx = event.x_root - self._badge_drag_state["origin"][0]
        dy = event.y_root - self._badge_drag_state["origin"][1]
        nx = self._badge_drag_state["position"][0] + dx
        ny = self._badge_drag_state["position"][1] + dy
        _, _, w, h = self._current_rect
        px, py = self._overlay_xy_from_host_at(nx, ny, badge)
        px, py = clamp_badge_position(
            px, py, badge.W, badge.H, w, h, self._edge_margin_pct(),
        )
        self._position_badge_host(seat, px, py, badge)

    def _overlay_xy_from_host_at(self, host_x, host_y, badge):
        tx, ty, _, _ = self._current_rect
        px = host_x - tx + (badge.W // 2)
        py = host_y - ty
        return px, py

    def _badge_drag_end(self, event, seat):
        if not self._layout_mode or not self._badge_drag_state:
            return
        self._badge_drag_move(event)
        badge = self._badges.get(seat)
        host = self._badge_hosts.get(seat)
        slot = self._layout_slot_for_seat(seat)
        if badge and host and self._current_rect and slot is not None:
            _, _, w, h = self._current_rect
            px, py = self._overlay_xy_from_host(host, badge)
            fx = max(0.0, min(1.0, px / w))
            fy = max(0.0, min(1.0, py / h))
            self._badge_drag_state = None
            self._persist_fraction_badge_profile(slot, fx, fy)
        else:
            self._badge_drag_state = None

    def _persist_dragged_profile(self):
        if self._summary_panel is None or self._current_rect is None:
            return
        profile = self._get_site_profile(self._current_site)
        profile_site = profile.get("profile_site")
        if not profile_site:
            return
        _, _, w, h = self._current_rect
        info = self._summary_panel.place_info()
        x = int(float(info.get("x", 0)))
        y = int(float(info.get("y", 0)))
        margin = 18
        positions = {
            "top-left": (margin, margin),
            "top-right": (max(margin, w - HUDSummaryPanel.W - margin), margin),
            "bottom-left": (margin, max(margin, h - HUDSummaryPanel.H - margin)),
            "bottom-right": (max(margin, w - HUDSummaryPanel.W - margin), max(margin, h - HUDSummaryPanel.H - margin)),
        }
        anchor = min(positions.keys(), key=lambda key: abs(positions[key][0] - x) + abs(positions[key][1] - y))
        base_x, base_y = positions[anchor]
        existing_profile = self.settings.get("hud_site_profiles", {}).get(profile_site, {})
        saved_profile = {
            "anchor": anchor,
            "offset_x": x - base_x,
            "offset_y": y - base_y,
            "density": profile.get("density", self.settings.get("hud_density", "standard")),
            "seat_layout": profile.get("seat_layout", self.settings.get("hud_seat_layout", "auto")),
            "badge_offsets": dict(existing_profile.get("badge_offsets", profile.get("badge_offsets", {}))),
        }
        self.settings.setdefault("hud_site_profiles", {})[profile_site] = saved_profile
        if self.on_profile_changed:
            self.on_profile_changed(profile_site, saved_profile)
        if self._last_seat_map:
            self.update_hand(self._last_seat_map, self._last_max_seats, self._current_site)

    def _nudge_selected_target(self, dx, dy):
        if not self._layout_mode or self._current_rect is None:
            return
        if self._selected_target == "summary":
            if self._summary_panel is None or not self._summary_panel.winfo_exists():
                return
            info = self._summary_panel.place_info()
            x = int(float(info.get("x", 0))) + dx
            y = int(float(info.get("y", 0))) + dy
            _, _, w, h = self._current_rect
            x = max(6, min(max(6, w - HUDSummaryPanel.W - 6), x))
            y = max(6, min(max(6, h - HUDSummaryPanel.H - 6), y))
            self._summary_panel.place(x=x, y=y, anchor="nw")
            self._persist_dragged_profile()
            return
        if isinstance(self._selected_target, tuple) and len(self._selected_target) == 2:
            seat = self._selected_target[1]
            badge = self._badges.get(seat)
            host = self._badge_hosts.get(seat)
            if badge is None or not badge.winfo_exists() or host is None:
                return
            px, py = self._overlay_xy_from_host(host, badge)
            px += dx
            py += dy
            _, _, w, h = self._current_rect
            px, py = clamp_badge_position(
                px, py, badge.W, badge.H, w, h, self._edge_margin_pct(),
            )
            self._position_badge_host(seat, px, py, badge)
            slot = self._layout_slot_for_seat(seat)
            if slot is None:
                return
            fx = max(0.0, min(1.0, px / w))
            fy = max(0.0, min(1.0, py / h))
            self._persist_fraction_badge_profile(slot, fx, fy)

    def _reset_selected_target(self):
        if not self._layout_mode:
            return
        if self._selected_target == "summary":
            profile = self._get_site_profile(self._current_site)
            profile_site = profile.get("profile_site")
            if not profile_site:
                return
            existing_profile = dict(self.settings.setdefault("hud_site_profiles", {}).get(profile_site, {}))
            saved_profile = {
                "anchor": profile.get("anchor", self.settings.get("hud_anchor", "top-left")),
                "offset_x": 0,
                "offset_y": 0,
                "density": existing_profile.get("density", profile.get("density", self.settings.get("hud_density", "standard"))),
                "seat_layout": existing_profile.get("seat_layout", profile.get("seat_layout", self.settings.get("hud_seat_layout", "auto"))),
                "badge_offsets": dict(existing_profile.get("badge_offsets", profile.get("badge_offsets", {}))),
            }
            self.settings.setdefault("hud_site_profiles", {})[profile_site] = saved_profile
            if self.on_profile_changed:
                self.on_profile_changed(profile_site, saved_profile)
            if self._last_seat_map:
                self.update_hand(self._last_seat_map, self._last_max_seats, self._current_site)
            return
        if isinstance(self._selected_target, tuple) and len(self._selected_target) == 2:
            self._reset_badge_offset(self._selected_target[1])

    def _persist_dragged_badge_profile(self, seat):
        if self._current_rect is None:
            return
        profile = self._get_site_profile(self._current_site)
        profile_site = profile.get("profile_site")
        badge = self._badges.get(seat)
        if not profile_site or badge is None:
            return

        layout_pref = str(profile.get("seat_layout", self.settings.get("hud_seat_layout", "auto"))).lower()
        forced_layout = {"2max": 2, "6max": 6, "9max": 9}.get(layout_pref)
        layout_key = forced_layout or min(SEAT_POSITIONS.keys(), key=lambda k: abs(k - self._last_max_seats))
        layout = SEAT_POSITIONS.get(layout_key, {})
        seat_pos = layout.get(seat)
        if seat_pos is None:
            return

        host = self._badge_hosts.get(seat)
        if host is None:
            return
        actual_x, actual_y = self._overlay_xy_from_host(host, badge)
        _, _, w, h = self._current_rect
        badge_dx, badge_dy = profile.get("badge_offset", (0, 0))
        base_x = int(seat_pos[0] * w) + badge_dx
        base_y = int(seat_pos[1] * h) + badge_dy
        base_x, base_y = clamp_badge_position(
            base_x, base_y, badge.W, badge.H, w, h, self._edge_margin_pct(),
        )
        offset_x = actual_x - base_x
        offset_y = actual_y - base_y

        existing_profile = dict(self.settings.setdefault("hud_site_profiles", {}).get(profile_site, {}))
        badge_offsets = dict(existing_profile.get("badge_offsets", profile.get("badge_offsets", {})))
        if abs(offset_x) <= 2 and abs(offset_y) <= 2:
            badge_offsets.pop(str(seat), None)
        else:
            badge_offsets[str(seat)] = {"x": offset_x, "y": offset_y}

        summary_offset = profile.get("summary_offset", (0, 0))
        saved_profile = {
            "anchor": existing_profile.get("anchor", profile.get("anchor", self.settings.get("hud_anchor", "top-left"))),
            "offset_x": existing_profile.get("offset_x", summary_offset[0]),
            "offset_y": existing_profile.get("offset_y", summary_offset[1]),
            "density": existing_profile.get("density", profile.get("density", self.settings.get("hud_density", "standard"))),
            "seat_layout": existing_profile.get("seat_layout", profile.get("seat_layout", self.settings.get("hud_seat_layout", "auto"))),
            "badge_offsets": badge_offsets,
        }
        self.settings.setdefault("hud_site_profiles", {})[profile_site] = saved_profile
        if self.on_profile_changed:
            self.on_profile_changed(profile_site, saved_profile)
        if self._last_seat_map:
            self.update_hand(self._last_seat_map, self._last_max_seats, self._current_site)

    def _persist_fraction_badge_profile(self, slot, fx, fy):
        """Save badge position as a fraction of the overlay window (scales on resize)."""
        profile = self._get_site_profile(self._current_site)
        existing_offsets = dict(profile.get("badge_offsets", {}))
        existing_offsets[str(slot)] = {"fx": round(fx, 4), "fy": round(fy, 4)}
        self._persist_badge_offsets(profile, existing_offsets)

    def _reset_badge_offset(self, seat):
        if not self._layout_mode:
            return
        self._select_target(("seat", seat))
        slot = self._layout_slot_for_seat(seat)
        if slot is None:
            return
        profile = self._get_site_profile(self._current_site)
        existing_offsets = dict(profile.get("badge_offsets", {}))
        if str(slot) not in existing_offsets:
            return
        existing_offsets.pop(str(slot), None)
        self._persist_badge_offsets(profile, existing_offsets)

    def _reset_all_seat_positions(self):
        profile = self._get_site_profile(self._current_site)
        if not profile.get("badge_offsets"):
            return
        self._persist_badge_offsets(profile, {})


# ─── Hand Replayer ────────────────────────────────────────────────────────────
class HandReplayerWindow:
    """Step-through hand replayer: canvas poker table + action-by-action navigation."""

    CANVAS_W = 900
    CANVAS_H = 500
    TABLE_COLOR = "#1a5c2a"
    TABLE_BORDER = "#2e8b57"
    CARD_BACK = "#3a3a5c"
    # Y boundary where the table oval ends — hero cards panel sits below this
    HERO_PANEL_Y = 420

    SUITS = {
        "s": ("♠", "#1a1a2e"),
        "h": ("♥", "#cc0000"),
        "d": ("♦", "#cc0000"),
        "c": ("♣", "#1a1a2e"),
    }

    def __init__(self, parent, hand, theme):
        self._hand = hand
        self._theme = theme
        self._step_idx = 0
        self._folded_seats = set()
        self._playing = False
        self._after_id = None

        self._win = tk.Toplevel(parent)
        self._win.title(f"Replay — {hand.hand_id}")
        self._win.geometry("900x660")
        self._win.resizable(False, False)
        self._win.configure(bg=theme["bg_base"])
        self._win.focus_force()

        self._steps = self._build_steps()
        self._seat_display_map = self._build_seat_display_map()

        # Canvas — table + hero card panel
        self._canvas = tk.Canvas(
            self._win,
            width=self.CANVAS_W,
            height=self.CANVAS_H,
            bg=theme["bg_base"],
            highlightthickness=0,
        )
        self._canvas.pack(pady=(6, 0))

        # Action text label — larger, gold for street headers
        self._action_var = tk.StringVar(value="")
        self._action_lbl = tk.Label(
            self._win,
            textvariable=self._action_var,
            bg=theme["bg_base"],
            fg=theme["gold"],
            font=("Consolas", 13, "bold"),
            wraplength=880,
        )
        self._action_lbl.pack(pady=(4, 0))

        # Controls
        ctrl = tk.Frame(self._win, bg=theme["bg_base"])
        ctrl.pack(pady=8)

        self._prev_btn = tk.Button(
            ctrl, text="◀ Prev", bg=theme["bg_accent"], fg=theme["text"],
            font=("Consolas", 11), relief="flat", padx=14,
            command=self._prev_step,
        )
        self._prev_btn.pack(side="left", padx=8)

        self._step_lbl = tk.Label(
            ctrl, text="", bg=theme["bg_base"], fg=theme["text_dim"],
            font=("Consolas", 11),
        )
        self._step_lbl.pack(side="left", padx=10)

        self._next_btn = tk.Button(
            ctrl, text="▶ Next", bg=theme["bg_accent"], fg=theme["text"],
            font=("Consolas", 11), relief="flat", padx=14,
            command=self._next_step,
        )
        self._next_btn.pack(side="left", padx=8)

        self._play_btn = tk.Button(
            ctrl, text="▶▶ Play", bg=theme["bg_accent"], fg=theme["gold"],
            font=("Consolas", 11, "bold"), relief="flat", padx=14,
            command=self._toggle_play,
        )
        self._play_btn.pack(side="left", padx=14)

        # Parse cards shown at showdown from raw text (name -> "Xc Yh" string)
        self._shown_cards = {}
        if getattr(hand, "raw_text", None):
            import re as _re
            for m in _re.finditer(r'([\w ]+?)\s+shows?\s+\[([^\]]+)\]', hand.raw_text):
                name = m.group(1).strip()
                self._shown_cards[name] = m.group(2).strip()

        self._win.protocol("WM_DELETE_WINDOW", self._on_close)
        self._draw_step(0)

    # ------------------------------------------------------------------
    def _on_close(self):
        if self._after_id:
            self._win.after_cancel(self._after_id)
        self._win.destroy()

    # ------------------------------------------------------------------
    def _build_steps(self):
        hand = self._hand
        steps = []

        # Deal step
        steps.append({
            "type": "deal",
            "text": f"Deal — Hero: {hand.hero_cards or '??'} ({hand.hero_position or '?'})",
            "board": [],
        })

        board_so_far = []
        for street in (hand.streets or []):
            street_name = street.get("name", "")
            new_cards = street.get("cards", [])
            board_so_far = board_so_far + new_cards

            if new_cards:
                card_str = " ".join(new_cards)
                label = f"── {street_name}: {card_str} ──"
            else:
                label = f"── {street_name} ──"

            steps.append({
                "type": "street",
                "text": label,
                "board": list(board_so_far),
            })

            for act in street.get("actions", []):
                player = act.get("player", "?")
                action = act.get("action", "?")
                amount = act.get("amount", 0.0)
                if amount:
                    text = f"{player}: {action}s ${amount:.2f}"
                else:
                    text = f"{player}: {action}s"
                steps.append({
                    "type": "action",
                    "text": text,
                    "board": list(board_so_far),
                    "actor": player,
                    "action": action,
                    "amount": amount,
                })

        # Result step
        won = getattr(hand, "hero_won", 0.0) or 0.0
        pot = getattr(hand, "pot", 0.0) or 0.0
        result_fmt = format_hero_result(hand, won)
        if hand.is_tournament:
            pot_label = f"{pot:,.0f} chips"
        else:
            pot_label = f"${pot:.2f}"
        if won > 0:
            result_text = f"Result: Won {result_fmt} | Pot: {pot_label}"
        elif won < 0:
            loss_fmt = format_hero_result(hand, abs(won))
            result_text = f"Result: Lost {loss_fmt} | Pot: {pot_label}"
        else:
            result_text = f"Result: Break even | Pot: {pot_label}"
        steps.append({
            "type": "result",
            "text": result_text,
            "board": list(board_so_far),
        })

        return steps

    # ------------------------------------------------------------------
    def _build_seat_display_map(self):
        """Map hand-history seat numbers to clockwise display slots with hero at bottom."""
        hand = self._hand
        seats_sorted = sorted(hand.players.keys())
        n = len(seats_sorted)
        if n == 0:
            return {}
        hero_seat = next(
            (seat for seat, info in hand.players.items() if info.get("is_hero")),
            seats_sorted[0],
        )
        hero_idx = seats_sorted.index(hero_seat)
        return {
            seat: (seats_sorted.index(seat) - hero_idx) % n
            for seat in seats_sorted
        }

    def _seat_fraction(self, seat):
        """Normalized table coordinates; slot 0 (hero) sits at the bottom."""
        n = max(len(self._hand.players), 2)
        slot = self._seat_display_map.get(seat, 0)
        if n == 2:
            return (0.50, 0.85) if slot == 0 else (0.50, 0.18)
        cx, cy = 0.50, 0.48
        rx, ry = 0.38, 0.34
        angle = -math.pi / 2 + (2 * math.pi * slot / n)
        fx = cx + rx * math.cos(angle)
        fy = cy - ry * math.sin(angle)
        return (max(0.10, min(0.90, fx)), max(0.12, min(0.86, fy)))

    # ------------------------------------------------------------------
    def _seat_pixel(self, seat):
        """Map seat number to canvas (x, y) pixel on the table oval."""
        fx, fy = self._seat_fraction(seat)
        px = int(fx * self.CANVAS_W)
        py = int(20 + fy * (self.HERO_PANEL_Y - 30))
        return px, py

    # ------------------------------------------------------------------
    def _draw_card(self, canvas, cx, cy, card_str, w=50, h=70, face_down=False):
        """Draw a card centered at (cx, cy). card_str like 'As', 'Tc', 'Kh'."""
        x0, y0 = cx - w // 2, cy - h // 2
        x1, y1 = cx + w // 2, cy + h // 2
        r = 5  # corner radius approximation via overlapping rects

        if face_down:
            canvas.create_rectangle(x0, y0, x1, y1, fill=self.CARD_BACK,
                                    outline="#7070a0", width=2)
            canvas.create_rectangle(x0 + 4, y0 + 4, x1 - 4, y1 - 4,
                                    fill="#2a2a50", outline="#4a4a80", width=1)
            return

        # Card face — white with dark border
        canvas.create_rectangle(x0, y0, x1, y1, fill="white",
                                 outline="#333333", width=2)

        if not card_str or len(card_str) < 2:
            return

        suit_char = card_str[-1].lower()
        rank = card_str[:-1]
        suit_sym, suit_color = self.SUITS.get(suit_char, ("?", "#333"))

        # Rank top-left
        canvas.create_text(x0 + 5, y0 + 4, text=rank, anchor="nw",
                            font=("Arial", 13, "bold"), fill=suit_color)
        # Suit symbol top-left (below rank)
        canvas.create_text(x0 + 5, y0 + 22, text=suit_sym, anchor="nw",
                            font=("Arial", 11), fill=suit_color)
        # Large suit in center
        canvas.create_text(cx, cy + 4, text=suit_sym, anchor="center",
                            font=("Arial", 26), fill=suit_color)
        # Rank bottom-right (inverted)
        canvas.create_text(x1 - 5, y1 - 4, text=rank, anchor="se",
                            font=("Arial", 13, "bold"), fill=suit_color)

    # ------------------------------------------------------------------
    def _draw_chips(self, canvas, cx, cy, amount, label_color="#ffffff"):
        """Draw a chip stack at (cx, cy) with dollar label above."""
        if amount <= 0:
            return
        palette = ["#1565c0", "#c62828", "#2e7d32", "#000000", "#7b1fa2"]
        r = 10
        n = min(5, max(1, int(amount / 0.20) + 1))
        for i in range(n):
            canvas.create_oval(cx - r, cy - i * 5 - r,
                               cx + r, cy - i * 5 + r,
                               fill=palette[i % len(palette)], outline="#dddddd", width=1)
        canvas.create_text(cx, cy - n * 5 - r - 3,
                           text=f"${amount:.2f}", anchor="s",
                           font=("Arial", 9, "bold"), fill=label_color)

    # ------------------------------------------------------------------
    def _draw_step(self, idx):
        if not self._steps:
            return

        idx = max(0, min(idx, len(self._steps) - 1))
        self._step_idx = idx
        step = self._steps[idx]

        # Recompute folded seats, pot, and per-player street bets from history
        self._folded_seats = set()
        pot_so_far = 0.0
        player_street_bets = {}

        for s in self._steps[: idx + 1]:
            stype = s.get("type", "")
            if stype == "street":
                pot_so_far += sum(player_street_bets.values())
                player_street_bets = {}
            elif stype == "action":
                amt = s.get("amount", 0.0) or 0.0
                actor = s.get("actor", "")
                action = s.get("action", "").lower()
                if action == "fold":
                    for seat, info in self._hand.players.items():
                        if info.get("name") == actor:
                            self._folded_seats.add(seat)
                            break
                if amt > 0 and action not in ("fold", "check"):
                    prev = player_street_bets.get(actor, 0.0)
                    player_street_bets[actor] = max(prev, amt)

        canvas = self._canvas
        canvas.delete("all")

        CW, CH = self.CANVAS_W, self.CANVAS_H
        TABLE_TOP = 20
        TABLE_BOT = self.HERO_PANEL_Y - 10
        table_cx = CW // 2
        table_cy = (TABLE_TOP + TABLE_BOT) // 2

        # 1. Table oval
        canvas.create_oval(70, TABLE_TOP, CW - 70, TABLE_BOT,
                           fill=self.TABLE_COLOR, outline=self.TABLE_BORDER, width=5)

        # 2. Community cards — upper half of table
        board = step.get("board", [])
        card_w, card_h = 48, 68
        spacing = 56
        board_cy = table_cy - 44

        if board:
            total_w = (len(board) - 1) * spacing + card_w
            start_x = table_cx - total_w // 2 + card_w // 2
            for i, c in enumerate(board):
                self._draw_card(canvas, start_x + i * spacing, board_cy, c, card_w, card_h)
        else:
            total_w = 4 * spacing + card_w
            start_x = table_cx - total_w // 2 + card_w // 2
            for i in range(5):
                self._draw_card(canvas, start_x + i * spacing, board_cy, "", card_w, card_h, face_down=True)

        # 3. Central pot chip stack
        total_pot = pot_so_far + sum(player_street_bets.values())
        pot_cy = table_cy + 52
        if total_pot > 0:
            self._draw_chips(canvas, table_cx, pot_cy, total_pot, label_color=self._theme["gold"])
            canvas.create_text(table_cx, pot_cy + 22, text="POT", anchor="n",
                               font=("Arial", 8, "bold"), fill="#aaffaa")

        # 4. Seat circles + player labels + per-player bet chips
        hand = self._hand
        actor_name = step.get("actor", "") if step.get("type") == "action" else ""

        for seat, info in hand.players.items():
            name = info.get("name", f"Seat{seat}")
            stack = info.get("stack", 0.0)
            is_hero = info.get("is_hero", False)
            px, py = self._seat_pixel(seat)

            r = 32
            folded = seat in self._folded_seats
            is_actor = (name == actor_name)

            if folded:
                fill_color, outline_color, outline_w = "#383838", "#555555", 2
            elif is_hero:
                fill_color = self._theme["gold"]
                outline_color = "#ffffff" if is_actor else self._theme["gold"]
                outline_w = 4 if is_actor else 2
            else:
                fill_color = "#1e1e3a"
                outline_color = "#ffffff" if is_actor else "#5555aa"
                outline_w = 4 if is_actor else 2

            canvas.create_oval(px - r, py - r, px + r, py + r,
                               fill=fill_color, outline=outline_color, width=outline_w)

            name_color = "#666" if folded else ("#111111" if is_hero else "#ffffff")
            canvas.create_text(px, py - 9, text=name[:11], anchor="center",
                               font=("Arial", 9, "bold"), fill=name_color)
            stack_color = "#555" if folded else ("#222222" if is_hero else "#aaaadd")
            canvas.create_text(px, py + 10, text=f"${stack:.2f}", anchor="center",
                               font=("Arial", 8), fill=stack_color)

            # Bet chip midway between seat and pot
            bet_amt = player_street_bets.get(name, 0.0)
            if bet_amt > 0 and not folded:
                bx = int(px + (table_cx - px) * 0.42)
                by = int(py + (pot_cy   - py) * 0.42)
                self._draw_chips(canvas, bx, by, bet_amt, label_color="#ffffff")

        # 5. Dealer button
        btn_seat = getattr(hand, "button_seat", None)
        if btn_seat and btn_seat in hand.players:
            bx, by = self._seat_pixel(btn_seat)
            canvas.create_oval(bx + 26, by - 44, bx + 50, by - 20,
                               fill="#ffffff", outline="#999999", width=2)
            canvas.create_text(bx + 38, by - 32, text="D", anchor="center",
                               font=("Arial", 11, "bold"), fill="#000000")

        # 6. At showdown/result: display all shown hole cards at seat positions
        is_showdown = (step.get("type") == "result")
        if is_showdown:
            for seat, info in hand.players.items():
                name = info.get("name", "")
                shown = self._shown_cards.get(name, "")
                if not shown:
                    continue
                px, py = self._seat_pixel(seat)
                vcards = shown.split()
                vcw, vch, vsp = 36, 50, 40
                _, fy = self._seat_fraction(seat)
                vy = py + 46 if fy >= 0.55 else py - 62
                vtotal = (len(vcards) - 1) * vsp + vcw
                vstart = px - vtotal // 2 + vcw // 2
                for j, vc in enumerate(vcards):
                    self._draw_card(canvas, vstart + j * vsp, vy, vc, vcw, vch)

        # 7. Hero hole-card panel
        panel_y = self.HERO_PANEL_Y + 5
        panel_h = CH - self.HERO_PANEL_Y - 2
        canvas.create_rectangle(0, self.HERO_PANEL_Y, CW, CH,
                                 fill=self._theme["bg_panel"], outline="")
        canvas.create_text(18, panel_y + panel_h // 2, text="YOUR\nHAND",
                           anchor="w", font=("Arial", 9, "bold"),
                           fill=self._theme["gold"], justify="center")

        if hand.hero_cards:
            cards = hand.hero_cards.split()
            hcw, hch, hsp = 54, 76, 62
            show_up = idx > 0
            total_w = (len(cards) - 1) * hsp + hcw
            sx = CW // 2 - total_w // 2 + hcw // 2
            cy_h = panel_y + panel_h // 2
            for i, c in enumerate(cards):
                self._draw_card(canvas, sx + i * hsp, cy_h, c, hcw, hch, face_down=not show_up)

        # Action label
        stype = step.get("type", "")
        if stype == "street":
            self._action_lbl.configure(fg=self._theme["gold"])
        elif stype == "result":
            won = getattr(hand, "hero_won", 0.0) or 0.0
            self._action_lbl.configure(fg=self._theme["green"] if won > 0 else self._theme["red"])
        else:
            self._action_lbl.configure(fg=self._theme["text"])
        self._action_var.set(step.get("text", ""))

        self._step_lbl.config(text=f"Step {idx + 1} / {len(self._steps)}")
        self._prev_btn.config(state="normal" if idx > 0 else "disabled")
        self._next_btn.config(state="normal" if idx < len(self._steps) - 1 else "disabled")

    # ------------------------------------------------------------------
    def _prev_step(self):
        self._stop_play()
        if self._step_idx > 0:
            self._draw_step(self._step_idx - 1)

    def _next_step(self):
        self._stop_play()
        if self._step_idx < len(self._steps) - 1:
            self._draw_step(self._step_idx + 1)

    # ------------------------------------------------------------------
    def _toggle_play(self):
        if self._playing:
            self._stop_play()
        else:
            self._playing = True
            self._play_btn.config(text="⏸ Pause")
            self._auto_advance()

    def _stop_play(self):
        self._playing = False
        self._play_btn.config(text="▶▶ Play")
        if self._after_id:
            self._win.after_cancel(self._after_id)
            self._after_id = None

    def _auto_advance(self):
        if not self._playing:
            return
        if self._step_idx >= len(self._steps) - 1:
            self._stop_play()
            return
        self._draw_step(self._step_idx + 1)
        self._after_id = self._win.after(1200, self._auto_advance)


# ─── GUI Application ──────────────────────────────────────────────────────────
class PokerApp(ctk.CTk):
    def __init__(self, hud_only: bool = False):
        super().__init__()
        self._hud_only = hud_only
        self.title("LeakSnipe Live HUD" if hud_only else "Poker Tracker")
        if hud_only:
            self.withdraw()
        else:
            self.geometry("1280x800")
        self.settings = load_settings()
        self.theme_name = self.settings.get("theme", "Slate Blue")
        self.theme = THEMES.get(self.theme_name, THEMES["Slate Blue"])
        self.advanced_mode = self.settings.get("advanced_mode", False)

        self.configure(fg_color=self.theme["bg_base"])

        self.db = HandDatabase()
        discovered_dirs = discover_scan_dirs(self.settings)
        self.settings["scan_dirs"] = merge_scan_dirs(self.settings.get("scan_dirs"), discovered_dirs)
        if not self.settings["hero_names"].get("BetACR"):
            self.settings["hero_names"]["BetACR"] = DEFAULT_SETTINGS["hero_names"].get("BetACR", "JohnDaWalka")
        if discovered_dirs:
            save_settings(self.settings)
        self.importer = HandImporter(self.settings, db=self.db)
        self.leak_engine = LeakEngine(self.settings)
        self.summary_gen = SummaryGenerator()
        self.ocr_engine = PokerOCR()
        self.capture_bridge = OCRCaptureBridge()
        self._capture_hotkey_thread = None
        self._capture_poll_job = None
        self.current_stats = {}
        self.station_detector = StationDetector(self.settings)
        self.ev_calculator = EVCalculator()
        self.tilt_meter = TiltMeter(self.settings)
        self.player_stats = []
        self.tilt_data = {}
        self._post_scan_generation = 0
        self._last_hands_snapshot = []
        self._post_scan_debounce_job = None
        self._hands_active_hand_id = None
        self._hand_tag_by_id: Dict[str, str] = {}
        self._hands_list_fingerprint = None
        self._hand_click_after_id = None
        self._dashboard_fingerprint = None
        self._leak_tab_fingerprint = None
        self._leak_graph_fingerprint = None

        # ── AI Engine ─────────────────────────────────────────────────────
        self.ai_processor = None
        if HAS_AI_ENGINE and not hud_only:
            try:
                self.ai_processor = AIProcessor()
            except Exception:
                pass

        # ── Live HUD ──────────────────────────────────────────────────────
        self.live_hud_overlay = None
        self.table_detector = None
        self.hand_monitor = None
        self._live_hud_on = False
        self._hud_layout_mode = False

        self._quit_hotkey_id = 2
        if hud_only:
            _write_hud_pid()
            self.protocol("WM_DELETE_WINDOW", self._on_hud_only_close)
            self.bind_all("<Escape>", self._on_hud_escape)
            self._start_quit_hotkey_listener()
        else:
            self._build_ui()
            self.protocol("WM_DELETE_WINDOW", self._on_app_close)
            self._start_ocr_capture_bridge()
            self._start_ocr_hotkeys()
            self._schedule_ocr_bridge_poll()
        if hud_only:
            self._hud_only_startup()
        else:
            self._initial_scan()

        if hud_only or self.settings.get("live_hud_enabled", False):
            delay = 800 if hud_only else 2000
            self.after(delay, self._start_live_hud)

    # ── UI Construction ───────────────────────────────────────────────────
    def _build_ui(self):
        self.tabview = ctk.CTkTabview(self, fg_color=self.theme["bg_panel"],
                                       segmented_button_fg_color=self.theme["bg_accent"],
                                       segmented_button_selected_color=self.theme["bg_accent"],
                                       segmented_button_unselected_color=self.theme["bg_base"],
                                       text_color=self.theme["text"])
        self.tabview.pack(fill="both", expand=True, padx=8, pady=(8, 0))

        # Full and abbreviated tab labels — swap when window gets narrow
        self._tab_labels_full  = ["Dashboard", "Hands", "Leaks", "OCR", "AI / GTO", "Settings"]
        self._tab_labels_short = ["Dash",      "Hands", "Leaks", "OCR", "AI/GTO",   "Config"]
        for label in self._tab_labels_full:
            self.tabview.add(label)

        _raw_dash     = self.tabview.tab("Dashboard")
        _raw_hands    = self.tabview.tab("Hands")
        _raw_leak     = self.tabview.tab("Leaks")
        _raw_ocr      = self.tabview.tab("OCR")
        _raw_ai       = self.tabview.tab("AI / GTO")
        _raw_settings = self.tabview.tab("Settings")

        def _scroll_tab(parent, tab_name):
            sf = ctk.CTkScrollableFrame(
                parent,
                fg_color="transparent",
                scrollbar_button_color=self.theme["bg_accent"],
                scrollbar_button_hover_color=self.theme["border_hl"],
            )
            sf.pack(fill="both", expand=True, pady=(0, 12))

            _orig_wheel = sf._mouse_wheel_all

            def _guarded_wheel(event, name=tab_name, orig=_orig_wheel):
                try:
                    if self.tabview.get() != name:
                        return
                except Exception:
                    return
                orig(event)

            sf._mouse_wheel_all = _guarded_wheel
            return sf

        def _plain_tab(parent):
            frame = ctk.CTkFrame(parent, fg_color="transparent")
            frame.pack(fill="both", expand=True, pady=(0, 12))
            return frame

        self.tab_dash     = _scroll_tab(_raw_dash, "Dashboard")
        self.tab_hands    = _plain_tab(_raw_hands)
        self.tab_leak     = _scroll_tab(_raw_leak, "Leaks")
        self.tab_ocr      = _scroll_tab(_raw_ocr, "OCR")
        self.tab_ai       = _scroll_tab(_raw_ai, "AI / GTO")
        self.tab_settings = _scroll_tab(_raw_settings, "Settings")
        self._tab_scroll_frames = {
            "Dashboard": self.tab_dash,
            "Leaks": self.tab_leak,
            "OCR": self.tab_ocr,
            "AI / GTO": self.tab_ai,
            "Settings": self.tab_settings,
        }
        self._tab_resize_job = None

        self._build_dashboard()
        self._build_hands_tab()
        self._build_leak_tab()
        self._build_ocr_tab()
        self._build_ai_tab()
        self._build_settings_tab()
        self._build_status_bar()
        self._build_header_bar()

        # Dynamically rename tabs when window width changes to avoid text clipping
        _TAB_BREAK = 920
        self._tabs_are_short = None
        def _apply_tab_labels(width):
            want_short = width < _TAB_BREAK
            if want_short == self._tabs_are_short:
                return
            self._tabs_are_short = want_short
            labels = self._tab_labels_short if want_short else self._tab_labels_full
            try:
                seg_btn = self.tabview._segmented_button
                for btn, lbl in zip(seg_btn._buttons_dict.values(), labels):
                    btn.configure(text=lbl)
            except Exception:
                pass

        def _tabview_configure(event):
            if event.widget is not self:
                return
            if self._tab_resize_job is not None:
                try:
                    self.after_cancel(self._tab_resize_job)
                except Exception:
                    pass
            self._tab_resize_job = self.after(200, lambda w=event.width: _apply_tab_labels(w))
        # Bind on the root window, not CTkTabview (CTkTabview.bind raises NotImplementedError)
        self.bind("<Configure>", _tabview_configure, add="+")

        # ── Fix tab-bar flicker ───────────────────────────────────────────────
        # CTkTabview._segmented_button_callback does grid_forget(old) THEN
        # grid(new), producing a single-frame blank flash between tabs.
        # Swapping the order — show new first, hide old second — eliminates the
        # white/background flash entirely without touching CTkTabview internals.
        def _no_flicker_tab_callback(selected_name, _tv=self.tabview):
            old_name = _tv._current_name
            _tv._current_name = selected_name
            _tv._set_grid_current_tab()             # place new tab first
            if old_name != selected_name:
                _tv._tab_dict[old_name].grid_forget()   # then remove old tab
            if _tv._command is not None:
                _tv._command()
        self.tabview._segmented_button.configure(command=_no_flicker_tab_callback)

    def _panel(self, parent, *, fill="x", expand=False, padx=6, pady=4, fg_color=None):
        t = self.theme
        # Layered shadow wrapper gives panels more depth without affecting layout.
        shadow = tk.Frame(parent, bg=_darken(t["bg_base"], 0.72), padx=0, pady=0)
        shadow.pack(fill=fill, expand=expand, padx=padx, pady=pady)
        mid_shadow = tk.Frame(shadow, bg=_blend(t["bg_panel"], t["bg_base"], 0.45), padx=0, pady=0)
        mid_shadow.pack(fill=fill, expand=expand, padx=(1, 5), pady=(1, 5))
        panel = ctk.CTkFrame(
            mid_shadow,
            fg_color=fg_color or t["bg_panel"],
            border_width=1,
            border_color=t["border"],
            corner_radius=12,
        )
        panel.pack(fill=fill, expand=expand, padx=(0, 3), pady=(0, 3))
        accent_bar = tk.Frame(panel, bg=t["bg_card"], height=4)
        accent_bar.pack(fill="x")
        for color in (
            _lighten(t["border_hl"], 0.22),
            _blend(t["border_hl"], t["gold"], 0.38),
            _blend(t["bg_accent"], t["border_hl"], 0.65),
            _darken(t["bg_accent"], 0.08),
        ):
            tk.Frame(accent_bar, bg=color, width=10).pack(side="left", fill="both", expand=True)
        tk.Frame(panel, bg=_lighten(t["bg_card"], 0.06), height=1).pack(fill="x")
        content = ctk.CTkFrame(panel, fg_color="transparent", corner_radius=0)
        content.pack(fill="both", expand=True)
        return content

    def _section_label(self, parent, text, *, size=13, color=None):
        import tkinter.font as tkfont
        accent      = color or _lighten(self.theme["border_hl"], 0.38)
        shadow_col  = _darken(self.theme["bg_base"], 0.55)
        shell_bg    = _blend(self.theme["bg_card"], self.theme["bg_panel"], 0.68)
        shell = ctk.CTkFrame(
            parent,
            fg_color=shell_bg,
            corner_radius=10,
            border_width=1,
            border_color=_blend(self.theme["border_hl"], self.theme["gold"], 0.28),
        )
        rail = tk.Frame(shell, bg=accent, width=3)
        rail.pack(side="left", fill="y", padx=(0, 8))
        # Measure text for canvas sizing
        _mf = tkfont.Font(family="Consolas", size=size, weight="bold")
        txt_w = _mf.measure(text.upper()) + 10
        txt_h = size + 14
        canvas = tk.Canvas(
            shell,
            bg=shell_bg,
            width=txt_w,
            height=txt_h,
            highlightthickness=0,
            bd=0,
        )
        canvas.create_text(2, 2, text=text.upper(), font=_mf, fill=shadow_col, anchor="nw")
        canvas.create_text(0, 0, text=text.upper(), font=_mf, fill=accent, anchor="nw")
        canvas.pack(side="left", padx=(2, 10), pady=(4, 0))
        underline = tk.Frame(shell, bg=_blend(accent, self.theme["gold"], 0.24), height=2)
        underline.pack(fill="x", padx=(0, 10), pady=(2, 5), side="bottom")
        return shell

    def _action_button(self, parent, text, command, *, tone="neutral", width=100, height=34, bold=False):
        t = self.theme
        palettes = {
            "neutral": (_blend(t["bg_accent"], t["border_hl"], 0.18),  t["bg_hover"],                         t["text"]),
            "accent":  (_blend(t["gold"], t["orange"], 0.16),       _darken(t["gold"],   0.25),             t["bg_base"]),
            "success": (_blend(t["green"], t["border_hl"], 0.14),      _darken(t["green"],  0.25),             t["bg_base"]),
            "danger":  (_blend(t["red"], t["orange"], 0.10),        _darken(t["red"],    0.25),             t["text"]),
        }
        fg_color, hover_color, text_color = palettes.get(tone, palettes["neutral"])
        weight = "bold" if bold else "normal"
        return ctk.CTkButton(
            parent,
            text=text,
            command=command,
            fg_color=fg_color,
            hover_color=hover_color,
            text_color=text_color,
            width=width,
            height=height,
            corner_radius=6,
            border_width=1,
            border_color=_lighten(fg_color, 0.28),
            font=(_FF, 11, weight),
        )

    def _create_scroll_textbox(self, parent, *, height=None, font=_F_DATA_MD, wrap="word"):
        container = tk.Frame(
            parent,
            bg=self.theme["bg_input"],
            highlightthickness=1,
            highlightbackground=self.theme["border"],
            highlightcolor=self.theme["border_hl"],
        )
        if height is not None:
            container.configure(height=height)
            container.pack_propagate(False)

        text = tk.Text(
            container,
            bg=self.theme["bg_input"],
            fg=self.theme["text"],
            insertbackground=self.theme["text"],
            selectbackground=self.theme["select_bg"],
            relief="flat",
            bd=0,
            wrap=wrap,
            font=font,
            padx=8,
            pady=6,
        )
        yscroll = tk.Scrollbar(
            container,
            orient="vertical",
            command=text.yview,
            bg=self.theme["bg_card"],
            activebackground=self.theme["bg_hover"],
            troughcolor=self.theme["bg_base"],
            highlightthickness=0,
            bd=0,
            width=12,
        )
        yscroll.pack(side="right", fill="y")
        text.configure(yscrollcommand=yscroll.set)

        if wrap == "none":
            xscroll = tk.Scrollbar(
                container,
                orient="horizontal",
                command=text.xview,
                bg=self.theme["bg_card"],
                activebackground=self.theme["bg_hover"],
                troughcolor=self.theme["bg_base"],
                highlightthickness=0,
                bd=0,
                width=12,
            )
            xscroll.pack(side="bottom", fill="x")
            text.configure(xscrollcommand=xscroll.set)

        text.pack(side="left", fill="both", expand=True)

        def _on_text_wheel(event, txt=text):
            txt.yview_scroll(int(-1 * (event.delta / 120)), "units")
            return "break"

        text.bind("<MouseWheel>", _on_text_wheel, add="+")
        return container, text

    def _build_dashboard(self):
        tab = self.tab_dash
        command = self._panel(tab, pady=(6, 3))
        command_row = tk.Frame(command, bg=self.theme["bg_panel"])
        command_row.pack(fill="x", padx=10, pady=10)

        command_copy = tk.Frame(command_row, bg=self.theme["bg_panel"])
        command_copy.pack(side="left", fill="x", expand=True)
        ctk.CTkLabel(
            command_copy,
            text="Session Command",
            text_color=_lighten(self.theme["border_hl"], 0.12),
            font=ctk.CTkFont(family=_FF, size=13, weight="bold"),
        ).pack(anchor="w")
        ctk.CTkLabel(
            command_copy,
            text="Live hands · tilt signal · HUD control",
            text_color=self.theme["text_dim"],
            font=ctk.CTkFont(family=_FF, size=11, slant="italic"),
        ).pack(anchor="w", pady=(2, 0))

        chip_row = tk.Frame(command_row, bg=self.theme["bg_panel"])
        chip_row.pack(side="right", padx=(12, 0))

        def _dash_chip(parent, title, value_var, fg, width):
            chip_shadow = tk.Frame(parent, bg=_darken(self.theme["bg_base"], 0.7))
            chip_shadow.pack(side="left", padx=4, pady=(2, 4))
            chip = ctk.CTkFrame(chip_shadow, fg_color=self.theme["bg_card"], corner_radius=14,
                                border_width=1, border_color=self.theme["border"])
            chip.pack(fill="both", expand=True, padx=(0, 3), pady=(0, 3))
            chip_bar = tk.Frame(chip, bg=self.theme["bg_card"], height=3)
            chip_bar.pack(fill="x", padx=10, pady=(8, 0))
            for color in (_lighten(fg, 0.18), fg, _blend(fg, self.theme["bg_accent"], 0.55)):
                tk.Frame(chip_bar, bg=color, width=10).pack(side="left", fill="both", expand=True)
            ctk.CTkLabel(
                chip,
                text=title.upper(),
                text_color=_lighten(fg, 0.2),
                font=ctk.CTkFont(
                    family="Consolas",
                    size=9,
                    weight="bold",
                    slant="italic",
                    underline=True,
                ),
            ).pack(anchor="w", padx=12, pady=(6, 0))
            ctk.CTkLabel(chip, textvariable=value_var, text_color=self.theme["text"],
                         font=_F_DATA, width=width, anchor="w", justify="left",
                         wraplength=max(width * 6, 120)).pack(anchor="w", padx=12, pady=(2, 10))

        self.dash_command_hands_var = ctk.StringVar(value="0 hands ready")
        self.dash_command_feed_var = ctk.StringVar(value="Waiting for imported hands")
        self.dash_command_status_var = ctk.StringVar(value="Tilt data pending")
        _dash_chip(chip_row, "Database", self.dash_command_hands_var, self.theme["green"], 160)
        _dash_chip(chip_row, "Latest Feed", self.dash_command_feed_var, self.theme["gold"], 180)
        _dash_chip(chip_row, "Mental Game", self.dash_command_status_var, self.theme["red"], 160)

        top = self._panel(tab, pady=6)

        self.dash_cards = {}
        card_defs = [
            ("Hands",   "0",    self.theme["text"]),
            ("VPIP",    "0%",   self.theme["gold"]),
            ("PFR",     "0%",   self.theme["gold"]),
            ("AF",      "0.0",  self.theme["gold"]),
            ("Won",     "0",    self.theme["green"]),
            ("Lost",    "0",    self.theme["red"]),
            ("EV Diff", "0",    self.theme["gold"]),
        ]
        t = self.theme
        # Inner frame owns the grid — avoids pack/grid conflict with _panel()'s accent line
        cards_row = tk.Frame(top, bg=t["bg_panel"])
        cards_row.pack(fill="x", padx=2, pady=(2, 6))
        # DPI-aware card sizing: scale 158×100 base by system DPI factor
        _dpi_scale = max(1.0, self.winfo_fpixels('1i') / 96.0)
        _cw, _ch = round(158 * _dpi_scale), round(100 * _dpi_scale)
        for i, (label, default, color) in enumerate(card_defs):
            cards_row.grid_columnconfigure(i, weight=1)
            # Shadow layer — outer size is DPI-scaled; inner frames fill via relwidth/relheight
            shadow = tk.Frame(cards_row, bg=_darken(t["bg_base"], 0.74),
                              width=_cw, height=_ch)
            shadow.grid(row=0, column=i, padx=5, pady=8, sticky="nsew")
            shadow.grid_propagate(False)
            inner_shadow = tk.Frame(shadow, bg=_blend(t["bg_panel"], t["bg_base"], 0.42))
            inner_shadow.place(x=1, y=1, relwidth=1, relheight=1)
            # Card on top of shadow (placed at 0,0, shadow shows at bottom-right)
            card = ctk.CTkFrame(inner_shadow, fg_color=t["bg_card"],
                                corner_radius=12, border_width=1,
                                border_color=t["border"])
            card.place(x=0, y=0, relwidth=1, relheight=1)
            card.pack_propagate(False)
            # Colored top accent bar with a subtle gradient.
            accent = tk.Frame(card, bg=t["bg_card"], height=4)
            accent.place(x=10, y=2, relwidth=0.87)
            for segment in (_lighten(color, 0.24), color, _blend(color, t["bg_accent"], 0.48)):
                tk.Frame(accent, bg=segment, width=10).pack(side="left", fill="both", expand=True)
            tk.Frame(card, bg=_lighten(t["bg_card"], 0.08), height=1).place(x=10, y=8, relwidth=0.87)
            # KPI card label — Segoe UI italic, no underline, lighter weight
            ctk.CTkLabel(
                card,
                text=label,
                text_color=_blend(color, t["text_dim"], 0.35),
                font=ctk.CTkFont(family=_FF, size=10, slant="italic"),
            ).place(relx=0.5, y=16, anchor="n")
            # Value label — Consolas bold (data font)
            val = ctk.CTkLabel(card, text=default, text_color=color,
                               font=_F_KPI)
            val.place(relx=0.5, y=40, anchor="n")
            self.dash_cards[label] = val

        overview_row = ctk.CTkFrame(tab, fg_color="transparent")
        overview_row.pack(fill="x", padx=6, pady=4)
        overview_row.grid_columnconfigure(0, weight=3)
        overview_row.grid_columnconfigure(1, weight=2)

        site_frame = ctk.CTkFrame(
            overview_row,
            fg_color=self.theme["bg_panel"],
            border_width=1,
            border_color=self.theme["border"],
            corner_radius=10,
        )
        site_frame.grid(row=0, column=0, sticky="nsew", padx=(0, 3))
        self._section_label(site_frame, "By Site").pack(anchor="w", padx=8, pady=4)
        dash_site_box, self.dash_site_text = self._create_scroll_textbox(
            site_frame, height=86, font=(_FM, 12, "normal")
        )
        dash_site_box.pack(fill="x", padx=8, pady=(0, 8))
        self.dash_site_text.configure(state="disabled")

        tilt_frame = ctk.CTkFrame(
            overview_row,
            fg_color=self.theme["bg_panel"],
            border_width=1,
            border_color=self.theme["border"],
            corner_radius=10,
        )
        tilt_frame.grid(row=0, column=1, sticky="nsew", padx=(3, 0))
        tilt_top = ctk.CTkFrame(tilt_frame, fg_color="transparent")
        tilt_top.pack(fill="x", padx=8, pady=(6, 0))
        self._section_label(tilt_top, "Tilt", size=13).pack(side="left")
        self.tilt_score_label = ctk.CTkLabel(
            tilt_top,
            text="Cool 0/100",
            text_color=self.theme["green"],
            font=("Consolas", 12, "bold"),
        )
        self.tilt_score_label.pack(side="right")
        self.tilt_bar = ctk.CTkProgressBar(
            tilt_frame,
            fg_color=self.theme["bg_input"],
            progress_color=self.theme["green"],
            height=18,
        )
        self.tilt_bar.pack(fill="x", padx=8, pady=4)
        self.tilt_bar.set(0)
        self.tilt_advice_label = ctk.CTkLabel(
            tilt_frame,
            text="Waiting for data",
            text_color=self.theme["text_dim"],
            font=_F_DATA_MD,
        )
        self.tilt_advice_label.pack(anchor="w", padx=12, pady=(0, 2))
        tilt_indicators_box, self.tilt_indicators_text = self._create_scroll_textbox(
            tilt_frame, height=52, font=_F_DATA
        )
        tilt_indicators_box.pack(fill="x", padx=8, pady=(0, 4))
        self.tilt_indicators_text.configure(state="disabled")

        action_bar = ctk.CTkFrame(tilt_frame, fg_color="transparent")
        action_bar.pack(fill="x", padx=8, pady=(0, 8))
        self._action_button(
            action_bar,
            "Player HUD",
            self._open_hud_window,
            tone="accent",
            width=120,
            bold=True,
        ).pack(side="left")

        self.graph_frame = self._panel(tab)
        self._section_label(self.graph_frame, "Trend & Mix").pack(anchor="w", padx=8, pady=4)
        self.dash_fig = Figure(figsize=(10, 3), dpi=80)
        self.dash_fig.patch.set_facecolor(self.theme["graph_bg"])
        self.dash_canvas = FigureCanvasTkAgg(self.dash_fig, master=self.graph_frame)
        _gw = self.dash_canvas.get_tk_widget()
        _gw.pack(fill="x", padx=8, pady=(0, 8))

        def _resize_graph(event, _fig=self.dash_fig, _cv=self.dash_canvas):
            w_in = max(4.0, (event.width - 16) / _fig.get_dpi())
            _fig.set_size_inches(w_in, 3, forward=False)
            _cv.draw_idle()
        _gw.bind("<Configure>", _resize_graph)

        recent_frame = self._panel(tab, fill="both", expand=True)
        self._section_label(recent_frame, "Recent Hands").pack(anchor="w", padx=8, pady=4)
        dash_recent_box, self.dash_recent = self._create_scroll_textbox(
            recent_frame, font=_F_DATA_MD, wrap="none"
        )
        dash_recent_box.pack(fill="both", expand=True, padx=8, pady=(0, 8))
        self.dash_recent.configure(state="disabled")

    def _build_hands_tab(self):
        tab = self.tab_hands

        filter_frame = ctk.CTkFrame(tab, fg_color=self.theme["bg_panel"],
                                    border_width=1, border_color=self.theme["border"])
        filter_frame.pack(fill="x", padx=6, pady=(6, 2))

        ctk.CTkLabel(filter_frame, text="Site", text_color=self.theme["text"]).pack(side="left", padx=4)
        self.hand_site_var = ctk.StringVar(value="All")
        self.hand_site_menu = ctk.CTkOptionMenu(
            filter_frame,
            variable=self.hand_site_var,
            values=["All", "CoinPoker", "BetACR", "GGPoker", "ReplayPoker"],
            fg_color=self.theme["bg_accent"],
            button_color=self.theme["bg_hover"],
            text_color=self.theme["text"],
            width=100,
            dropdown_fg_color=self.theme["bg_card"],
            dropdown_hover_color=self.theme["bg_accent"],
            command=lambda _: self._refresh_hands_list(),
        )
        self.hand_site_menu.pack(side="left", padx=4)

        ctk.CTkLabel(filter_frame, text="Net", text_color=self.theme["text"]).pack(side="left", padx=4)
        self.hand_result_var = ctk.StringVar(value="All")
        ctk.CTkOptionMenu(
            filter_frame,
            variable=self.hand_result_var,
            values=["All", "Won", "Lost"],
            fg_color=self.theme["bg_accent"],
            button_color=self.theme["bg_hover"],
            text_color=self.theme["text"],
            width=80,
            dropdown_fg_color=self.theme["bg_card"],
            dropdown_hover_color=self.theme["bg_accent"],
            command=lambda _: self._refresh_hands_list(),
        ).pack(side="left", padx=4)

        ctk.CTkLabel(filter_frame, text="Sort", text_color=self.theme["text"]).pack(side="left", padx=(12, 4))
        self.hand_sort_var = ctk.StringVar(value="Date ↓")
        ctk.CTkOptionMenu(
            filter_frame,
            variable=self.hand_sort_var,
            values=["Date ↓", "Date ↑", "Result ↓ (Big wins)", "Result ↑ (Big losses)", "Pot ↓", "Pot ↑"],
            fg_color=self.theme["bg_accent"],
            button_color=self.theme["bg_hover"],
            text_color=self.theme["text"],
            width=170,
            dropdown_fg_color=self.theme["bg_card"],
            dropdown_hover_color=self.theme["bg_accent"],
            command=lambda _: self._refresh_hands_list(),
        ).pack(side="left", padx=4)

        self._action_button(filter_frame, "Reload", self._manual_refresh, width=74).pack(side="right", padx=4)

        adv_filter_frame = ctk.CTkFrame(tab, fg_color=self.theme["bg_panel"],
                                        border_width=1, border_color=self.theme["border"])
        adv_filter_frame.pack(fill="x", padx=6, pady=(0, 2))

        ctk.CTkLabel(adv_filter_frame, text="From", text_color=self.theme["text"], font=_F_BODY).pack(side="left", padx=(6, 2))
        self.filter_date_from_var = ctk.StringVar(value="")
        ctk.CTkEntry(
            adv_filter_frame,
            textvariable=self.filter_date_from_var,
            fg_color=self.theme["bg_input"],
            text_color=self.theme["text"],
            width=90,
            placeholder_text="MM/DD/YYYY",
            font=_F_DATA,
        ).pack(side="left", padx=2)

        ctk.CTkLabel(adv_filter_frame, text="To",text_color=self.theme["text"], font=_F_BODY).pack(side="left", padx=(6, 2))
        self.filter_date_to_var = ctk.StringVar(value="")
        ctk.CTkEntry(
            adv_filter_frame,
            textvariable=self.filter_date_to_var,
            fg_color=self.theme["bg_input"],
            text_color=self.theme["text"],
            width=90,
            placeholder_text="MM/DD/YYYY",
            font=_F_DATA,
        ).pack(side="left", padx=2)

        ctk.CTkLabel(adv_filter_frame, text="Pot",text_color=self.theme["text"], font=_F_BODY).pack(side="left", padx=(10, 2))
        self.filter_pot_min_var = ctk.StringVar(value="")
        ctk.CTkEntry(
            adv_filter_frame,
            textvariable=self.filter_pot_min_var,
            fg_color=self.theme["bg_input"],
            text_color=self.theme["text"],
            width=60,
            placeholder_text="Min",
            font=_F_DATA,
        ).pack(side="left", padx=2)

        self.filter_pot_max_var = ctk.StringVar(value="")
        ctk.CTkEntry(
            adv_filter_frame,
            textvariable=self.filter_pot_max_var,
            fg_color=self.theme["bg_input"],
            text_color=self.theme["text"],
            width=60,
            placeholder_text="Max",
            font=_F_DATA,
        ).pack(side="left", padx=2)

        ctk.CTkLabel(adv_filter_frame, text="Game", text_color=self.theme["text"], font=_F_BODY).pack(side="left", padx=(10, 2))
        self.filter_type_var = ctk.StringVar(value="All")
        ctk.CTkOptionMenu(
            adv_filter_frame,
            variable=self.filter_type_var,
            values=["All", "Cash", "Tournament"],
            fg_color=self.theme["bg_accent"],
            button_color=self.theme["bg_hover"],
            text_color=self.theme["text"],
            width=100,
            dropdown_fg_color=self.theme["bg_card"],
            dropdown_hover_color=self.theme["bg_accent"],
            font=_F_DATA,
            command=lambda _: self._refresh_hands_list(),
        ).pack(side="left", padx=2)

        ctk.CTkLabel(adv_filter_frame, text="Tag",text_color=self.theme["text"], font=_F_BODY).pack(side="left", padx=(10, 2))
        self.filter_tag_var = ctk.StringVar(value="All")
        self.filter_tag_menu = ctk.CTkOptionMenu(
            adv_filter_frame,
            variable=self.filter_tag_var,
            values=["All"],
            fg_color=self.theme["bg_accent"],
            button_color=self.theme["bg_hover"],
            text_color=self.theme["text"],
            width=110,
            dropdown_fg_color=self.theme["bg_card"],
            dropdown_hover_color=self.theme["bg_accent"],
            font=_F_DATA,
            command=lambda _: self._refresh_hands_list(),
        )
        self.filter_tag_menu.pack(side="left", padx=2)

        self._action_button(adv_filter_frame, "Apply", self._refresh_hands_list, width=64, height=24, bold=True).pack(side="left", padx=(8, 2))
        ctk.CTkLabel(adv_filter_frame, text="Villain", text_color=self.theme["text"], font=_F_BODY).pack(side="left", padx=(10, 2))
        self.filter_opp_type_var = ctk.StringVar(value="All")
        self.filter_opp_type_menu = ctk.CTkOptionMenu(
            adv_filter_frame,
            variable=self.filter_opp_type_var,
            values=["All", "Fish", "Calling Station", "LAG", "TAG", "Nit", "Maniac", "Regular", "Unknown"],
            fg_color=self.theme["bg_accent"],
            button_color=self.theme["bg_hover"],
            text_color=self.theme["text"],
            width=120,
            dropdown_fg_color=self.theme["bg_card"],
            dropdown_hover_color=self.theme["bg_accent"],
            font=_F_DATA,
            command=lambda _: self._refresh_hands_list(),
        )
        self.filter_opp_type_menu.pack(side="left", padx=2)

        self._action_button(adv_filter_frame, "Clear", self._clear_filters, width=60, height=24).pack(side="left", padx=2)

        sel_frame = ctk.CTkFrame(tab, fg_color=self.theme["bg_panel"],
                                 border_width=1, border_color=self.theme["border"])
        sel_frame.pack(fill="x", padx=6, pady=(0, 4))

        self.select_all_var = ctk.BooleanVar(value=False)
        ctk.CTkCheckBox(
            sel_frame,
            text="All",
            variable=self.select_all_var,
            fg_color=self.theme["bg_accent"],
            hover_color=self.theme["green"],
            text_color=self.theme["text"],
            font=_F_DATA_MD,
            checkbox_width=18,
            checkbox_height=18,
            command=self._toggle_select_all,
        ).pack(side="left", padx=8)

        self.hand_sel_count_label = ctk.CTkLabel(
            sel_frame,
            text="0 selected",
            text_color=self.theme["text_dim"],
            font=_F_DATA_MD,
        )
        self.hand_sel_count_label.pack(side="left", padx=8)

        self._action_button(sel_frame, "View", self._view_selected_hand, tone="accent", width=72, bold=True).pack(side="left", padx=4)
        self._action_button(sel_frame, "Compare", self._compare_selected, tone="accent", width=100, bold=True).pack(side="left", padx=4)
        self._action_button(sel_frame, "Copy", self._copy_selected_hands, width=84).pack(side="left", padx=4)
        self._action_button(sel_frame, "Tag", self._tag_selected_hands, width=70).pack(side="left", padx=4)
        self._action_button(sel_frame, "Analyze", self._analyze_filtered, tone="accent", width=96, bold=True).pack(side="left", padx=4)
        self._action_button(sel_frame, "Export", self._export_filtered, width=82).pack(side="right", padx=4)

        hand_list_container = ctk.CTkFrame(tab, fg_color=self.theme["bg_input"],
                                           border_width=1, border_color=self.theme["border"],
                                           corner_radius=10)
        hand_list_container.pack(fill="both", expand=True, padx=6, pady=2)

        header_text = f"  {'Date':14s} {'Site':10s} {'Game':5s} {'Cards':8s} {'Pos':4s} {'Net':>8s} {'Pot':>7s} {'EV':>7s}  Tags"
        header_label = ctk.CTkLabel(
            hand_list_container,
            text=header_text,
            text_color=self.theme["gold"],
            font=_F_DATA_BOLD,
            anchor="w",
        )
        header_label.pack(fill="x", padx=2, pady=(2, 0))

        self.hands_text = tk.Text(
            hand_list_container,
            bg=self.theme["bg_input"],
            fg=self.theme["text"],
            font=_F_DATA_MD,
            relief="flat",
            cursor="arrow",
            selectbackground=self.theme["select_bg"],
            wrap="none",
            exportselection=False,
        )
        self.hands_text.pack(fill="both", expand=True, side="left")
        self.hands_text.bind("<Key>", lambda _e: "break")
        self.hands_text.bind("<MouseWheel>", self._on_hands_mousewheel)
        hands_scrollbar = tk.Scrollbar(
            hand_list_container,
            command=self.hands_text.yview,
            bg=self.theme["bg_card"],
            activebackground=self.theme["bg_hover"],
            troughcolor=self.theme["bg_base"],
            highlightthickness=0,
            bd=0,
            width=12,
        )
        hands_scrollbar.pack(fill="y", side="right")
        hands_xscrollbar = tk.Scrollbar(
            hand_list_container,
            orient="horizontal",
            command=self.hands_text.xview,
            bg=self.theme["bg_card"],
            activebackground=self.theme["bg_hover"],
            troughcolor=self.theme["bg_base"],
            highlightthickness=0,
            bd=0,
            width=12,
        )
        hands_xscrollbar.pack(fill="x", side="bottom")
        self.hands_text.configure(yscrollcommand=hands_scrollbar.set, xscrollcommand=hands_xscrollbar.set)

        self.hand_count_label = ctk.CTkLabel(tab, text="0 hands", text_color=self.theme["text_dim"], font=_F_DATA)
        self.hand_count_label.pack(anchor="w", padx=10, pady=(0, 2))

        detail_frame = ctk.CTkFrame(tab, fg_color=self.theme["bg_panel"],
                                    border_width=1, border_color=self.theme["border"])
        detail_frame.pack(fill="both", expand=True, padx=6, pady=4)

        detail_top = ctk.CTkFrame(detail_frame, fg_color=self.theme["bg_panel"])
        detail_top.pack(fill="x")
        # Shadow frame + label stack for crisp readable title
        _title_canvas_frame = tk.Frame(detail_top, bg=self.theme["bg_panel"])
        _title_canvas_frame.pack(side="left", anchor="w", padx=8, pady=4)
        import tkinter.font as tkfont
        _tf = tkfont.Font(family="Consolas", size=13, weight="bold")
        _tw = _tf.measure("Details") + 20
        _title_cv = tk.Canvas(_title_canvas_frame, bg=self.theme["bg_panel"],
                              width=_tw, height=22, highlightthickness=0, bd=0)
        _title_cv.pack()
        _shadow_id  = _title_cv.create_text(2, 2, text="Details",
                                             font=_tf,
                                             fill=_darken(self.theme["bg_base"], 0.6),
                                             anchor="nw")
        _text_id    = _title_cv.create_text(0, 0, text="Details",
                                             font=_tf,
                                             fill=self.theme["gold"],
                                             anchor="nw")
        # Store canvas + ids so we can update text later
        self._detail_title_canvas = _title_cv
        self._detail_title_shadow_id = _shadow_id
        self._detail_title_text_id   = _text_id
        # Keep a dummy label for backward compat (configure calls will be rerouted)
        self.detail_title_label = _title_cv
        self.detail_title_label.configure = lambda **kw: (
            _title_cv.itemconfigure(_shadow_id, text=kw.get("text", "Details")),
            _title_cv.itemconfigure(_text_id,   text=kw.get("text", "Details")),
        ) if "text" in kw else None

        # ── Inline tag strip ──
        self._detail_tag_strip_frame = tk.Frame(detail_frame, bg=self.theme["bg_panel"])
        self._detail_tag_strip_frame.pack(fill="x", padx=8, pady=(0, 2))
        self._detail_tag_hand = None   # currently displayed hand
        # Populated in _refresh_detail_tag_strip()

        hand_detail_box, self.hand_detail_text = self._create_scroll_textbox(
            detail_frame, font=_F_DATA, wrap="none"
        )
        hand_detail_box.pack(fill="both", expand=True, padx=8, pady=(0, 8))

        self._hand_objects = {}
        self._selected_hand_ids = set()

    def _refresh_detail_tag_strip(self, hand=None):
        """Rebuild the inline tag strip for the given hand (or clear it)."""
        frame = self._detail_tag_strip_frame
        for w in frame.winfo_children():
            w.destroy()
        self._detail_tag_hand = hand
        if hand is None:
            return
        t = self.theme
        current_tags = set(self.db.get_tags(hand.hand_id))

        # Label
        tk.Label(frame, text="Tags:", bg=t["bg_panel"], fg=t["text_dim"],
                 font=_F_CAPTION).pack(side="left", padx=(0, 6))

        def _make_toggle(entry):
            label, key, color, _cat = entry
            active = key in current_tags

            bg_on  = color
            bg_off = _blend(color, t["bg_panel"], 0.82)
            fg_on  = "#111111"
            fg_off = _lighten(color, 0.25)
            border_col = _blend(color, t["bg_panel"], 0.4)

            btn_frame = tk.Frame(frame, bg=border_col, padx=1, pady=1)
            btn_frame.pack(side="left", padx=2, pady=2)
            btn = tk.Button(
                btn_frame,
                text=label,
                bg=bg_on if active else bg_off,
                fg=fg_on if active else fg_off,
                activebackground=bg_on,
                activeforeground=fg_on,
                font=(_FF, 9, "bold") if active else (_FF, 9, "normal"),
                relief="flat",
                bd=0,
                padx=7,
                pady=2,
                cursor="hand2",
            )
            btn.pack()

            def _toggle(k=key, b=btn, on=bg_on, off=bg_off, fon=fg_on, foff=fg_off):
                cur = set(self.db.get_tags(self._detail_tag_hand.hand_id))
                if k in cur:
                    self.db.remove_tag(self._detail_tag_hand.hand_id, k)
                    b.configure(bg=off, fg=foff, font=(_FF, 9, "normal"))
                else:
                    self.db.add_tag(self._detail_tag_hand.hand_id, k)
                    b.configure(bg=on, fg=fon, font=(_FF, 9, "bold"))
                self._refresh_tag_filter()

            btn.configure(command=_toggle)

        for entry in HAND_TAG_PRESETS:
            _make_toggle(entry)

        # Custom tag quick-entry
        tk.Frame(frame, bg=t["border"], width=1).pack(side="left", fill="y", padx=6, pady=2)
        custom_var = tk.StringVar()
        custom_entry = tk.Entry(frame, textvariable=custom_var, bg=t["bg_input"], fg=t["text"],
                                insertbackground=t["text"], relief="flat", bd=1,
                                font=(_FF, 9), width=10)
        custom_entry.pack(side="left", padx=2, pady=3)
        custom_entry.insert(0, "custom…")
        custom_entry.bind("<FocusIn>", lambda e: custom_entry.delete(0, "end") if custom_var.get() == "custom…" else None)

        def _add_custom():
            tag = custom_var.get().strip()
            if tag and tag != "custom…" and self._detail_tag_hand:
                self.db.add_tag(self._detail_tag_hand.hand_id, tag)
                self._refresh_tag_filter()
                self._refresh_detail_tag_strip(self._detail_tag_hand)
        custom_entry.bind("<Return>", lambda e: _add_custom())
        tk.Button(frame, text="+", bg=t["bg_accent"], fg=t["text"],
                  font=(_FF, 9, "bold"), relief="flat", bd=0, padx=6, pady=2,
                  cursor="hand2", command=_add_custom).pack(side="left", padx=1)

    def _build_leak_tab(self):
        tab = self.tab_leak
        top = self._panel(tab, pady=6)
        ctk.CTkLabel(top, text="Leak Analysis", text_color=self.theme["gold"], font=_F_HEADER).pack(pady=8)

        self.leak_stats_frame = self._panel(tab)
        self._leak_stat_cards = {}
        stat_names = ["VPIP", "PFR", "AF", "WTSD", "W$SD", "C-Bet"]
        for i, name in enumerate(stat_names):
            frame = ctk.CTkFrame(
                self.leak_stats_frame,
                fg_color=self.theme["bg_card"],
                corner_radius=8,
                width=140,
                height=70,
                border_width=1,
                border_color=self.theme["border"],
            )
            frame.grid(row=0, column=i, padx=4, pady=4, sticky="nsew")
            frame.grid_propagate(False)
            self.leak_stats_frame.grid_columnconfigure(i, weight=1)
            ctk.CTkLabel(
                frame, text=name, text_color=self.theme["text_dim"], font=_F_DATA_MD,
            ).pack(pady=(6, 0))
            val_lbl = ctk.CTkLabel(
                frame, text="—", text_color=self.theme["text"], font=("Consolas", 18, "bold"),
            )
            val_lbl.pack()
            self._leak_stat_cards[name] = val_lbl

        self.leak_alerts_frame = self._panel(tab)
        self._section_label(self.leak_alerts_frame, "Alerts").pack(anchor="w", padx=8, pady=4)
        leak_alerts_box, self.leak_alerts_text = self._create_scroll_textbox(
            self.leak_alerts_frame, height=120, font=(_FM, 12, "normal")
        )
        leak_alerts_box.pack(fill="x", padx=8, pady=(0, 8))

        pos_frame = self._panel(tab, fill="both", expand=True)
        self._section_label(pos_frame, "By Position").pack(anchor="w", padx=8, pady=4)
        leak_pos_box, self.leak_pos_text = self._create_scroll_textbox(
            pos_frame, font=(_FM, 12, "normal"), wrap="none"
        )
        leak_pos_box.pack(fill="both", expand=True, padx=8, pady=(0, 8))

        self.leak_graph_frame = self._panel(tab)
        self._section_label(self.leak_graph_frame, "Position VPIP / PFR").pack(anchor="w", padx=8, pady=4)
        self.leak_fig = Figure(figsize=(10, 3), dpi=80)
        self.leak_fig.patch.set_facecolor(self.theme["graph_bg"])
        self.leak_canvas = FigureCanvasTkAgg(self.leak_fig, master=self.leak_graph_frame)
        self.leak_canvas.get_tk_widget().pack(fill="x", padx=8, pady=(0, 8))

        site_frame = self._panel(tab)
        self._section_label(site_frame, "By Site").pack(anchor="w", padx=8, pady=4)
        leak_site_box, self.leak_site_text = self._create_scroll_textbox(
            site_frame, height=80, font=(_FM, 12, "normal")
        )
        leak_site_box.pack(fill="x", padx=8, pady=(0, 8))

    def _build_ocr_tab(self):
        tab = self.tab_ocr

        top_frame = self._panel(tab, pady=6)
        self._section_label(top_frame, "Table Screenshot").pack(anchor="w", padx=8, pady=4)

        btn_row = ctk.CTkFrame(top_frame, fg_color=self.theme["bg_panel"])
        btn_row.pack(fill="x", padx=8, pady=4)
        self._action_button(btn_row, "Browse", self._ocr_browse, tone="success", width=92, bold=True).pack(side="left", padx=4)
        self._action_button(btn_row, "Paste", self._ocr_paste, width=88).pack(side="left", padx=4)
        self._action_button(btn_row, "Analyze", self._ocr_analyze, tone="accent", width=92, bold=True).pack(side="left", padx=4)
        self._action_button(btn_row, "Copy", self._ocr_copy_analysis, width=84).pack(side="left", padx=4)
        self._action_button(btn_row, "Save OCR", self._ocr_save_to_db, width=96).pack(side="right", padx=4)

        capture_row = ctk.CTkFrame(top_frame, fg_color=self.theme["bg_panel"])
        capture_row.pack(fill="x", padx=8, pady=(0, 4))
        self._action_button(
            capture_row,
            "Capture Replay Window",
            lambda: self._ocr_capture_replay_window(analyze=True),
            tone="accent",
            width=170,
            bold=True,
        ).pack(side="left", padx=4)
        self._action_button(
            capture_row,
            "Paste + Analyze",
            lambda: self._ocr_load_clipboard_image(analyze=True),
            width=130,
        ).pack(side="left", padx=4)
        self._action_button(
            capture_row,
            "Start Bridge",
            self._start_ocr_capture_bridge,
            width=100,
        ).pack(side="left", padx=4)
        self._action_button(
            capture_row,
            "Stop Bridge",
            self._stop_ocr_capture_bridge,
            tone="neutral",
            width=100,
        ).pack(side="left", padx=4)

        self.ocr_capture_status_var = ctk.StringVar(value="Capture: ready")
        ctk.CTkLabel(
            top_frame,
            textvariable=self.ocr_capture_status_var,
            text_color=self.theme["text_dim"],
            font=_F_DATA,
        ).pack(anchor="w", padx=12, pady=(0, 2))

        self.ocr_bridge_status_var = ctk.StringVar(value="Bridge: starting...")
        ctk.CTkLabel(
            top_frame,
            textvariable=self.ocr_bridge_status_var,
            text_color=self.theme["text_dim"],
            font=_F_CAPTION,
        ).pack(anchor="w", padx=12, pady=(0, 2))

        self.ocr_hotkeys_status_var = ctk.StringVar(
            value="Hotkeys: Ctrl+Shift+R capture Replay window | Ctrl+Shift+V paste clipboard image"
        )
        ctk.CTkLabel(
            top_frame,
            textvariable=self.ocr_hotkeys_status_var,
            text_color=self.theme["text_dim"],
            font=_F_CAPTION,
        ).pack(anchor="w", padx=12, pady=(0, 4))

        self.ocr_file_var = ctk.StringVar(value="No image")
        ctk.CTkLabel(top_frame, textvariable=self.ocr_file_var, text_color=self.theme["text_dim"], font=_F_DATA).pack(anchor="w", padx=12, pady=(0, 4))

        content = self._panel(tab, fill="both", expand=True, pady=(0, 2))
        content.grid_columnconfigure(0, weight=1)
        content.grid_columnconfigure(1, weight=1)
        content.grid_rowconfigure(0, weight=1)

        left = ctk.CTkFrame(content, fg_color=self.theme["bg_input"], corner_radius=8)
        left.grid(row=0, column=0, sticky="nsew", padx=(4, 2), pady=4)
        ctk.CTkLabel(left, text="Preview", text_color=self.theme["text_dim"], font=_F_BODY).pack(anchor="w", padx=6, pady=2)

        self.ocr_preview_label = ctk.CTkLabel(
            left,
            text="No image\n\nPaste or browse to start",
            text_color=self.theme["text_dim"],
            font=(_FM, 12, "normal"),
            fg_color=self.theme["bg_input"],
        )
        self.ocr_preview_label.pack(fill="both", expand=True, padx=4, pady=4)
        self._ocr_photo = None

        right = ctk.CTkFrame(content, fg_color=self.theme["bg_input"], corner_radius=8)
        right.grid(row=0, column=1, sticky="nsew", padx=(2, 4), pady=4)
        ctk.CTkLabel(right, text="Result", text_color=self.theme["text_dim"], font=_F_BODY).pack(anchor="w", padx=6, pady=2)

        ocr_result_box, self.ocr_result_text = self._create_scroll_textbox(
            right, font=_F_DATA_MD, wrap="none"
        )
        ocr_result_box.pack(fill="both", expand=True, padx=4, pady=4)
        ocr_method = "Tesseract + Windows OCR fallback" if HAS_TESSERACT else "Windows built-in OCR"
        self.ocr_result_text.insert(
            "1.0",
            "Quick start\n\n"
            "1. Capture the table.\n"
            "2. Paste or browse the image.\n"
            "3. Analyze the hand.\n"
            "4. Copy the result or save it.\n\n"
            f"Engine: {ocr_method}\n"
            "Formats: PNG, JPG, BMP",
        )

        self._ocr_current_path = None
        self._ocr_current_elements = None
        self._ocr_current_raw_text = ""

        convert_frame = self._panel(tab, pady=(2, 2))
        ctk.CTkLabel(convert_frame, text="Save Hand", text_color=self.theme["gold"], font=_F_LABEL).grid(row=0, column=0, columnspan=6, sticky="w", padx=8, pady=4)

        ctk.CTkLabel(convert_frame, text="Hero", text_color=self.theme["text"], font=_F_BODY).grid(row=1, column=0, padx=4, pady=2, sticky="w")
        self.ocr_hero_cards_var = ctk.StringVar()
        ctk.CTkEntry(convert_frame, textvariable=self.ocr_hero_cards_var, fg_color=self.theme["bg_input"], text_color=self.theme["text"], width=100).grid(row=1, column=1, padx=4, pady=2, sticky="w")

        ctk.CTkLabel(convert_frame, text="Board", text_color=self.theme["text"], font=_F_BODY).grid(row=1, column=2, padx=4, pady=2, sticky="w")
        self.ocr_board_var = ctk.StringVar()
        ctk.CTkEntry(convert_frame, textvariable=self.ocr_board_var, fg_color=self.theme["bg_input"], text_color=self.theme["text"], width=140).grid(row=1, column=3, padx=4, pady=2, sticky="w")

        ctk.CTkLabel(convert_frame, text="Pot", text_color=self.theme["text"], font=_F_BODY).grid(row=1, column=4, padx=4, pady=2, sticky="w")
        self.ocr_pot_var = ctk.StringVar(value="0")
        ctk.CTkEntry(convert_frame, textvariable=self.ocr_pot_var, fg_color=self.theme["bg_input"], text_color=self.theme["text"], width=80).grid(row=1, column=5, padx=4, pady=2, sticky="w")

        ctk.CTkLabel(convert_frame, text="Pos", text_color=self.theme["text"], font=_F_BODY).grid(row=2, column=0, padx=4, pady=2, sticky="w")
        self.ocr_position_var = ctk.StringVar(value="BTN")
        ctk.CTkOptionMenu(
            convert_frame,
            variable=self.ocr_position_var,
            values=["BTN", "CO", "MP", "EP", "SB", "BB"],
            fg_color=self.theme["bg_accent"],
            button_color=self.theme["bg_hover"],
            text_color=self.theme["text"],
            width=80,
            dropdown_fg_color=self.theme["bg_card"],
            dropdown_hover_color=self.theme["bg_accent"],
        ).grid(row=2, column=1, padx=4, pady=2, sticky="w")

        ctk.CTkLabel(convert_frame, text="Net", text_color=self.theme["text"], font=_F_BODY).grid(row=2, column=2, padx=4, pady=2, sticky="w")
        self.ocr_result_var = ctk.StringVar(value="0")
        ctk.CTkEntry(convert_frame, textvariable=self.ocr_result_var, fg_color=self.theme["bg_input"], text_color=self.theme["text"], width=80).grid(row=2, column=3, padx=4, pady=2, sticky="w")

        ctk.CTkLabel(convert_frame, text="Site", text_color=self.theme["text"], font=_F_BODY).grid(row=2, column=4, padx=4, pady=2, sticky="w")
        self.ocr_site_var = ctk.StringVar(value="Manual")
        ctk.CTkOptionMenu(
            convert_frame,
            variable=self.ocr_site_var,
            values=["CoinPoker", "BetACR", "GGPoker", "ReplayPoker", "Manual"],
            fg_color=self.theme["bg_accent"],
            button_color=self.theme["bg_hover"],
            text_color=self.theme["text"],
            width=100,
            dropdown_fg_color=self.theme["bg_card"],
            dropdown_hover_color=self.theme["bg_accent"],
        ).grid(row=2, column=5, padx=4, pady=2, sticky="w")

        ctk.CTkLabel(convert_frame, text="Notes", text_color=self.theme["text"], font=_F_BODY).grid(row=3, column=0, padx=4, pady=2, sticky="w")
        self.ocr_notes_var = ctk.StringVar()
        ctk.CTkEntry(convert_frame, textvariable=self.ocr_notes_var, fg_color=self.theme["bg_input"], text_color=self.theme["text"], width=350).grid(row=3, column=1, columnspan=4, padx=4, pady=2, sticky="w")
        self._action_button(convert_frame, "Save Hand", self._ocr_save_as_hand, tone="success", width=98, bold=True).grid(row=3, column=5, padx=4, pady=2)

        history_frame = self._panel(tab, pady=(2, 4))
        ctk.CTkLabel(history_frame, text="Recent OCR", text_color=self.theme["gold"], font=_F_DATA_BOLD).pack(anchor="w", padx=8, pady=2)
        ocr_history_box, self.ocr_history_text = self._create_scroll_textbox(
            history_frame, height=70, font=_F_DATA, wrap="none"
        )
        ocr_history_box.pack(fill="x", padx=8, pady=(0, 4))
        self.ocr_history_text.configure(state="disabled")
        self._refresh_ocr_history()

    def _ocr_browse(self):
        filetypes = [("Image files", "*.png *.jpg *.jpeg *.bmp *.gif *.tiff"),
                     ("All files", "*.*")]
        path = filedialog.askopenfilename(title="Select poker table screenshot",
                                          filetypes=filetypes)
        if path:
            self._ocr_load_image(path)

    def _ocr_paste(self):
        """Grab image from clipboard and save to temp file."""
        self._ocr_load_clipboard_image(analyze=False, site=None)

    def _ocr_load_image(self, path):
        self._ocr_current_path = path
        self.ocr_file_var.set(os.path.basename(path))
        try:
            img = Image.open(path)
            if img.mode == "RGBA":
                bg = Image.new("RGB", img.size, (255, 255, 255))
                bg.paste(img, mask=img.split()[3])
                img = bg
            pw = self.ocr_preview_label.winfo_width() or 450
            ph = self.ocr_preview_label.winfo_height() or 350
            pw, ph = max(pw - 10, 200), max(ph - 10, 150)
            img.thumbnail((pw, ph), Image.LANCZOS)
            self._ocr_photo = ImageTk.PhotoImage(img)
            self.ocr_preview_label.configure(image=self._ocr_photo, text="")
            self._set_status(f"Image loaded: {os.path.basename(path)}")
        except Exception as e:
            self.ocr_preview_label.configure(image=None, text=f"Error loading image:\n{e}")
            self._set_status(f"Image load error: {e}")

    def _ocr_analyze(self):
        if not self._ocr_current_path:
            self._set_status("No image loaded — browse or paste first")
            return
        self._set_status("Running OCR analysis...")
        self.ocr_result_text.delete("1.0", "end")
        self.ocr_result_text.insert("1.0", "  Analyzing image...\n  Please wait...")
        threading.Thread(target=self._ocr_do_analyze, daemon=True).start()

    def _ocr_do_analyze(self):
        raw_text = self.ocr_engine.ocr_image(self._ocr_current_path)
        elements = self.ocr_engine.parse_poker_elements(raw_text)
        analysis = self.ocr_engine.format_analysis(elements)
        self.after(0, lambda: self._ocr_show_result(analysis, elements, raw_text))

    def _ocr_show_result(self, analysis, elements=None, raw_text=""):
        self.ocr_result_text.delete("1.0", "end")
        self.ocr_result_text.insert("1.0", analysis)
        self._set_status("OCR analysis complete!")
        if elements:
            self._ocr_current_elements = elements
            self._ocr_current_raw_text = raw_text
            # Pre-fill convert fields
            cards = elements.get("cards", [])
            if len(cards) >= 2:
                self.ocr_hero_cards_var.set(f"{cards[0]} {cards[1]}")
            if len(cards) >= 5:
                self.ocr_board_var.set(" ".join(cards[2:7]))
            elif elements.get("board"):
                self.ocr_board_var.set(" ".join(elements["board"]))
            if elements.get("pot"):
                self.ocr_pot_var.set(str(elements["pot"]))

    def _ocr_copy_analysis(self):
        text = self.ocr_result_text.get("1.0", "end").strip()
        if text:
            self.clipboard_clear()
            self.clipboard_append(text)
            self._set_status("Analysis copied to clipboard!")

    def _ocr_save_to_db(self):
        if not self._ocr_current_elements:
            self._set_status("Run OCR analysis first")
            return
        notes = self.ocr_notes_var.get().strip()
        self.db.save_ocr_import(
            self._ocr_current_path or "",
            self._ocr_current_raw_text,
            self._ocr_current_elements,
            notes=notes,
        )
        self._set_status("OCR import saved to database!")
        self._refresh_ocr_history()
        # Also show action count in status if we have parsed actions
        if self._ocr_current_elements and self._ocr_current_elements.get("actions"):
            actions = self._ocr_current_elements["actions"]
            self._set_status(f"OCR import saved! ({len(actions)} actions detected)")

    def _ocr_save_as_hand(self):
        hero_cards = self.ocr_hero_cards_var.get().strip()
        board_str = self.ocr_board_var.get().strip()
        try:
            pot = float(self.ocr_pot_var.get().strip())
        except (ValueError, TypeError):
            pot = 0.0
        position = self.ocr_position_var.get()
        try:
            result_val = float(self.ocr_result_var.get().strip())
        except (ValueError, TypeError):
            result_val = 0.0
        site = self.ocr_site_var.get()

        h = Hand()
        h.hand_id = f"OCR_{int(time.time() * 1000)}"
        h.site = site if site != "Manual" else "OCR"
        h.date = datetime.now()
        h.game_type = "NLHE"
        h.hero_cards = hero_cards
        h.board_cards = board_str.split() if board_str else []
        h.pot = pot
        h.hero_won = result_val
        h.hero_position = position
        notes = self.ocr_notes_var.get().strip()
        h.raw_text = notes if notes else f"OCR import from {self._ocr_current_path or 'clipboard'}"

        # Build streets from OCR-parsed actions
        if self._ocr_current_elements and self._ocr_current_elements.get("actions"):
            streets_map = OrderedDict()
            for act in self._ocr_current_elements["actions"]:
                sname = act.get("street", "preflop")
                if sname not in streets_map:
                    streets_map[sname] = {"name": sname, "cards": [], "actions": []}
                streets_map[sname]["actions"].append({
                    "player": act["player"],
                    "action": act["action"],
                    "amount": act.get("amount", 0),
                })
            h.streets = list(streets_map.values())

        # Build players from OCR
        if self._ocr_current_elements and self._ocr_current_elements.get("players_detected"):
            for i, pname in enumerate(self._ocr_current_elements["players_detected"]):
                h.players[i + 1] = {"name": pname, "stack": 0, "is_hero": False}

        # Save via db
        self.db.save_hand(h, source_file="OCR Import")

        # If there's a pending OCR import, link it
        ocr_imports = self.db.get_ocr_imports()
        if ocr_imports:
            latest = ocr_imports[0]
            if not latest.get("hand_id"):
                self.db.save_ocr_as_hand(latest["id"], h)

        self._set_status(f"Hand {h.hand_id} saved to database!")
        self._refresh_ocr_history()
        self._post_scan()

    def _refresh_ocr_history(self):
        try:
            imports = self.db.get_ocr_imports()[:10]
            self.ocr_history_text.configure(state="normal")
            self.ocr_history_text.delete("1.0", "end")
            if not imports:
                self.ocr_history_text.insert("1.0", "  No OCR imports yet")
            else:
                for imp in imports:
                    dt = imp.get("created_at", "?")
                    if len(dt) > 16:
                        dt = dt[:16]
                    cards = imp.get("parsed_cards", "")
                    pot = imp.get("parsed_pot", 0)
                    linked = " -> " + imp["hand_id"] if imp.get("hand_id") else ""
                    fname = os.path.basename(imp.get("image_path", "") or "clipboard")
                    self.ocr_history_text.insert("end",
                        f"  {dt}  {fname:20s}  cards: {cards:16s}  pot: {pot:>8.0f}{linked}\n")
            self.ocr_history_text.configure(state="disabled")
        except Exception:
            pass

    def _ocr_load_clipboard_image(self, analyze=False, site="ReplayPoker"):
        try:
            from PIL import ImageGrab

            img = ImageGrab.grabclipboard()
            if img is None or isinstance(img, list):
                self._set_status("No image found on clipboard")
                return False
            tmp = os.path.join(tempfile.gettempdir(), f"poker_ocr_clipboard_{int(time.time() * 1000)}.png")
            img.save(tmp, "PNG")
            self._ocr_process_image_capture(tmp, source="clipboard", site=site, analyze=analyze)
            return True
        except Exception as e:
            self._set_status(f"Clipboard error: {e}")
            return False

    def _ocr_capture_replay_window(self, analyze=True):
        try:
            capture = ReplayWindowCapture.capture_window(allow_foreground_fallback=True)
        except Exception as exc:
            self._set_status(f"Replay window capture failed: {exc}")
            return
        self._ocr_process_image_capture(
            capture["path"],
            source=f"window:{capture.get('title', 'Replay Poker')}",
            site="ReplayPoker",
            analyze=analyze,
        )

    def _ocr_process_image_capture(self, path, source="capture", site="ReplayPoker", analyze=True):
        self._ocr_load_image(path)
        if site and hasattr(self, "ocr_site_var"):
            self.ocr_site_var.set(site)
        if hasattr(self, "ocr_notes_var"):
            self.ocr_notes_var.set(f"Live capture from {source}")
        if hasattr(self, "ocr_capture_status_var"):
            self.ocr_capture_status_var.set(f"Capture: image loaded from {source}")
        self._set_status(f"OCR capture loaded from {source}")
        if analyze:
            self._ocr_analyze()

    def _ocr_process_text_capture(self, text, source="bridge-text", site="ReplayPoker"):
        if hasattr(self, "ocr_site_var"):
            self.ocr_site_var.set(site)
        if hasattr(self, "ocr_notes_var"):
            self.ocr_notes_var.set(f"Bridge text capture from {source}")
        self._ocr_current_path = None
        self._ocr_current_raw_text = text
        elements = self.ocr_engine.parse_poker_elements(text)
        analysis = self.ocr_engine.format_analysis(elements)
        self._ocr_show_result(analysis, elements, text)
        if hasattr(self, "ocr_capture_status_var"):
            self.ocr_capture_status_var.set(f"Capture: bridge text received from {source}")
        self._set_status(f"Bridge text capture processed from {source}")

    def _start_ocr_capture_bridge(self):
        started = self.capture_bridge.start()
        if hasattr(self, "ocr_bridge_status_var"):
            if started:
                self.ocr_bridge_status_var.set(
                    f"Bridge: {self.capture_bridge.capture_text_url} | {self.capture_bridge.capture_image_url}"
                )
            else:
                self.ocr_bridge_status_var.set("Bridge: failed to start")
        return started

    def _stop_ocr_capture_bridge(self):
        self.capture_bridge.stop()
        if hasattr(self, "ocr_bridge_status_var"):
            self.ocr_bridge_status_var.set("Bridge: stopped")
        self._set_status("OCR bridge stopped")

    def _schedule_ocr_bridge_poll(self):
        if self._capture_poll_job is not None:
            try:
                self.after_cancel(self._capture_poll_job)
            except Exception:
                pass
        self._capture_poll_job = self.after(800, self._poll_ocr_bridge)

    def _poll_ocr_bridge(self):
        payload = self.capture_bridge.get_capture()
        while payload:
            if payload.get("type") == "image":
                self._ocr_process_image_capture(
                    payload["path"],
                    source=payload.get("source", "bridge-image"),
                    site=payload.get("site", "ReplayPoker"),
                    analyze=True,
                )
            elif payload.get("type") == "text":
                self._ocr_process_text_capture(
                    payload.get("text", ""),
                    source=payload.get("source", "bridge-text"),
                    site=payload.get("site", "ReplayPoker"),
                )
            payload = self.capture_bridge.get_capture()
        self._schedule_ocr_bridge_poll()

    def _start_ocr_hotkeys(self):
        if not HAS_WIN32:
            if hasattr(self, "ocr_hotkeys_status_var"):
                self.ocr_hotkeys_status_var.set("Hotkeys: unavailable (pywin32 missing)")
            return

        def _listen():
            try:
                import ctypes
                import ctypes.wintypes

                WM_HOTKEY = 0x0312
                MOD_CONTROL = 0x0002
                MOD_SHIFT = 0x0004
                registrations = {
                    2101: (MOD_CONTROL | MOD_SHIFT, 0x52, lambda: self._ocr_capture_replay_window(analyze=True)),
                    2102: (MOD_CONTROL | MOD_SHIFT, 0x56, lambda: self._ocr_load_clipboard_image(analyze=True)),
                }
                registered = {}
                for hotkey_id, (mods, vk, callback) in registrations.items():
                    if ctypes.windll.user32.RegisterHotKey(None, hotkey_id, mods, vk):
                        registered[hotkey_id] = callback

                if hasattr(self, "ocr_hotkeys_status_var"):
                    if registered:
                        self.after(
                            0,
                            lambda: self.ocr_hotkeys_status_var.set(
                                "Hotkeys: Ctrl+Shift+R capture Replay window | Ctrl+Shift+V paste clipboard image"
                            ),
                        )
                    else:
                        self.after(
                            0,
                            lambda: self.ocr_hotkeys_status_var.set("Hotkeys: unavailable (registration failed)"),
                        )

                msg = ctypes.wintypes.MSG()
                while registered:
                    ret = ctypes.windll.user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
                    if ret <= 0:
                        break
                    if msg.message == WM_HOTKEY and msg.wParam in registered:
                        self.after(0, registered[msg.wParam])
                    ctypes.windll.user32.TranslateMessage(ctypes.byref(msg))
                    ctypes.windll.user32.DispatchMessageW(ctypes.byref(msg))
            except Exception:
                if hasattr(self, "ocr_hotkeys_status_var"):
                    self.after(0, lambda: self.ocr_hotkeys_status_var.set("Hotkeys: unavailable"))

        self._capture_hotkey_thread = threading.Thread(target=_listen, daemon=True)
        self._capture_hotkey_thread.start()

    def _on_app_close(self):
        try:
            if self._capture_poll_job is not None:
                self.after_cancel(self._capture_poll_job)
        except Exception:
            pass
        try:
            self.capture_bridge.stop()
        except Exception:
            pass
        self.destroy()

    def _on_hud_escape(self, event=None):
        overlays = list(getattr(self, "_hud_overlays", {}).values())
        if self._hud_layout_mode or any(getattr(o, "_layout_mode", False) for o in overlays):
            self._hud_layout_mode = False
            for overlay in overlays:
                try:
                    overlay.set_layout_mode(False)
                except Exception:
                    pass
            return "break"
        self._on_hud_only_close()
        return "break"

    def _start_quit_hotkey_listener(self):
        import threading

        def _listen():
            try:
                import ctypes
                import ctypes.wintypes
                MOD_CONTROL = 0x0002
                MOD_SHIFT = 0x0004
                VK_Q = 0x51
                WM_HOTKEY = 0x0312
                ctypes.windll.user32.RegisterHotKey(
                    None, self._quit_hotkey_id, MOD_CONTROL | MOD_SHIFT, VK_Q,
                )
                msg = ctypes.wintypes.MSG()
                while getattr(self, "_live_hud_on", True):
                    ret = ctypes.windll.user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
                    if ret <= 0:
                        break
                    if msg.message == WM_HOTKEY and msg.wParam == self._quit_hotkey_id:
                        self.after(0, self._on_hud_only_close)
                    ctypes.windll.user32.TranslateMessage(ctypes.byref(msg))
                    ctypes.windll.user32.DispatchMessageW(ctypes.byref(msg))
            except Exception:
                pass
            finally:
                try:
                    import ctypes
                    ctypes.windll.user32.UnregisterHotKey(None, self._quit_hotkey_id)
                except Exception:
                    pass

        self._quit_hotkey_thread = threading.Thread(target=_listen, daemon=True)
        self._quit_hotkey_thread.start()

    def _request_hud_quit(self):
        if self._hud_only:
            self._on_hud_only_close()

    def _on_hud_only_close(self):
        if getattr(self, "_hud_closing", False):
            return
        self._hud_closing = True
        self._stop_live_hud()
        try:
            self.importer.stop_watcher()
        except Exception:
            pass
        job = getattr(self, "_hud_cache_refresh_job", None)
        if job is not None:
            try:
                self.after_cancel(job)
            except Exception:
                pass
        _remove_hud_pid()
        try:
            self.quit()
        except Exception:
            pass
        self.destroy()

    def _build_ai_tab(self):
        tab = self.tab_ai
        t = self.theme

        # ── AI Engine status bar ──
        ai_status_bar = ctk.CTkFrame(tab, fg_color=t["bg_card"], corner_radius=8,
                                      border_width=1, border_color=t["border"])
        ai_status_bar.pack(fill="x", padx=6, pady=(6, 2))
        self.ai_engine_status = ctk.CTkLabel(
            ai_status_bar,
            text="⬡ AI Engine: checking…",
            text_color=t["text_dim"], font=_F_CAPTION)
        self.ai_engine_status.pack(side="left", padx=8, pady=4)

        # Provider selector
        self.ai_provider_var = ctk.StringVar(value="ollama")
        ctk.CTkLabel(ai_status_bar, text="Provider:", text_color=t["text_dim"],
                     font=_F_CAPTION).pack(side="right", padx=(8, 2), pady=4)
        ctk.CTkOptionMenu(
            ai_status_bar, variable=self.ai_provider_var,
            values=["ollama", "openai", "grok"],
            fg_color=t["bg_accent"], button_color=t["bg_hover"],
            text_color=t["text"], width=100, height=24,
            dropdown_fg_color=t["bg_card"], dropdown_hover_color=t["bg_accent"],
            font=_F_CAPTION,
        ).pack(side="right", padx=(0, 8), pady=4)

        # ── Source selector ──
        src_frame = ctk.CTkFrame(tab, fg_color=t["bg_panel"],
                                  border_width=1, border_color=t["border"])
        src_frame.pack(fill="x", padx=6, pady=2)

        ctk.CTkLabel(src_frame, text="Analyze:", text_color=t["text"],
                     font=_F_BODY).pack(side="left", padx=(8, 4), pady=6)
        self.ai_source_var = ctk.StringVar(value="Filtered Hands")
        ctk.CTkOptionMenu(src_frame, variable=self.ai_source_var,
                          values=["All Hands", "Filtered Hands", "Selected Hands"],
                          fg_color=t["bg_accent"], button_color=t["bg_hover"],
                          text_color=t["text"], width=160,
                          dropdown_fg_color=t["bg_card"],
                          dropdown_hover_color=t["bg_accent"],
                          font=_F_BODY).pack(side="left", padx=4, pady=6)

        self.ai_filter_label = ctk.CTkLabel(src_frame, text="Filters: All Hands (no filters)",
                                             text_color=t["text_dim"],
                                             font=_F_CAPTION_I)
        self.ai_filter_label.pack(side="left", padx=12, pady=6)

        # ── Action buttons — two groups ──
        btn_frame = ctk.CTkFrame(tab, fg_color=t["bg_panel"],
                                  border_width=1, border_color=t["border"])
        btn_frame.pack(fill="x", padx=6, pady=2)

        # Primary actions (left)
        self._action_button(btn_frame, "\U0001f4ca Generate Analysis",
                            self._generate_summary, tone="success", width=170,
                            bold=True).pack(side="left", padx=(8, 4), pady=6)
        self._action_button(btn_frame, "\U0001f9e0 AI Analyze Hand",
                            self._ai_analyze_selected, tone="accent", width=160,
                            bold=True).pack(side="left", padx=4, pady=6)
        self._action_button(btn_frame, "\U0001f50d Find Similar",
                            self._ai_find_similar, width=130).pack(side="left", padx=4, pady=6)

        # Separator
        tk.Frame(btn_frame, bg=t["border"], width=1).pack(
            side="left", fill="y", padx=6, pady=8)

        # Secondary actions
        self._action_button(btn_frame, "Copy", self._copy_summary,
                            width=70).pack(side="left", padx=2, pady=6)
        self._action_button(btn_frame, "\U0001f4be Save", self._save_summary_as,
                            width=78).pack(side="left", padx=2, pady=6)

        # GTO export (right)
        self._action_button(btn_frame, "Export GTO Wizard",
                            self._export_gto_wizard, tone="accent", width=150,
                            bold=True).pack(side="right", padx=8, pady=6)

        # ── Output area (analysis results, top 60%) ──
        ai_text_box, self.ai_text = self._create_scroll_textbox(
            tab, font=_F_DATA_MD, wrap="none"
        )
        ai_text_box.pack(fill="both", expand=True, padx=6, pady=(4, 2))

        # ── Chat panel ──────────────────────────────────────────────────
        chat_frame = ctk.CTkFrame(tab, fg_color=t["bg_panel"],
                                   border_width=1, border_color=t["border"],
                                   corner_radius=8)
        chat_frame.pack(fill="x", padx=6, pady=(2, 2))

        ctk.CTkLabel(chat_frame, text="🤖 Ask the Coach",
                     text_color=t["gold"], font=_F_LABEL
                     ).pack(anchor="w", padx=8, pady=(4, 2))

        chat_box_frame, self.ai_chat_display = self._create_scroll_textbox(
            chat_frame, height=120, font=_F_DATA, wrap="word"
        )
        chat_box_frame.pack(fill="x", padx=6, pady=(0, 4))

        chat_input_row = ctk.CTkFrame(chat_frame, fg_color="transparent")
        chat_input_row.pack(fill="x", padx=6, pady=(0, 6))

        self.ai_chat_var = tk.StringVar()
        self._ai_chat_entry = ctk.CTkEntry(
            chat_input_row, textvariable=self.ai_chat_var,
            placeholder_text="Ask a poker strategy question…",
            fg_color=t["bg_input"], text_color=t["text"],
            font=_F_BODY, height=32,
        )
        self._ai_chat_entry.pack(side="left", fill="x", expand=True, padx=(0, 6))
        self._ai_chat_entry.bind("<Return>", lambda e: self._send_chat())

        ctk.CTkButton(
            chat_input_row, text="Send", width=70, height=32,
            fg_color=t["bg_accent"], hover_color=t["bg_hover"],
            text_color=t["gold"], font=_F_DATA_BOLD,
            command=self._send_chat,
        ).pack(side="left")

        ctk.CTkButton(
            chat_input_row, text="Clear", width=60, height=32,
            fg_color=t["bg_card"], hover_color=t["bg_hover"],
            text_color=t["text_dim"], font=_F_DATA,
            command=self._clear_chat,
        ).pack(side="left", padx=(4, 0))

        # ── Footer ──────────────────────────────────────────────────────
        ai_footer = ctk.CTkFrame(tab, fg_color=t["bg_card"], corner_radius=6,
                                  height=28)
        ai_footer.pack(fill="x", padx=6, pady=(0, 6))
        ai_footer.pack_propagate(False)
        self.ai_footer_label = ctk.CTkLabel(
            ai_footer, text="Ready", text_color=t["text_dim"],
            font=_F_CAPTION, anchor="w")
        self.ai_footer_label.pack(side="left", padx=8)
        self.ai_vector_label = ctk.CTkLabel(
            ai_footer, text="", text_color=t["text_dim"],
            font=_F_CAPTION, anchor="e")
        self.ai_vector_label.pack(side="right", padx=8)

        # Update AI status after build
        self.after(500, self._update_ai_status)

    def _build_settings_tab(self):
        tab = self.tab_settings
        t = self.theme

        # ── API Keys ──────────────────────────────────────────────────────────
        api_frame = ctk.CTkFrame(tab, fg_color=t["bg_panel"],
                                  corner_radius=10,
                                  border_width=1, border_color=t["border"])
        api_frame.pack(fill="x", padx=6, pady=6)
        ctk.CTkLabel(api_frame, text="🔑 AI API Keys", text_color=t["gold"],
                     font=_F_TITLE).pack(anchor="w", padx=8, pady=4)

        oai_row = ctk.CTkFrame(api_frame, fg_color=t["bg_panel"])
        oai_row.pack(fill="x", padx=8, pady=2)
        ctk.CTkLabel(oai_row, text="OpenAI Key:", text_color=t["text"],
                     width=110).pack(side="left")
        self.openai_key_var = ctk.StringVar(
            value=self.settings.get("openai_api_key", ""))
        oai_entry = ctk.CTkEntry(
            oai_row, textvariable=self.openai_key_var,
            fg_color=t["bg_input"], text_color=t["text"],
            show="*", width=340, placeholder_text="sk-...")
        oai_entry.pack(side="left", padx=4)
        self._action_button(
            oai_row, "Show/Hide",
            lambda: oai_entry.configure(show="" if oai_entry.cget("show") == "*" else "*"),
            tone="neutral", width=80, height=28,
        ).pack(side="left", padx=4)
        ctk.CTkLabel(oai_row, text="gpt-4o-mini / gpt-4o",
                     text_color=t["text_dim"],
                     font=_F_CAPTION).pack(side="left", padx=8)

        ant_row = ctk.CTkFrame(api_frame, fg_color=t["bg_panel"])
        ant_row.pack(fill="x", padx=8, pady=(2, 6))
        ctk.CTkLabel(ant_row, text="Anthropic Key:", text_color=t["text"],
                     width=110).pack(side="left")
        self.anthropic_key_var = ctk.StringVar(
            value=self.settings.get("anthropic_api_key", ""))
        ctk.CTkEntry(ant_row, textvariable=self.anthropic_key_var,
                     fg_color=t["bg_input"], text_color=t["text"],
                     show="*", width=340,
                     placeholder_text="sk-ant-... (optional)").pack(side="left", padx=4)
        ctk.CTkLabel(ant_row, text="Claude fallback (optional)",
                     text_color=t["text_dim"],
                     font=_F_CAPTION).pack(side="left", padx=8)

        hero_frame = ctk.CTkFrame(tab, fg_color=self.theme["bg_panel"],
                                   corner_radius=10,
                                   border_width=1, border_color=self.theme["border"])
        hero_frame.pack(fill="x", padx=6, pady=6)
        ctk.CTkLabel(hero_frame, text="Hero Names", text_color=self.theme["gold"],
                     font=_F_TITLE).pack(anchor="w", padx=8, pady=4)

        row1 = ctk.CTkFrame(hero_frame, fg_color=self.theme["bg_panel"])
        row1.pack(fill="x", padx=8, pady=2)
        ctk.CTkLabel(row1, text="CoinPoker:", text_color=self.theme["text"], width=100).pack(side="left")
        self.hero_cp_var = ctk.StringVar(value=self.settings["hero_names"].get("CoinPoker", ""))
        ctk.CTkEntry(row1, textvariable=self.hero_cp_var, fg_color=self.theme["bg_input"],
                     text_color=self.theme["text"], width=200).pack(side="left", padx=4)

        row2 = ctk.CTkFrame(hero_frame, fg_color=self.theme["bg_panel"])
        row2.pack(fill="x", padx=8, pady=2)
        ctk.CTkLabel(row2, text="BetACR:", text_color=self.theme["text"], width=100).pack(side="left")
        self.hero_bacr_var = ctk.StringVar(value=self.settings["hero_names"].get("BetACR", ""))
        ctk.CTkEntry(row2, textvariable=self.hero_bacr_var, fg_color=self.theme["bg_input"],
                     text_color=self.theme["text"], width=200).pack(side="left", padx=4)
        ctk.CTkLabel(row2, text="(WPN/ACR skin)", text_color=self.theme["text_dim"],
                     font=_F_DATA).pack(side="left", padx=8)

        row3 = ctk.CTkFrame(hero_frame, fg_color=self.theme["bg_panel"])
        row3.pack(fill="x", padx=8, pady=2)
        ctk.CTkLabel(row3, text="GGPoker:", text_color=self.theme["text"], width=100).pack(side="left")
        self.hero_gg_var = ctk.StringVar(value=self.settings["hero_names"].get("GGPoker", ""))
        ctk.CTkEntry(row3, textvariable=self.hero_gg_var, fg_color=self.theme["bg_input"],
                     text_color=self.theme["text"], width=200).pack(side="left", padx=4)

        row4 = ctk.CTkFrame(hero_frame, fg_color=self.theme["bg_panel"])
        row4.pack(fill="x", padx=8, pady=2)
        ctk.CTkLabel(row4, text="ReplayPoker:", text_color=self.theme["text"], width=100).pack(side="left")
        self.hero_rp_var = ctk.StringVar(value=self.settings["hero_names"].get("ReplayPoker", ""))
        ctk.CTkEntry(row4, textvariable=self.hero_rp_var, fg_color=self.theme["bg_input"],
                     text_color=self.theme["text"], width=200).pack(side="left", padx=4)
        ctk.CTkLabel(row4, text="(casino.org)", text_color=self.theme["text_dim"],
                     font=_F_DATA).pack(side="left", padx=8)

        dir_frame = ctk.CTkFrame(tab, fg_color=self.theme["bg_panel"],
                                  corner_radius=10,
                                  border_width=1, border_color=self.theme["border"])
        dir_frame.pack(fill="both", expand=True, padx=6, pady=4)
        ctk.CTkLabel(dir_frame, text="Scan Directories", text_color=self.theme["gold"],
                     font=_F_TITLE).pack(anchor="w", padx=8, pady=4)

        dir_list_box, self.dir_listbox = self._create_scroll_textbox(
            dir_frame, height=120, font=_F_DATA_MD, wrap="none"
        )
        dir_list_box.pack(fill="both", expand=True, padx=8, pady=4)
        self._refresh_dir_list()

        dir_btn_row = ctk.CTkFrame(dir_frame, fg_color=self.theme["bg_panel"])
        dir_btn_row.pack(fill="x", padx=8, pady=4)
        ctk.CTkLabel(dir_btn_row, text="Path:", text_color=self.theme["text"]).pack(side="left")
        self.new_dir_var = ctk.StringVar()
        ctk.CTkEntry(dir_btn_row, textvariable=self.new_dir_var, fg_color=self.theme["bg_input"],
                     text_color=self.theme["text"], width=280).pack(side="left", padx=4)
        self._action_button(dir_btn_row, "Browse...", self._browse_dir,
                            tone="neutral", width=80).pack(side="left", padx=2)
        ctk.CTkLabel(dir_btn_row, text="Site:", text_color=self.theme["text"]).pack(side="left", padx=(8, 0))
        self.new_dir_site_var = ctk.StringVar(value="CoinPoker")
        ctk.CTkOptionMenu(dir_btn_row, variable=self.new_dir_site_var,
                          values=["CoinPoker", "BetACR", "GGPoker", "ReplayPoker"], fg_color=self.theme["bg_accent"],
                          button_color=self.theme["bg_hover"], text_color=self.theme["text"],
                          dropdown_fg_color=self.theme["bg_card"],
                          dropdown_hover_color=self.theme["bg_accent"]).pack(side="left", padx=4)
        self._action_button(dir_btn_row, "Add", self._add_dir,
                            tone="success", width=60).pack(side="left", padx=4)
        self._action_button(dir_btn_row, "Remove Last", self._remove_dir,
                            tone="danger", width=100).pack(side="left", padx=4)

        opts_frame = ctk.CTkFrame(tab, fg_color=self.theme["bg_panel"],
                                   corner_radius=10,
                                   border_width=1, border_color=self.theme["border"])
        opts_frame.pack(fill="x", padx=6, pady=4)
        self.auto_refresh_var = ctk.BooleanVar(value=self.settings.get("auto_refresh", True))
        ctk.CTkCheckBox(opts_frame, text="Auto-refresh", variable=self.auto_refresh_var,
                        text_color=self.theme["text"], fg_color=self.theme["bg_accent"],
                        hover_color=self.theme["green"]).pack(side="left", padx=8, pady=6)

        ctk.CTkLabel(opts_frame, text="Interval (s):", text_color=self.theme["text"]).pack(side="left", padx=4)
        self.interval_var = ctk.StringVar(value=str(self.settings.get("refresh_interval", 5)))
        ctk.CTkEntry(opts_frame, textvariable=self.interval_var, fg_color=self.theme["bg_input"],
                     text_color=self.theme["text"], width=60).pack(side="left", padx=4)

        self._action_button(opts_frame, "Save Settings", self._save_settings,
                            tone="success", width=130, bold=True).pack(side="right", padx=8, pady=6)

        # ── Appearance / Theme Section ────────────────────────────────────
        theme_frame = ctk.CTkFrame(tab, fg_color=self.theme["bg_panel"],
                                    corner_radius=10,
                                    border_width=1, border_color=self.theme["border"])
        theme_frame.pack(fill="x", padx=6, pady=4)

        ctk.CTkLabel(theme_frame, text="Appearance", text_color=self.theme["gold"],
                     font=_F_TITLE).pack(anchor="w", padx=8, pady=4)

        theme_row = ctk.CTkFrame(theme_frame, fg_color=self.theme["bg_panel"])
        theme_row.pack(fill="x", padx=8, pady=4)

        ctk.CTkLabel(theme_row, text="Theme:", text_color=self.theme["text"],
                     width=100).pack(side="left")
        self.settings_theme_var = ctk.StringVar(value=self.theme_name)
        ctk.CTkOptionMenu(theme_row, variable=self.settings_theme_var,
                          values=list(THEMES.keys()),
                          fg_color=self.theme["bg_accent"], button_color=self.theme["bg_hover"],
                          text_color=self.theme["text"], width=160,
                          dropdown_fg_color=self.theme["bg_card"],
                          dropdown_hover_color=self.theme["bg_accent"],
                          command=self._change_theme).pack(side="left", padx=4)

        adv_row = ctk.CTkFrame(theme_frame, fg_color=self.theme["bg_panel"])
        adv_row.pack(fill="x", padx=8, pady=(0, 6))

        self.settings_adv_var = ctk.BooleanVar(value=self.advanced_mode)
        ctk.CTkCheckBox(adv_row, text="Advanced Mode (show extra stats & EV columns)",
                        variable=self.settings_adv_var,
                        text_color=self.theme["text"], fg_color=self.theme["bg_accent"],
                        hover_color=self.theme["green"],
                        command=lambda: self._toggle_advanced_from_settings()).pack(side="left", padx=4)

        # ── Live HUD Settings ─────────────────────────────────────────────
        hud_frame = ctk.CTkFrame(tab, fg_color=self.theme["bg_panel"],
                                  corner_radius=10,
                                  border_width=1, border_color=self.theme["border"])
        hud_frame.pack(fill="x", padx=6, pady=6)
        ctk.CTkLabel(hud_frame, text="⬡ Live HUD Overlay", text_color=self.theme["gold"],
                     font=_F_TITLE).pack(anchor="w", padx=8, pady=4)

        hud_row1 = ctk.CTkFrame(hud_frame, fg_color=self.theme["bg_panel"])
        hud_row1.pack(fill="x", padx=8, pady=2)
        self.hud_enabled_var = ctk.BooleanVar(value=self.settings.get("live_hud_enabled", False))
        ctk.CTkCheckBox(hud_row1, text="Enable Live HUD on startup",
                        variable=self.hud_enabled_var,
                        text_color=self.theme["text"], fg_color=self.theme["bg_accent"],
                        hover_color=self.theme["green"]).pack(side="left", padx=4)

        hud_row2 = ctk.CTkFrame(hud_frame, fg_color=self.theme["bg_panel"])
        hud_row2.pack(fill="x", padx=8, pady=2)
        ctk.CTkLabel(hud_row2, text="Opacity:", text_color=self.theme["text"], width=80).pack(side="left")
        self.hud_opacity_var = ctk.DoubleVar(value=self.settings.get("hud_opacity", 0.9))
        ctk.CTkSlider(hud_row2, from_=0.3, to=1.0, variable=self.hud_opacity_var,
                      fg_color=self.theme["border"], progress_color=self.theme["bg_accent"],
                      button_color=self.theme["gold"], width=200).pack(side="left", padx=8)
        self.hud_opacity_label_var = ctk.StringVar(value=f"{self.hud_opacity_var.get():.2f}")
        self.hud_opacity_var.trace_add(
            "write",
            lambda *_: self.hud_opacity_label_var.set(f"{self.hud_opacity_var.get():.2f}"),
        )
        ctk.CTkLabel(hud_row2, textvariable=self.hud_opacity_label_var, text_color=self.theme["text_dim"],
                     font=_F_DATA, width=40).pack(side="left")

        hud_row3 = ctk.CTkFrame(hud_frame, fg_color=self.theme["bg_panel"])
        hud_row3.pack(fill="x", padx=8, pady=2)
        ctk.CTkLabel(hud_row3, text="Seat Layout:", text_color=self.theme["text"], width=80).pack(side="left")
        self.hud_layout_var = ctk.StringVar(value=self.settings.get("hud_seat_layout", "auto"))
        ctk.CTkOptionMenu(hud_row3, variable=self.hud_layout_var,
                          values=["auto", "2max", "6max", "9max"],
                          fg_color=self.theme["bg_accent"], button_color=self.theme["bg_accent"],
                          button_hover_color=self.theme["bg_hover"],
                          text_color=self.theme["text"], width=120).pack(side="left", padx=4)

        hud_row4 = ctk.CTkFrame(hud_frame, fg_color=self.theme["bg_panel"])
        hud_row4.pack(fill="x", padx=8, pady=2)
        ctk.CTkLabel(hud_row4, text="Density:", text_color=self.theme["text"], width=80).pack(side="left")
        self.hud_density_var = ctk.StringVar(value=self.settings.get("hud_density", "standard"))
        ctk.CTkOptionMenu(hud_row4, variable=self.hud_density_var,
                          values=list(HUD_DENSITY_OPTIONS),
                          fg_color=self.theme["bg_accent"], button_color=self.theme["bg_accent"],
                          button_hover_color=self.theme["bg_hover"],
                          text_color=self.theme["text"], width=120).pack(side="left", padx=4)

        ctk.CTkLabel(hud_row4, text="Site Preset:", text_color=self.theme["text"], width=90).pack(side="left", padx=(14, 0))
        self.hud_site_preset_var = ctk.StringVar(value=self.settings.get("hud_site_preset", "auto"))
        ctk.CTkOptionMenu(hud_row4, variable=self.hud_site_preset_var,
                          values=list(HUD_SITE_PRESET_OPTIONS),
                          fg_color=self.theme["bg_accent"], button_color=self.theme["bg_accent"],
                          button_hover_color=self.theme["bg_hover"],
                          text_color=self.theme["text"], width=130).pack(side="left", padx=4)

        hud_row5 = ctk.CTkFrame(hud_frame, fg_color=self.theme["bg_panel"])
        hud_row5.pack(fill="x", padx=8, pady=2)
        ctk.CTkLabel(hud_row5, text="Anchor:", text_color=self.theme["text"], width=80).pack(side="left")
        self.hud_anchor_var = ctk.StringVar(value=self.settings.get("hud_anchor", "top-left"))
        ctk.CTkOptionMenu(hud_row5, variable=self.hud_anchor_var,
                          values=list(HUD_ANCHOR_OPTIONS),
                          fg_color=self.theme["bg_accent"], button_color=self.theme["bg_accent"],
                          button_hover_color=self.theme["bg_hover"],
                          text_color=self.theme["text"], width=120).pack(side="left", padx=4)

        ctk.CTkLabel(hud_row5, text="Offset X:", text_color=self.theme["text"], width=70).pack(side="left", padx=(14, 0))
        self.hud_offset_x_var = ctk.StringVar(value=str(self.settings.get("hud_offset_x", 0)))
        ctk.CTkEntry(hud_row5, textvariable=self.hud_offset_x_var, fg_color=self.theme["bg_input"],
                     text_color=self.theme["text"], width=60).pack(side="left", padx=4)
        ctk.CTkLabel(hud_row5, text="Offset Y:", text_color=self.theme["text"], width=70).pack(side="left", padx=(10, 0))
        self.hud_offset_y_var = ctk.StringVar(value=str(self.settings.get("hud_offset_y", 0)))
        ctk.CTkEntry(hud_row5, textvariable=self.hud_offset_y_var, fg_color=self.theme["bg_input"],
                     text_color=self.theme["text"], width=60).pack(side="left", padx=4)

        hud_row6 = ctk.CTkFrame(hud_frame, fg_color=self.theme["bg_panel"])
        hud_row6.pack(fill="x", padx=8, pady=2)
        ctk.CTkLabel(hud_row6, text="Badge scale:", text_color=self.theme["text"], width=80).pack(side="left")
        self.hud_badge_scale_var = ctk.StringVar(value=str(self.settings.get("hud_badge_scale", 1.5)))
        ctk.CTkEntry(hud_row6, textvariable=self.hud_badge_scale_var, fg_color=self.theme["bg_input"],
                     text_color=self.theme["text"], width=60).pack(side="left", padx=4)
        ctk.CTkLabel(hud_row6, text="Edge margin %:", text_color=self.theme["text"], width=100).pack(side="left", padx=(14, 0))
        self.hud_edge_margin_var = ctk.StringVar(value=str(int(float(self.settings.get("hud_edge_margin_pct", 0.12)) * 100)))
        ctk.CTkEntry(hud_row6, textvariable=self.hud_edge_margin_var, fg_color=self.theme["bg_input"],
                     text_color=self.theme["text"], width=60).pack(side="left", padx=4)

        ctk.CTkLabel(
            hud_frame,
            text="Badges avoid left/right screen edges for BetACR action buttons and side info panels. "
                 "Badge scale 1.5 = 50% larger; edge margin % keeps badges inset horizontally.",
            text_color=self.theme["text_dim"],
            font=_F_DATA,
        ).pack(anchor="w", padx=10, pady=(2, 4))

        ctk.CTkLabel(
            hud_frame,
            text="Site preset can reposition the summary card and shift all badges by poker client. Manual offset applies on top.",
            text_color=self.theme["text_dim"],
            font=_F_DATA,
        ).pack(anchor="w", padx=10, pady=(2, 4))

        hud_profile_row = ctk.CTkFrame(hud_frame, fg_color=self.theme["bg_panel"])
        hud_profile_row.pack(fill="x", padx=8, pady=(0, 4))
        ctk.CTkLabel(hud_profile_row, text="Profile Site:", text_color=self.theme["text"], width=80).pack(side="left")
        self.hud_profile_site_var = ctk.StringVar(value="CoinPoker")
        ctk.CTkOptionMenu(hud_profile_row, variable=self.hud_profile_site_var,
                          values=list(HUD_PROFILE_SITES),
                          fg_color=self.theme["bg_accent"], button_color=self.theme["bg_accent"],
                          button_hover_color=self.theme["bg_hover"],
                          text_color=self.theme["text"], width=120).pack(side="left", padx=4)
        self._action_button(hud_profile_row, "Save Profile", self._save_hud_profile_target,
                            tone="success", width=110).pack(side="left", padx=4)
        self._action_button(hud_profile_row, "Load Profile", self._load_hud_profile_target,
                            width=110).pack(side="left", padx=4)
        self._action_button(hud_profile_row, "Reset Badge Nudges", self._clear_hud_badge_offsets_target,
                    tone="neutral", width=150).pack(side="left", padx=4)
        self._action_button(hud_profile_row, "Clear Profile", self._clear_hud_profile_target,
                            tone="danger", width=110).pack(side="left", padx=4)

        self.hud_profile_status = ctk.CTkLabel(
            hud_frame,
            text="No site profile loaded",
            text_color=self.theme["text_dim"],
            font=_F_DATA,
        )
        self.hud_profile_status.pack(anchor="w", padx=10, pady=(0, 4))

        if not HAS_WIN32:
            ctk.CTkLabel(hud_frame, text="⚠ pywin32 not installed — run: pip install pywin32",
                         text_color=self.theme["red"], font=_F_DATA).pack(anchor="w", padx=8, pady=(0, 4))

    def _build_status_bar(self):
        self.taskbar = ctk.CTkFrame(self, fg_color=self.theme["bg_panel"], height=34, corner_radius=0)
        self.taskbar.pack(fill="x", side="bottom", padx=0, pady=0)
        self.taskbar.pack_propagate(False)
        # Single top accent line — replaces the multi-color glow
        tk.Frame(self.taskbar, bg=_blend(self.theme["border_hl"], self.theme["gold"], 0.28),
                 height=1).pack(fill="x", side="top")

        self._action_button(self.taskbar, "Import", self._manual_import, width=80, bold=True).pack(side="left", padx=(8, 3), pady=3)
        self._action_button(self.taskbar, "Reload", self._manual_refresh, width=72).pack(side="left", padx=3, pady=3)

        self.live_hud_btn = self._action_button(
            self.taskbar, "⬡ Live HUD", self._toggle_live_hud, tone="neutral", width=90
        )
        self.live_hud_btn.pack(side="left", padx=3, pady=3)

        self.hud_layout_btn = self._action_button(
            self.taskbar, "Unlock HUD", self._toggle_hud_layout_mode, tone="accent", width=100
        )
        self.hud_layout_btn.pack(side="left", padx=3, pady=3)

        self.adv_mode_var = ctk.BooleanVar(value=self.advanced_mode)
        ctk.CTkSwitch(
            self.taskbar,
            text="Advanced",
            variable=self.adv_mode_var,
            fg_color=self.theme["border"],
            progress_color=self.theme["gold"],
            text_color=self.theme["text_dim"],
            font=_F_CAPTION,
            command=self._toggle_advanced_mode,
        ).pack(side="left", padx=8, pady=3)

        self.status_bar = ctk.CTkLabel(
            self.taskbar, text="Starting…", text_color=self.theme["text_dim"],
            font=_F_CAPTION, anchor="e")
        self.status_bar.pack(side="right", fill="x", expand=True, padx=8)

        self.theme_var = ctk.StringVar(value=self.theme_name)
        ctk.CTkOptionMenu(
            self.taskbar,
            variable=self.theme_var,
            values=list(THEMES.keys()),
            fg_color=self.theme["bg_accent"],
            button_color=self.theme["bg_hover"],
            text_color=self.theme["text"],
            width=110,
            height=24,
            font=_F_CAPTION,
            dropdown_fg_color=self.theme["bg_card"],
            dropdown_hover_color=self.theme["bg_accent"],
            command=self._change_theme,
        ).pack(side="right", padx=(3, 6), pady=3)
        ctk.CTkLabel(self.taskbar, text="Theme:", text_color=self.theme["text_dim"],
                     font=_F_CAPTION).pack(side="right", padx=(6, 0), pady=3)

    def _build_header_bar(self):
        t = self.theme
        header = tk.Frame(self, bg=t["bg_panel"], height=46)
        header.pack(fill="x", side="top", before=self.tabview)
        header.pack_propagate(False)
        # Single bottom accent line — clean, not cluttered
        tk.Frame(header, bg=_blend(t["border_hl"], t["gold"], 0.35), height=1).pack(
            fill="x", side="bottom")

        left = tk.Frame(header, bg=t["bg_panel"])
        left.pack(side="left", fill="y", padx=(14, 0), pady=0)

        title_row = tk.Frame(left, bg=t["bg_panel"])
        title_row.pack(anchor="w", fill="y", expand=True)

        # Suit symbols — compact row
        for sym, col in [("♠", t["text"]), ("♥", t["red"]), ("♦", t["gold"]), ("♣", t["green"])]:
            tk.Label(title_row, text=sym, bg=t["bg_panel"], fg=col,
                     font=("Segoe UI Symbol", 13)).pack(side="left", padx=1)

        tk.Frame(title_row, bg=_blend(t["border_hl"], t["gold"], 0.3),
                 width=1).pack(side="left", fill="y", padx=(6, 8), pady=8)

        # App name — Segoe UI, bold
        tk.Label(title_row, text="LEAKSNIPE",
                 bg=t["bg_panel"], fg=t.get("text_header", t["text"]),
                 font=_F_HEADER).pack(side="left")

        # Italic subtitle next to name
        self._header_subtitle = tk.Label(
            title_row,
            text=" — import · review · HUD",
            bg=t["bg_panel"], fg=t["text_dim"],
            font=_F_BODY_I,
            anchor="w",
        )
        self._header_subtitle.pack(side="left", padx=(4, 0))

        right = tk.Frame(header, bg=t["bg_panel"])
        right.pack(side="right", padx=12, fill="y")

        # Compact HUD badge — just a pill, no shadow box
        hud_pill = tk.Frame(right, bg=t["bg_accent"],
                            highlightthickness=1,
                            highlightbackground=_blend(t["border_hl"], t["gold"], 0.3))
        hud_pill.pack(side="right", padx=(6, 0), pady=10)
        tk.Label(hud_pill, text="⬡ HUD READY",
                 bg=t["bg_accent"], fg=t["text"],
                 font=_F_CAPTION, padx=8, pady=2).pack()

        # Theme name pill
        theme_pill = tk.Frame(right, bg=t["bg_card"],
                              highlightthickness=1,
                              highlightbackground=t["border"])
        theme_pill.pack(side="right", pady=10)
        tk.Label(theme_pill, text=self.theme_name.upper(),
                 bg=t["bg_card"], fg=t["text_dim"],
                 font=_F_CAPTION, padx=6, pady=2).pack()

        def _header_configure(event):
            avail = max(80, event.width - right.winfo_reqwidth() - 60)
            self._header_subtitle.configure(wraplength=avail)
        header.bind("<Configure>", _header_configure, add="+")

    def _toggle_advanced_mode(self):
        self.advanced_mode = self.adv_mode_var.get()
        self.settings["advanced_mode"] = self.advanced_mode
        self._save_settings_quiet()
        self._refresh_hands_list()
        self._update_leak_tab()

    def _toggle_advanced_from_settings(self):
        self.advanced_mode = self.settings_adv_var.get()
        self.adv_mode_var.set(self.advanced_mode)
        self.settings["advanced_mode"] = self.advanced_mode
        self._save_settings_quiet()
        self._refresh_hands_list()
        self._update_leak_tab()

    def _change_theme(self, new_theme):
        self.theme_name = new_theme
        self.theme = THEMES.get(new_theme, THEMES["Slate Blue"])
        self.settings["theme"] = new_theme
        self._save_settings_quiet()
        self._set_status(f"Theme changed to {new_theme} — restart app to fully apply")

    def _save_settings_quiet(self):
        """Save settings without UI feedback."""
        try:
            with open(SETTINGS_PATH, "w") as f:
                json.dump(self.settings, f, indent=2)
        except Exception:
            pass

    def _hud_only_startup(self):
        """Lightweight startup for --live-hud: watcher only, no full GUI scan."""
        logging.info("HUD-only mode: incremental watcher (skipping full hand scan)")
        self._prewarm_hud_stats_async()
        if self.settings.get("auto_refresh", True):
            self.importer.start_watcher(callback=self._hud_watcher_callback)

    def _hud_watcher_callback(self, new_count, file_count):
        if new_count:
            self.after(0, self._schedule_hud_cache_refresh)
            monitor = getattr(self, "hand_monitor", None)
            if monitor is not None:
                self.after(400, monitor.check_now)

    def _schedule_hud_cache_refresh(self):
        if getattr(self, "_hud_cache_refresh_job", None) is not None:
            try:
                self.after_cancel(self._hud_cache_refresh_job)
            except Exception:
                pass
        self._hud_cache_refresh_job = self.after(2000, self._refresh_hud_player_cache)

    def _refresh_hud_player_cache(self):
        self._hud_cache_refresh_job = None
        threading.Thread(target=self._compute_players_bg, daemon=True).start()
        for overlay in list(getattr(self, "_hud_overlays", {}).values()):
            try:
                overlay.invalidate_stats_cache()
                overlay.refresh_stats_only()
            except Exception:
                pass

    def _prewarm_hud_stats_async(self):
        threading.Thread(target=self._prewarm_hud_stats, daemon=True).start()

    def _prewarm_hud_stats(self):
        try:
            if self.db.count_player_types() > 0:
                logging.info("HUD stats cache warm (%d players in player_types)", self.db.count_player_types())
                return
            logging.info("HUD stats cache empty — computing player_types in background")
            self._compute_players_bg()
        except Exception:
            logging.exception("HUD stats prewarm failed")

    # ── Actions / Callbacks ───────────────────────────────────────────────
    def _initial_scan(self):
        self._set_status("Scanning hand history directories...")
        threading.Thread(target=self._do_initial_scan, daemon=True).start()

    def _do_initial_scan(self):
        new_count, file_count = self.importer.full_scan()
        reparsed = self.importer.reparse_hands_missing_hero()
        total_hands = len(self.importer.get_hands())
        status = f"Scan: {new_count} new from {file_count} files | {total_hands} total hands"
        if reparsed:
            status += f" | reparsed {reparsed}"
        self.after(0, lambda: self._set_status(status))
        self.after(0, self._post_scan)
        if self.settings.get("auto_refresh", True):
            self.importer.start_watcher(callback=self._watcher_callback)

    def _watcher_callback(self, new_count, file_count):
        if new_count:
            self.after(0, self._post_scan)

    def _post_scan(self):
        self._post_scan_generation += 1
        generation = self._post_scan_generation
        if self._post_scan_debounce_job is not None:
            try:
                self.after_cancel(self._post_scan_debounce_job)
            except Exception:
                pass
        self._post_scan_debounce_job = self.after(
            500, lambda g=generation: self._start_post_scan_bg(g)
        )

    def _start_post_scan_bg(self, generation):
        self._post_scan_debounce_job = None
        if generation != self._post_scan_generation:
            return
        threading.Thread(target=self._post_scan_bg, args=(generation,), daemon=True).start()

    def _post_scan_bg(self, generation):
        status_text = self.importer.get_stats_text()
        hands = self.importer.get_hands()
        stats = self.leak_engine.analyze(hands) if hands else {}
        self.after(0, lambda: self._apply_post_scan(generation, status_text, hands, stats))

    def _apply_post_scan(self, generation, status_text, hands, stats):
        if generation != self._post_scan_generation:
            return
        self._last_hands_snapshot = hands
        self.current_stats = stats
        self._set_status(status_text)
        if getattr(self, "_hud_only", False):
            threading.Thread(target=self._compute_players_bg, daemon=True).start()
            return
        dash_fp = (
            stats.get("total_hands"),
            stats.get("vpip"),
            stats.get("pfr"),
            len(hands),
        )
        if dash_fp != self._dashboard_fingerprint:
            self._dashboard_fingerprint = dash_fp
            self._update_dashboard_with_hands(hands)
        self._refresh_hands_list_with_data(hands)
        self._update_leak_tab()
        threading.Thread(target=self._compute_players_bg, daemon=True).start()

    def _manual_refresh(self):
        self._set_status("Refreshing...")
        threading.Thread(target=self._do_manual_refresh, daemon=True).start()

    def _do_manual_refresh(self):
        self.importer.full_scan()
        self.after(0, self._post_scan)

    def _manual_import(self):
        """Open file dialog to manually import hand history files."""
        file_paths = filedialog.askopenfilenames(
            title="Select Hand History Files",
            filetypes=[("Text files", "*.txt"), ("All files", "*.*")],
        )
        if not file_paths:
            return
        self._set_status(f"Importing {len(file_paths)} file(s)...")

        def _do_import():
            saved, files = self.importer.import_files(file_paths)
            self.after(0, lambda: self._set_status(
                f"Manual import: {saved} new hand(s) from {files} file(s)"))
            self.after(0, self._post_scan)

        threading.Thread(target=_do_import, daemon=True).start()

    def _set_status(self, text):
        if hasattr(self, "status_bar") and self.status_bar is not None:
            try:
                self.status_bar.configure(text=text)
            except Exception:
                pass
        logging.info("Status: %s", text)

    def _update_dashboard(self):
        self._update_dashboard_with_hands(self.importer.get_hands())

    def _update_dashboard_with_hands(self, hands):
        s = self.current_stats
        if not s:
            return
        total_hands = s.get("total_hands", 0)
        site_count = sum(1 for sd in s.get("by_site", {}).values() if sd.get("total"))
        self.dash_command_hands_var.set(f"{total_hands} hands across {site_count or 0} sites")
        self.dash_cards["Hands"].configure(text=str(s.get("total_hands", 0)))
        self.dash_cards["VPIP"].configure(text=f"{s.get('vpip', 0)}%")
        self.dash_cards["PFR"].configure(text=f"{s.get('pfr', 0)}%")
        self.dash_cards["AF"].configure(text=str(s.get("af", 0)))

        total_won = sum(d["won"] for d in s.get("by_site", {}).values())
        total_lost = sum(d["lost"] for d in s.get("by_site", {}).values())
        self.dash_cards["Won"].configure(text=f"+${total_won:.2f}")
        self.dash_cards["Lost"].configure(text=f"-${total_lost:.2f}")

        self.dash_site_text.configure(state="normal")
        self.dash_site_text.delete("1.0", "end")
        for site, sd in s.get("by_site", {}).items():
            chip_note = ""
            if sd.get("chip_net"):
                chip_note = f"  Chips {sd['chip_net']:+,.0f}"
            self.dash_site_text.insert(
                "end",
                f"  {site:10s} {sd['total']:4d}h  VPIP {sd['vpip']:>2}%  PFR {sd['pfr']:>2}%  "
                f"Cash ${sd['net']:+.2f}{chip_note}\n",
            )
        self.dash_site_text.configure(state="disabled")

        self.dash_recent.configure(state="normal")
        self.dash_recent.delete("1.0", "end")
        recent = sorted(hands, key=lambda h: h.date or datetime.min, reverse=True)[:10]
        if recent:
            latest = recent[0]
            latest_result = format_hero_result(latest)
            latest_cards = latest.hero_cards or "--"
            self.dash_command_feed_var.set(f"{latest.site}  {latest_cards}  {latest.hero_position or '--'}  {latest_result}")
        else:
            self.dash_command_feed_var.set("Waiting for imported hands")
        for h in recent:
            dt = h.date.strftime("%m/%d %H:%M") if h.date else "?"
            result_str = format_hero_result(h)
            self.dash_recent.insert(
                "end",
                f"  {dt}  {h.site:10s}  {h.hero_cards:8s}  {h.hero_position:3s}  {result_str}\n",
            )
        if not recent:
            self.dash_recent.insert("end", "  No hands yet")
        self.dash_recent.configure(state="disabled")

        td = self.tilt_meter.analyze(hands)
        self.tilt_data = td
        tilt_text = f"{td['label']} {td['score']}/100"
        self.tilt_score_label.configure(text=tilt_text, text_color=td["color"])
        self.tilt_bar.set(td["score"] / 100)
        self.tilt_bar.configure(progress_color=td["color"])
        self.tilt_advice_label.configure(text=td["advice"], text_color=td["color"])
        self.dash_command_status_var.set(f"{td['label']}  {td['score']}/100")
        self.tilt_indicators_text.configure(state="normal")
        self.tilt_indicators_text.delete("1.0", "end")
        for ind in td.get("indicators", []):
            self.tilt_indicators_text.insert("end", f"  - {ind}\n")
        if not td.get("indicators"):
            self.tilt_indicators_text.insert("end", "  No tilt flags")
        self.tilt_indicators_text.configure(state="disabled")

        total_ev_diff = sum(self.ev_calculator.calc_ev_diff(h, self.settings) for h in hands)
        ev_str = f"+{total_ev_diff:.0f}" if total_ev_diff >= 0 else f"{total_ev_diff:.0f}"
        ev_color = self.theme["green"] if total_ev_diff >= 0 else self.theme["red"]
        self.dash_cards["EV Diff"].configure(text=ev_str, text_color=ev_color)

        self._update_dashboard_graphs(hands)

    def _update_dashboard_graphs(self, hands=None):
        """Render profit line graph and game-type pie chart on dashboard."""
        if not hasattr(self, 'dash_fig'):
            return
        t = self.theme
        if hands is None:
            hands = self.importer.get_hands()
        self.dash_fig.clear()
        self.dash_fig.patch.set_facecolor(t["graph_bg"])

        # Left: Profit/Loss over time
        ax1 = self.dash_fig.add_subplot(121)
        ax1.set_facecolor(t["graph_face"])
        ax1.tick_params(colors=t["text_dim"], labelsize=8)
        ax1.set_title("Session Profit / Loss", color=t["gold"], fontsize=10, fontweight="bold")
        for spine in ax1.spines.values():
            spine.set_color(t["graph_grid"])

        if hands:
            sorted_hands = sorted([h for h in hands if h.date], key=lambda h: h.date)
            cumulative = []
            running = 0.0
            dates = []
            for h in sorted_hands:
                running += h.hero_won
                cumulative.append(running)
                dates.append(h.date)
            ax1.plot(range(len(cumulative)), cumulative, color=t["graph_line"], linewidth=1.5)
            ax1.axhline(y=0, color=t["red"], linewidth=0.5, linestyle="--", alpha=0.5)
            ax1.fill_between(range(len(cumulative)), cumulative, 0,
                             where=[c >= 0 for c in cumulative], alpha=0.15, color=t["green"])
            ax1.fill_between(range(len(cumulative)), cumulative, 0,
                             where=[c < 0 for c in cumulative], alpha=0.15, color=t["red"])
            ax1.set_xlabel("Hands", color=t["text_dim"], fontsize=8)
            ax1.set_ylabel("Profit", color=t["text_dim"], fontsize=8)
        else:
            ax1.text(0.5, 0.5, "No data", ha="center", va="center", color=t["text_dim"], fontsize=12)
        ax1.grid(True, color=t["graph_grid"], alpha=0.3, linewidth=0.5)

        # Right: Game type pie chart
        ax2 = self.dash_fig.add_subplot(122)
        ax2.set_facecolor(t["graph_face"])
        ax2.set_title("Game Types", color=t["gold"], fontsize=10, fontweight="bold")
        if hands:
            from collections import Counter
            game_counts = Counter(h.game_type or "Unknown" for h in hands)
            if game_counts:
                labels = list(game_counts.keys())
                sizes = list(game_counts.values())
                colors = t["pie_colors"][:len(labels)]
                ax2.pie(sizes, labels=labels, colors=colors, autopct="%1.0f%%",
                        textprops={"color": t["text"], "fontsize": 8}, startangle=90)
            else:
                ax2.text(0.5, 0.5, "No data", ha="center", va="center", color=t["text_dim"])
        else:
            ax2.text(0.5, 0.5, "No data", ha="center", va="center", color=t["text_dim"], fontsize=12)

        self.dash_fig.tight_layout(pad=1.5)
        self.dash_canvas.draw()

    # ── Hands tab ─────────────────────────────────────────────────────────
    def _on_hands_mousewheel(self, event):
        self.hands_text.yview_scroll(int(-1 * (event.delta / 120)), "units")
        return "break"

    def _hands_list_signature(self, hands):
        if not hands:
            return (0, self.hand_site_var.get(), self.hand_result_var.get(), self.hand_sort_var.get())
        sample = hands[:3]
        return (
            len(hands),
            self.hand_site_var.get(),
            self.hand_result_var.get(),
            self.hand_sort_var.get(),
            tuple(h.hand_id for h in sample),
            hands[0].hand_id if hands else None,
        )

    def _refresh_hands_list(self):
        self._hands_list_fingerprint = None
        self._refresh_hands_list_with_data(self.importer.get_hands())

    def _refresh_hands_list_with_data(self, hands):
        filtered = self._apply_filters(hands)
        signature = self._hands_list_signature(filtered)
        if signature == self._hands_list_fingerprint and self._hand_objects:
            self._update_hand_row_highlights()
            self.hand_count_label.configure(
                text=f"{len(filtered)} hands ({min(len(filtered), 500)} shown)"
            )
            return
        self._hands_list_fingerprint = signature

        yview = self.hands_text.yview()
        preserved_selection = set(self._selected_hand_ids)
        preserved_active = self._hands_active_hand_id

        self.hands_text.delete("1.0", "end")
        self._hand_objects.clear()
        self._hand_tag_by_id.clear()
        self._selected_hand_ids = {hid for hid in preserved_selection if hid}
        if preserved_active:
            self._hands_active_hand_id = preserved_active

        sort_choice = self.hand_sort_var.get()
        if "Date \u2193" in sort_choice:
            filtered.sort(key=lambda h: h.date or datetime.min, reverse=True)
        elif "Date \u2191" in sort_choice:
            filtered.sort(key=lambda h: h.date or datetime.min)
        elif "Big wins" in sort_choice:
            filtered.sort(key=lambda h: h.hero_won, reverse=True)
        elif "Big losses" in sort_choice:
            filtered.sort(key=lambda h: h.hero_won)
        elif "Pot \u2193" in sort_choice:
            filtered.sort(key=lambda h: h.pot, reverse=True)
        elif "Pot \u2191" in sort_choice:
            filtered.sort(key=lambda h: h.pot)

        for i, h in enumerate(filtered[:500]):
            self._hand_objects[h.hand_id] = h
            dt = h.date.strftime("%m/%d %H:%M") if h.date else "?"
            game = "Trn" if h.is_tournament else "Cash"
            result = format_hero_result(h)
            pot_str = f"{h.pot:.0f}" if h.pot else ""
            ev_diff = self.ev_calculator.calc_ev_diff(h, self.settings)
            ev_str = f"+{ev_diff:.0f}" if ev_diff >= 0 else f"{ev_diff:.0f}"

            tags = getattr(h, "tags", None)
            if tags is None:
                tags = self.db.get_tags(h.hand_id)
            tag_str = f" [{','.join(tags)}]" if tags else ""
            cards = (h.hero_cards or "--").strip()
            if len(cards) > 8:
                cards = cards[:8]
            cards = cards.ljust(8)
            line = f"  {dt:14s} {h.site:10s} {game:5s} {cards:8s} {h.hero_position:4s} {result:>8s} {pot_str:>7s} {ev_str:>7s}{tag_str}\n"
            tag_name = f"hand_{i}"
            self.hands_text.insert("end", line, (tag_name,))
            self._hand_tag_by_id[h.hand_id] = tag_name

            self.hands_text.tag_bind(
                tag_name, "<Button-1>",
                lambda e, hand=h: self._on_hand_row_click(e, hand),
            )
            self.hands_text.tag_bind(
                tag_name, "<Double-Button-1>",
                lambda e, hand=h: self._on_hand_row_double_click(e, hand),
            )
            if h.hero_won > 0:
                self.hands_text.tag_configure(tag_name, foreground=self.theme["row_win"])
            elif h.hero_won < 0:
                self.hands_text.tag_configure(tag_name, foreground=self.theme["row_loss"])
            else:
                self.hands_text.tag_configure(tag_name, foreground=self.theme["row_even"])

        if not filtered:
            self.hands_text.insert("end", "  No hands match filters")

        self.hand_count_label.configure(text=f"{len(filtered)} hands ({min(len(filtered), 500)} shown)")
        self._update_hand_row_highlights()
        self._update_selection_count()
        try:
            self.hands_text.yview_moveto(yview[0])
        except Exception:
            pass

    def _apply_filters(self, hands):
        """Apply all active filters to hands list."""
        site_filter = self.hand_site_var.get()
        result_filter = self.hand_result_var.get()

        # Advanced filters
        date_from_str = self.filter_date_from_var.get().strip() if hasattr(self, 'filter_date_from_var') else ""
        date_to_str = self.filter_date_to_var.get().strip() if hasattr(self, 'filter_date_to_var') else ""
        pot_min_str = self.filter_pot_min_var.get().strip() if hasattr(self, 'filter_pot_min_var') else ""
        pot_max_str = self.filter_pot_max_var.get().strip() if hasattr(self, 'filter_pot_max_var') else ""
        type_filter = self.filter_type_var.get() if hasattr(self, 'filter_type_var') else "All"
        tag_filter = self.filter_tag_var.get() if hasattr(self, 'filter_tag_var') else "All"
        opp_type_filter = self.filter_opp_type_var.get() if hasattr(self, 'filter_opp_type_var') else "All"

        # Parse dates
        date_from = None
        date_to = None
        for fmt in ("%m/%d/%Y", "%Y-%m-%d", "%m-%d-%Y"):
            if date_from_str and not date_from:
                try:
                    date_from = datetime.strptime(date_from_str, fmt)
                except ValueError:
                    pass
            if date_to_str and not date_to:
                try:
                    date_to = datetime.strptime(date_to_str, fmt)
                    date_to = date_to.replace(hour=23, minute=59, second=59)
                except ValueError:
                    pass

        # Parse pot range
        pot_min = None
        pot_max = None
        try:
            if pot_min_str:
                pot_min = float(pot_min_str)
        except ValueError:
            pass
        try:
            if pot_max_str:
                pot_max = float(pot_max_str)
        except ValueError:
            pass

        # Get tagged hand IDs if filtering by tag
        tag_hand_ids = None
        if tag_filter and tag_filter != "All":
            tag_hand_ids = self.db.get_hand_ids_by_tag(tag_filter)

        # Get player names matching opponent type filter
        opp_type_names = None
        if opp_type_filter and opp_type_filter != "All":
            opp_type_names = self.db.get_players_by_type(opp_type_filter)

        filtered = []
        for h in hands:
            # Treat legacy "ACR" hands as "BetACR" (same site — BetACR.eu / WPN skin)
            h_site = "BetACR" if h.site == "ACR" else h.site
            if site_filter != "All" and h_site != site_filter:
                continue
            if result_filter == "Won" and h.hero_won <= 0:
                continue
            if result_filter == "Lost" and h.hero_won >= 0:
                continue
            if date_from and h.date and h.date < date_from:
                continue
            if date_to and h.date and h.date > date_to:
                continue
            if pot_min is not None and h.pot < pot_min:
                continue
            if pot_max is not None and h.pot > pot_max:
                continue
            if type_filter == "Cash" and h.is_tournament:
                continue
            if type_filter == "Tournament" and not h.is_tournament:
                continue
            if tag_hand_ids is not None and h.hand_id not in tag_hand_ids:
                continue
            if opp_type_names is not None:
                hand_players = {info["name"] for info in (h.players or {}).values()}
                if not hand_players or not hand_players.intersection(opp_type_names):
                    continue
            filtered.append(h)
        return filtered

    def _get_filtered_hands(self):
        """Return currently filtered hands list (for AI analysis and export)."""
        hands = self.importer.get_hands()
        return self._apply_filters(hands)

    def _get_filter_description(self):
        """Return a human-readable description of active filters."""
        parts = []
        site = self.hand_site_var.get()
        if site != "All":
            parts.append(f"Site: {site}")
        result = self.hand_result_var.get()
        if result != "All":
            parts.append(f"Result: {result}")
        if hasattr(self, 'filter_date_from_var'):
            df = self.filter_date_from_var.get().strip()
            dt = self.filter_date_to_var.get().strip()
            if df:
                parts.append(f"From: {df}")
            if dt:
                parts.append(f"To: {dt}")
        if hasattr(self, 'filter_pot_min_var'):
            pm = self.filter_pot_min_var.get().strip()
            px = self.filter_pot_max_var.get().strip()
            if pm:
                parts.append(f"Pot ≥ {pm}")
            if px:
                parts.append(f"Pot ≤ {px}")
        if hasattr(self, 'filter_type_var'):
            t = self.filter_type_var.get()
            if t != "All":
                parts.append(f"Type: {t}")
        if hasattr(self, 'filter_tag_var'):
            tag = self.filter_tag_var.get()
            if tag != "All":
                parts.append(f"Tag: {tag}")
        if hasattr(self, 'filter_opp_type_var'):
            opp = self.filter_opp_type_var.get()
            if opp != "All":
                parts.append(f"Vs: {opp}")
        return " | ".join(parts) if parts else "All Hands (no filters)"

    def _clear_filters(self):
        """Reset all filters to defaults."""
        self.hand_site_var.set("All")
        self.hand_result_var.set("All")
        if hasattr(self, 'filter_date_from_var'):
            self.filter_date_from_var.set("")
            self.filter_date_to_var.set("")
        if hasattr(self, 'filter_pot_min_var'):
            self.filter_pot_min_var.set("")
            self.filter_pot_max_var.set("")
        if hasattr(self, 'filter_type_var'):
            self.filter_type_var.set("All")
        if hasattr(self, 'filter_tag_var'):
            self.filter_tag_var.set("All")
        if hasattr(self, 'filter_opp_type_var'):
            self.filter_opp_type_var.set("All")
        self._refresh_hands_list()

    def _refresh_tag_filter(self):
        """Refresh the tag filter dropdown with current tags from DB."""
        if hasattr(self, 'filter_tag_menu'):
            tags = self.db.get_all_tags()
            values = ["All"] + tags
            self.filter_tag_menu.configure(values=values)

    def _tag_selected_hands(self):
        """Open dialog to tag selected hands."""
        selected = self._get_selected_hands()
        if not selected:
            self._set_status("Select hands first, then tag them")
            return
        self._open_tag_dialog(selected)

    def _open_tag_dialog(self, hands, parent=None):
        """Visual tag dialog — color-coded toggle buttons grouped by category."""
        logging.debug("Opening tag dialog for %d hand(s); parent=%s", len(hands), type(parent).__name__ if parent is not None else "self")
        owner = parent if parent is not None else self
        dialog = tk.Toplevel(owner)
        dialog.title("Tag Hands")
        dialog.geometry("520x420")
        dialog.configure(bg=self.theme["bg_base"])
        dialog.transient(owner)
        dialog.lift()
        dialog.focus_force()
        if owner is not self:
            dialog.attributes("-topmost", True)
            dialog.after(150, lambda: dialog.wm_attributes("-topmost", False))
        t = self.theme

        # Header
        hdr = tk.Frame(dialog, bg=t["bg_accent"], height=36)
        hdr.pack(fill="x")
        hdr.pack_propagate(False)
        count_word = f"{len(hands)} hand" if len(hands) == 1 else f"{len(hands)} hands"
        tk.Label(hdr, text=f"🏷  Tag  {count_word}", bg=t["bg_accent"], fg=t["text"],
                 font=_F_LABEL).pack(side="left", padx=10, pady=6)

        # Current tags pill row
        all_tags_on = set()
        for h in hands:
            for tag in self.db.get_tags(h.hand_id):
                all_tags_on.add(tag)

        pill_frame = tk.Frame(dialog, bg=t["bg_panel"])
        pill_frame.pack(fill="x", padx=12, pady=(8, 0))
        tk.Label(pill_frame, text="Active tags:", bg=t["bg_panel"], fg=t["text_dim"],
                 font=_F_CAPTION).pack(side="left", padx=(0, 6))
        self._tag_dialog_pill_frame = pill_frame
        self._tag_dialog_hands = hands
        self._tag_dialog_active = all_tags_on

        def _rebuild_pills():
            for w in pill_frame.winfo_children()[1:]:
                w.destroy()
            cur = set()
            for h in hands:
                for tag in self.db.get_tags(h.hand_id):
                    cur.add(tag)
            if not cur:
                tk.Label(pill_frame, text="none", bg=t["bg_panel"], fg=t["text_dim"],
                         font=_F_CAPTION_I).pack(side="left")
            else:
                for tag in sorted(cur):
                    col = HAND_TAG_COLORS.get(tag, t["border_hl"])
                    pf = tk.Frame(pill_frame, bg=col, padx=1, pady=1)
                    pf.pack(side="left", padx=2)
                    tk.Label(pf, text=tag, bg=col, fg="#111111",
                             font=(_FF, 9, "bold"), padx=6, pady=1).pack()

        _rebuild_pills()

        # Preset area (plain tkinter to avoid CTk/Tk popup issues)
        preset_container = tk.Frame(dialog, bg=t["bg_panel"])
        preset_container.pack(fill="both", expand=True, padx=12, pady=6)

        # Group by category
        from collections import OrderedDict
        categories = OrderedDict()
        for entry in HAND_TAG_PRESETS:
            label, key, color, cat = entry
            categories.setdefault(cat, []).append(entry)

        CAT_ICONS = {"Review": "📋", "Decision": "🎯", "Situation": "💥",
                     "Mistake": "❌", "Other": "🔧"}

        for cat, entries in categories.items():
            cat_row = tk.Frame(preset_container, bg=t["bg_panel"])
            cat_row.pack(fill="x", pady=(8, 2))
            tk.Label(cat_row, text=f"{CAT_ICONS.get(cat,'•')} {cat}",
                     bg=t["bg_panel"], fg=t["text_dim"],
                     font=_F_CAPTION_I).pack(side="left", padx=4)
            tk.Frame(cat_row, bg=t["border"], height=1).pack(
                side="left", fill="x", expand=True, padx=(6, 0), pady=6)

            btns_row = tk.Frame(preset_container, bg=t["bg_panel"])
            btns_row.pack(fill="x", padx=4, pady=2)

            for entry in entries:
                lbl_text, key, color, _cat = entry

                def _make_btn(k=key, c=color, display=lbl_text):
                    cur_active = any(k in self.db.get_tags(h.hand_id) for h in hands)
                    bg_on  = c
                    bg_off = _blend(c, t["bg_panel"], 0.80)
                    fg_on  = "#111111"
                    fg_off = _lighten(c, 0.3)
                    bf = tk.Frame(btns_row, bg=_blend(c, t["bg_panel"], 0.5), padx=1, pady=1)
                    bf.pack(side="left", padx=3, pady=3)
                    btn = tk.Button(
                        bf, text=display,
                        bg=bg_on if cur_active else bg_off,
                        fg=fg_on if cur_active else fg_off,
                        activebackground=bg_on,
                        activeforeground=fg_on,
                        font=(_FF, 10, "bold") if cur_active else (_FF, 10, "normal"),
                        relief="flat", bd=0, padx=10, pady=4, cursor="hand2",
                    )
                    btn.pack()

                    def _toggle(b=btn, bk=k, bc=c, bon=bg_on, boff=bg_off,
                                bfon=fg_on, bfoff=fg_off):
                        logging.debug("Toggling tag '%s' for %d hand(s)", bk, len(hands))
                        currently = any(bk in self.db.get_tags(h.hand_id) for h in hands)
                        if currently:
                            for h in hands:
                                self.db.remove_tag(h.hand_id, bk)
                            b.configure(bg=boff, fg=bfoff, font=(_FF, 10, "normal"))
                            status_lbl.configure(text=f"  Removed '{bk}' from {len(hands)} hand(s)", fg=t["text_dim"])
                        else:
                            for h in hands:
                                self.db.add_tag(h.hand_id, bk)
                            b.configure(bg=bon, fg=bfon, font=(_FF, 10, "bold"))
                            status_lbl.configure(text=f"  ✓ Tagged {len(hands)} hand(s) with '{bk}'",
                                                 fg=bc)
                        _rebuild_pills()
                        self._refresh_tag_filter()
                    btn.configure(command=_toggle)

                _make_btn()

        # Custom tag row
        custom_row = tk.Frame(dialog, bg=t["bg_panel"])
        custom_row.pack(fill="x", padx=12, pady=(2, 4))
        tk.Label(custom_row, text="Custom:", bg=t["bg_panel"], fg=t["text"],
                 font=_F_BODY).pack(side="left", padx=(0, 6))
        tag_entry_var = tk.StringVar()
        tag_entry = tk.Entry(
            custom_row,
            textvariable=tag_entry_var,
            bg=t["bg_input"],
            fg=t["text"],
            insertbackground=t["text"],
            relief="flat",
            bd=1,
            width=22,
            font=_F_BODY,
        )
        tag_entry.pack(side="left", padx=4)

        def _add_custom():
            tag = tag_entry_var.get().strip()
            if not tag:
                return
            logging.debug("Adding custom tag '%s' for %d hand(s)", tag, len(hands))
            for h in hands:
                self.db.add_tag(h.hand_id, tag)
            status_lbl.configure(text=f"  ✓ Added '{tag}'", fg=t["green"])
            tag_entry_var.set("")
            _rebuild_pills()
            self._refresh_tag_filter()

        tag_entry.bind("<Return>", lambda e: _add_custom())
        tk.Button(
            custom_row,
            text="Add",
            bg=t["green"],
            fg=t["bg_base"],
            activebackground=t["bg_accent"],
            activeforeground=t["text"],
            relief="flat",
            bd=0,
            padx=10,
            pady=3,
            cursor="hand2",
            font=_F_SEMIBOLD,
            command=_add_custom,
        ).pack(side="left", padx=4)

        # Status + Done
        status_lbl = tk.Label(
            dialog,
            text="  Click a tag to toggle it on/off",
            bg=t["bg_base"],
            fg=t["text_dim"],
            font=_F_CAPTION_I,
            anchor="w",
        )
        status_lbl.pack(fill="x", padx=16, pady=(0, 2))

        def _close():
            logging.debug("Closing tag dialog")
            dialog.destroy()
            self._refresh_hands_list()
            if self._detail_tag_hand:
                self._refresh_detail_tag_strip(self._detail_tag_hand)

        dialog.protocol("WM_DELETE_WINDOW", _close)

        tk.Button(
            dialog,
            text="Done  ✓",
            bg=t["bg_accent"],
            fg=t["text"],
            activebackground=t["green"],
            activeforeground=t["bg_base"],
            relief="flat",
            bd=0,
            padx=14,
            pady=5,
            cursor="hand2",
            font=_F_SEMIBOLD,
            command=_close,
        ).pack(pady=(4, 10))

    def _on_hand_row_click(self, event, hand):
        """Defer single-click selection so double-click can still fire."""
        ctrl = bool(event.state & 0x0004)
        if self._hand_click_after_id is not None:
            try:
                self.after_cancel(self._hand_click_after_id)
            except Exception:
                pass
        self._hand_click_after_id = self.after(
            220, lambda h=hand, c=ctrl: self._apply_hand_row_select(h, c)
        )

    def _on_hand_row_double_click(self, event, hand):
        """Double-click opens detail panel and hand replayer."""
        if self._hand_click_after_id is not None:
            try:
                self.after_cancel(self._hand_click_after_id)
            except Exception:
                pass
            self._hand_click_after_id = None
        self._show_hand_detail(hand, open_replayer=True)
        return "break"

    def _apply_hand_row_select(self, hand, ctrl=False):
        """Single-click selection only — no detail panel or replayer."""
        self._hand_click_after_id = None
        hid = hand.hand_id
        if ctrl:
            if hid in self._selected_hand_ids:
                self._selected_hand_ids.discard(hid)
            else:
                self._selected_hand_ids.add(hid)
        else:
            self._selected_hand_ids = {hid}
        self._hands_active_hand_id = hid
        self.select_all_var.set(
            len(self._selected_hand_ids) == len(self._hand_objects) and bool(self._hand_objects)
        )
        self._update_hand_row_highlights()
        self._update_selection_count()

    def _update_hand_row_highlights(self):
        t = self.theme
        active_bg = _blend(t["bg_accent"], t["bg_input"], 0.45)
        selected_bg = _blend(t["green"], t["bg_input"], 0.72)
        for hid, hand in self._hand_objects.items():
            tag_name = self._hand_tag_by_id.get(hid)
            if not tag_name:
                continue
            if hid == self._hands_active_hand_id:
                fg = t["text"]
                bg = active_bg
            elif hid in self._selected_hand_ids:
                fg = t["text"]
                bg = selected_bg
            elif hand.hero_won > 0:
                fg = t["row_win"]
                bg = t["bg_input"]
            elif hand.hero_won < 0:
                fg = t["row_loss"]
                bg = t["bg_input"]
            else:
                fg = t["row_even"]
                bg = t["bg_input"]
            self.hands_text.tag_configure(tag_name, foreground=fg, background=bg)

    def _view_selected_hand(self):
        selected = self._get_selected_hands()
        if not selected:
            self._set_status("Select a hand first, then click View")
            return
        self._show_hand_detail(selected[-1], open_replayer=True)

    def _update_selection_count(self):
        count = len(self._selected_hand_ids)
        self.hand_sel_count_label.configure(text=f"{count} selected")

    def _toggle_select_all(self):
        val = self.select_all_var.get()
        if val:
            self._selected_hand_ids = set(self._hand_objects.keys())
        else:
            self._selected_hand_ids.clear()
        self._update_hand_row_highlights()
        self._update_selection_count()

    def _get_selected_hands(self):
        selected = []
        for hid in self._selected_hand_ids:
            if hid in self._hand_objects:
                selected.append(self._hand_objects[hid])
        return selected

    def _compare_selected(self):
        selected = self._get_selected_hands()
        if len(selected) < 2:
            self._set_status("Select at least 2 hands to compare")
            return
        self.detail_title_label.configure(text=f"Compare {len(selected)} Hands")
        self.hand_detail_text.configure(state="normal")
        self.hand_detail_text.delete("1.0", "end")

        sep = "=" * 60
        self.hand_detail_text.insert("end", f"{sep}\n")
        self.hand_detail_text.insert("end", f"  HAND COMPARISON  ({len(selected)} hands)\n")
        self.hand_detail_text.insert("end", f"{sep}\n\n")

        # Summary table
        self.hand_detail_text.insert("end",
            f"  {'#':3s} {'Site':10s} {'Cards':10s} {'Pos':4s} {'Result':>9s} {'Pot':>8s} {'Date'}\n")
        self.hand_detail_text.insert("end", "  " + "-" * 70 + "\n")
        total_result = 0.0
        for i, h in enumerate(selected, 1):
            dt = h.date.strftime("%m/%d %H:%M") if h.date else "?"
            res = format_hero_result(h)
            total_result += h.hero_won
            self.hand_detail_text.insert("end",
                f"  {i:<3d} {h.site:10s} {h.hero_cards:10s} {h.hero_position:4s} "
                f"{res:>9s} {h.pot:>8.0f} {dt}\n")
        self.hand_detail_text.insert("end", "  " + "-" * 70 + "\n")
        net_str = f"+{total_result:.0f}" if total_result >= 0 else f"{total_result:.0f}"
        self.hand_detail_text.insert("end", f"  NET RESULT: {net_str}\n\n")

        # Stats for selected hands only
        positions: Dict[str, int] = defaultdict(int)
        vpip_count: int = 0
        for h in selected:
            positions[h.hero_position] += 1
            if h.streets:
                pf = h.streets[0]
                hero = h.hero_name(self.settings) if hasattr(h, 'hero_name') else ""
                for act in pf.get("actions", []):
                    if act["player"] == hero and act["action"] in ("call", "raise", "bet"):
                        vpip_count += 1
                        break

        self.hand_detail_text.insert("end", "  Positions: ")
        for pos in ["EP", "MP", "CO", "BTN", "SB", "BB"]:
            if positions.get(pos, 0) > 0:
                self.hand_detail_text.insert("end", f"{pos}={positions[pos]}  ")
        self.hand_detail_text.insert("end", "\n")
        if len(selected) > 0:
            self.hand_detail_text.insert("end",
                f"  VPIP in selection: {100*vpip_count/len(selected):.0f}%\n")
        self.hand_detail_text.insert("end", f"\n{sep}\n")
        self.hand_detail_text.insert("end", "  FULL HAND DETAILS:\n")
        self.hand_detail_text.insert("end", f"{sep}\n\n")

        for i, h in enumerate(selected, 1):
            self.hand_detail_text.insert("end", f"--- Hand {i}/{len(selected)} ---\n")
            self.hand_detail_text.insert("end", h.raw_text if h.raw_text else "(no raw text)")
            self.hand_detail_text.insert("end", "\n\n")

        self.hand_detail_text.configure(state="disabled")
        self._set_status(f"Comparing {len(selected)} hands — net result: {net_str}")

    def _copy_selected_hands(self):
        selected = self._get_selected_hands()
        if not selected:
            self._set_status("No hands selected")
            return
        text_parts = []
        for h in selected:
            if h.raw_text:
                text_parts.append(h.raw_text)
        full = "\n\n".join(text_parts)
        self.clipboard_clear()
        self.clipboard_append(full)
        self._set_status(f"Copied {len(selected)} hands to clipboard!")

    def _show_hand_detail(self, hand, popup=False, open_replayer=False):
        """Show hand details in the embedded panel; optional popup and replayer."""
        self._hands_active_hand_id = hand.hand_id
        self._selected_hand_ids = {hand.hand_id}
        self._update_hand_row_highlights()
        self._update_selection_count()

        # Also update the embedded detail panel
        self.detail_title_label.configure(text="Details")
        self.hand_detail_text.configure(state="normal")
        self.hand_detail_text.delete("1.0", "end")
        ev_diff = self.ev_calculator.calc_ev_diff(hand, self.settings)
        strength = self.ev_calculator.get_hand_strength(hand.hero_cards)
        ev_str = f"+{ev_diff:.1f}" if ev_diff >= 0 else f"{ev_diff:.1f}"
        cards_display = hand.hero_cards or "(unknown)"
        board_display = " ".join(hand.board_cards or []) or "(none)"
        self.hand_detail_text.insert("end", "\u2500\u2500 Hand Summary \u2500\u2500\n")
        self.hand_detail_text.insert("end",
            f"  Hole Cards: {cards_display}    Board: {board_display}\n")
        self.hand_detail_text.insert("end", "\u2500\u2500 EV Analysis \u2500\u2500\n")
        self.hand_detail_text.insert("end",
            f"  Hand Strength: {strength}/100 | EV Diff: {ev_str}\n")
        self.hand_detail_text.insert("end",
            f"  Position: {hand.hero_position} | Pot: {hand.pot:.0f} | "
            f"Result: {format_hero_result(hand)}\n\n")
        self.hand_detail_text.insert("end", hand.raw_text if hand.raw_text else "(no raw text)")

        # Show opponent types
        hero = hand.hero_name(self.settings)
        opponents = [info["name"] for info in hand.players.values() if info["name"] != hero]
        if opponents:
            self.hand_detail_text.insert("end", "\n── Opponent Types ──\n")
            for opp in opponents:
                pinfo = self.db.get_player_type(opp)
                if pinfo:
                    etype = pinfo["effective_type"]
                    override_mark = " (manual)" if pinfo["manual_type"] else ""
                    self.hand_detail_text.insert("end",
                        f"  {opp:20s}  {etype}{override_mark}  "
                        f"({pinfo['hands']} hands, VPIP:{pinfo['vpip']:.0f}% PFR:{pinfo['pfr']:.0f}%)\n")
                else:
                    self.hand_detail_text.insert("end", f"  {opp:20s}  Unknown (no data)\n")

        self.hand_detail_text.configure(state="disabled")
        self._refresh_detail_tag_strip(hand)

        if popup:
            self._open_hand_popup(hand, ev_diff, strength)
        if open_replayer:
            HandReplayerWindow(self, hand, self.theme)

    def _open_hand_popup(self, hand, ev_diff, strength):
        """Lightweight native tkinter Toplevel for hand detail."""
        popup = tk.Toplevel(self)
        popup.title(f"Hand {hand.hand_id}")
        popup.geometry("620x500")
        popup.configure(bg=self.theme["bg_input"])
        popup.attributes("-topmost", True)
        popup.focus_force()

        # Header bar
        header = tk.Frame(popup, bg=self.theme["bg_accent"], height=36)
        header.pack(fill="x")
        header.pack_propagate(False)

        dt = hand.date.strftime("%m/%d/%Y %H:%M") if hand.date else "?"
        res = format_hero_result(hand)
        res_color = self.theme["green"] if hand.hero_won >= 0 else self.theme["red"]
        ev_str = f"+{ev_diff:.1f}" if ev_diff >= 0 else f"{ev_diff:.1f}"

        tk.Label(header, text=f"{hand.hero_cards}  |  {hand.hero_position}  |  {dt}",
                 bg=self.theme["bg_accent"], fg=self.theme["text"], font=_F_LABEL).pack(side="left", padx=8)
        tk.Label(header, text=f"Result: {res}", bg=self.theme["bg_accent"], fg=res_color,
                 font=_F_LABEL).pack(side="right", padx=8)

        # EV bar
        ev_bar = tk.Frame(popup, bg=self.theme["bg_panel"], height=28)
        ev_bar.pack(fill="x")
        ev_bar.pack_propagate(False)
        tk.Label(ev_bar, text=f"Strength: {strength}/100  |  EV Diff: {ev_str}  |  Pot: {hand.pot:.0f}  |  {hand.site}",
                 bg=self.theme["bg_panel"], fg=self.theme["gold"], font=_F_DATA).pack(side="left", padx=8)

        # Tag row — colored pills + quick-tag button
        tag_row = tk.Frame(popup, bg=self.theme["bg_panel"], height=28)
        tag_row.pack(fill="x", padx=4, pady=(0, 2))
        tag_row.pack_propagate(False)
        _popup_tags = self.db.get_tags(hand.hand_id)
        tk.Label(tag_row, text="Tags:", bg=self.theme["bg_panel"],
                 fg=self.theme["text_dim"], font=(_FF, 9)).pack(side="left", padx=(8, 4))
        if _popup_tags:
            for _tg in _popup_tags:
                _tc = HAND_TAG_COLORS.get(_tg, self.theme["border_hl"])
                _pf = tk.Frame(tag_row, bg=_tc, padx=1, pady=1)
                _pf.pack(side="left", padx=2)
                tk.Label(_pf, text=_tg, bg=_tc, fg="#111111",
                         font=(_FF, 9, "bold"), padx=6, pady=1).pack()
        else:
            tk.Label(tag_row, text="none", bg=self.theme["bg_panel"],
                     fg=self.theme["text_dim"], font=(_FF, 9, "italic")).pack(side="left")
        tk.Button(tag_row, text="🏷 Tag", bg=self.theme["bg_accent"],
                  fg=self.theme["text"], font=(_FF, 9), relief="flat",
                  bd=0, padx=8, pady=1, cursor="hand2",
                  command=lambda p=popup: self._open_tag_dialog([hand], parent=p)
                  ).pack(side="right", padx=8)

        # Raw hand text
        txt = tk.Text(popup, bg=self.theme["bg_input"], fg=self.theme["text"], font=_F_DATA,
                      insertbackground=self.theme["text"], relief="flat", padx=8, pady=6,
                      selectbackground=self.theme["select_bg"])
        txt.pack(fill="both", expand=True, padx=4, pady=4)
        txt.insert("1.0", hand.raw_text if hand.raw_text else "(no raw text)")
        txt.configure(state="disabled")

        # Bottom buttons
        btn_bar = tk.Frame(popup, bg=self.theme["bg_input"], height=32)
        btn_bar.pack(fill="x", pady=(0, 4))

        def _copy():
            popup.clipboard_clear()
            popup.clipboard_append(hand.raw_text or "")
            close_btn.configure(text="Copied!")
            popup.after(1500, lambda: close_btn.configure(text="Close"))

        tk.Button(btn_bar, text="Copy Hand", bg=self.theme["bg_accent"], fg=self.theme["text"],
                  font=_F_DATA, relief="flat", padx=12, command=_copy
                  ).pack(side="left", padx=8)
        tk.Button(btn_bar, text="▶ Replay", bg=self.theme["bg_accent"], fg=self.theme["gold"],
                  font=(_FM, 10, "bold"), relief="flat", padx=12,
                  command=lambda: HandReplayerWindow(popup, hand, self.theme)
                  ).pack(side="left", padx=4)
        close_btn = tk.Button(btn_bar, text="Close", bg=self.theme["bg_accent"], fg=self.theme["text"],
                              font=_F_DATA, relief="flat", padx=12,
                              command=popup.destroy)
        close_btn.pack(side="right", padx=8)

    # ── HUD Popup Window ──────────────────────────────────────────────────
    def _open_hud_window(self):
        """Open a separate native Toplevel window for Player HUD / Station Detector."""

        hud = tk.Toplevel(self)
        hud.title("\u2660 Player HUD / Station Detector")
        hud.geometry("750x550")
        hud.configure(bg=self.theme["bg_input"])
        hud.attributes("-topmost", True)
        hud.focus_force()

        # Ensure player stats are computed
        if not self.player_stats:
            hands = self.importer.get_hands()
            if hands:
                self.player_stats = self.station_detector.analyze_players(hands)

        # Top bar
        top_bar = tk.Frame(hud, bg=self.theme["bg_accent"])
        top_bar.pack(fill="x")
        tk.Label(top_bar, text="\u2660 Player HUD / Station Detector \u2665",
                 bg=self.theme["bg_accent"], fg=self.theme["gold"], font=_F_TITLE).pack(side="left", padx=8, pady=6)

        def _copy_hud():
            if not self.player_stats:
                return
            lines = [f"{'Player':20s} {'Type':16s} {'VPIP':>6s} {'PFR':>6s} {'AF':>5s} {'F2CB':>6s} {'Hands':>6s}"]
            lines.append("-" * 68)
            for p in self.player_stats:
                lines.append(f"{p['name']:20s} {p['classification']:16s} {p['vpip']:5.1f}% {p['pfr']:5.1f}% "
                           f"{p['af']:5.2f} {p['fold_cbet']:5.1f}% {p['hands']:6d}")
            hud.clipboard_clear()
            hud.clipboard_append("\n".join(lines))

        tk.Button(top_bar, text="Copy Stats", bg=self.theme["bg_accent"], fg=self.theme["text"],
                  font=_F_DATA, relief="flat", padx=10,
                  command=_copy_hud).pack(side="right", padx=8, pady=4)

        # Search
        search_frame = tk.Frame(hud, bg=self.theme["bg_panel"])
        search_frame.pack(fill="x", padx=4, pady=2)
        tk.Label(search_frame, text="Search:", bg=self.theme["bg_panel"], fg=self.theme["text"],
                 font=_F_DATA).pack(side="left", padx=6)
        search_var = tk.StringVar()
        tk.Entry(search_frame, textvariable=search_var, bg=self.theme["bg_input"], fg=self.theme["text"],
                 font=_F_DATA, insertbackground=self.theme["text"],
                 relief="flat", width=25).pack(side="left", padx=4)

        # Type filter
        tk.Label(search_frame, text="  Type:", bg=self.theme["bg_panel"],
                 fg=self.theme["text"], font=_F_DATA).pack(side="left", padx=(8, 2))
        hud_type_var = tk.StringVar(value="All")
        type_values = ["All", "Fish", "Calling Station", "LAG", "TAG", "Nit", "Maniac", "Regular", "Unknown"]
        hud_type_menu = tk.OptionMenu(search_frame, hud_type_var, *type_values,
                                      command=lambda _: _populate(search_var.get(), hud_type_var.get()))
        hud_type_menu.configure(bg=self.theme["bg_accent"], fg=self.theme["text"],
                                font=_F_CAPTION, relief="flat", highlightthickness=0)
        hud_type_menu["menu"].configure(bg=self.theme["bg_card"], fg=self.theme["text"],
                                         font=_F_CAPTION)
        hud_type_menu.pack(side="left", padx=2)

        # Legend
        legend = tk.Frame(hud, bg=self.theme["bg_panel"])
        legend.pack(fill="x", padx=4, pady=1)
        type_badges = [("Calling Station", self.theme["red"]), ("Nit", self.theme["text_dim"]),
                       ("TAG", self.theme["green"]),
                       ("LAG", self.theme["yellow"]), ("Maniac", self.theme["red"]),
                       ("Fish", self.theme["red"])]
        for tname, tcolor in type_badges:
            tk.Label(legend, text=tname, bg=self.theme["bg_panel"], fg=tcolor,
                     font=(_FM, 9, "bold")).pack(side="left", padx=4)

        # Header
        header = tk.Frame(hud, bg=self.theme["bg_accent"])
        header.pack(fill="x", padx=4, pady=(2, 0))
        cols = ["Player", "Type", "VPIP", "PFR", "AF", "F2CB", "WTSD", "Hands"]
        col_widths = [18, 14, 7, 7, 6, 7, 7, 6]
        header_text = "  ".join(f"{c:<{w}}" for c, w in zip(cols, col_widths))
        tk.Label(header, text=header_text, bg=self.theme["bg_accent"], fg=self.theme["gold"],
                 font=(_FM, 10, "bold"), anchor="w").pack(fill="x", padx=6, pady=2)

        # Scrollable player list using Canvas + Text widget (lightweight)
        list_text = tk.Text(hud, bg=self.theme["bg_input"], fg=self.theme["text"], font=_F_DATA,
                            relief="flat", padx=6, pady=4, state="disabled",
                            selectbackground=self.theme["select_bg"], cursor="arrow")
        scrollbar = tk.Scrollbar(
            hud,
            command=list_text.yview,
            bg=self.theme["bg_card"],
            activebackground=self.theme["bg_hover"],
            troughcolor=self.theme["bg_base"],
            highlightthickness=0,
            bd=0,
            width=12,
        )
        xscrollbar = tk.Scrollbar(
            hud,
            orient="horizontal",
            command=list_text.xview,
            bg=self.theme["bg_card"],
            activebackground=self.theme["bg_hover"],
            troughcolor=self.theme["bg_base"],
            highlightthickness=0,
            bd=0,
            width=12,
        )
        list_text.configure(yscrollcommand=scrollbar.set, xscrollcommand=xscrollbar.set)
        scrollbar.pack(side="right", fill="y", padx=(0, 4), pady=4)
        xscrollbar.pack(side="bottom", fill="x", padx=(4, 4), pady=(0, 4))
        list_text.pack(fill="both", expand=True, padx=(4, 0), pady=4)

        # Define color tags
        type_color_map = {
            "Calling Station": self.theme["red"], "Nit": self.theme["text_dim"],
            "TAG": self.theme["green"],
            "LAG": self.theme["yellow"], "Maniac": self.theme["red"],
            "Fish": self.theme["red"],
            "Unknown": self.theme["text_dim"], "Regular": self.theme["text"],
        }
        for tname, tcolor in type_color_map.items():
            list_text.tag_configure(f"type_{tname}", foreground=tcolor)
        list_text.tag_configure("stat_good", foreground=self.theme["green"])
        list_text.tag_configure("stat_warn", foreground=self.theme["yellow"])
        list_text.tag_configure("stat_bad", foreground=self.theme["red"])
        list_text.tag_configure("dim", foreground=self.theme["text_dim"])

        def _populate(filter_text="", type_filter="All"):
            list_text.configure(state="normal")
            list_text.delete("1.0", "end")
            ft = filter_text.lower()
            count = 0
            for p in self.player_stats:
                if ft and ft not in p["name"].lower():
                    continue
                if type_filter != "All" and p["classification"] != type_filter:
                    continue
                if count >= 200:
                    break
                count += 1
                override = p.get("manual_type", "")
                type_display = p["classification"]
                if override:
                    type_display = f"{override}*"
                name_str = f"  {p['name']:<18s}"
                type_str = f"{type_display:<14s}"
                vpip_str = f"{p['vpip']:5.1f}%  "
                pfr_str = f"{p['pfr']:5.1f}%  "
                af_str = f"{p['af']:5.2f} "
                ftcb_str = f"{p['fold_cbet']:5.1f}%  "
                wtsd_str = f"{p['wtsd']:5.1f}%  "
                hands_str = f"{p['hands']:5d}\n"

                list_text.insert("end", name_str)
                list_text.insert("end", type_str, f"type_{p['classification']}")
                # Color VPIP
                vtag = "stat_bad" if p["vpip"] > 35 else "stat_good" if p["vpip"] > 25 else "dim"
                list_text.insert("end", vpip_str, vtag)
                # Color PFR
                ptag = "stat_good" if 12 <= p["pfr"] <= 22 else "stat_warn" if p["pfr"] < 12 else "stat_bad"
                list_text.insert("end", pfr_str, ptag)
                # Color AF
                atag = "stat_good" if 1.5 <= p["af"] <= 3.5 else "stat_warn" if p["af"] < 1.5 else "stat_bad"
                list_text.insert("end", af_str, atag)
                list_text.insert("end", ftcb_str, "stat_good" if p["fold_cbet"] > 60 else "stat_warn")
                list_text.insert("end", wtsd_str, "stat_good" if 25 <= p["wtsd"] <= 35 else "stat_warn")
                list_text.insert("end", hands_str, "dim")
            if count == 0:
                list_text.insert("end", "  No players found. Import hands first.")
            list_text.configure(state="disabled")

        _populate()

        def _on_search(*_):
            _populate(search_var.get(), hud_type_var.get())
        search_var.trace_add("write", _on_search)

        # Manual type override section
        override_frame = tk.Frame(hud, bg=self.theme["bg_panel"])
        override_frame.pack(fill="x", padx=4, pady=(0, 4))

        tk.Label(override_frame, text="Override Player Type:", bg=self.theme["bg_panel"],
                 fg=self.theme["gold"], font=(_FM, 10, "bold")).pack(side="left", padx=6)

        override_name_var = tk.StringVar()
        tk.Label(override_frame, text="Player:", bg=self.theme["bg_panel"],
                 fg=self.theme["text"], font=_F_DATA).pack(side="left", padx=(4, 2))
        override_entry = tk.Entry(override_frame, textvariable=override_name_var,
                                   bg=self.theme["bg_input"], fg=self.theme["text"],
                                   font=_F_DATA, insertbackground=self.theme["text"],
                                   relief="flat", width=18)
        override_entry.pack(side="left", padx=2)

        override_type_var = tk.StringVar(value="Fish")
        override_types = ["Fish", "Calling Station", "LAG", "TAG", "Nit", "Maniac", "Regular", "Whale", "Rec"]
        override_menu = tk.OptionMenu(override_frame, override_type_var, *override_types)
        override_menu.configure(bg=self.theme["bg_accent"], fg=self.theme["text"],
                                font=_F_CAPTION, relief="flat", highlightthickness=0)
        override_menu["menu"].configure(bg=self.theme["bg_card"], fg=self.theme["text"],
                                         font=_F_CAPTION)
        override_menu.pack(side="left", padx=4)

        def _set_override():
            pname = override_name_var.get().strip()
            ptype = override_type_var.get()
            if not pname:
                return
            self.db.set_manual_player_type(pname, ptype)
            # Update in-memory stats
            for p in self.player_stats:
                if p["name"] == pname:
                    p["manual_type"] = ptype
                    p["classification"] = ptype
                    break
            _populate(search_var.get(), hud_type_var.get())

        def _clear_override():
            pname = override_name_var.get().strip()
            if not pname:
                return
            self.db.set_manual_player_type(pname, "")
            for p in self.player_stats:
                if p["name"] == pname:
                    p["manual_type"] = ""
                    p["classification"] = p.get("auto_type", "Unknown")
                    break
            _populate(search_var.get(), hud_type_var.get())

        tk.Button(override_frame, text="Set Type", bg=self.theme["green"],
                  fg=self.theme["bg_base"], font=(_FM, 9, "bold"),
                  relief="flat", padx=8, command=_set_override).pack(side="left", padx=4)
        tk.Button(override_frame, text="Clear", bg=self.theme["red"],
                  fg=self.theme["text"], font=_F_CAPTION,
                  relief="flat", padx=6, command=_clear_override).pack(side="left", padx=2)

        # Click player name to auto-fill override
        def _on_player_click(event):
            idx = list_text.index(f"@{event.x},{event.y}")
            line = list_text.get(f"{idx} linestart", f"{idx} lineend").strip()
            if line:
                pname = line.split()[0] if line.split() else ""
                override_name_var.set(pname)
        list_text.bind("<Double-Button-1>", _on_player_click)

    # ── Leak Analysis tab ─────────────────────────────────────────────────
    def _update_leak_tab(self):
        s = self.current_stats
        if not s:
            return
        leak_fp = (
            s.get("vpip"), s.get("pfr"), s.get("af"), s.get("wtsd"), s.get("wsd"), s.get("cbet"),
            tuple((pos, tuple(pd.items())) for pos, pd in sorted(s.get("by_position", {}).items())),
            tuple((site, tuple(sd.items())) for site, sd in sorted(s.get("by_site", {}).items())),
            tuple(s.get("alerts", [])),
        )
        if leak_fp == self._leak_tab_fingerprint:
            return
        self._leak_tab_fingerprint = leak_fp

        stat_defs = [
            ("VPIP", f"{s['vpip']}%", self._stat_color(s["vpip"], 15, 22, 30)),
            ("PFR", f"{s['pfr']}%", self._stat_color(s["pfr"], 10, 20, 25)),
            ("AF", str(s["af"]), self._stat_color(s["af"], 1.5, 3.5, 4.5)),
            ("WTSD", f"{s['wtsd']}%", self._stat_color(s["wtsd"], 20, 32, 40)),
            ("W$SD", f"{s['wsd']}%", self.theme["green"] if s["wsd"] >= 50 else self.theme["yellow"] if s["wsd"] >= 45 else self.theme["red"]),
            ("C-Bet", f"{s['cbet']}%", self._stat_color(s["cbet"], 50, 70, 80)),
        ]
        for name, val, color in stat_defs:
            lbl = self._leak_stat_cards.get(name)
            if lbl:
                lbl.configure(text=val, text_color=color)

        self.leak_alerts_text.configure(state="normal")
        self.leak_alerts_text.delete("1.0", "end")
        for color, msg in s.get("alerts", []):
            icon = {"green": "\u2705", "yellow": "\u26a0\ufe0f", "red": "\u274c"}.get(color, "")
            self.leak_alerts_text.insert("end", f"  {icon}  {msg}\n")
        self.leak_alerts_text.configure(state="disabled")

        self.leak_pos_text.configure(state="normal")
        self.leak_pos_text.delete("1.0", "end")
        self.leak_pos_text.insert("end", f"  {'Pos':4s} {'Hands':>6s} {'VPIP':>7s} {'PFR':>7s}\n")
        self.leak_pos_text.insert("end", "  " + "-" * 30 + "\n")
        for pos in ["EP", "MP", "CO", "BTN", "SB", "BB"]:
            pd = s.get("by_position", {}).get(pos)
            if pd:
                self.leak_pos_text.insert("end",
                                           f"  {pos:4s} {pd['total']:6d} {pd['vpip']:6.1f}% {pd['pfr']:6.1f}%\n")
        self.leak_pos_text.configure(state="disabled")

        self.leak_site_text.configure(state="normal")
        self.leak_site_text.delete("1.0", "end")
        for site, sd in s.get("by_site", {}).items():
            chip_note = ""
            if sd.get("chip_net"):
                chip_note = f" | Chips {sd['chip_net']:+,.0f}"
            self.leak_site_text.insert(
                "end",
                f"  {site}: {sd['total']} hands | VPIP {sd['vpip']}% | "
                f"PFR {sd['pfr']}% | Cash ${sd['net']:+.2f}{chip_note}\n",
            )
        self.leak_site_text.configure(state="disabled")

        self._update_leak_graphs()

    def _update_leak_graphs(self):
        """Render positional VPIP/PFR bar chart on leak tab."""
        if not hasattr(self, 'leak_fig'):
            return
        t = self.theme
        s = self.current_stats
        pos_stats = s.get("by_position", {}) if s else {}
        graph_fp = tuple(
            (pos, pd.get("vpip"), pd.get("pfr"))
            for pos, pd in sorted(pos_stats.items())
        )
        if graph_fp == self._leak_graph_fingerprint:
            return
        self._leak_graph_fingerprint = graph_fp
        self.leak_fig.clear()
        self.leak_fig.patch.set_facecolor(t["graph_bg"])

        ax = self.leak_fig.add_subplot(111)
        ax.set_facecolor(t["graph_face"])
        ax.tick_params(colors=t["text_dim"], labelsize=8)
        ax.set_title("VPIP / PFR by Position", color=t["gold"], fontsize=10, fontweight="bold")
        for spine in ax.spines.values():
            spine.set_color(t["graph_grid"])

        if pos_stats:
            positions = list(pos_stats.keys())
            vpip_vals = [pos_stats[p].get("vpip", 0) for p in positions]
            pfr_vals = [pos_stats[p].get("pfr", 0) for p in positions]
            import numpy as np
            x = np.arange(len(positions))
            w = 0.35
            ax.bar(x - w/2, vpip_vals, w, label="VPIP", color=t["graph_bar1"], alpha=0.85)
            ax.bar(x + w/2, pfr_vals, w, label="PFR", color=t["graph_bar2"], alpha=0.85)
            ax.set_xticks(x)
            ax.set_xticklabels(positions, color=t["text_dim"], fontsize=9)
            ax.set_ylabel("%", color=t["text_dim"], fontsize=9)
            ax.legend(facecolor=t["graph_face"], edgecolor=t["graph_grid"],
                      labelcolor=t["text_dim"], fontsize=8)
        else:
            ax.text(0.5, 0.5, "No positional data", ha="center", va="center",
                    color=t["text_dim"], fontsize=12)
        ax.grid(True, axis="y", color=t["graph_grid"], alpha=0.3, linewidth=0.5)

        self.leak_fig.tight_layout(pad=1.5)
        self.leak_canvas.draw()

    def _stat_color(self, val, low, high_good, high_bad):
        if val < low:
            return self.theme["red"]
        if val > high_bad:
            return self.theme["red"]
        if low <= val <= high_good:
            return self.theme["green"]
        return self.theme["yellow"]

    # ── AI Summary ────────────────────────────────────────────────────────
    def _generate_summary(self):
        source = self.ai_source_var.get() if hasattr(self, 'ai_source_var') else "All Hands"
        if source == "Selected Hands":
            hands = self._get_selected_hands()
            desc = f"Selected Hands ({len(hands)})"
        elif source == "Filtered Hands":
            hands = self._get_filtered_hands()
            desc = self._get_filter_description()
        else:
            hands = self.importer.get_hands()
            desc = "All Hands"

        if hasattr(self, 'ai_filter_label'):
            self.ai_filter_label.configure(text=f"Source: {desc}")

        if not hands:
            self.ai_text.configure(state="normal")
            self.ai_text.delete("1.0", "end")
            self.ai_text.insert("end", "No hands match current selection/filters.")
            return
        stats = self.leak_engine.analyze(hands)
        summary = self.summary_gen.generate(stats, hands)

        # Prepend filter info header
        header = f"{'='*60}\nANALYSIS SOURCE: {desc}\nHands Analyzed: {len(hands)}\n{'='*60}\n\n"
        self.ai_text.configure(state="normal")
        self.ai_text.delete("1.0", "end")
        self.ai_text.insert("end", header + summary)

    # ── Chat methods ──────────────────────────────────────────────────────────
    def _send_chat(self):
        msg = self.ai_chat_var.get().strip()
        if not msg:
            return
        if not self.ai_processor or not self.ai_processor.is_available():
            self._chat_append("⚠️ No AI provider. Set OPENAI_API_KEY env var.\n")
            return

        # Build player context from current stats
        hands = self._get_filtered_hands()
        if hands and not getattr(self, "_chat_context_set", False):
            from collections import Counter
            n = len(hands)
            won = sum(1 for h in hands if (h.hero_won or 0) > 0)
            ctx = (f"{n} hands loaded | Win rate: {won/n*100:.0f}% | "
                   f"Site: {hands[0].site if hands else 'N/A'} | "
                   f"Hero: {hands[0].hero_cards if hands else 'N/A'}")
            self.ai_processor.set_context(ctx)
            self._chat_context_set = True

        self.ai_chat_var.set("")
        self._chat_append(f"You: {msg}\n")
        self._ai_chat_entry.configure(state="disabled")

        def _ask():
            try:
                reply = self.ai_processor.chat(msg)
            except Exception as e:
                reply = f"[Error: {e}]"
            self.after(0, lambda: self._chat_append(f"Coach: {reply}\n\n"))
            self.after(0, lambda: self._ai_chat_entry.configure(state="normal"))

        import threading
        threading.Thread(target=_ask, daemon=True).start()

    def _chat_append(self, text):
        self.ai_chat_display.configure(state="normal")
        self.ai_chat_display.insert("end", text)
        self.ai_chat_display.see("end")
        self.ai_chat_display.configure(state="disabled")

    def _clear_chat(self):
        if self.ai_processor:
            self.ai_processor.clear_chat()
        self._chat_context_set = False
        self.ai_chat_display.configure(state="normal")
        self.ai_chat_display.delete("1.0", "end")
        self.ai_chat_display.configure(state="disabled")

    def _update_ai_status(self):
        """Update the AI engine status indicators."""
        if self.ai_processor:
            try:
                status = self.ai_processor.get_status()
                avail = status.get("llm_available", False)
                provider = status.get("llm_provider", "?")
                embeddings = status.get("embeddings", "?")
                vec_count = status.get("vector_store_count", 0)
                if avail:
                    self.ai_engine_status.configure(
                        text=f"\u2b22 AI Engine: {provider} \u2713 connected",
                        text_color=self.theme["green"])
                else:
                    self.ai_engine_status.configure(
                        text=f"\u2b22 AI Engine: {provider} (fallback mode)",
                        text_color=self.theme["yellow"])
                self.ai_vector_label.configure(
                    text=f"Embeddings: {embeddings} | Vectors: {vec_count}")
            except Exception as e:
                self.ai_engine_status.configure(
                    text=f"\u2b22 AI Engine: error ({e})",
                    text_color=self.theme["red"])
        else:
            self.ai_engine_status.configure(
                text="\u2b22 AI Engine: not available (install ai_processor)",
                text_color=self.theme["text_dim"])

    def _ai_analyze_selected(self):
        """Run AI analysis on selected/filtered hands via AIProcessor."""
        if not self.ai_processor:
            self._set_status("AI Engine not available — check ai_processor.py")
            return

        hands = self._get_filtered_hands()
        if not hands:
            self._set_status("No hands to analyze")
            return

        provider = self.ai_provider_var.get() if hasattr(self, "ai_provider_var") else None
        self.ai_text.delete("1.0", "end")
        self.ai_text.insert("1.0", f"Analyzing {len(hands)} hands with AI engine...\n")
        self.ai_footer_label.configure(text=f"Processing {len(hands)} hands...")

        def do_analysis():
            session_id = f"session_{int(time.time())}"
            results    = []
            hero_name  = self.settings.get("hero_names", {}).get(
                hands[0].site if hands else "", "Hero") or "Hero"

            # ── Compute real session stats from filtered hands (PT4-style) ────
            total = len(hands)
            vpip_hands = pfr_hands = won_sd = went_sd = 0
            pos_profit: dict = {}
            for h in hands:
                raw_acts = [a for s in h.streets for a in s.get("actions", [])]
                hero_pre = [a for a in raw_acts
                            if a.get("player") == hero_name and a.get("street") == "PREFLOP"]
                did_vpip = any(a["action"] in ("call", "raise", "bet") for a in hero_pre)
                did_pfr  = any(a["action"] in ("raise", "bet") for a in hero_pre)
                if did_vpip: vpip_hands += 1
                if did_pfr:  pfr_hands  += 1
                all_streets = {a.get("street") for a in raw_acts if a.get("player") == hero_name}
                if "SHOWDOWN" in all_streets or len(all_streets) >= 3:
                    went_sd += 1
                    if (h.hero_won or 0) > 0:
                        won_sd += 1
                pos = h.hero_position or "?"
                pos_profit[pos] = pos_profit.get(pos, 0) + (h.hero_won or 0)

            real_stats = {
                "hands_analyzed": total,
                "hero":           hero_name,
                "VPIP":           f"{vpip_hands/total*100:.1f}%" if total else "N/A",
                "PFR":            f"{pfr_hands/total*100:.1f}%"  if total else "N/A",
                "WTSD":           f"{went_sd/total*100:.1f}%"    if total else "N/A",
                "W_SD":           f"{won_sd/went_sd*100:.1f}%"   if went_sd else "N/A",
                "net_profit":     sum(h.hero_won or 0 for h in hands),
                "by_position":    pos_profit,
            }

            for i, h in enumerate(hands[:50]):  # Cap at 50 for performance
                raw = h.raw_text or (
                    f"Hand {h.hand_id} | {h.site} | {h.game_type}\n"
                    f"Hero: {hero_name} | Cards: {h.hero_cards} | Pos: {h.hero_position}\n"
                    f"Board: {' '.join(h.board_cards or [])} | Pot: {h.pot} | Won: {h.hero_won}\n"
                )
                try:
                    result = self.ai_processor.analyze_hand(
                        raw_text=raw, hero_name=hero_name, hand_id=str(h.hand_id)) or {}
                except Exception as e:
                    result = {"summary": f"Error: {e}"}

                # Attach display metadata so renderer always has real values
                result["_hand_id"]  = h.hand_id
                result["_cards"]    = h.hero_cards or "?"
                result["_position"] = h.hero_position or "?"
                result["_won"]      = h.hero_won or 0
                result["_pot"]      = h.pot or 0
                results.append(result)

                if (i + 1) % 5 == 0:
                    self.after(0, lambda n=i+1: self.ai_footer_label.configure(
                        text=f"Processed {n}/{min(len(hands), 50)} hands..."))

            # Session summary with REAL stats
            summary = self.ai_processor.summarize_session(
                session_id, hand_results=results, stats=real_stats)

            def show_results():
                self.ai_text.delete("1.0", "end")
                self.ai_text.insert("1.0", f"AI ANALYSIS — {len(results)} hands\n")
                self.ai_text.insert("end", f"Session: {session_id}\n")
                self.ai_text.insert("end", "=" * 60 + "\n\n")

                # Live stats header (PT4-style)
                self.ai_text.insert("end", "PLAYER STATS\n" + "-" * 40 + "\n")
                for k, v in real_stats.items():
                    if k not in ("by_position", "hero"):
                        self.ai_text.insert("end", f"  {k:<18} {v}\n")
                pos_p = real_stats.get("by_position", {})
                if pos_p:
                    self.ai_text.insert("end", "  By Position:\n")
                    for pos, profit in sorted(pos_p.items(), key=lambda x: -abs(x[1])):
                        self.ai_text.insert("end", f"    {pos:<8} {profit:+.2f}\n")
                self.ai_text.insert("end", "\n")

                # AI session summary
                self.ai_text.insert("end", "SESSION COACHING REPORT\n" + "-" * 40 + "\n")
                sum_text = summary.get("summary", "") if isinstance(summary, dict) else str(summary)
                self.ai_text.insert("end", sum_text + "\n\n")

                # Tag frequency
                tags = summary.get("tag_frequency", {}) if isinstance(summary, dict) else {}
                if tags:
                    self.ai_text.insert("end", "LEAK TAGS\n" + "-" * 40 + "\n")
                    for tag, cnt in sorted(tags.items(), key=lambda x: -x[1])[:10]:
                        self.ai_text.insert("end", f"  {tag}: {cnt}\n")
                    self.ai_text.insert("end", "\n")

                # Individual hand results — full PT4-style detail
                self.ai_text.insert("end", "INDIVIDUAL HAND ANALYSES\n" + "-" * 40 + "\n")
                for r in results:
                    hid      = r.get("_hand_id", "?")
                    cards    = r.get("_cards",    "?")
                    pos      = r.get("_position", "?")
                    won      = r.get("_won",       0)
                    pot      = r.get("_pot",       0)
                    conf     = r.get("confidence", 0.5)
                    style    = r.get("play_style", "")
                    ev       = r.get("ev_estimate", "")
                    summary_ = r.get("summary", "")
                    mistakes = r.get("mistakes_found", 0)
                    hand_tags= ", ".join(r.get("tags", [])) if r.get("tags") else ""
                    result_s = f"+{won:.2f}" if won >= 0 else f"{won:.2f}"

                    self.ai_text.insert("end", f"\n{'─'*55}\n")
                    self.ai_text.insert("end",
                        f"Hand: {hid}  [{cards}]  Pos: {pos}  "
                        f"Pot: {pot:.2f}  Result: {result_s}\n")
                    if style or ev:
                        self.ai_text.insert("end",
                            f"Style: {style or 'N/A'}  |  EV: {ev or 'N/A'}  |  "
                            f"Mistakes: {mistakes}  |  Conf: {conf:.0%}\n")
                    if summary_:
                        self.ai_text.insert("end", f"  → {summary_}\n")
                    if hand_tags:
                        self.ai_text.insert("end", f"  Tags: {hand_tags}\n")

                deep  = sum(1 for r in results if r.get("play_style"))
                errs  = sum(1 for r in results if "Error" in r.get("summary",""))
                self.ai_footer_label.configure(
                    text=f"Done — {len(results)} hands  |  "
                         f"{deep} analyzed  |  {errs} errors")
                self._update_ai_status()

            self.after(0, show_results)

        threading.Thread(target=do_analysis, daemon=True).start()

    def _ai_find_similar(self):
        """Find hands similar to the currently selected hand."""
        if not self.ai_processor:
            self._set_status("AI Engine not available")
            return

        selected = self._get_selected_hands()
        if not selected:
            self._set_status("Select a hand first in the Hands tab")
            return

        hand = selected[0]
        hand_json = {
            "hand_id": hand.hand_id,
            "hero_cards": hand.hero_cards or "",
            "hero_position": hand.hero_position or "",
            "board": " ".join(hand.board_cards) if hand.board_cards else "",
            "pot_size": hand.pot or 0,
            "hero_won": hand.hero_won or 0,
            "site": hand.site or "",
        }

        similar = self.ai_processor.find_similar_hands(hand_json, k=10)

        self.ai_text.delete("1.0", "end")
        self.ai_text.insert("1.0", f"SIMILAR HANDS to {hand.hand_id}\n")
        self.ai_text.insert("end", f"Hero: [{hand.hero_cards}] | Board: {' '.join(hand.board_cards or [])}\n")
        self.ai_text.insert("end", "=" * 60 + "\n\n")

        if not similar:
            self.ai_text.insert("end", "No similar hands found. Run AI analysis first to build the vector index.\n")
        else:
            for i, item in enumerate(similar, 1):
                hid = item["hand_id"]
                score = item["similarity"]
                meta = item.get("metadata", {})
                tags = ", ".join(meta.get("tags", [])) or "none"
                self.ai_text.insert("end",
                    f"  {i:2d}. {hid}  similarity: {score:.3f}  "
                    f"pot: {meta.get('pot_size', 0):,.0f}  "
                    f"result: {meta.get('hero_won', 0):+,.0f}  "
                    f"tags: {tags}\n")

        self.ai_footer_label.configure(text=f"Found {len(similar)} similar hands")

    def _analyze_filtered(self):
        """Analyze currently filtered hands in the AI tab."""
        if hasattr(self, 'ai_source_var'):
            self.ai_source_var.set("Filtered Hands")
        self.tabview.set("AI / GTO")
        self._generate_summary()

    def _export_filtered(self):
        """Export filtered hands to file (txt/csv/json)."""
        hands = self._get_filtered_hands()
        if not hands:
            self._set_status("No hands match filters")
            return

        path = filedialog.asksaveasfilename(
            title="Export Filtered Hands",
            defaultextension=".txt",
            filetypes=[("Text file", "*.txt"), ("CSV file", "*.csv"),
                       ("JSON file", "*.json"), ("All files", "*.*")]
        )
        if not path:
            return

        try:
            ext = os.path.splitext(path)[1].lower()
            filter_desc = self._get_filter_description()
            stats = self.leak_engine.analyze(hands)

            if ext == ".csv":
                lines = ["hand_id,date,site,type,cards,position,result,pot,ev_diff,tags"]
                for h in hands:
                    dt = h.date.strftime("%Y-%m-%d %H:%M") if h.date else ""
                    game = "Tournament" if h.is_tournament else "Cash"
                    ev = self.ev_calculator.calc_ev_diff(h, self.settings)
                    tags = ";".join(self.db.get_tags(h.hand_id) or [])
                    lines.append(f"{h.hand_id},{dt},{h.site},{game},{h.hero_cards},"
                                 f"{h.hero_position},{h.hero_won:.2f},{h.pot:.2f},{ev:.2f},{tags}")
                with open(path, "w", encoding="utf-8") as f:
                    f.write("\n".join(lines))

            elif ext == ".json":
                data = {
                    "export_date": datetime.now().isoformat(),
                    "filters": filter_desc,
                    "total_hands": len(hands),
                    "stats": stats,
                    "hands": []
                }
                for h in hands:
                    data["hands"].append({
                        "hand_id": h.hand_id,
                        "date": h.date.isoformat() if h.date else None,
                        "site": h.site,
                        "is_tournament": h.is_tournament,
                        "tournament_id": h.tournament_id,
                        "hero_cards": h.hero_cards,
                        "position": h.hero_position,
                        "result": h.hero_won,
                        "pot": h.pot,
                        "board": " ".join(h.board_cards) if h.board_cards else "",
                        "tags": self.db.get_tags(h.hand_id),
                        "raw_text": h.raw_text or "",
                    })
                with open(path, "w", encoding="utf-8") as f:
                    json.dump(data, f, indent=2, default=str)

            else:  # .txt or any other
                summary = self.summary_gen.generate(stats, hands)
                header = (f"{'='*60}\n"
                          f"POKER HAND TRACKER — FILTERED EXPORT\n"
                          f"{'='*60}\n"
                          f"Filters: {filter_desc}\n"
                          f"Hands: {len(hands)}\n"
                          f"Export Date: {datetime.now().strftime('%Y-%m-%d %H:%M')}\n"
                          f"{'='*60}\n\n")
                raw_section = "\n\n" + "="*60 + "\nRAW HAND HISTORIES\n" + "="*60 + "\n\n"
                raw_texts = []
                for h in hands:
                    if h.raw_text:
                        tags = self.db.get_tags(h.hand_id) or []
                        tag_line = f"[Tags: {', '.join(tags)}]\n" if tags else ""
                        raw_texts.append(tag_line + h.raw_text)
                with open(path, "w", encoding="utf-8") as f:
                    f.write(header + summary + raw_section + "\n\n".join(raw_texts))

            self._set_status(f"Exported {len(hands)} hands to {os.path.basename(path)}")
        except Exception as e:
            self._set_status(f"Export failed: {e}")

    def _save_summary_as(self):
        """Save AI analysis with file type picker."""
        text = self.ai_text.get("1.0", "end").strip()
        if not text:
            self._set_status("Nothing to save — generate an analysis first")
            return
        path = filedialog.asksaveasfilename(
            title="Save Analysis",
            defaultextension=".txt",
            filetypes=[("Text file", "*.txt"), ("Markdown", "*.md"), ("All files", "*.*")]
        )
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(text)
            self._set_status(f"Analysis saved to {os.path.basename(path)}")
        except Exception as e:
            self._set_status(f"Save failed: {e}")

    def _copy_summary(self):
        try:
            text = self.ai_text.get("1.0", "end").strip()
            if text:
                self.clipboard_clear()
                self.clipboard_append(text)
                self._set_status("Summary copied to clipboard!")
        except Exception:
            self._set_status("Failed to copy to clipboard")

    def _save_summary(self):
        text = self.ai_text.get("1.0", "end").strip()
        if not text:
            self._set_status("Nothing to save — generate a summary first")
            return
        outpath = r"C:\poker-build\ai_summary.txt"
        try:
            with open(outpath, "w", encoding="utf-8") as f:
                f.write(text)
            self._set_status(f"Summary saved to {outpath}")
        except Exception as e:
            self._set_status(f"Save failed: {e}")

    # ── GTO Wizard Export ─────────────────────────────────────────────────
    def _export_gto_wizard(self):
        source = self.ai_source_var.get() if hasattr(self, 'ai_source_var') else "All Hands"
        if source == "Selected Hands":
            hands = self._get_selected_hands()
        elif source == "Filtered Hands":
            hands = self._get_filtered_hands()
        else:
            hands = self.importer.get_hands()
        if not hands:
            self._set_status("No hands to export")
            return
        output_parts = []
        for h in hands:
            if not h.raw_text:
                continue
            if h.site in ("ACR", "BetACR"):
                output_parts.append(h.raw_text)
            elif h.site == "CoinPoker":
                output_parts.append(self._convert_coinpoker_to_pokerstars(h))
            else:
                output_parts.append(h.raw_text)
        export_path = r"C:\poker-build\gto_wizard_export.txt"
        try:
            with open(export_path, "w", encoding="utf-8") as f:
                f.write("\n\n\n".join(output_parts))
            self._set_status(
                f"Exported {len(output_parts)} hands to {export_path}")
        except Exception as e:
            self._set_status(f"Export failed: {e}")

    def _convert_coinpoker_to_pokerstars(self, hand):
        """Convert CoinPoker hand history to PokerStars-compatible format."""
        text = hand.raw_text
        lines = text.split("\n")
        out = []
        for i, line in enumerate(lines):
            if i == 0:
                converted = line.replace("CoinPoker Hand #", "PokerStars Hand #")
                converted = converted.replace("\u20ae", "$")
                if " ET" not in converted and " UTC" not in converted:
                    converted = converted.rstrip() + " ET"
                out.append(converted)
            else:
                out.append(line.replace("\u20ae", "$"))
        return "\n".join(out)

    # ── Players / HUD tab ─────────────────────────────────────────────────
    def _compute_players_bg(self):
        """Run heavy player analysis off the GUI thread and persist to DB."""
        hands = self.importer.get_hands()
        stats = self.station_detector.analyze_players(hands)
        stats = self.station_detector.apply_manual_overrides(stats, self.db)
        self.player_stats = stats
        # Persist player types to database
        for p in stats:
            try:
                self.db.save_player_type(
                    name=p["name"], auto_type=p.get("auto_type", p["classification"]),
                    hands=p["hands"], vpip=p["vpip"], pfr=p["pfr"],
                    af=p["af"], fold_cbet=p["fold_cbet"], wtsd=p["wtsd"])
            except Exception:
                pass

    # ── Settings ──────────────────────────────────────────────────────────
    def _refresh_dir_list(self):
        self.dir_listbox.configure(state="normal")
        self.dir_listbox.delete("1.0", "end")
        for entry in self.settings.get("scan_dirs", []):
            self.dir_listbox.insert("end", f"  [{entry['site']}]  {entry['path']}\n")

    def _add_dir(self):
        path = self.new_dir_var.get().strip()
        site = self.new_dir_site_var.get()
        if not path:
            self._set_status("Enter or Browse a folder path first.")
            return
        self.settings.setdefault("scan_dirs", []).append({"path": path, "site": site})
        self._refresh_dir_list()
        self.new_dir_var.set("")
        save_settings(self.settings)
        self.importer.update_settings(self.settings)
        self._set_status(f"Added [{site}] {path}")

    def _browse_dir(self):
        """Open a folder picker and set the path entry in the Add Directory row."""
        path = filedialog.askdirectory(title="Select Hand History Directory")
        if path:
            # Normalise to Windows backslash style
            self.new_dir_var.set(os.path.normpath(path))

    def _remove_dir(self):
        dirs = self.settings.get("scan_dirs", [])
        if dirs:
            dirs.pop()
            self._refresh_dir_list()

    def _save_settings(self):
        self.settings["hero_names"]["CoinPoker"] = self.hero_cp_var.get().strip()
        if hasattr(self, "hero_bacr_var"):
            self.settings["hero_names"]["BetACR"] = self.hero_bacr_var.get().strip()
        if hasattr(self, "hero_gg_var"):
            self.settings["hero_names"]["GGPoker"] = self.hero_gg_var.get().strip()
        if hasattr(self, "hero_rp_var"):
            self.settings["hero_names"]["ReplayPoker"] = self.hero_rp_var.get().strip()

        # API keys
        if hasattr(self, "openai_key_var"):
            self.settings["openai_api_key"] = self.openai_key_var.get().strip()
        if hasattr(self, "anthropic_key_var"):
            self.settings["anthropic_api_key"] = self.anthropic_key_var.get().strip()
        self.settings["auto_refresh"] = self.auto_refresh_var.get()
        try:
            self.settings["refresh_interval"] = int(self.interval_var.get())
        except ValueError:
            self.settings["refresh_interval"] = 5

        # Live HUD settings
        self.settings["live_hud_enabled"] = self.hud_enabled_var.get()
        self.settings["hud_opacity"] = round(self.hud_opacity_var.get(), 2)
        self.settings["hud_seat_layout"] = self.hud_layout_var.get()
        self.settings["hud_density"] = self.hud_density_var.get()
        self.settings["hud_site_preset"] = self.hud_site_preset_var.get()
        self.settings["hud_anchor"] = self.hud_anchor_var.get()
        try:
            self.settings["hud_offset_x"] = int(self.hud_offset_x_var.get())
        except ValueError:
            self.settings["hud_offset_x"] = 0
        try:
            self.settings["hud_offset_y"] = int(self.hud_offset_y_var.get())
        except ValueError:
            self.settings["hud_offset_y"] = 0
        try:
            self.settings["hud_badge_scale"] = float(self.hud_badge_scale_var.get())
        except ValueError:
            self.settings["hud_badge_scale"] = 1.5
        try:
            self.settings["hud_edge_margin_pct"] = float(self.hud_edge_margin_var.get()) / 100.0
        except ValueError:
            self.settings["hud_edge_margin_pct"] = 0.12

        self.settings = normalize_settings(self.settings)
        self._refresh_dir_list()
        save_settings(self.settings)
        self.importer.update_settings(self.settings)
        self._set_status("Settings saved!")

        # Reinitialise AI processor with new keys
        if self.ai_processor:
            self.ai_processor._settings = self.settings
            self.ai_processor._openai_client = None
            self.ai_processor._anthropic_client = None
            self.ai_processor._active_provider = None
            self.ai_processor._init()
        self.after(200, self._update_ai_status)

        if self.settings["auto_refresh"]:
            self.importer.stop_watcher()
            self.importer.start_watcher(callback=self._watcher_callback)
        else:
            self.importer.stop_watcher()

    def _current_hud_profile_payload(self):
        site = self.hud_profile_site_var.get() if hasattr(self, "hud_profile_site_var") else None
        existing_profile = {}
        if site:
            existing_profile = dict(self.settings.get("hud_site_profiles", {}).get(site, {}))
        try:
            offset_x = int(self.hud_offset_x_var.get())
        except ValueError:
            offset_x = 0
        try:
            offset_y = int(self.hud_offset_y_var.get())
        except ValueError:
            offset_y = 0
        return {
            "anchor": self.hud_anchor_var.get(),
            "offset_x": offset_x,
            "offset_y": offset_y,
            "density": self.hud_density_var.get(),
            "seat_layout": self.hud_layout_var.get(),
            "badge_offsets": dict(existing_profile.get("badge_offsets", {})),
        }

    def _apply_hud_profile_to_controls(self, profile, *, update_status=True):
        self.hud_anchor_var.set(profile.get("anchor", "top-left"))
        self.hud_offset_x_var.set(str(profile.get("offset_x", 0)))
        self.hud_offset_y_var.set(str(profile.get("offset_y", 0)))
        self.hud_density_var.set(profile.get("density", "standard"))
        self.hud_layout_var.set(profile.get("seat_layout", "auto"))
        if update_status:
            self.hud_profile_status.configure(text=f"Loaded profile for {self.hud_profile_site_var.get()}")

    def _save_hud_profile_target(self):
        site = self.hud_profile_site_var.get()
        self.settings.setdefault("hud_site_profiles", {})[site] = self._current_hud_profile_payload()
        self.settings = normalize_settings(self.settings)
        save_settings(self.settings)
        self.hud_profile_status.configure(text=f"Saved profile for {site}")
        if self.live_hud_overlay and self._live_hud_on:
            self.live_hud_overlay.settings = self.settings
            self.live_hud_overlay.update_hand(
                self.live_hud_overlay._last_seat_map,
                self.live_hud_overlay._last_max_seats,
                self.live_hud_overlay._current_site,
            )

    def _load_hud_profile_target(self):
        site = self.hud_profile_site_var.get()
        profile = self.settings.get("hud_site_profiles", {}).get(site)
        if not profile:
            self.hud_profile_status.configure(text=f"No saved profile for {site}")
            return
        self._apply_hud_profile_to_controls(profile)

    def _clear_hud_profile_target(self):
        site = self.hud_profile_site_var.get()
        profiles = self.settings.setdefault("hud_site_profiles", {})
        if site in profiles:
            profiles.pop(site, None)
            self.settings = normalize_settings(self.settings)
            save_settings(self.settings)
            self.hud_profile_status.configure(text=f"Cleared profile for {site}")
        else:
            self.hud_profile_status.configure(text=f"No saved profile for {site}")

    def _clear_hud_badge_offsets_target(self):
        site = self.hud_profile_site_var.get()
        profile = self.settings.get("hud_site_profiles", {}).get(site)
        if not profile:
            self.hud_profile_status.configure(text=f"No saved profile for {site}")
            return
        if not profile.get("badge_offsets"):
            self.hud_profile_status.configure(text=f"No badge nudges saved for {site}")
            return
        updated_profile = dict(profile)
        updated_profile["badge_offsets"] = {}
        self.settings.setdefault("hud_site_profiles", {})[site] = updated_profile
        self.settings["hud_slot_positions"] = {}
        self.settings = normalize_settings(self.settings)
        save_settings(self.settings)
        self.hud_profile_status.configure(text=f"Cleared seat positions for {site}")
        if self.live_hud_overlay and self._live_hud_on:
            self.live_hud_overlay.settings = self.settings
            self.live_hud_overlay.update_hand(
                self.live_hud_overlay._last_seat_map,
                self.live_hud_overlay._last_max_seats,
                self.live_hud_overlay._current_site,
            )

    def _handle_hud_lock_changed(self, settings):
        self.settings = normalize_settings(settings)
        save_settings(self.settings)

    def _handle_hud_profile_changed(self, site, profile):
        self.settings.setdefault("hud_site_profiles", {})[site] = profile
        self.settings = normalize_settings(self.settings)
        save_settings(self.settings)
        if hasattr(self, "hud_profile_status"):
            self.hud_profile_status.configure(text=f"Dragged and saved profile for {site}")
        if hasattr(self, "hud_profile_site_var") and self.hud_profile_site_var.get() == site:
            self._apply_hud_profile_to_controls(profile, update_status=False)

    def _toggle_hud_layout_mode(self):
        if not self._live_hud_on or not getattr(self, '_hud_overlays', {}):
            self._set_status("Start Live HUD first, then unlock the HUD to drag seat badges.")
            return
        self._hud_layout_mode = not self._hud_layout_mode
        for overlay in getattr(self, '_hud_overlays', {}).values():
            overlay.set_layout_mode(self._hud_layout_mode)
        if self._hud_layout_mode:
            self.hud_layout_btn.configure(text="Lock HUD", fg_color=self.theme["orange"], text_color=self.theme["bg_base"])
            self._set_status("Drag mode — drag seat badges, then Lock HUD for click-through play.")
        else:
            self.hud_layout_btn.configure(text="Unlock HUD", fg_color=self.theme["gold"], text_color=self.theme["bg_base"])
            self._set_status("HUD locked — overlay is click-through for poker play.")

    # ── Live HUD Actions ──────────────────────────────────────────────────
    def _toggle_live_hud(self):
        if self._live_hud_on:
            self._stop_live_hud()
        else:
            self._start_live_hud()

    def _start_live_hud(self):
        if not HAS_WIN32:
            msg = (
                "Live HUD requires pywin32.\n"
                "Run: pip install pywin32\n"
                "Then restart LeakSnipe Live HUD."
            )
            logging.error("HUD FAIL: HAS_WIN32 is False — pywin32 not available")
            _show_hud_error("LeakSnipe Live HUD", msg)
            self._set_status("Live HUD requires pywin32 — run: pip install pywin32")
            return

        logging.info(f"HUD START: HAS_WIN32={HAS_WIN32}, cwd={os.getcwd()}")
        self._live_hud_on = True
        self._hud_overlays: dict = {}  # hwnd → LiveHUDOverlay
        self._prewarm_hud_stats_async()

        if hasattr(self, "live_hud_btn"):
            self.live_hud_btn.configure(
                fg_color=self.theme["green"],
                text_color=self.theme["bg_base"],
                text="⬡ HUD ON",
            )
        self._hud_layout_mode = not bool(self.settings.get("hud_locked", True))
        if hasattr(self, "hud_layout_btn"):
            if self._hud_layout_mode:
                self.hud_layout_btn.configure(
                    text="Lock HUD", fg_color=self.theme["orange"], text_color=self.theme["bg_base"]
                )
            else:
                self.hud_layout_btn.configure(
                    text="Unlock HUD", fg_color=self.theme["gold"], text_color=self.theme["bg_base"]
                )

        def _get_or_create_overlay(hwnd):
            if hwnd not in self._hud_overlays:
                overlay = LiveHUDOverlay(
                    self, self.theme, self.db, self.settings,
                    on_profile_changed=self._handle_hud_profile_changed,
                    on_quit=self._request_hud_quit if self._hud_only else None,
                    on_lock_changed=self._handle_hud_lock_changed,
                )
                self._hud_overlays[hwnd] = overlay
                self.live_hud_overlay = overlay
            return self._hud_overlays[hwnd]

        def _on_table_added(hwnd, rect, title=""):
            def _do():
                overlay = _get_or_create_overlay(hwnd)
                overlay.bind_table(title)
                overlay.update_rect(rect)
                if self.hand_monitor:
                    self.hand_monitor.register_window(hwnd, title)
                    self.hand_monitor.check_now()
                n = len(self._hud_overlays)
                label = title or self.table_detector.get_window_title(hwnd)
                self._set_status(f"Live HUD on ACR table — {n} table(s). {label[:60]}")
            self.after(0, _do)

        def _on_table_removed(hwnd):
            def _do():
                if self.hand_monitor:
                    self.hand_monitor.unregister_window(hwnd)
                overlay = self._hud_overlays.pop(hwnd, None)
                if overlay:
                    try:
                        overlay.destroy()
                    except Exception:
                        pass
                self.live_hud_overlay = next(iter(self._hud_overlays.values()), None)
                n = len(self._hud_overlays)
                if n == 0:
                    self._set_status("No ACR table found — HUD hidden.")
                else:
                    self._set_status(f"Table closed. HUD active on {n} table(s).")
            self.after(0, _do)

        def _on_table_moved(hwnd, rect, title=""):
            def _do():
                overlay = self._hud_overlays.get(hwnd)
                if overlay:
                    if title:
                        overlay.bind_table(title)
                    overlay.update_rect(rect)
            self.after(0, _do)

        def _on_table_switched(hwnd, old_title, new_title, rect):
            def _do():
                overlay = self._hud_overlays.get(hwnd)
                if overlay:
                    overlay.reset_for_table_switch()
                    overlay.bind_table(new_title)
                    overlay.update_rect(rect)
                if self.hand_monitor:
                    self.hand_monitor.register_window(hwnd, new_title)
                    self.hand_monitor.check_now()
                self._set_status(f"Table switch — HUD re-anchored ({new_title[:48]})")
            self.after(0, _do)

        def _on_hand_update(hwnd, hand_id, seat_map, max_seats, site, table_name):
            def _do(h=hwnd, hid=hand_id, s=seat_map, n=max_seats, site_name=site):
                overlay = self._hud_overlays.get(h)
                if overlay:
                    overlay.update_hand(s, n, site_name, hand_id=hid)
            self.after(0, _do)

        self.table_detector = MultiTableDetector(
            on_table_added=_on_table_added,
            on_table_removed=_on_table_removed,
            on_table_moved=_on_table_moved,
            on_table_switched=_on_table_switched,
        )
        self.hand_monitor = MultiHandMonitor(
            db=self.db,
            settings=self.settings,
            on_hand_update=_on_hand_update,
            poll_interval=2.0,
        )
        self.table_detector.start()
        self.hand_monitor.start()
        self._set_status("Live HUD started — scanning for ACR tournament tables...")
        self._schedule_hud_stats_refresh()

        # Immediate scan so overlay appears without waiting for poll interval
        try:
            for hwnd, info in self.table_detector.find_all_windows().items():
                _on_table_added(hwnd, info[:4], info[4])
        except Exception:
            logging.exception("HUD initial table scan failed")

    def _schedule_hud_stats_refresh(self):
        if not self._live_hud_on:
            return
        for overlay in list(getattr(self, "_hud_overlays", {}).values()):
            try:
                overlay.refresh_stats_only()
            except Exception:
                pass
        self.after(30000, self._schedule_hud_stats_refresh)

    def _stop_live_hud(self):
        self._live_hud_on = False
        self._hud_layout_mode = False
        if hasattr(self, "live_hud_btn"):
            self.live_hud_btn.configure(
                fg_color=self.theme["bg_accent"],
                text_color=self.theme["text"],
                text="⬡ Live HUD",
            )
        if hasattr(self, "hud_layout_btn"):
            self.hud_layout_btn.configure(
                text="Unlock HUD", fg_color=self.theme["gold"], text_color=self.theme["bg_base"]
            )
        if self.table_detector:
            self.table_detector.stop()
            self.table_detector = None
        if self.hand_monitor:
            self.hand_monitor.stop()
            self.hand_monitor = None
        for overlay in list(getattr(self, "_hud_overlays", {}).values()):
            try:
                overlay.destroy()
            except Exception:
                pass
        self._hud_overlays = {}
        self.live_hud_overlay = None
        self._set_status("Live HUD stopped.")


# ─── Main Entry Point ─────────────────────────────────────────────────────────
if __name__ == "__main__":
    ctk.set_appearance_mode("dark")
    ctk.set_default_color_theme("dark-blue")
    hud_only = "--live-hud" in sys.argv
    try:
        app = PokerApp(hud_only=hud_only)
        app.mainloop()
    except Exception as exc:
        logging.exception("Fatal HUD/GUI startup error")
        title = "LeakSnipe Live HUD" if hud_only else "LeakSnipe"
        _show_hud_error(title, f"{exc}\n\nAlso see: {LOG_PATH}")
        sys.exit(1)
