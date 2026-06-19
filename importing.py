"""
Hand importing from files with automatic hand history path detection.
Supports file watching and batch imports for all major poker sites.
"""

import os
import json
import sqlite3
import threading
import hashlib
import re
from typing import Dict, List, Tuple, Optional, Any, Callable
from datetime import datetime
from collections import defaultdict
import logging

from models import Hand, HandDatabase
from parsers import HandParser


def _canonical_path(path: str) -> str:
    """Convert path to canonical form for comparison."""
    if not path:
        return ""
    try:
        return os.path.normcase(os.path.realpath(os.path.normpath(path)))
    except Exception:
        return os.path.normcase(os.path.normpath(path))


def _path_exists_quick(path: str, timeout_sec: float = 1.0) -> bool:
    """Check path existence without blocking the API on slow/unreachable folders."""
    if not path:
        return False
    result = {"exists": False}

    def _probe() -> None:
        try:
            result["exists"] = os.path.isdir(path)
        except OSError:
            result["exists"] = False

    probe = threading.Thread(target=_probe, daemon=True)
    probe.start()
    probe.join(timeout=timeout_sec)
    return result["exists"]


def _is_drive_root(path: str) -> bool:
    """Check if path is a drive root."""
    if not path:
        return False
    norm = os.path.normpath(path)
    drive, tail = os.path.splitdrive(norm)
    return bool(drive) and tail in ("\\", "/")


def _prune_nested_scan_dirs(entries: List[Dict[str, str]]) -> List[Dict[str, str]]:
    """Drop parent scan paths when a more specific child path is also configured."""
    normalized: List[Tuple[str, Dict[str, str]]] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        path = os.path.normpath(str(entry.get("path", "")).strip())
        if not path or _is_drive_root(path):
            continue
        normalized.append((path, entry))
    if len(normalized) <= 1:
        return [entry for _, entry in normalized]

    canonical = [_canonical_path(path) for path, _ in normalized]
    keep: List[Dict[str, str]] = []
    for i, (path, entry) in enumerate(normalized):
        parent_path = canonical[i]
        is_parent = False
        for j, other_path in enumerate(canonical):
            if i == j:
                continue
            if other_path != parent_path and other_path.startswith(parent_path + os.sep):
                is_parent = True
                break
        if not is_parent:
            keep.append(entry)
    return keep if keep else [entry for _, entry in normalized]


