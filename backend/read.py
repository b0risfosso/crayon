# read.py
from __future__ import annotations

import os
import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Optional, Iterable

from flask import Flask, request, jsonify

# Pull shared DB paths and connect() from your project
# (These exist in your codebase alongside jid.py/canvas.py)
from db_shared import connect, USAGE_DB, PICTURE_DB

# ------------------------------------------------------------------------------
# Flask app

app = Flask(__name__)

# ------------------------------------------------------------------------------
# Local helpers used by the read endpoints

def _safe_get_picture_db_path() -> str:
    """
    Resolve the path to the picture DB. Prefer env override, else shared constant.
    """
    return os.getenv("PICTURE_DB", PICTURE_DB)

@contextmanager
def _maybe_connect(db_path: str):
    """
    Short-lived sqlite connection contextmanager for read endpoints.
    """
    conn = sqlite3.connect(db_path)
    try:
        yield conn
    finally:
        try:
            conn.close()
        except Exception:
            pass

def _row_to_dict(cursor: sqlite3.Cursor, row: sqlite3.Row) -> dict:
    return {d[0]: row[i] for i, d in enumerate(cursor.description)}

def _ensure_prompt_outputs_table(db_path: str) -> None:
    """
    Create prompt_outputs table if missing.
    Matches the columns used by canvas.run_collection storage.
    """
    ddl = """
    CREATE TABLE IF NOT EXISTS prompt_outputs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        vision_id INTEGER,
        picture_id INTEGER,
        collection TEXT,
        prompt_key TEXT,
        prompt_text TEXT,
        system_text TEXT,
        output_text TEXT,
        model TEXT,
        email TEXT,
        metadata TEXT,
        created_at TEXT
    );
    """
    with _maybe_connect(db_path) as conn:
        conn.execute(ddl)
        conn.commit()

# ------------------------------------------------------------------------------
# Defaults (model fallback if caller omits)
DEFAULT_MODEL = os.getenv("LLM_MODEL", "gpt-5-mini-2025-08-07")

# ------------------------------------------------------------------------------
# Endpoints

@app.route("/read/usage", methods=["GET"])
def usage_today():
    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    model = request.args.get("model", DEFAULT_MODEL)
    conn = connect(USAGE_DB)
    try:
        row = conn.execute(
            "SELECT tokens_in, tokens_out, total_tokens, calls FROM totals_daily WHERE day=? AND model=?",
            (day, model),
        ).fetchone()
        data = {"day": day, "model": model}
        if row:
            data.update({"tokens_in": row[0], "tokens_out": row[1], "total_tokens": row[2], "calls": row[3]})
        else:
            data.update({"tokens_in": 0, "tokens_out": 0, "total_tokens": 0, "calls": 0})
        return jsonify(data), 200
    finally:
        conn.close()


@app.get("/read/architectures")
def list_architectures():
    """
    Query params:
      picture_id (int)               -- required unless vision_id provided
      vision_id  (int)               -- optional
      collection (str)               -- optional filter (e.g., 'duet_worldwright_x_wax')
      include_body (0|1)             -- include output_text (default 0)
      limit (int)                    -- default 50
    Returns: { items: [{ id, collection, prompt_key, created_at, model, email, ...(maybe output_text) }], count }
    """
    pic_id = request.args.get("picture_id", type=int)
    vis_id = request.args.get("vision_id", type=int)
    if not pic_id and not vis_id:
        return jsonify({"error": "picture_id or vision_id is required"}), 400

    collection = (request.args.get("collection") or "").strip() or None
    include_body = bool(int(request.args.get("include_body", "0")))
    limit = request.args.get("limit", default=50, type=int)
    db_path = _safe_get_picture_db_path()

    # Ensure table (no-op if already created)
    try:
        _ensure_prompt_outputs_table(db_path)
    except Exception:
        return jsonify({"items": [], "count": 0})

    cols = "id, collection, prompt_key, created_at, model, email"
    if include_body:
        cols += ", output_text"

    where = []
    args = []
    if pic_id:
        where.append("picture_id = ?")
        args.append(pic_id)
    if vis_id:
        where.append("vision_id = ?")
        args.append(vis_id)
    if collection:
        where.append("collection = ?")
        args.append(collection)

    sql = f"SELECT {cols} FROM prompt_outputs"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY created_at DESC, id DESC LIMIT ?"
    args.append(limit)

    items = []
    with _maybe_connect(db_path) as conn:
        cur = conn.execute(sql, tuple(args))
        cols_list = [d[0] for d in cur.description]
        for row in cur.fetchall():
            items.append({k: v for k, v in zip(cols_list, row)})
    return jsonify({"items": items, "count": len(items)})


