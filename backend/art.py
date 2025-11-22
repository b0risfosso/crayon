#!/usr/bin/env python3
"""
art_api.py

Flask CRUD API for art.db (SQLite).

Table expected (from our earlier schema):
CREATE TABLE art (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    art         TEXT NOT NULL,
    email       TEXT,
    metadata    TEXT,     -- optional JSON string
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);

Notes:
- If your table uses DEFAULT timestamps you can omit created_at/updated_at on insert.
- This API will always supply timestamps to be safe.
"""

from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Tuple

from flask import Flask, request, jsonify, g, abort

DB_PATH = os.environ.get("ART_DB_PATH", "art.db")

app = Flask(__name__)
app.config["JSON_SORT_KEYS"] = False


# -------------------------
# DB helpers
# -------------------------

def utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def get_db() -> sqlite3.Connection:
    """
    Opens a connection per request and stores it on flask.g
    """
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
    d = dict(row)
    # attempt to parse metadata (if it's JSON)
    md = d.get("metadata")
    if isinstance(md, str):
        try:
            d["metadata"] = json.loads(md)
        except Exception:
            # leave as raw string if not valid JSON
            pass
    return d


def require_json() -> Dict[str, Any]:
    if not request.is_json:
        abort(400, description="Request must be application/json")
    payload = request.get_json(silent=True)
    if payload is None:
        abort(400, description="Invalid JSON body")
    return payload


def paginate_args() -> Tuple[int, int]:
    try:
        limit = int(request.args.get("limit", 50))
        offset = int(request.args.get("offset", 0))
    except ValueError:
        abort(400, description="limit/offset must be integers")
    limit = max(1, min(limit, 500))
    offset = max(0, offset)
    return limit, offset


# -------------------------
# Health / meta
# -------------------------

@app.get("/health")
def health():
    return jsonify({"ok": True, "db_path": DB_PATH})


# -------------------------
# CREATE
# -------------------------

