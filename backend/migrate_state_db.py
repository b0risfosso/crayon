#!/usr/bin/env python3
"""
Recreate /var/www/site/data/state.db with the new schema
- Drop legacy fields: target, current, unit
- Add new field: webpage (empty for all migrated rows)
- Preserve: id (if present), title, description (or ""), start_ts (or NULL), start_iso_local (or NULL)
- Safe: creates a timestamped backup before migration.

Usage:
  python3 migrate_state_db.py
"""

import sqlite3, shutil, time, os
from pathlib import Path

DB_PATH = Path("/var/www/site/data/state.db")
BACKUP_DIR = DB_PATH.parent
BACKUP_PATH = BACKUP_DIR / f"state.db.bak-{int(time.time())}"

NEW_SCHEMA_SQL = """
CREATE TABLE narratives_new(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  title TEXT NOT NULL,
  description TEXT DEFAULT '',
  webpage TEXT DEFAULT '',
  start_ts INTEGER,
  start_iso_local TEXT
);
"""

def get_cols(con, table):
    cur = con.execute(f"PRAGMA table_info({table})")
    return {row[1] for row in cur.fetchall()}  # column names

def main():
    # Ensure DB exists (create empty file if not)
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    if not DB_PATH.exists():
        print(f"[info] {DB_PATH} does not exist; creating new database with desired schema.")
        con = sqlite3.connect(DB_PATH)
        con.executescript("""
            PRAGMA journal_mode=WAL;
            PRAGMA foreign_keys=ON;
        """)
        con.executescript(NEW_SCHEMA_SQL.replace("narratives_new", "narratives"))
        con.commit()
        con.close()
        print("[done] Fresh database created.")
        return

    # Backup current DB
    print(f"[info] Backing up DB to {BACKUP_PATH}")
    shutil.copy2(DB_PATH, BACKUP_PATH)

    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    cur = con.cursor()
    cur.execute("PRAGMA foreign_keys=OFF;")  # safer for table replacement inside txn

    # Does old table exist?
    exists = cur.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='narratives'").fetchone() is not None
    if not exists:
        print("[warn] No 'narratives' table found. Creating a fresh one.")
        cur.executescript(NEW_SCHEMA_SQL.replace("narratives_new", "narratives"))
        con.commit()
        con.close()
        print("[done] Fresh database created (no rows to migrate).")
        return

    # Introspect old columns
    old_cols = get_cols(con, "narratives")
    print(f"[info] Old columns: {sorted(old_cols)}")

    # Build a robust INSERT ... SELECT that tolerates missing cols
    # We'll copy: (id?), title, description (or ''), start_ts (or NULL), start_iso_local (or NULL), and add webpage='' for all
    select_parts = []
    insert_cols = []

    # id: keep if present
    if "id" in old_cols:
        insert_cols.append("id")
        select_parts.append("id")
    # title (required logically; if missing, abort)
    if "title" not in old_cols:
        con.close()
        raise SystemExit("[fatal] Source table is missing required column 'title'. Cannot migrate.")
    insert_cols.append("title")
    select_parts.append("title")

    # description (optional)
    insert_cols.append("description")
    if "description" in old_cols:
        select_parts.append("COALESCE(description, '') AS description")
    else:
        select_parts.append("'' AS description")

    # webpage (new; always '')
    insert_cols.append("webpage")
    select_parts.append("'' AS webpage")

    # start_ts (optional)
    insert_cols.append("start_ts")
    if "start_ts" in old_cols:
        select_parts.append("start_ts")
    else:
        select_parts.append("NULL AS start_ts")

    # start_iso_local (optional)
    insert_cols.append("start_iso_local")
    if "start_iso_local" in old_cols:
        select_parts.append("start_iso_local")
    else:
        select_parts.append("NULL AS start_iso_local")

    insert_cols_sql = ", ".join(insert_cols)
    select_sql = ", ".join(select_parts)

    print(f"[info] Insert columns: {insert_cols_sql}")
    print(f"[info] Select exprs:   {select_sql}")

    # Perform migration inside a transaction
    try:
        cur.execute("BEGIN;")
        # New table
        cur.executescript(NEW_SCHEMA_SQL)

        # Migrate rows
        migrate_sql = f"INSERT INTO narratives_new ({insert_cols_sql}) SELECT {select_sql} FROM narratives"
        cur.execute(migrate_sql)
        migrated = cur.rowcount
        print(f"[info] Migrated rows: {migrated}")

        # Swap tables
        cur.execute("DROP TABLE narratives")
        cur.execute("ALTER TABLE narratives_new RENAME TO narratives")

        # Clean up
        cur.execute("COMMIT;")
        con.execute("VACUUM;")
        print("[done] Migration completed successfully.")
    except Exception as e:
        cur.execute("ROLLBACK;")
        print("[error] Migration failed, DB rolled back. See backup for recovery.")
        raise
    finally:
        con.close()

if __name__ == "__main__":
    main()
