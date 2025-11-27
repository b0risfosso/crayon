#!/usr/bin/env python3
import sqlite3
from typing import Optional, Dict, Any, List

ART_DB_PATH = "/var/www/site/data/art.db"


def get_art_db() -> sqlite3.Connection:
    """
    Open a connection to the art.db with foreign keys enabled
    and Row objects for dict-like access.
    """
    conn = sqlite3.connect(ART_DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn


def fetch_collection(email: str, name: str) -> Optional[Dict[str, Any]]:
    """
    Return a single art_collections row (as dict) matching email + name,
    or None if no such collection exists.
    """
    db = get_art_db()
    try:
        row = db.execute(
            """
            SELECT *
            FROM art_collections
            WHERE email = ?
              AND name  = ?
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (email, name),
        ).fetchone()
    finally:
        db.close()

    if row is None:
        return None
    return dict(row)


def fetch_collection_items(collection_id: int) -> List[Dict[str, Any]]:
    """
    Return all items in a given collection, joined with art,
    ordered by position (if present) then art_id.
    """
    db = get_art_db()
    try:
        rows = db.execute(
            """
            SELECT
              aci.id          AS item_id,
              aci.collection_id,
              aci.art_id,
              aci.position,
              aci.created_at  AS item_created_at,
              a.art,
              a.email         AS art_email,
              a.metadata      AS art_metadata,
              a.created_at    AS art_created_at,
              a.updated_at    AS art_updated_at
            FROM art_collection_items aci
            JOIN art a ON a.id = aci.art_id
            WHERE aci.collection_id = ?
            ORDER BY
              COALESCE(aci.position, 999999),
              aci.art_id
            """,
            (collection_id,),
        ).fetchall()
    finally:
        db.close()

    return [dict(r) for r in rows]


if __name__ == "__main__":
    # Find the specific collection
    email = "boris@fantasiagenesis.com"
    name = "fantasiagenesis"

    collection = fetch_collection(email, name)
    if collection is None:
        print(f"No collection found for {email!r} with name {name!r}")
    else:
        print("Collection:")
        print(collection)

        # Load its items
        items = fetch_collection_items(collection["id"])
        print(f"\nItems in collection (count={len(items)}):")
        for item in items:
            print(f"- art_id={item['art_id']}, position={item['position']}, snippet={item['art'][:80]!r}")
