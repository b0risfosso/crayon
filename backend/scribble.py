# scribble.py
from __future__ import annotations

import os
import json
import sqlite3
from typing import Any, Dict, List, Optional, Tuple

from flask import Flask, request, jsonify

from db_shared import (
    connect,
    PICTURE_DB,
    init_picture_db,
    _email_match_clause,   # reuse existing email semantics
    _iso_now,
)

# ------------------------------------------------------------------------------
# Flask app + DB init
# ------------------------------------------------------------------------------

app = Flask(__name__)

# Ensure picture_schema.sql (including thoughts table) is applied
init_picture_db()

THOUGHT_COLUMNS = [
    "id",
    "collection",
    "title",
    "text",
    "vision_id",
    "core_idea_id",
    "source",
    "author",
    "context",
    "order_index",
    "tags",
    "email",
    "metadata",
    "created_at",
    "updated_at",
]


def _row_to_dict(row: sqlite3.Row) -> Dict[str, Any]:
    return {k: row[k] for k in row.keys()}


def _parse_metadata_field(raw: Any) -> Any:
    """
    Attempt to JSON-decode metadata; if it fails, return as-is.
    """
    if raw is None:
        return None
    if isinstance(raw, (dict, list)):
        return raw
    try:
        return json.loads(raw)
    except Exception:
        return raw


def _get_db_path() -> str:
    """
    Keep symmetry with other modules, but allow override.
    """
    return os.getenv("PICTURE_DB", PICTURE_DB)


# ------------------------------------------------------------------------------
# GET /api/thoughts/collections
# ------------------------------------------------------------------------------

@app.get("/api/thoughts/collections")
def thoughts_collections():
    """
    Return all collections with counts.

    Query params:
      email (str, optional)  -- if provided, restrict to thoughts for this email

    Response:
      [
        { "collection": "money", "count": 12 },
        { "collection": "infinite_beauty_depth", "count": 7 },
        ...
      ]
    """
    email_param = request.args.get("email")
    db_path = _get_db_path()

    sql = """
        SELECT collection, COUNT(*) AS count
        FROM thoughts
    """
    where: List[str] = []
    params: List[Any] = []

    # Only filter by email if caller passes ?email=...
    if email_param is not None:
        clause, extra = _email_match_clause(email_param or None)
        where.append(clause)
        params.extend(extra)

    if where:
        sql += " WHERE " + " AND ".join(where)

    sql += " GROUP BY collection ORDER BY collection COLLATE NOCASE"

    conn = connect(db_path)
    try:
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute(sql, params)
        rows = cur.fetchall()
        out = [{"collection": r["collection"], "count": r["count"]} for r in rows]
        return jsonify(out), 200
    except Exception as e:
        return jsonify({"error": f"DB error: {e}"}), 500
    finally:
        conn.close()


# ------------------------------------------------------------------------------
# GET /api/thoughts?collection=name
# ------------------------------------------------------------------------------

@app.get("/api/thoughts")
def list_thoughts():
    """
    List all thoughts in a collection.

    Query params:
      collection (str)  -- required
      email (str)       -- optional, only thoughts for that email

    Response:
      [
        {
          "id": ...,
          "collection": "...",
          "title": "...",
          "text": "...",
          "vision_id": ...,
          "core_idea_id": ...,
          "source": "...",
          "author": "...",
          "context": "...",
          "order_index": ...,
          "tags": "...",
          "email": "...",
          "metadata": {...} | "raw" | null,
          "created_at": "...",
          "updated_at": "..."
        },
        ...
      ]
    """
    collection = (request.args.get("collection") or "").strip()
    if not collection:
        return jsonify({"error": "collection is required"}), 400

    email_param = request.args.get("email")
    db_path = _get_db_path()

    sql = f"""
        SELECT {", ".join(THOUGHT_COLUMNS)}
        FROM thoughts
        WHERE collection = ?
    """
    params: List[Any] = [collection]
    where_extra: List[str] = []

    if email_param is not None:
        clause, extra = _email_match_clause(email_param or None)
        where_extra.append(clause)
        params.extend(extra)

    if where_extra:
        sql += " AND " + " AND ".join(where_extra)

    sql += """
        ORDER BY
          COALESCE(order_index, 0) ASC,
          COALESCE(created_at, updated_at) ASC,
          id ASC
    """

    conn = connect(db_path)
    try:
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute(sql, params)
        rows = cur.fetchall()

        out: List[Dict[str, Any]] = []
        for r in rows:
            d = _row_to_dict(r)
            d["metadata"] = _parse_metadata_field(d.get("metadata"))
            out.append(d)
        return jsonify(out), 200
    except Exception as e:
        return jsonify({"error": f"DB error: {e}"}), 500
    finally:
        conn.close()


