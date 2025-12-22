#!/usr/bin/env python3
"""
Simple Flask API for entities.
- Stores entities in SQLite at /var/www/site/data/oasis.db
- Table name: entites
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from flask import Flask, g, request, jsonify, abort

DB_PATH = "/var/www/site/data/oasis.db"

app = Flask(__name__)
app.config["JSON_SORT_KEYS"] = False


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def get_db() -> sqlite3.Connection:
    if "db" not in g:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON;")
        g.db = conn
    return g.db


@app.teardown_appcontext
def close_db(_exc: Optional[BaseException]) -> None:
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


def require_json() -> Dict[str, Any]:
    if not request.is_json:
        abort(400, description="Request must be application/json")
    payload = request.get_json(silent=True)
    if payload is None:
        abort(400, description="Invalid JSON body")
    return payload


def _require_entity(entity_id: int) -> sqlite3.Row:
    db = get_db()
    row = db.execute("SELECT * FROM entites WHERE id = ?", (entity_id,)).fetchone()
    if not row:
        abort(404, description="Entity not found")
    return row


@app.get("/oasis/health")
def health() -> Any:
    return jsonify({"ok": True, "db_path": DB_PATH})


@app.get("/oasis")
def list_entities() -> Any:
    db = get_db()
    rows = db.execute("SELECT * FROM entites ORDER BY created_at DESC, id DESC").fetchall()
    return jsonify([row_to_dict(r) for r in rows])


@app.post("/oasis")
def create_entity() -> Any:
    payload = require_json()
    name = payload.get("name")
    description = payload.get("description") or ""

    if not isinstance(name, str) or not name.strip():
        abort(400, description="'name' is required and must be a non-empty string")
    if description is not None and not isinstance(description, str):
        abort(400, description="'description' must be a string when provided")

    now = utc_now_iso()
    db = get_db()
    cur = db.execute(
        """
        INSERT INTO entites (name, description, created_at, updated_at)
        VALUES (?, ?, ?, ?)
        """,
        (name.strip(), description.strip(), now, now),
    )
    db.commit()

    row = db.execute("SELECT * FROM entites WHERE id = ?", (cur.lastrowid,)).fetchone()
    return jsonify(row_to_dict(row)), 201


@app.get("/oasis/<int:entity_id>")
def get_entity(entity_id: int) -> Any:
    row = _require_entity(entity_id)
    return jsonify(row_to_dict(row))


@app.delete("/oasis/<int:entity_id>")
def delete_entity(entity_id: int) -> Any:
    db = get_db()
    _require_entity(entity_id)
    db.execute("DELETE FROM entites WHERE id = ?", (entity_id,))
    db.commit()
    return jsonify({"ok": True, "deleted_id": entity_id})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