class HandImporter:
    """Watches hand history directories and imports new hands."""

    def __init__(self, settings: Dict[str, Any], db: Optional[HandDatabase] = None):
        self.settings = settings
        self.parser = HandParser(settings)
        self.db = db
        self.hands: List[Hand] = []
        self.files_scanned: set = set()
        self.file_mtimes: Dict[str, float] = {}
        self.file_signatures: Dict[str, Tuple] = {}
        self.lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self.last_scan_at: Optional[str] = None
        self.last_scan_saved: int = 0
        self.last_scan_files: int = 0
        self.watcher_running: bool = False

    def update_settings(self, settings: Dict[str, Any]) -> None:
        """Update settings and recreate parser."""
        with self.lock:
            self.settings = settings
            self.parser = HandParser(settings)

    def _save_hand_if_new(self, hand: Hand, source_file: str) -> bool:
        """Save hand to database or memory if it doesn't exist."""
        if self.db:
            if self.db.hand_exists(hand.hand_id):
                if (
                    self.db.hand_needs_hero_backfill(hand.hand_id)
                    and self.db.hand_has_hero_fields(hand)
                ):
                    self.db.save_hand(hand, source_file=source_file)
                    return True
                return False
            self.db.save_hand(hand, source_file=source_file)
            return True

        with self.lock:
            existing_ids = {hh.hand_id for hh in self.hands}
            if hand.hand_id in existing_ids:
                return False
            self.hands.append(hand)
            return True

    def _get_file_signature(self, fpath: str) -> Optional[Tuple[int, int, str]]:
        """Get file signature (mtime, size, tail hash)."""
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

    def full_scan(self) -> Tuple[int, int]:
        """Scan all configured directories and import new hands. Returns (saved, files_scanned)."""
        with self.lock:
            scan_dirs = _prune_nested_scan_dirs(list(self.settings.get("scan_dirs", [])))

        saved = 0
        files_count = 0
        for entry in scan_dirs:
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
                    with self.lock:
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
                    with self.lock:
                        files_count += 1
                        self.files_scanned.add(fpath)
        with self.lock:
            self.last_scan_at = datetime.now().isoformat()
            self.last_scan_saved = saved
            self.last_scan_files = files_count
        if saved > 0:
            logging.info("Import scan: saved %d new hand(s) from %d file(s)", saved, files_count)
        return saved, files_count

    def reparse_hands_missing_hero(self) -> int:
        """Backfill hero cards/stats for hands parsed with the wrong hero name."""
        if not self.db:
            return 0
        return self.db.reparse_hands_missing_hero(self.parser)

    def import_files(self, file_paths: List[str]) -> Tuple[int, int]:
        """Import hands from explicit file paths. Returns (saved, files_count)."""
        new_hands: List[Tuple[Hand, str]] = []
        files_count = 0
        saved = 0
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
                    if (
                        self.db.hand_needs_hero_backfill(h.hand_id)
                        and self.db.hand_has_hero_fields(h)
                    ):
                        self.db.save_hand(h, source_file=fpath)
                        saved += 1
                    continue
                new_hands.append((h, fpath))
            files_count += 1
            signature = self._get_file_signature(fpath)
            if signature is not None:
                self.file_signatures[fpath] = signature
                self.file_mtimes[fpath] = signature[0]
            self.files_scanned.add(fpath)
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

    def start_watcher(self, callback: Optional[Callable] = None) -> None:
        """Start background file watcher."""
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self.watcher_running = True
        self._thread = threading.Thread(target=self._watch_loop, args=(callback,), daemon=True)
        self._thread.start()
        dirs = [e.get("path", "") for e in self.settings.get("scan_dirs", [])]
        logging.info(
            "Hand watcher started — polling every %ss, watching %d folder(s): %s",
            self.settings.get("refresh_interval", 5),
            len(dirs),
            "; ".join(dirs[:5]) + ("…" if len(dirs) > 5 else ""),
        )

    def stop_watcher(self) -> None:
        """Stop background file watcher."""
        self._stop.set()
        self.watcher_running = False
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2.0)
        self._thread = None

    def get_status(self) -> Dict[str, Any]:
        """Watcher/import status for the UI."""
        dirs = []
        for entry in self.settings.get("scan_dirs", []):
            path = os.path.normpath(str(entry.get("path", "")).strip())
            exists = _path_exists_quick(path)
            dirs.append({
                "path": path,
                "site": str(entry.get("site", "")).strip(),
                "exists": exists,
            })
        alive = bool(self._thread and self._thread.is_alive())
        return {
            "watcher_running": alive and self.watcher_running,
            "poll_interval_sec": self.settings.get("refresh_interval", 5),
            "watch_folders": dirs,
            "watch_folder_count": len(dirs),
            "existing_folder_count": sum(1 for d in dirs if d["exists"]),
            "last_scan_at": self.last_scan_at,
            "last_scan_saved": self.last_scan_saved,
            "last_scan_files": self.last_scan_files,
            "files_tracked": len(self.file_signatures),
        }

    def _watch_loop(self, callback: Optional[Callable]) -> None:
        """Background loop for watching files."""
        while not self._stop.is_set():
            try:
                new_count, file_count = self.full_scan()
                if callback and new_count > 0:
                    callback(new_count, file_count)
            except Exception as e:
                logging.error(f"Error in watch loop: {e}", exc_info=True)
            interval = self.settings.get("refresh_interval", 5)
            self._stop.wait(interval)

    def get_hands(self) -> List[Hand]:
        """Get all hands from database or memory."""
        if self.db:
            return self.db.get_all_hands()
        with self.lock:
            return list(self.hands)

    def get_stats_text(self) -> str:
        """Get human-readable stats text."""
        if self.db:
            counts = self.db.get_hand_count()
            total = sum(counts.values())
            parts = [f"{site}: {count}" for site, count in counts.items() if count > 0]
            fcount = len(self.files_scanned)
            return f"{total} hands imported from {fcount} files ({', '.join(parts)})"
        with self.lock:
            total = len(self.hands)
            counts = defaultdict(int)
            for h in self.hands:
                counts[h.site] += 1
            parts = [f"{site}: {count}" for site, count in counts.items()]
            fcount = len(self.files_scanned)
        return f"{total} hands imported from {fcount} files ({', '.join(parts)})"