# ------------------------------------------------------------------------------
# POST /api/thoughts
# ------------------------------------------------------------------------------

@app.post("/api/thoughts")
def create_thought():
    """
    Create a new thought.

    JSON payload:
      {
        "collection": "money",          -- required
        "title": "Money as Flow",      -- optional
        "text": "...",                 -- required
        "vision_id": 123,              -- optional
        "core_idea_id": 456,           -- optional
        "source": "manual|book|jid",   -- optional
        "author": "Boris",             -- optional
        "context": "Marshall, ch.8",   -- optional
        "order_index": 0,              -- optional
        "tags": "fluid, competition",  -- optional
        "email": "me@example.com",     -- optional
        "metadata": {...}              -- optional (dict/list/string)
      }

    Response: full saved row (same shape as GET /api/thoughts).
    """
    try:
        payload = request.get_json(force=True, silent=False) or {}
    except Exception:
        return jsonify({"error": "invalid JSON"}), 400

    collection = (payload.get("collection") or "").strip()
    text = (payload.get("text") or "").strip()

    if not collection:
        return jsonify({"error": "collection is required"}), 400
    if not text:
        return jsonify({"error": "text is required"}), 400

    title = (payload.get("title") or "").strip() or None
    vision_id = payload.get("vision_id")
    core_idea_id = payload.get("core_idea_id")
    source = (payload.get("source") or "").strip() or None
    author = (payload.get("author") or "").strip() or None
    context = (payload.get("context") or "").strip() or None
    order_index = payload.get("order_index")
    tags = (payload.get("tags") or "").strip() or None
    email = (payload.get("email") or "").strip() or None
    metadata = payload.get("metadata")

    if isinstance(metadata, (dict, list)):
        metadata_str = json.dumps(metadata, separators=(",", ":"))
    else:
        metadata_str = metadata if metadata is None or isinstance(metadata, str) else str(metadata)

    db_path = _get_db_path()
    conn = connect(db_path)
    try:
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()

        # rely on DB triggers or explicit timestamps; we do explicit here
        cur.execute(
            """
            INSERT INTO thoughts
                (collection, title, text, vision_id, core_idea_id, source,
                 author, context, order_index, tags, email, metadata,
                 created_at, updated_at)
            VALUES
                (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                 strftime('%Y-%m-%dT%H:%M:%fZ','now'),
                 strftime('%Y-%m-%dT%H:%M:%fZ','now'))
            """,
            [
                collection,
                title,
                text,
                vision_id,
                core_idea_id,
                source,
                author,
                context,
                order_index,
                tags,
                email,
                metadata_str,
            ],
        )
        new_id = cur.lastrowid

        cur.execute(
            f"SELECT {', '.join(THOUGHT_COLUMNS)} FROM thoughts WHERE id = ?",
            [new_id],
        )
        row = cur.fetchone()
        if not row:
            return jsonify({"error": "Failed to re-read newly created thought"}), 500

        d = _row_to_dict(row)
        d["metadata"] = _parse_metadata_field(d.get("metadata"))
        return jsonify(d), 201
    except Exception as e:
        return jsonify({"error": f"DB error: {e}"}), 500
    finally:
        conn.close()


# ------------------------------------------------------------------------------
# PUT /api/thoughts/<id>
# ------------------------------------------------------------------------------