@app.post("/api/art")
def create_art():
    payload = require_json()
    art_text = payload.get("art")
    email = payload.get("email")
    metadata = payload.get("metadata")

    if not isinstance(art_text, str) or not art_text.strip():
        abort(400, description="'art' is required and must be a non-empty string")

    created_at = utc_now_iso()
    updated_at = created_at

    md_str = None
    if metadata is not None:
        # accept dict/list/str; store as JSON string if not already
        if isinstance(metadata, (dict, list)):
            md_str = json.dumps(metadata, ensure_ascii=False)
        elif isinstance(metadata, str):
            md_str = metadata
        else:
            abort(400, description="'metadata' must be object/array/string if provided")

    db = get_db()
    cur = db.execute(
        """
        INSERT INTO art (art, email, metadata, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        (art_text.strip(), email, md_str, created_at, updated_at),
    )
    db.commit()

    new_id = cur.lastrowid
    row = db.execute("SELECT * FROM art WHERE id = ?", (new_id,)).fetchone()
    return jsonify(row_to_dict(row)), 201


# -------------------------
# READ (list)
# -------------------------

@app.get("/api/art")
def list_art():
    limit, offset = paginate_args()
    email = request.args.get("email")
    q = request.args.get("q")
    order = request.args.get("order", "desc").lower()

    if order not in ("asc", "desc"):
        abort(400, description="order must be 'asc' or 'desc'")

    where = []
    params = []

    if email:
        where.append("email = ?")
        params.append(email)

    if q:
        where.append("art LIKE ?")
        params.append(f"%{q}%")

    where_sql = ("WHERE " + " AND ".join(where)) if where else ""
    sql = f"""
        SELECT * FROM art
        {where_sql}
        ORDER BY created_at {order}
        LIMIT ? OFFSET ?
    """
    params.extend([limit, offset])

    db = get_db()
    rows = db.execute(sql, params).fetchall()
    return jsonify([row_to_dict(r) for r in rows])


# -------------------------
# READ (single)
# -------------------------

@app.get("/api/art/<int:art_id>")
def get_art(art_id: int):
    db = get_db()
    row = db.execute("SELECT * FROM art WHERE id = ?", (art_id,)).fetchone()
    if row is None:
        abort(404, description="art not found")
    return jsonify(row_to_dict(row))


# -------------------------
# UPDATE (full replace)
# -------------------------

@app.put("/api/art/<int:art_id>")
def update_art(art_id: int):
    payload = require_json()
    art_text = payload.get("art")
    email = payload.get("email")
    metadata = payload.get("metadata")

    if art_text is None:
        abort(400, description="'art' is required for PUT")

    if not isinstance(art_text, str) or not art_text.strip():
        abort(400, description="'art' must be a non-empty string")

    md_str = None
    if metadata is not None:
        if isinstance(metadata, (dict, list)):
            md_str = json.dumps(metadata, ensure_ascii=False)
        elif isinstance(metadata, str):
            md_str = metadata
        else:
            abort(400, description="'metadata' must be object/array/string if provided")

    updated_at = utc_now_iso()
    db = get_db()

    cur = db.execute(
        """
        UPDATE art
        SET art = ?, email = ?, metadata = ?, updated_at = ?
        WHERE id = ?
        """,
        (art_text.strip(), email, md_str, updated_at, art_id),
    )
    db.commit()

    if cur.rowcount == 0:
        abort(404, description="art not found")

    row = db.execute("SELECT * FROM art WHERE id = ?", (art_id,)).fetchone()
    return jsonify(row_to_dict(row))


# -------------------------
# UPDATE (partial)
# -------------------------

@app.patch("/api/art/<int:art_id>")
def patch_art(art_id: int):
    payload = require_json()
    fields = []
    params = []

    if "art" in payload:
        art_text = payload["art"]
        if not isinstance(art_text, str) or not art_text.strip():
            abort(400, description="'art' must be a non-empty string")
        fields.append("art = ?")
        params.append(art_text.strip())

    if "email" in payload:
        fields.append("email = ?")
        params.append(payload.get("email"))

    if "metadata" in payload:
        metadata = payload.get("metadata")
        if metadata is None:
            md_str = None
        elif isinstance(metadata, (dict, list)):
            md_str = json.dumps(metadata, ensure_ascii=False)
        elif isinstance(metadata, str):
            md_str = metadata
        else:
            abort(400, description="'metadata' must be object/array/string if provided")
        fields.append("metadata = ?")
        params.append(md_str)

    if not fields:
        abort(400, description="No valid fields to patch")

    fields.append("updated_at = ?")
    params.append(utc_now_iso())
    params.append(art_id)

    sql = f"UPDATE art SET {', '.join(fields)} WHERE id = ?"

    db = get_db()
    cur = db.execute(sql, params)
    db.commit()

    if cur.rowcount == 0:
        abort(404, description="art not found")

    row = db.execute("SELECT * FROM art WHERE id = ?", (art_id,)).fetchone()
    return jsonify(row_to_dict(row))


# -------------------------
# DELETE
# -------------------------

@app.delete("/api/art/<int:art_id>")
def delete_art(art_id: int):
    db = get_db()
    cur = db.execute("DELETE FROM art WHERE id = ?", (art_id,))
    db.commit()
    if cur.rowcount == 0:
        abort(404, description="art not found")
    return jsonify({"deleted": True, "id": art_id})


# -------------------------
# Simple error JSON
# -------------------------

@app.errorhandler(400)
@app.errorhandler(404)
@app.errorhandler(500)
def json_error(err):
    code = getattr(err, "code", 500)
    return jsonify({
        "error": True,
        "code": code,
        "message": getattr(err, "description", str(err)),
    }), code


if __name__ == "__main__":
    # Example:
    #   ART_DB_PATH=/var/www/site/current/art.db python art_api.py
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5055)), debug=True)
