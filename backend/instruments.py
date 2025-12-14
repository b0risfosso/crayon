#!/usr/bin/env python3
"""
Simple Flask API for instruments.
- Stores instruments in SQLite at /var/www/site/data/instruments.db
- Supports create, list, read, update, delete
"""

from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timezone
from typing import Dict, Any

from flask import Flask, request, jsonify, g, abort

DB_PATH = "/var/www/site/data/instruments.db"

app = Flask(__name__)
app.config["JSON_SORT_KEYS"] = False


def iso_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat() + "Z"


def init_db() -> None:
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    try:
        conn.execute("PRAGMA foreign_keys = ON;")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS instruments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                description TEXT DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            """
        )
        conn.commit()
    finally:
        conn.close()


def get_db() -> sqlite3.Connection:
    if "db" not in g:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON;")
        g.db = conn
    return g.db


@app.teardown_appcontext
def close_db(_exc: BaseException | None) -> None:
    conn = g.pop("db", None)
    if conn is not None:
        conn.close()


def row_to_dict(row: sqlite3.Row) -> Dict[str, Any]:
    return {
        "id": row["id"],
        "name": row["name"],
        "description": row["description"] or "",
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


@app.get("/instruments/health")
def health() -> Any:
    return jsonify({"ok": True, "db_path": DB_PATH})


@app.get("/instruments")
def list_instruments() -> Any:
    db = get_db()
    rows = db.execute("SELECT * FROM instruments ORDER BY created_at DESC, id DESC").fetchall()
    return jsonify([row_to_dict(r) for r in rows])


@app.post("/instruments")
def create_instrument() -> Any:
    payload = request.get_json(silent=True) or {}
    name = payload.get("name")
    description = payload.get("description") or ""

    if not isinstance(name, str) or not name.strip():
        abort(400, description="'name' is required and must be a non-empty string")

    now = iso_now()
    db = get_db()
    cur = db.execute(
        """
        INSERT INTO instruments (name, description, created_at, updated_at)
        VALUES (?, ?, ?, ?)
        """,
        (name.strip(), description.strip(), now, now),
    )
    db.commit()

    row = db.execute("SELECT * FROM instruments WHERE id = ?", (cur.lastrowid,)).fetchone()
    return jsonify(row_to_dict(row)), 201


def _require_instrument(instrument_id: int) -> sqlite3.Row:
    db = get_db()
    row = db.execute("SELECT * FROM instruments WHERE id = ?", (instrument_id,)).fetchone()
    if not row:
        abort(404, description="Instrument not found")
    return row


@app.get("/instruments/<int:instrument_id>")
def get_instrument(instrument_id: int) -> Any:
    row = _require_instrument(instrument_id)
    return jsonify(row_to_dict(row))


@app.put("/instruments/<int:instrument_id>")
@app.patch("/instruments/<int:instrument_id>")
def update_instrument(instrument_id: int) -> Any:
    payload = request.get_json(silent=True) or {}
    updates = {}

    if "name" in payload:
        name = payload.get("name")
        if not isinstance(name, str) or not name.strip():
            abort(400, description="'name' must be a non-empty string when provided")
        updates["name"] = name.strip()

    if "description" in payload:
        desc = payload.get("description")
        if desc is None:
            desc = ""
        elif not isinstance(desc, str):
            abort(400, description="'description' must be a string when provided")
        updates["description"] = desc.strip()

    if not updates:
        abort(400, description="No valid fields to update")

    db = get_db()
    _require_instrument(instrument_id)

    updates["updated_at"] = iso_now()
    sets = ", ".join(f"{k} = ?" for k in updates.keys())
    args = list(updates.values()) + [instrument_id]
    db.execute(f"UPDATE instruments SET {sets} WHERE id = ?", args)
    db.commit()

    row = db.execute("SELECT * FROM instruments WHERE id = ?", (instrument_id,)).fetchone()
    return jsonify(row_to_dict(row))


@app.delete("/instruments/<int:instrument_id>")
def delete_instrument(instrument_id: int) -> Any:
    db = get_db()
    _require_instrument(instrument_id)
    db.execute("DELETE FROM instruments WHERE id = ?", (instrument_id,))
    db.commit()
    return jsonify({"ok": True, "deleted_id": instrument_id})


# Ensure the DB exists when the module is imported.
init_db()


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