@app.put("/api/thoughts/<int:thought_id>")
def update_thought(thought_id: int):
    """
    Update an existing thought.

    JSON payload: same keys as POST /api/thoughts.
    Keys present in the payload replace existing values; keys omitted are kept.

    Response: updated row.
    """
    try:
        payload = request.get_json(force=True, silent=False) or {}
    except Exception:
        return jsonify({"error": "invalid JSON"}), 400

    db_path = _get_db_path()
    conn = connect(db_path)
    try:
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()

        cur.execute(
            f"SELECT {', '.join(THOUGHT_COLUMNS)} FROM thoughts WHERE id = ?",
            [thought_id],
        )
        row = cur.fetchone()
        if not row:
            return jsonify({"error": "Thought not found"}), 404

        current = _row_to_dict(row)

        def _get_field(name: str, strip: bool = True) -> Any:
            if name not in payload:
                return current.get(name)
            val = payload.get(name)
            if strip and isinstance(val, str):
                val = val.strip()
            return val

        collection = _get_field("collection")
        text = _get_field("text")

        if not collection:
            return jsonify({"error": "collection is required"}), 400
        if not text:
            return jsonify({"error": "text is required"}), 400

        title = _get_field("title")
        vision_id = _get_field("vision_id", strip=False)
        core_idea_id = _get_field("core_idea_id", strip=False)
        source = _get_field("source")
        author = _get_field("author")
        context = _get_field("context")
        order_index = _get_field("order_index", strip=False)
        tags = _get_field("tags")
        email = _get_field("email")
        metadata = _get_field("metadata", strip=False)

        if isinstance(metadata, (dict, list)):
            metadata_str = json.dumps(metadata, separators=(",", ":"))
        else:
            metadata_str = metadata if metadata is None or isinstance(metadata, str) else str(metadata)

        cur.execute(
            """
            UPDATE thoughts
            SET
              collection = ?,
              title      = ?,
              text       = ?,
              vision_id  = ?,
              core_idea_id = ?,
              source     = ?,
              author     = ?,
              context    = ?,
              order_index = ?,
              tags       = ?,
              email      = ?,
              metadata   = ?,
              updated_at = strftime('%Y-%m-%dT%H:%M:%fZ','now')
            WHERE id = ?
            """,
            [
                collection,
                (title or None),
                text,
                vision_id,
                core_idea_id,
                (source or None),
                (author or None),
                (context or None),
                order_index,
                (tags or None),
                (email or None),
                metadata_str,
                thought_id,
            ],
        )

        cur.execute(
            f"SELECT {', '.join(THOUGHT_COLUMNS)} FROM thoughts WHERE id = ?",
            [thought_id],
        )
        row2 = cur.fetchone()
        if not row2:
            return jsonify({"error": "Failed to re-read updated thought"}), 500

        d = _row_to_dict(row2)
        d["metadata"] = _parse_metadata_field(d.get("metadata"))
        return jsonify(d), 200
    except Exception as e:
        return jsonify({"error": f"DB error: {e}"}), 500
    finally:
        conn.close()


# ------------------------------------------------------------------------------
# DELETE /api/thoughts/<id>
# ------------------------------------------------------------------------------

@app.delete("/api/thoughts/<int:thought_id>")
def delete_thought(thought_id: int):
    """
    Delete a thought.

    Response:
      { "deleted": true } on success
      { "deleted": false, "error": "..." } on error
    """
    db_path = _get_db_path()
    conn = connect(db_path)
    try:
        cur = conn.cursor()
        cur.execute("DELETE FROM thoughts WHERE id = ?", [thought_id])
        if cur.rowcount == 0:
            return jsonify({"deleted": False, "error": "Thought not found"}), 404
        return jsonify({"deleted": True}), 200
    except Exception as e:
        return jsonify({"deleted": False, "error": f"DB error: {e}"}), 500
    finally:
        conn.close()


@app.get("/api/thoughts/<int:thought_id>")
def get_thought(thought_id: int):
    """
    Fetch a single thought by id.

    Response:
      {
        "id": ...,
        "collection": "...",
        "title": "...",
        "text": "...",
        "vision_id": ...,
        "core_idea_id": ...,
        "source": "...",
        "author": "...",
        "context": "...",
        "order_index": ...,
        "tags": "...",
        "email": "...",
        "metadata": {...} | "raw" | null,
        "created_at": "...",
        "updated_at": "..."
      }
    """
    db_path = _get_db_path()
    conn = connect(db_path)
    try:
      conn.row_factory = sqlite3.Row
      cur = conn.cursor()
      cur.execute(
          f"SELECT {', '.join(THOUGHT_COLUMNS)} FROM thoughts WHERE id = ?",
          (thought_id,),
      )
      row = cur.fetchone()
      if not row:
          return jsonify({"error": "not found"}), 404

      d = _row_to_dict(row)
      d["metadata"] = _parse_metadata_field(d.get("metadata"))
      return jsonify(d), 200
    except Exception as e:
      return jsonify({"error": f"DB error: {e}"}), 500
    finally:
      conn.close()



