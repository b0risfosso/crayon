#!/usr/bin/env python3
"""
ensure_narrative_dimensions.py

Ensures the `narrative_dimensions` table in narratives_data.db matches the expected schema.
- Creates the table if missing
- Validates columns, PK, FK
- Auto-migrates to correct schema if mismatched (preserves data)
- Adds indexes

Expected schema:

CREATE TABLE IF NOT EXISTS narrative_dimensions (
  id           INTEGER PRIMARY KEY,
  narrative_id INTEGER NOT NULL,
  title        TEXT NOT NULL,
  description  TEXT,
  created_at   TEXT NOT NULL DEFAULT (datetime('now')),
  FOREIGN KEY (narrative_id) REFERENCES narratives(id) ON DELETE CASCADE
);

Indexes:
- CREATE INDEX IF NOT EXISTS idx_narrative_dimensions_narrative_id ON narrative_dimensions(narrative_id);
- (Optional) CREATE UNIQUE INDEX IF NOT EXISTS idx_narrative_dimensions_unique ON narrative_dimensions(narrative_id, title);
"""

import sqlite3
from typing import List, Tuple

DB_PATH = "narratives_data.db"

EXPECTED_COLUMNS: List[Tuple[str, str, int, int, str]] = [
    # name,           type,     notnull(0/1), pk(0/1), default
    ("id",            "INTEGER", 0, 1, None),
    ("narrative_id",  "INTEGER", 1, 0, None),
    ("title",         "TEXT",    1, 0, None),
    ("description",   "TEXT",    0, 0, None),
    ("created_at",    "TEXT",    1, 0, "datetime('now')"),
]

CREATE_SQL = """
CREATE TABLE IF NOT EXISTS narrative_dimensions (
  id           INTEGER PRIMARY KEY,
  narrative_id INTEGER NOT NULL,
  title        TEXT NOT NULL,
  description  TEXT,
  created_at   TEXT NOT NULL DEFAULT (datetime('now')),
  FOREIGN KEY (narrative_id) REFERENCES narratives(id) ON DELETE CASCADE
);
"""

CREATE_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_narrative_dimensions_narrative_id ON narrative_dimensions(narrative_id);",
    # Uncomment if you want to prevent duplicate titles under the same narrative:
    # "CREATE UNIQUE INDEX IF NOT EXISTS idx_narrative_dimensions_unique ON narrative_dimensions(narrative_id, title);",
]

def table_exists(cur, name: str) -> bool:
    cur.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?;", (name,))
    return cur.fetchone() is not None

def get_table_info(cur, name: str):
    cur.execute(f"PRAGMA table_info({name});")
    # cid, name, type, notnull, dflt_value, pk
    return cur.fetchall()

def get_foreign_keys(cur, name: str):
    cur.execute(f"PRAGMA foreign_key_list({name});")
    return cur.fetchall()

def matches_expected(info_rows) -> bool:
    # Build maps {name: (type, notnull, dflt, pk)}
    actual = {}
    for cid, name, coltype, notnull, dflt, pk in info_rows:
        actual[name] = (coltype.upper() if coltype else "", int(notnull), dflt, int(pk))
    for name, typ, notnull, pk, dflt in EXPECTED_COLUMNS:
        if name not in actual:
            return False
        a_type, a_notnull, a_dflt, a_pk = actual[name]
        # allow type synonyms like "INTEGER" vs "INTEGER PRIMARY KEY" handled via pk flag
        if typ not in a_type:
            return False
        if a_notnull != notnull:
            return False
        if a_pk != pk:
            return False
        # Compare defaults (normalize None/"NULL")
        exp = dflt
        act = a_dflt
        if exp is None and act is not None:
            return False
        if exp is not None and (act is None or exp.replace(" ", "") != str(act).replace(" ", "")):
            return False
    return True

