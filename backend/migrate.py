#!/usr/bin/env python3
"""
One-off migration script to backfill writings and writing_ids
for runs whose response JSON contains ideas without writing_id.

Usage:
    python migrate_backfill_writings.py

Make sure the DB_PATH matches your deployment.
"""

import json
import sqlite3
from typing import Any, Dict, List, Optional

DB_PATH = "/var/www/site/data/lang.db"


def get_db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def find_existing_writing(
    conn: sqlite3.Connection,
    *,
    run_id: int,
    name: str,
    parent_text_a: str,
    parent_text_b: str,
) -> Optional[int]:
    """Look for an existing writing that matches this idea context."""
    row = conn.execute(
        """
        SELECT id
        FROM writings
        WHERE parent_run_id = ?
          AND name = ?
          AND parent_text_a = ?
          AND parent_text_b = ?
        ORDER BY id DESC
        LIMIT 1
        """,
        (run_id, name, parent_text_a, parent_text_b),
    ).fetchone()
    if row:
        return int(row["id"])
    return None


def create_writing(
    conn: sqlite3.Connection,
    *,
    name: str,
    description: str,
    parent_run_id: int,
    parent_text_a: str,
    parent_text_b: str,
) -> int:
    """Insert a new writing row and return its id."""
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO writings (
            name,
            description,
            parent_run_id,
            parent_text_a,
            parent_text_b,
            parent_writing_id,
            notes
        )
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            name,
            description,
            parent_run_id,
            parent_text_a,
            parent_text_b,
            None,   # parent_writing_id
            "",     # notes
        ),
    )
    return int(cur.lastrowid)


def process_run(conn: sqlite3.Connection, row: sqlite3.Row) -> int:
    """
    Process a single run row.
    Returns the number of ideas for which we created/attached a writing_id.
    """
    run_id = int(row["id"])
    text_a = row["text_a"] or ""
    text_b = row["text_b"] or ""
    response_text = row["response"]

    if not response_text:
        return 0

    try:
        data = json.loads(response_text)
    except json.JSONDecodeError:
        # Non-JSON or malformed; skip
        return 0

    # We expect structure like {"ideas": [...]} (matching IdeaSet)
    if not isinstance(data, dict):
        return 0

    ideas = data.get("ideas")
    if not isinstance(ideas, list):
        return 0

    updated = False
    ideas_updated = 0

    for idea in ideas:
        if not isinstance(idea, dict):
            continue

        # Skip if already has a writing_id
        existing_wid = idea.get("writing_id")
        if existing_wid:
            continue

        name = (idea.get("name") or "").strip() or "(untitled)"
        # Original field is "desciription" (typo) in your schema; fall back to "description"
        description = (
            (idea.get("desciription") or idea.get("description") or "").strip()
        )

        # Try to reuse an existing writing if present
        writing_id = find_existing_writing(
            conn,
            run_id=run_id,
            name=name,
            parent_text_a=text_a,
            parent_text_b=text_b,
        )
        if writing_id is None:
            writing_id = create_writing(
                conn,
                name=name,
                description=description,
                parent_run_id=run_id,
                parent_text_a=text_a,
                parent_text_b=text_b,
            )

        idea["writing_id"] = writing_id
        updated = True
        ideas_updated += 1

    if updated:
        new_response_text = json.dumps(data, ensure_ascii=True)
        conn.execute(
            """
            UPDATE runs
            SET response = ?
            WHERE id = ?
            """,
            (new_response_text, run_id),
        )

    return ideas_updated


def main() -> None:
    conn = get_db()

    rows = conn.execute(
        """
        SELECT id, text_a, text_b, response
        FROM runs
        ORDER BY id ASC
        """
    ).fetchall()

    total_runs = len(rows)
    total_ideas_updated = 0
    runs_touched = 0

    print(f"Found {total_runs} runs to inspect")

    try:
        for idx, row in enumerate(rows, start=1):
            ideas_updated = process_run(conn, row)
            if ideas_updated:
                runs_touched += 1
                total_ideas_updated += ideas_updated
            if idx % 100 == 0:
                # Periodic commit to avoid long transactions
                conn.commit()
                print(
                    f"Processed {idx}/{total_runs} runs "
                    f"(runs updated: {runs_touched}, ideas updated: {total_ideas_updated})"
                )

        conn.commit()
    finally:
        conn.close()

    print("Migration complete.")
    print(f"Runs updated: {runs_touched}")
    print(f"Ideas with writing_id attached: {total_ideas_updated}")


if __name__ == "__main__":
    main()