# ------------------------------------------------------------------------------
# GET /api/thoughts/random?collection=name
# ------------------------------------------------------------------------------

@app.get("/api/thoughts/random")
def random_thought():
    """
    Return a single random thought from a collection.

    Query params:
      collection (str) -- required
      email (str)      -- optional

    Response:
      { <full row> } if found
      {}             if no rows in that collection
    """
    collection = (request.args.get("collection") or "").strip()
    if not collection:
        return jsonify({"error": "collection is required"}), 400

    email_param = request.args.get("email")
    db_path = _get_db_path()

    sql = f"""
        SELECT {', '.join(THOUGHT_COLUMNS)}
        FROM thoughts
        WHERE collection = ?
    """
    params: List[Any] = [collection]
    where_extra: List[str] = []

    if email_param is not None:
        clause, extra = _email_match_clause(email_param or None)
        where_extra.append(clause)
        params.extend(extra)

    if where_extra:
        sql += " AND " + " AND ".join(where_extra)

    sql += " ORDER BY RANDOM() LIMIT 1"

    conn = connect(db_path)
    try:
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute(sql, params)
        row = cur.fetchone()
        if not row:
            return jsonify({}), 200
        d = _row_to_dict(row)
        d["metadata"] = _parse_metadata_field(d.get("metadata"))
        return jsonify(d), 200
    except Exception as e:
        return jsonify({"error": f"DB error: {e}"}), 500
    finally:
        conn.close()


# ------------------------------------------------------------------------------
# POST /api/thoughts/<thought_id>/core_ideas
# Attach a manual core idea to a specific thought.
# ------------------------------------------------------------------------------