@app.get("/read/architecture/<int:arch_id>")
def get_architecture(arch_id: int):
    db_path = _safe_get_picture_db_path()
    try:
        _ensure_prompt_outputs_table(db_path)
    except Exception:
        return jsonify({"error": "not found"}), 404

    with _maybe_connect(db_path) as conn:
        cur = conn.execute("""
            SELECT id, vision_id, picture_id, collection, prompt_key, prompt_text, system_text,
                   output_text, model, email, metadata, created_at
            FROM prompt_outputs
            WHERE id = ?
        """, (arch_id,))
        row = cur.fetchone()
        if not row:
            return jsonify({"error": "not found"}), 404
        cols = [d[0] for d in cur.description]
    rec = {k: v for k, v in zip(cols, row)}
    # Try to parse metadata
    try:
        if rec.get("metadata"):
            rec["metadata"] = json.loads(rec["metadata"])
    except Exception:
        pass
    return jsonify(rec)


@app.route("/read/by_email", methods=["GET"])
def read_by_email():
    email = (request.args.get("email") or "").strip().lower()
    if not email:
        return jsonify({"error": "email is required"}), 400

    try:
        conn = connect(PICTURE_DB)
        cur = conn.cursor()

        # 1) visions for this email
        cur.execute("""
            SELECT id, text, focuses, status, slug, created_at, updated_at
            FROM visions
            WHERE (email = ? OR (email IS NULL AND ? = ''))
            ORDER BY COALESCE(updated_at, created_at) DESC, id DESC
        """, (email, email))
        vision_rows = cur.fetchall()

        visions = []
        vision_ids = []
        for r in vision_rows:
            d = _row_to_dict(cur, r)
            vid = d["id"]
            vision_ids.append(vid)

            # parse focuses (TEXT -> JSON list) defensively
            f_raw = d.get("focuses")
            try:
                f_list = json.loads(f_raw) if f_raw else []
                if isinstance(f_list, dict) and "focuses" in f_list:
                    f_list = f_list["focuses"]
            except Exception:
                f_list = []
            d["focuses"] = f_list

            d.pop("slug", None)  # not needed for this view
            visions.append({**d, "pictures": []})

        # early return if no visions
        if not vision_ids:
            return jsonify({"email": email, "visions": []})

        # 2) pictures for those visions, restricted to this email
        q_marks = ",".join("?" for _ in vision_ids)
        cur.execute(f"""
            SELECT id, vision_id, title, description, function, status, created_at, updated_at
            FROM pictures
            WHERE vision_id IN ({q_marks})
              AND (email = ? OR (email IS NULL AND ? = ''))
            ORDER BY COALESCE(updated_at, created_at) DESC, id DESC
        """, (*vision_ids, email, email))
        pic_rows = cur.fetchall()

        # index visions by id
        vmap = { v["id"]: v for v in visions }

        for r in pic_rows:
            d = _row_to_dict(cur, r)
            vid = d.pop("vision_id", None)
            if vid in vmap:
                vmap[vid]["pictures"].append(d)

        return jsonify({"email": email, "visions": visions})

    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        try:
            conn.close()
        except Exception:
            pass


# ------------------------------------------------------------------------------
# Optional: simple health check
@app.get("/read/healthz")
def healthz():
    return jsonify({"status": "ok"}), 200


@app.get("/read/architectures/counts")
def architecture_counts():
    """
    Query:
      picture_ids: CSV of ints (required unless vision_id provided)
      vision_id: int (optional) -> counts for all pictures in this vision
      collection: str (optional) -> filter counts by collection
    Returns:
      {
        "counts": { "<picture_id>": { "total": N, "by_collection": { "<coll>": n, ... } }, ... }
      }
    """
    pic_ids_csv = (request.args.get("picture_ids") or "").strip()
    vision_id = request.args.get("vision_id", type=int)
    collection = (request.args.get("collection") or "").strip() or None
    db_path = _safe_get_picture_db_path()

    try:
        _ensure_prompt_outputs_table(db_path)
    except Exception:
        return jsonify({"counts": {}})

    where = []
    args = []
    if vision_id:
        where.append("picture_id IN (SELECT id FROM pictures WHERE vision_id = ?)")
        args.append(vision_id)
    else:
        if not pic_ids_csv:
            return jsonify({"error": "picture_ids or vision_id is required"}), 400
        try:
            pic_ids = [int(x) for x in pic_ids_csv.split(",") if x.strip()]
        except Exception:
            return jsonify({"error": "invalid picture_ids"}), 400
        if not pic_ids:
            return jsonify({"counts": {}})
        marks = ",".join("?" for _ in pic_ids)
        where.append(f"picture_id IN ({marks})")
        args.extend(pic_ids)

    if collection:
        where.append("collection = ?")
        args.append(collection)

    sql = f"""
        SELECT picture_id, collection, COUNT(*) AS n
        FROM prompt_outputs
        {"WHERE " + " AND ".join(where) if where else ""}
        GROUP BY picture_id, collection
    """

    out = {}
    with _maybe_connect(db_path) as conn:
        for pid, coll, n in conn.execute(sql, tuple(args)).fetchall():
            d = out.setdefault(str(pid), {"total": 0, "by_collection": {}})
            d["total"] += int(n or 0)
            d["by_collection"][coll or ""] = int(n or 0)

    return jsonify({"counts": out})

