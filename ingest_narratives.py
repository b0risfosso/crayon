#!/usr/bin/env python3
"""
ingest_narratives.py

Safely:
  1) backup the DB
  2) ensure `narratives` table schema matches expectations
  3) ingest rows from CSV (title, description)

Usage:
  python ingest_narratives.py --db narratives_data.db --csv narratives.csv [--dedupe]
"""
import argparse
import csv
import os
import shutil
import sqlite3
import sys
from datetime import datetime

EXPECTED_COLUMNS = ["id", "title", "description", "created_at"]

CREATE_NARRATIVES_SQL = """
CREATE TABLE IF NOT EXISTS narratives (
  id          INTEGER PRIMARY KEY,
  title       TEXT NOT NULL,
  description TEXT,
  created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);
"""

CREATE_UNIQUE_TITLE_INDEX_SQL = """
CREATE UNIQUE INDEX IF NOT EXISTS idx_narratives_title_unique ON narratives(title);
"""

def backup_db(db_path: str) -> str:
    ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    backup_path = f"{db_path}.backup_{ts}"
    shutil.copy2(db_path, backup_path)
    return backup_path

def get_table_info(cur, table_name: str):
    cur.execute(f"PRAGMA table_info({table_name});")
    # returns: cid, name, type, notnull, dflt_value, pk
    return cur.fetchall()

def column_names(info_rows):
    return [r[1] for r in info_rows]

def table_exists(cur, table_name: str) -> bool:
    cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?;", (table_name,))
    return cur.fetchone() is not None

def ensure_schema(conn: sqlite3.Connection):
    cur = conn.cursor()

    # If table doesn't exist, just create it fresh.
    if not table_exists(cur, "narratives"):
        cur.execute(CREATE_NARRATIVES_SQL)
        conn.commit()
        return

    # Table exists: check columns
    info = get_table_info(cur, "narratives")
    cols = column_names(info)

    # If it already matches (has id primary key and created_at), keep it.
    has_id = "id" in cols
    has_created_at = "created_at" in cols

    if has_id and has_created_at:
        return

    # Otherwise migrate data to a new table with correct schema
    cur.execute("""
        CREATE TABLE IF NOT EXISTS narratives_new (
          id          INTEGER PRIMARY KEY,
          title       TEXT NOT NULL,
          description TEXT,
          created_at  TEXT NOT NULL DEFAULT (datetime('now'))
        );
    """)

    # Figure out which source columns exist to build a compatible INSERT...SELECT
    has_desc_source = "description" in cols
    has_created_source = "created_at" in cols

    select_cols = ["title"]
    if has_desc_source:
        select_cols.append("description")
    else:
        # If description is missing (unlikely), supply NULL
        select_cols.append("NULL AS description")

    if has_created_source:
        select_cols.append("COALESCE(created_at, datetime('now')) AS created_at")
    else:
        select_cols.append("datetime('now') AS created_at")

    select_sql = f"INSERT INTO narratives_new (title, description, created_at) SELECT {', '.join(select_cols)} FROM narratives;"
    cur.execute(select_sql)

    cur.execute("DROP TABLE narratives;")
    cur.execute("ALTER TABLE narratives_new RENAME TO narratives;")
    conn.commit()

def ingest_csv(conn: sqlite3.Connection, csv_path: str, dedupe: bool):
    cur = conn.cursor()
    # Ensure base schema
    cur.execute(CREATE_NARRATIVES_SQL)

    if dedupe:
        # Create unique index on title for upserts
        cur.execute(CREATE_UNIQUE_TITLE_INDEX_SQL)

    with open(csv_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        missing = [c for c in ("title", "description") if c not in reader.fieldnames]
        if missing:
            raise SystemExit(f"CSV missing required columns: {missing}")

        if dedupe:
            # Upsert on title (requires the UNIQUE index)
            sql = """
                INSERT INTO narratives (title, description)
                VALUES (?, ?)
                ON CONFLICT(title) DO UPDATE SET
                    description=excluded.description
            """
        else:
            sql = "INSERT INTO narratives (title, description) VALUES (?, ?)"

        rows = []
        for row in reader:
            title = (row["title"] or "").strip()
            desc  = (row["description"] or "").strip()
            if not title:
                continue
            rows.append((title, desc))

        cur.executemany(sql, rows)

    conn.commit()

def count_rows(conn):
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM narratives;")
    return cur.fetchone()[0]

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True, help="Path to SQLite database (e.g., narratives_data.db)")
    ap.add_argument("--csv", required=True, help="Path to CSV file (header: title,description)")
    ap.add_argument("--dedupe", action="store_true", help="Create UNIQUE(title) and upsert by title")
    args = ap.parse_args()

    if not os.path.exists(args.db):
        # If DB file doesn't exist, SQLite will create it—no backup needed
        pass
    else:
        backup = backup_db(args.db)
        print(f"[info] Backed up DB to: {backup}")

    conn = sqlite3.connect(args.db)
    try:
        ensure_schema(conn)
        before = count_rows(conn)
        ingest_csv(conn, args.csv, dedupe=args.dedupe)
        after = count_rows(conn)
        print(f"[done] Ingest complete. Rows before: {before}, after: {after}, inserted/updated: {after - before if not args.dedupe else 'see dedupe logic'}")
    finally:
        conn.close()

if __name__ == "__main__":
    sys.exit(main())