@app.post("/api/thoughts/<int:thought_id>/core_ideas")
def create_core_idea_for_thought(thought_id: int):
    """
    Attach a manual core idea to an existing thought.

    JSON payload:
      {
        "core_idea": "short distilled sentence",   # required
        "email": "me@example.com",                 # optional
        "origin": "manual",                        # optional (defaults to 'manual')
        "metadata": {...}                          # optional, JSON-serializable
      }

    Response (201):
      {
        "id": ...,
        "source": "thought:<thought_id>",
        "core_idea": "...",
        "email": "...",
        "origin": "manual",
        "metadata": {...} | "raw" | null,
        "created_at": "...",
        "updated_at": "..."
      }
    """
    payload = request.get_json(silent=True) or {}

    core_text = (payload.get("core_idea") or "").strip()
    if not core_text:
        return jsonify({"error": "core_idea is required"}), 400

    email = (payload.get("email") or "").strip() or None
    origin = (payload.get("origin") or "").strip() or "manual"
    metadata = payload.get("metadata")

    if isinstance(metadata, (dict, list)):
        metadata_str = json.dumps(metadata, separators=(",", ":"))
    else:
        metadata_str = metadata if metadata is None or isinstance(metadata, str) else str(metadata)

    db_path = _get_db_path()
    conn = connect(db_path)
    try:
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()

        # Ensure the thought exists so we don't attach to nothing.
        cur.execute("SELECT 1 FROM thoughts WHERE id = ?", (thought_id,))
        if cur.fetchone() is None:
            return jsonify({"error": f"Thought id {thought_id} not found"}), 404

        source = f"thought:{thought_id}"

        # created_at/updated_at will be filled by triggers if omitted
        ts = _iso_now()
        cur.execute(
            """
            INSERT INTO core_ideas (
                source,
                core_idea,
                email,
                origin,
                metadata,
                created_at,
                updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (source, core_text, email, origin, metadata_str, ts, ts),
        )
        new_id = cur.lastrowid

        cur.execute(
            """
            SELECT id, source, core_idea, email, origin, metadata,
                   created_at, updated_at
            FROM core_ideas
            WHERE id = ?
            """,
            (new_id,),
        )
        row = cur.fetchone()
        if not row:
            return jsonify({"error": "Failed to re-read newly created core_idea"}), 500

        d = _row_to_dict(row)
        d["metadata"] = _parse_metadata_field(d.get("metadata"))
        return jsonify(d), 201

    except Exception as e:
        return jsonify({"error": f"DB error: {e}"}), 500
    finally:
        conn.close()


# ------------------------------------------------------------------------------
# PUT /api/core_ideas/<core_id>
# Edit a core idea (typically text, optionally email/origin/metadata).
# ------------------------------------------------------------------------------

@app.put("/api/core_ideas/<int:core_id>")
def update_core_idea(core_id: int):
    """
    Update a core idea.

    JSON payload:
      {
        "core_idea": "new text",            # required
        "email": "optional@email",          # optional
        "origin": "manual|jid|crayon",      # optional
        "metadata": {...} or "raw string"   # optional
      }

    Response:
      {
        "id": ...,
        "source": "thought:<id>" | "...",
        "core_idea": "...",
        "email": "...",
        "origin": "...",
        "metadata": {...}|"...",
        "created_at": "...",
        "updated_at": "..."
      }
    """
    try:
        payload = request.get_json(force=True, silent=False) or {}
    except Exception:
        return jsonify({"error": "invalid JSON"}), 400

    core_text = (payload.get("core_idea") or "").strip()
    if not core_text:
        return jsonify({"error": "core_idea is required"}), 400

    email = payload.get("email", None)
    origin = payload.get("origin", None)
    metadata = payload.get("metadata", None)

    # Normalize optional fields
    email_val = (email or "").strip() or None if email is not None else None
    origin_val = (origin or "").strip() or None if origin is not None else None

    if isinstance(metadata, (dict, list)):
        metadata_str = json.dumps(metadata, separators=(",", ":"))
    elif metadata is None:
        metadata_str = None
    else:
        metadata_str = metadata if isinstance(metadata, str) else str(metadata)

    db_path = _get_db_path()
    conn = connect(db_path)
    try:
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()

        # Build dynamic UPDATE set clause
        fields = ["core_idea = ?"]
        params = [core_text]

        if email is not None:
            fields.append("email = ?")
            params.append(email_val)

        if origin is not None:
            fields.append("origin = ?")
            params.append(origin_val)

        if metadata is not None:
            fields.append("metadata = ?")
            params.append(metadata_str)

        params.append(core_id)

        sql = f"UPDATE core_ideas SET {', '.join(fields)} WHERE id = ?"
        cur.execute(sql, params)

        if cur.rowcount == 0:
            return jsonify({"error": "Core idea not found"}), 404

        # Trigger trg_core_ideas_update_ts will update updated_at
        cur.execute(
            """
            SELECT id, source, core_idea, email, origin, metadata,
                   created_at, updated_at
            FROM core_ideas
            WHERE id = ?
            """,
            (core_id,),
        )
        row = cur.fetchone()
        if not row:
            return jsonify({"error": "Core idea not found after update"}), 404

        d = _row_to_dict(row)
        d["metadata"] = _parse_metadata_field(d.get("metadata"))
        return jsonify(d), 200

    except Exception as e:
        return jsonify({"error": f"DB error: {e}"}), 500
    finally:
        conn.close()


# ------------------------------------------------------------------------------
# DELETE /api/core_ideas/<core_id>
# Delete a core idea.
# ------------------------------------------------------------------------------

@app.delete("/api/core_ideas/<int:core_id>")
def delete_core_idea(core_id: int):
    """
    Delete a core idea.

    Notes:
      - thoughts.core_idea_id has
        FOREIGN KEY (core_idea_id) REFERENCES core_ideas(id) ON DELETE SET NULL
        so deleting a core idea will nullify any dependent thoughts' core_idea_id.
    """
    db_path = _get_db_path()
    conn = connect(db_path)
    try:
        cur = conn.cursor()
        cur.execute("DELETE FROM core_ideas WHERE id = ?", (core_id,))
        if cur.rowcount == 0:
            return jsonify({"deleted": False, "error": "Core idea not found"}), 404
        return jsonify({"deleted": True}), 200
    except Exception as e:
        return jsonify({"deleted": False, "error": f"DB error: {e}"}), 500
    finally:
        conn.close()


# Optional: local dev
if __name__ == "__main__":
    port = int(os.getenv("SCRIBBLE_PORT", "8083"))
    app.run(host="0.0.0.0", port=port, debug=True)