def fk_is_expected(fk_rows) -> bool:
    # PRAGMA foreign_key_list columns: (id, seq, table, from, to, on_update, on_delete, match)
    for row in fk_rows:
        # e.g., (0, 0, 'narratives', 'narrative_id', 'id', 'NO ACTION', 'CASCADE', 'NONE')
        table = row[2]
        from_col = row[3]
        to_col = row[4]
        on_delete = (row[6] or "").upper()
        if table == "narratives" and from_col == "narrative_id" and to_col == "id" and on_delete == "CASCADE":
            return True
    return False

def migrate_to_expected(conn: sqlite3.Connection):
    cur = conn.cursor()
    # Create new table with the correct schema
    cur.execute("""
        CREATE TABLE IF NOT EXISTS narrative_dimensions_new (
          id           INTEGER PRIMARY KEY,
          narrative_id INTEGER NOT NULL,
          title        TEXT NOT NULL,
          description  TEXT,
          created_at   TEXT NOT NULL DEFAULT (datetime('now')),
          FOREIGN KEY (narrative_id) REFERENCES narratives(id) ON DELETE CASCADE
        );
    """)
    # Determine which columns are present in the old table to copy over
    cur.execute("PRAGMA table_info(narrative_dimensions);")
    old_cols = {r[1] for r in cur.fetchall()}  # names

    # Build a compatible INSERT ... SELECT
    sel_parts = []
    # id: preserve if exists, else NULL (will auto-assign new IDs)
    sel_parts.append("id" if "id" in old_cols else "NULL AS id")
    sel_parts.append("narrative_id" if "narrative_id" in old_cols else "NULL AS narrative_id")
    sel_parts.append("title" if "title" in old_cols else "NULL AS title")
    sel_parts.append("description" if "description" in old_cols else "NULL AS description")
    if "created_at" in old_cols:
        sel_parts.append("COALESCE(created_at, datetime('now')) AS created_at")
    else:
        sel_parts.append("datetime('now') AS created_at")

    cur.execute(f"""
        INSERT INTO narrative_dimensions_new (id, narrative_id, title, description, created_at)
        SELECT {", ".join(sel_parts)} FROM narrative_dimensions;
    """)

    # Swap
    cur.execute("DROP TABLE narrative_dimensions;")
    cur.execute("ALTER TABLE narrative_dimensions_new RENAME TO narrative_dimensions;")
    conn.commit()

def ensure_indexes(conn: sqlite3.Connection):
    cur = conn.cursor()
    for sql in CREATE_INDEXES:
        cur.execute(sql)
    conn.commit()

def ensure_narrative_dimensions(db_path=DB_PATH, verbose=True):
    conn = sqlite3.connect(db_path)
    try:
        # Always enforce FK behavior during this session
        conn.execute("PRAGMA foreign_keys = ON;")

        cur = conn.cursor()
        if not table_exists(cur, "narrative_dimensions"):
            if verbose: print("[info] narrative_dimensions not found; creating.")
            cur.execute(CREATE_SQL)
            ensure_indexes(conn)
            if verbose: print("[ok] narrative_dimensions created.")
            return

        # Validate
        info = get_table_info(cur, "narrative_dimensions")
        fks = get_foreign_keys(cur, "narrative_dimensions")

        need_migration = not matches_expected(info) or not fk_is_expected(fks)
        if need_migration:
            if verbose:
                print("[warn] narrative_dimensions schema mismatch or bad/missing FK; migrating to expected schema.")
            migrate_to_expected(conn)
            ensure_indexes(conn)
            if verbose: print("[ok] narrative_dimensions migrated and indexed.")
        else:
            ensure_indexes(conn)
            if verbose: print("[ok] narrative_dimensions schema and FK are correct; indexes ensured.")

        # Final verification printout
        if verbose:
            cur.execute("PRAGMA table_info(narrative_dimensions);")
            print("[schema] narrative_dimensions:")
            for row in cur.fetchall():
                print("  ", row)  # (cid, name, type, notnull, dflt_value, pk)
            cur.execute("PRAGMA foreign_key_list(narrative_dimensions);")
            print("[fk] narrative_dimensions:", cur.fetchall())
    finally:
        conn.close()

if __name__ == "__main__":
    ensure_narrative_dimensions()