@app.get("/read/core_ideas")
def read_core_ideas():
    """
    Query saved core ideas.
    Query params:
      - source_like: substring match on source (optional)
      - email: exact match (optional)
      - limit: int (default 50, max 500)
    Response:
      { "items": [ {id, source, core_idea, email, created_at, updated_at} ] }
    """
    source_like = (request.args.get("source_like") or "").strip()
    email = (request.args.get("email") or "").strip().lower()
    try:
        limit = int(request.args.get("limit", "50"))
    except Exception:
        limit = 50
    limit = max(1, min(500, limit))

    sql = """
      SELECT id, source, core_idea, email, created_at, updated_at
      FROM core_ideas
    """
    clauses, params = [], []
    if source_like:
        clauses.append("source LIKE ?")
        params.append(f"%{source_like}%")
    if email:
        clauses.append("LOWER(IFNULL(email,'')) = ?")
        params.append(email)
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    sql += " ORDER BY datetime(created_at) DESC, id DESC LIMIT ?"
    params.append(limit)

    path = PICTURE_DB
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    try:
        cur = conn.execute(sql, params)
        rows = [dict(r) for r in cur.fetchall()]
        return jsonify({"items": rows})
    except Exception as e:
        return jsonify({"error": f"DB error: {e}"}), 500
    finally:
        conn.close()


@app.get("/read/visions_by_core_idea")
def read_visions_by_core_idea():
    """
    Query visions attached to a specific core_idea_id.
    Query params:
      - core_idea_id: int (optional; if omitted returns latest visions with any link)
      - email: exact match (optional)
      - limit: int (default 50, max 500)
    Response:
      { "items": [ {id, core_idea_id, title, text, email, status, created_at, updated_at} ] }
    """
    cid_raw = request.args.get("core_idea_id")
    email = (request.args.get("email") or "").strip().lower()
    try:
        limit = int(request.args.get("limit", "50"))
    except Exception:
        limit = 50
    limit = max(1, min(500, limit))

    clauses, params = [], []
    if cid_raw is not None and cid_raw != "":
        try:
            cid = int(cid_raw)
            clauses.append("v.core_idea_id = ?")
            params.append(cid)
        except Exception:
            return jsonify({"error": "core_idea_id must be an integer"}), 400
    else:
        clauses.append("v.core_idea_id IS NOT NULL")

    if email:
        clauses.append("LOWER(IFNULL(v.email,'')) = ?")
        params.append(email)

    sql = """
    SELECT v.id, v.core_idea_id, v.title, v.text, v.focuses, v.email, v.status,
            v.created_at, v.updated_at
    FROM visions v
    """

    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    sql += " ORDER BY datetime(v.created_at) DESC, v.id DESC LIMIT ?"
    params.append(limit)

    path = PICTURE_DB
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    try:
        cur = conn.execute(sql, params)
        rows = []
        for r in cur.fetchall():
            d = dict(r)
            # default
            d["realization"] = None
            # extract first focus' "focus" field as the realization text
            try:
                foc = json.loads(d.get("focuses") or "[]")
                if isinstance(foc, list) and foc:
                    f0 = foc[0] or {}
                    d["realization"] = ((f0.get("focus") or "").strip()) or None
            except Exception:
                d["realization"] = None
            # don't expose raw focuses blob
            d.pop("focuses", None)
            rows.append(d)
        return jsonify({"items": rows})
    except Exception as e:
        return jsonify({"error": f"DB error: {e}"}), 500
    finally:
        conn.close()