def get_default_hh_paths() -> dict:
    """
    Return default hand history folder paths for each supported poker site.
    Checks common install locations and returns existing paths only.
    """
    appdata = os.environ.get("APPDATA", "")
    localappdata = os.environ.get("LOCALAPPDATA", "")
    userprofile = os.environ.get("USERPROFILE", "")
    documents = os.path.join(userprofile, "Documents")

    candidates = {
        "CoinPoker": [
            os.path.join(appdata, "CoinPoker", "logs"),
            os.path.join(appdata, "CoinPoker", "HandHistory"),
            os.path.join(localappdata, "CoinPoker", "HandHistory"),
        ],
        "BetACR": [
            os.path.join(r"C:\ACR Poker\handHistory"),
            os.path.join(localappdata, "WPN", "HandHistory"),
            os.path.join(appdata, "WPN", "HandHistory"),
            os.path.join(appdata, "Americas Cardroom", "HandHistory"),
            os.path.join(localappdata, "Americas Cardroom", "HandHistory"),
            os.path.join(localappdata, "ACR", "HandHistory"),
            os.path.join(documents, "ACR Poker", "HandHistory"),
            os.path.join(documents, "Americas Cardroom", "HandHistory"),
            os.path.join(documents, "ACR", "HandHistory"),
            os.path.join(documents, "BetACR", "HandHistory"),
            os.path.join(appdata, "ACR", "HandHistory"),
            os.path.join(appdata, "ACR Poker", "HandHistory"),
            os.path.join(localappdata, "ACR Poker", "HandHistory"),
        ],
        "GGPoker": [
            os.path.join(localappdata, "GGPoker", "HandHistory"),
            os.path.join(appdata, "GGPoker", "HandHistory"),
        ],
        "ClubGG": [
            os.path.join(localappdata, "ClubGG", "HandHistory"),
            os.path.join(appdata, "ClubGG", "HandHistory"),
        ],
        "PokerStars": [
            os.path.join(appdata, "PokerStars", "HandHistory"),
            os.path.join(appdata, "PokerStars.EU", "HandHistory"),
            os.path.join(appdata, "PokerStars.FR", "HandHistory"),
        ],
        "888poker": [
            os.path.join(localappdata, "888poker", "HandHistory"),
            os.path.join(appdata, "888poker", "HandHistory"),
        ],
        "Ignition": [
            os.path.join(documents, "Ignition", "HandHistory"),
            os.path.join(userprofile, "Ignition", "HandHistory"),
        ],
    }

    result = {}
    for site, paths in candidates.items():
        for path in paths:
            if path and os.path.isdir(path):
                result[site] = path
                break
    return result


def discover_scan_dirs(settings: Optional[Dict[str, Any]] = None) -> List[Dict[str, str]]:
    """Find existing hand-history folders for all supported sites."""
    settings = settings or {}
    hero_names = settings.get("hero_names") or {}
    betacr_hero = str(hero_names.get("BetACR") or "JohnDaWalka").strip()

    candidates: List[Tuple[str, str]] = []

    acr_hh_root = os.path.join(r"C:\ACR Poker", "handHistory")
    if betacr_hero:
        candidates.append((os.path.join(acr_hh_root, betacr_hero), "BetACR"))
        candidates.append((os.path.join(r"C:\ACR Poker", "TournamentSummary", betacr_hero), "BetACR"))

    if os.path.isdir(acr_hh_root):
        for name in os.listdir(acr_hh_root):
            subdir = os.path.join(acr_hh_root, name)
            if os.path.isdir(subdir):
                candidates.append((subdir, "BetACR"))

    for site, path in get_default_hh_paths().items():
        candidates.append((path, site))

    extra_betacr = [
        r"C:\HM3Archive\Winning Poker Network",
        r"C:\Hand2Note4Hh\MyHandsArchive_H2N4\WinningPokerNetwork",
    ]
    for path in extra_betacr:
        candidates.append((path, "BetACR"))

    discovered: List[Dict[str, str]] = []
    seen = set()
    for raw_path, site in candidates:
        path = os.path.normpath(str(raw_path).strip())
        if not path or _is_drive_root(path) or not os.path.isdir(path):
            continue
        key = (site, os.path.normcase(path))
        if key in seen:
            continue
        seen.add(key)
        discovered.append({"path": path, "site": site})
    return discovered


def merge_scan_dirs(
    existing: Optional[List[Dict[str, str]]],
    discovered: Optional[List[Dict[str, str]]],
) -> List[Dict[str, str]]:
    """Merge configured and auto-discovered scan directories, keeping only existing folders."""
    merged: List[Dict[str, str]] = []
    seen = set()
    for entries in (existing or [], discovered or []):
        if not isinstance(entries, list):
            continue
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            path = os.path.normpath(str(entry.get("path", "")).strip())
            site = str(entry.get("site", "")).strip() or "CoinPoker"
            if not path or _is_drive_root(path) or not os.path.isdir(path):
                continue
            key = (site, os.path.normcase(path))
            if key in seen:
                continue
            seen.add(key)
            merged.append({"path": path, "site": site})
    return merged

