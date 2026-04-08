"""
merge_dbs.py — Merge old poker_hands.db into active poker_hands.db
Old DB:    D:\dist\poker-build\poker_hands.db  (19.32 MB, ~4369 hands)
Active DB: D:\dist\poker_hands.db             (2.28  MB, ~329 hands)

Dedup key: hand_id (TEXT PRIMARY KEY in both schemas).
All related tables (players, actions, winners, hand_tags) are migrated
transactionally per hand so partial imports never leave orphaned rows.
"""

import sqlite3
import sys
import os

OLD_DB   = r"D:\dist\poker-build\poker_hands.db"
NEW_DB   = r"D:\dist\poker_hands.db"

# Tables that reference hand_id as a foreign key
CHILD_TABLES = ["players", "actions", "winners", "hand_tags"]


def count_hands(conn):
    return conn.execute("SELECT COUNT(*) FROM hands").fetchone()[0]


def merge(old_path: str, new_path: str) -> int:
    """Merge all hands from old_path into new_path. Returns number of new hands inserted."""
    if not os.path.exists(old_path):
        print(f"ERROR: Old DB not found: {old_path}")
        sys.exit(1)
    if not os.path.exists(new_path):
        print(f"ERROR: Active DB not found: {new_path}")
        sys.exit(1)

    old_conn = sqlite3.connect(old_path)
    old_conn.row_factory = sqlite3.Row
    new_conn = sqlite3.connect(new_path)
    new_conn.row_factory = sqlite3.Row

    before_count = count_hands(new_conn)
    print(f"Active DB before merge : {before_count} hands")
    old_count    = count_hands(old_conn)
    print(f"Old    DB hand count   : {old_count} hands")

    # Fetch all hand_ids already in the active DB for fast dedup
    existing_ids = {
        row[0] for row in new_conn.execute("SELECT hand_id FROM hands")
    }
    print(f"Existing hand_ids      : {len(existing_ids)}")

    # Fetch column names for each table from the NEW db (canonical schema)
    def get_columns(conn, table):
        cur = conn.execute(f"PRAGMA table_info({table})")
        return [row["name"] for row in cur.fetchall()]

    hands_cols   = get_columns(new_conn, "hands")
    child_cols   = {t: get_columns(new_conn, t) for t in CHILD_TABLES}

    inserted = 0
    skipped  = 0
    errors   = 0

    # Iterate old hands
    old_hands = old_conn.execute("SELECT * FROM hands").fetchall()

    new_conn.execute("PRAGMA journal_mode=WAL")
    new_conn.execute("BEGIN")

    for hand_row in old_hands:
        hid = hand_row["hand_id"]
        if hid in existing_ids:
            skipped += 1
            continue

        try:
            # Insert hand row — only columns present in new schema
            vals = [hand_row[c] if c in hand_row.keys() else None for c in hands_cols]
            placeholders = ", ".join("?" * len(hands_cols))
            cols_str = ", ".join(hands_cols)
            new_conn.execute(
                f"INSERT INTO hands ({cols_str}) VALUES ({placeholders})",
                vals
            )

            # Insert child rows for each child table
            for table in CHILD_TABLES:
                try:
                    rows = old_conn.execute(
                        f"SELECT * FROM {table} WHERE hand_id = ?", (hid,)
                    ).fetchall()
                    if not rows:
                        continue
                    t_cols = child_cols[table]
                    t_ph   = ", ".join("?" * len(t_cols))
                    t_cs   = ", ".join(t_cols)
                    for row in rows:
                        rvals = [row[c] if c in row.keys() else None for c in t_cols]
                        new_conn.execute(
                            f"INSERT OR IGNORE INTO {table} ({t_cs}) VALUES ({t_ph})",
                            rvals
                        )
                except sqlite3.OperationalError:
                    # Table might not exist in old DB — skip silently
                    pass

            existing_ids.add(hid)
            inserted += 1

        except Exception as e:
            errors += 1
            print(f"  WARN: Failed to insert {hid}: {e}")

    new_conn.execute("COMMIT")
    new_conn.close()
    old_conn.close()

    return inserted, skipped, errors, before_count


if __name__ == "__main__":
    print("=" * 60)
    print("Poker DB Merge Utility")
    print(f"  Old: {OLD_DB}")
    print(f"  New: {NEW_DB}")
    print("=" * 60)

    inserted, skipped, errors, before = merge(OLD_DB, NEW_DB)

    # Reopen to verify final count
    verify_conn = sqlite3.connect(NEW_DB)
    after = verify_conn.execute("SELECT COUNT(*) FROM hands").fetchone()[0]
    verify_conn.close()

    print()
    print("=" * 60)
    print(f"Merge complete!")
    print(f"  Hands before : {before}")
    print(f"  Inserted     : {inserted}")
    print(f"  Skipped dups : {skipped}")
    print(f"  Errors       : {errors}")
    print(f"  Hands after  : {after}")
    print("=" * 60)
