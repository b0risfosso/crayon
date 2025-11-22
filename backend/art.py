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

import re

def normalize(s: str) -> str:
    return re.sub(r"\s+", " ", s.lower()).strip()

def tokenize(s: str):
    s = normalize(s)
    return [t for t in re.split(r"[^a-z0-9]+", s) if t]

def trigram_set(s: str):
    s = normalize(s)
    s = f"  {s}  "
    return {s[i:i+3] for i in range(len(s)-2)}

def similarity(query: str, text: str) -> float:
    """
    Cheap but decent semantic-ish closeness:
    - token Jaccard
    - trigram Jaccard
    Weighted toward token overlap.
    """
    qt = set(tokenize(query))
    tt = set(tokenize(text))
    if not qt or not tt:
        token_score = 0.0
    else:
        token_score = len(qt & tt) / len(qt | tt)

    q3 = trigram_set(query)
    t3 = trigram_set(text)
    if not q3 or not t3:
        tri_score = 0.0
    else:
        tri_score = len(q3 & t3) / len(q3 | t3)

    return 0.7 * token_score + 0.3 * tri_score

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
        limit = int(request.args.get("limit", 500))
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
    random_flag = request.args.get("random")

    if order not in ("asc", "desc"):
        abort(400, description="order must be 'asc' or 'desc'")

    where = []
    params = []

    if email:
        where.append("email = ?")
        params.append(email)

    db = get_db()

    # -------------------------
    # RANDOM LOAD MODE
    # -------------------------
    if random_flag in ("1", "true", "yes"):
        where_sql = ("WHERE " + " AND ".join(where)) if where else ""
        sql = f"""
            SELECT * FROM art
            {where_sql}
            ORDER BY RANDOM()
            LIMIT ?
        """
        rows = db.execute(sql, params + [limit]).fetchall()
        return jsonify([row_to_dict(r) for r in rows])

    # -------------------------
    # SEARCH MODE
    # -------------------------
    if q:
        tokens = tokenize(q)

        # Candidate pool: any row that matches any token in LIKE
        cand_where = list(where)
        cand_params = list(params)

        if tokens:
            like_clauses = []
            for t in tokens:
                like_clauses.append("art LIKE ?")
                cand_params.append(f"%{t}%")
            cand_where.append("(" + " OR ".join(like_clauses) + ")")
        else:
            cand_where.append("art LIKE ?")
            cand_params.append(f"%{q}%")

        cand_where_sql = "WHERE " + " AND ".join(cand_where)
        # Pull a bigger pool to rank from
        candidate_limit = max(limit * 5, 100)

        cand_sql = f"""
            SELECT * FROM art
            {cand_where_sql}
            LIMIT ?
        """
        candidates = db.execute(cand_sql, cand_params + [candidate_limit]).fetchall()

        scored = []
        for r in candidates:
            txt = r["art"] or ""
            scored.append((similarity(q, txt), r))

        scored.sort(key=lambda x: x[0], reverse=True)
        top_rows = [row_to_dict(r) for score, r in scored[:limit]]
        return jsonify(top_rows)

    # -------------------------
    # NORMAL LIST MODE
    # -------------------------
    where_sql = ("WHERE " + " AND ".join(where)) if where else ""
    sql = f"""
        SELECT * FROM art
        {where_sql}
        ORDER BY created_at {order}
        LIMIT ? OFFSET ?
    """
    rows = db.execute(sql, params + [limit, offset]).fetchall()
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
