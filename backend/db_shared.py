from __future__ import annotations
import sqlite3, json, os, datetime as dt
from typing import Optional, Dict, Any, Tuple
import hashlib

PICTURE_DB = os.getenv("PICTURE_DB", "/var/www/site/data/picture.db")
USAGE_DB   = os.getenv("LLM_USAGE_DB", "/var/www/site/data/llm_usage.db")

def _iso_now() -> str:
    return dt.datetime.utcnow().replace(microsecond=0).isoformat() + "Z"

def connect(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path, isolation_level=None, check_same_thread=False)
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn

def _has_column(conn, table, col):
    cur = conn.execute(f"PRAGMA table_info({table})")
    return any(r[1] == col for r in cur.fetchall())

def ensure_visions_core_idea(conn):
    # Add the column only if missing (old SQLite-safe; no IF NOT EXISTS)
    if not _has_column(conn, "visions", "core_idea_id"):
        conn.execute(
            "ALTER TABLE visions "
            "ADD COLUMN core_idea_id INTEGER REFERENCES core_ideas(id) ON DELETE SET NULL"
        )
    # Index is safe to create repeatedly with IF NOT EXISTS
    conn.execute("CREATE INDEX IF NOT EXISTS idx_visions_core_idea ON visions(core_idea_id)")
    conn.commit()


# --- init ---


def init_picture_db(conn: Optional[sqlite3.Connection]=None) -> None:
    close = False
    if conn is None:
        conn, close = connect(PICTURE_DB), True
    try:
        conn.executescript(open("picture_schema.sql", "r", encoding="utf-8").read())
        ensure_visions_core_idea(conn)
    finally:
        if close:
            conn.close()

def init_usage_db(conn: Optional[sqlite3.Connection]=None) -> None:
    close = False
    if conn is None:
        conn, close = connect(USAGE_DB), True
    try:
        conn.executescript(open("llm_usage_schema.sql", "r", encoding="utf-8").read())
    finally:
        if close:
            conn.close()


# ---------------------- Picture lookup/update helpers -------------------------
from typing import Optional, Dict, Any, Tuple

def _email_match_clause(email: Optional[str]) -> Tuple[str, Tuple]:
    if email is None:
        return "email IS NULL", tuple()
    else:
        return "email = ?", (email,)

def find_picture_id_by_signature(
    *,
    vision_id: int,
    title: str,
    description: str,
    email: Optional[str],
) -> Optional[int]:
    """
    Return picture.id if a row under this vision matches title, description, and email (or NULL).
    """
    conn = connect(PICTURE_DB)
    try:
        email_clause, email_args = _email_match_clause(email)
        row = conn.execute(
            f"""
            SELECT id FROM pictures
            WHERE vision_id = ? AND description = ? AND {email_clause}
            ORDER BY created_at ASC LIMIT 1
            """,
            (vision_id, description, *email_args),
        ).fetchone()
        return int(row[0]) if row else None
    finally:
        conn.close()


# Find-or-create picture by (vision_id, title, description, email)
def find_or_create_picture_by_signature(
    *,
    vision_id: int,
    title: str,
    description: str,
    email: Optional[str],
    source: Optional[str] = "crayon",
    default_status: str = "draft",
    metadata: Optional[Dict[str, Any]] = None
) -> int:
    pic_id = find_picture_id_by_signature(
        vision_id=vision_id,
        title=title,
        description=description,
        email=email,
    )
    if pic_id:
        return pic_id
    return create_picture(
        vision_id=vision_id,
        focus=None,
        title=title,
        description=description,
        function=None,
        explanation=None,
        email=email,
        order_index=0,
        status=default_status,
        source=source,
        metadata=metadata or {},
        assets={},
    )

import json
import hashlib

def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()

def find_wax_id_by_picture_email(*, picture_id: int, email: Optional[str]) -> Optional[int]:
    conn = connect(PICTURE_DB)
    try:
        if email is None:
            row = conn.execute(
                "SELECT id FROM waxes WHERE picture_id=? AND email IS NULL LIMIT 1",
                (picture_id,)
            ).fetchone()
        else:
            row = conn.execute(
                "SELECT id FROM waxes WHERE picture_id=? AND email=? LIMIT 1",
                (picture_id, email)
            ).fetchone()
        return int(row[0]) if row else None
    finally:
        conn.close()

def update_wax_append(
    wax_id: int,
    *,
    add_content: str,
    email: Optional[str],
    source: Optional[str] = "crayon",
    metadata_merge: Optional[Dict[str, Any]] = None,
    separator: str = "\n\n---\n\n"
) -> int:
    """
    Append new content to an existing wax row; update content_hash and metadata.
    """
    conn = connect(PICTURE_DB)
    try:
        row = conn.execute("SELECT content, metadata FROM waxes WHERE id=?", (wax_id,)).fetchone()
        if not row:
            return wax_id
        cur_content, cur_meta = row
        new_content = (cur_content or "") + (separator + add_content if add_content else "")
        new_hash = _sha256(new_content)
        merged_meta = _json_merge(cur_meta, json.dumps(metadata_merge or {}, ensure_ascii=False))
        conn.execute(
            "UPDATE waxes SET content=?, content_hash=?, email=?, source=?, metadata=?, updated_at=? WHERE id=?",
            (new_content, new_hash, email, source, merged_meta, _iso_now(), wax_id)
        )
        conn.commit()
        return wax_id
    finally:
        conn.close()

def create_wax_for_picture(
    *,
    vision_id: int,
    picture_id: int,
    title: Optional[str],
    content: str,
    email: Optional[str],
    source: Optional[str] = "crayon",
    metadata: Optional[Dict[str, Any]] = None
) -> int:
    conn = connect(PICTURE_DB)
    try:
        now = _iso_now()
        meta_s = json.dumps(metadata or {}, ensure_ascii=False)
        content_hash = _sha256(content)
        cur = conn.execute(
            """
            INSERT INTO waxes
                (vision_id, picture_id, title, content, content_hash, email, source, metadata, created_at, updated_at)
            VALUES (?,?,?,?,?,?,?,?,?,?)
            """,
            (vision_id, picture_id, title, content, content_hash, email, source, meta_s, now, now)
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()

def upsert_wax_by_picture_append(
    *,
    vision_id: int,
    picture_id: int,
    title: Optional[str],
    content: str,
    email: Optional[str],
    source: Optional[str] = "crayon",
    metadata: Optional[Dict[str, Any]] = None
) -> int:
    """
    Idempotency policy:
      - If a wax exists for (picture_id, email): APPEND new content to existing row.
      - Else: CREATE a new row.
    """
    existing = find_wax_id_by_picture_email(picture_id=picture_id, email=email)
    if existing:
        return update_wax_append(
            existing,
            add_content=content,
            email=email,
            source=source,
            metadata_merge=metadata,
        )
    return create_wax_for_picture(
        vision_id=vision_id,
        picture_id=picture_id,
        title=title,
        content=content,
        email=email,
        source=source,
        metadata=metadata,
    )


def update_picture_fields(
    picture_id: int,
    *,
    explanation: Optional[str] = None,   # overwrite if provided
    focus: Optional[str] = None,         # overwrite if provided
    status: Optional[str] = None,
    metadata_merge: Optional[Dict[str, Any]] = None,
) -> None:
    """
    Overwrite simple fields if provided. Shallow-merge metadata if provided.
    """
    conn = connect(PICTURE_DB)
    try:
        sets = ["updated_at = ?"]
        vals = [_iso_now()]

        if explanation is not None:
            sets.append("explanation = ?"); vals.append(explanation)
        if focus is not None:
            sets.append("focus = ?"); vals.append(focus)
        if status is not None:
            sets.append("status = ?"); vals.append(status)

        if metadata_merge is not None:
            # fetch current metadata and shallow-merge
            row = conn.execute("SELECT metadata FROM pictures WHERE id = ?", (picture_id,)).fetchone()
            cur_meta = row[0] if row else None
            merged_meta = _json_merge(cur_meta, json.dumps(metadata_merge, ensure_ascii=False))
            sets.append("metadata = ?"); vals.append(merged_meta)

        conn.execute(f"UPDATE pictures SET {', '.join(sets)} WHERE id = ?", (*vals, picture_id))
        conn.commit()
    finally:
        conn.close()


# --- CRUD: picture.db ---

# db_shared.py (only the two functions shown need replacing)

def create_vision(
    text: str,
    email: Optional[str] = None,
    *,
    title: Optional[str] = None,
    focuses: Optional[str] = None,         # NEW: TEXT column (store JSON string or CSV)
    explanation: Optional[str] = None,     # NEW: TEXT column
    status: str = "draft",
    priority: int = 0,
    tags: Optional[str] = None,
    source: Optional[str] = None,
    slug: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None
) -> int:
    conn = connect(PICTURE_DB)
    now = _iso_now()
    meta_s = json.dumps(metadata or {})
    cur = conn.execute(
        """
        INSERT INTO visions
            (title, text, focuses, explanation, email, status, priority, tags, source, slug, metadata, created_at, updated_at)
        VALUES (?,     ?,    ?,        ?,           ?,    ?,      ?,        ?,    ?,      ?,    ?,        ?,          ?)
        """,
        (title, text, focuses, explanation, email, status, priority, tags, source, slug, meta_s, now, now)
    )
    vid = cur.lastrowid
    conn.close()
    return vid


def create_picture(
    vision_id: int,
    *,
    focus: Optional[str] = None,           # NEW: TEXT column
    title: Optional[str] = None,
    description: Optional[str] = None,
    function: Optional[str] = None,
    explanation: Optional[str] = None,     # NEW: TEXT column
    email: Optional[str] = None,
    order_index: int = 0,
    status: str = "draft",
    source: Optional[str] = None,
    slug: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
    assets: Optional[Dict[str, Any]] = None
) -> int:
    conn = connect(PICTURE_DB)
    now = _iso_now()
    meta_s = json.dumps(metadata or {})
    assets_s = json.dumps(assets or {})
    cur = conn.execute(
        """
        INSERT INTO pictures
            (vision_id, focus, title, description, function, explanation, email, order_index, status, source, slug, metadata, assets, created_at, updated_at)
        VALUES (?,        ?,     ?,     ?,           ?,        ?,           ?,     ?,           ?,      ?,      ?,    ?,        ?,      ?,          ?)
        """,
        (vision_id, focus, title, description, function, explanation, email, order_index, status, source, slug, meta_s, assets_s, now, now)
    )
    pid = cur.lastrowid
    conn.close()
    return pid


def update_picture_status(picture_id: int, status: str) -> None:
    conn = connect(PICTURE_DB)
    conn.execute("UPDATE pictures SET status=?, updated_at=? WHERE id=?",
                 (status, _iso_now(), picture_id))
    conn.close()

# --- LLM usage logging ---

def log_usage(*, app: str, model: str, tokens_in: int, tokens_out: int,
              endpoint: Optional[str]=None, email: Optional[str]=None,
              request_id: Optional[str]=None, duration_ms: int=0, cost_usd: float=0.0,
              meta: Optional[Dict[str, Any]]=None) -> None:
    total = int(tokens_in) + int(tokens_out)
    day   = _iso_now()[:10]
    meta_s = json.dumps(meta or {})
    ts = _iso_now()

    conn = connect(USAGE_DB)
    try:
        conn.execute(
            """
            INSERT INTO usage_events (ts, app, model, endpoint, email, request_id, tokens_in, tokens_out, total_tokens, duration_ms, cost_usd, meta)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (ts, app, model, endpoint, email, request_id, tokens_in, tokens_out, total, duration_ms, cost_usd, meta_s)
        )
        # all time
        conn.execute(
            """
            INSERT INTO totals_all_time (id, tokens_in, tokens_out, total_tokens, calls, last_ts)
            VALUES (1, ?, ?, ?, 1, ?)
            ON CONFLICT(id) DO UPDATE SET
              tokens_in    = tokens_in    + excluded.tokens_in,
              tokens_out   = tokens_out   + excluded.tokens_out,
              total_tokens = total_tokens + excluded.total_tokens,
              calls        = calls        + 1,
              last_ts      = excluded.last_ts
            """,
            (tokens_in, tokens_out, total, ts)
        )
        # by model
        conn.execute(
            """
            INSERT INTO totals_by_model (model, tokens_in, tokens_out, total_tokens, calls, first_ts, last_ts)
            VALUES (?, ?, ?, ?, 1, ?, ?)
            ON CONFLICT(model) DO UPDATE SET
              tokens_in    = totals_by_model.tokens_in    + excluded.tokens_in,
              tokens_out   = totals_by_model.tokens_out   + excluded.tokens_out,
              total_tokens = totals_by_model.total_tokens + excluded.total_tokens,
              calls        = totals_by_model.calls        + 1,
              last_ts      = excluded.last_ts
            """,
            (model, tokens_in, tokens_out, total, ts, ts)
        )
        # daily
        conn.execute(
            """
            INSERT INTO totals_daily (day, model, tokens_in, tokens_out, total_tokens, calls)
            VALUES (?, ?, ?, ?, ?, 1)
            ON CONFLICT(day, model) DO UPDATE SET
              tokens_in    = totals_daily.tokens_in    + excluded.tokens_in,
              tokens_out   = totals_daily.tokens_out   + excluded.tokens_out,
              total_tokens = totals_daily.total_tokens + excluded.total_tokens,
              calls        = totals_daily.calls        + 1
            """,
            (day, model, tokens_in, tokens_out, total)
        )
    finally:
        conn.close()

def get_all_time_summary() -> Dict[str, Any]:
    conn = connect(USAGE_DB)
    row = conn.execute("SELECT tokens_in, tokens_out, total_tokens, calls, last_ts FROM totals_all_time WHERE id=1").fetchone()
    conn.close()
    if not row:
        return {"tokens_in":0, "tokens_out":0, "total_tokens":0, "calls":0, "last_ts":None}
    return {"tokens_in":row[0], "tokens_out":row[1], "total_tokens":row[2], "calls":row[3], "last_ts":row[4]}


# --------------------- Redundancy-aware Vision Helpers ------------------------

def _json_merge(a_s: Optional[str], b_s: Optional[str]) -> str:
    """
    Merge two JSON-encoded dicts (or None). Simple shallow merge with 'b' winning.
    Returns a JSON string.
    """
    def _parse(s):
        if not s:
            return {}
        try:
            return json.loads(s)
        except Exception:
            return {}
    a = _parse(a_s)
    b = _parse(b_s)
    a.update(b or {})
    return json.dumps(a, ensure_ascii=False)

def _email_match_clause(email: Optional[str]) -> Tuple[str, Tuple]:
    """
    Build a SQL WHERE clause fragment that matches:
      - email = ? when provided
      - email IS NULL when email is None
    """
    if email is None:
        return "email IS NULL", tuple()
    else:
        return "email = ?", (email,)

def find_existing_vision_id_by_text_email(text: str, email: Optional[str]) -> Optional[int]:
    """
    Return the oldest vision.id where text == ? and email matches (or NULL).
    """
    conn = connect(PICTURE_DB)
    try:
        where_email, args_email = _email_match_clause(email)
        row = conn.execute(
            f"SELECT id FROM visions WHERE text = ? AND {where_email} ORDER BY created_at ASC LIMIT 1",
            (text, *args_email)
        ).fetchone()
        return int(row[0]) if row else None
    finally:
        conn.close()

def _ensure_focuses_json_array(s: Optional[str]) -> list:
    """
    Normalize a focuses value to a list of objects.
    Accepts:
      - None/empty → []
      - JSON array string → parsed list
      - Any other string → parse into [{"dimension": <derived>, "focus": <text>, "goal": None}]
    """
    import re, json
    if not s or not str(s).strip():
        return []
    try:
        val = json.loads(s)
        if isinstance(val, list):
            return val
    except Exception:
        pass
    # Fallback: convert a single line into one object
    return [{"dimension": None, "focus": s}]

def _merge_focus_arrays(a: list, b: list) -> list:
    """
    Merge two focus arrays, de-duplicating by JSON signature of items.
    """
    import json
    seen = set()
    out = []
    for x in (a or []) + (b or []):
        sig = json.dumps(x, sort_keys=True, ensure_ascii=False)
        if sig not in seen:
            seen.add(sig)
            out.append(x)
    return out

def update_vision_fields(
    vision_id: int,
    *,
    focuses: Optional[str] = None,
    explanation: Optional[str] = None,
    status: Optional[str] = None,
    tags: Optional[str] = None,
    source: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
    title: Optional[str] = None,
) -> None:
    """
    Non-destructive update with focuses normalization/merge.
    """
    conn = connect(PICTURE_DB)
    try:
        existing = conn.execute(
            "SELECT focuses, explanation, metadata FROM visions WHERE id = ?",
            (vision_id,)
        ).fetchone()
        if not existing:
            return
        cur_focuses, cur_expl, cur_meta = existing

        # Normalize to arrays and merge
        if focuses is not None:
            cur_arr = _ensure_focuses_json_array(cur_focuses)
            new_arr = _ensure_focuses_json_array(focuses)
            merged_arr = _merge_focus_arrays(cur_arr, new_arr)
            new_focuses = json.dumps(merged_arr, ensure_ascii=False)
        else:
            new_focuses = cur_focuses

        # explanation: set only if empty
        new_expl = cur_expl
        if explanation is not None and (not cur_expl or not str(cur_expl).strip()):
            new_expl = explanation

        # metadata shallow-merge
        new_meta = _json_merge(cur_meta, json.dumps(metadata or {}, ensure_ascii=False))

        sets = ["updated_at = ?"]
        vals = [_iso_now()]

        if title is not None:
            sets.append("title = ?"); vals.append(title)
        if new_focuses != cur_focuses:
            sets.append("focuses = ?"); vals.append(new_focuses)
        if new_expl != cur_expl:
            sets.append("explanation = ?"); vals.append(new_expl)
        if status is not None:
            sets.append("status = ?"); vals.append(status)
        if tags is not None:
            sets.append("tags = ?"); vals.append(tags)
        if source is not None:
            sets.append("source = ?"); vals.append(source)
        sets.append("metadata = ?"); vals.append(new_meta)

        conn.execute(f"UPDATE visions SET {', '.join(sets)} WHERE id = ?", (*vals, vision_id))
        conn.commit()
    finally:
        conn.close()


def upsert_vision_by_text_email(
    *,
    text: str,
    email: Optional[str],
    title: Optional[str] = None,
    focuses: Optional[str] = None,
    explanation: Optional[str] = None,
    status: str = "draft",
    priority: int = 0,
    tags: Optional[str] = None,
    source: Optional[str] = None,
    slug: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> int:
    """
    Idempotent create-or-update by (text, email).
    If a row exists, merge non-destructively; else create.
    Returns the vision_id in either case.
    """
    existing_id = find_existing_vision_id_by_text_email(text=text, email=email)
    if existing_id:
        update_vision_fields(
            existing_id,
            focuses=focuses,
            explanation=explanation,
            status=status if status else None,
            tags=tags,
            source=source,
            metadata=metadata or {},
            title=title,
        )
        return existing_id
    # create new
    return create_vision(
        text=text, email=email, title=title, focuses=focuses, explanation=explanation,
        status=status, priority=priority, tags=tags, source=source, slug=slug,
        metadata=metadata or {}
    )


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()

def create_wax(
    *,
    vision_id: int,
    title: Optional[str],
    content: str,
    email: Optional[str],
    source: Optional[str] = "crayon",
    metadata: Optional[Dict[str, Any]] = None,
) -> int:
    conn = connect(PICTURE_DB)
    try:
        now = _iso_now()
        meta_s = json.dumps(metadata or {}, ensure_ascii=False)
        content_hash = _sha256(content)
        cur = conn.execute(
            """
            INSERT INTO waxes
                (vision_id, title, content, content_hash, email, source, metadata, created_at, updated_at)
            VALUES (?,        ?,     ?,       ?,            ?,     ?,      ?,        ?,          ?)
            """,
            (vision_id, title, content, content_hash, email, source, meta_s, now, now)
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()

def find_wax_id_by_hash(content_hash: str) -> Optional[int]:
    conn = connect(PICTURE_DB)
    try:
        row = conn.execute("SELECT id FROM waxes WHERE content_hash = ?", (content_hash,)).fetchone()
        return int(row[0]) if row else None
    finally:
        conn.close()


def upsert_wax_by_content(
    *,
    vision_id: int,
    content: str,
    title: str | None = None,
    email: str | None = None,
    picture_id: int | None = None,            # Link wax to a specific picture when provided
    source: str | None = "crayon",
    metadata: dict | None = None,
    policy: str = "append",                    # "append" (default) or "overwrite" for same (picture_id, email)
    separator: str = "\n\n---\n\n"            # Used when appending
) -> int:
    """
    Idempotent + picture-aware upsert for WAX rows.

    Behavior:
      1) If an identical content_hash exists anywhere in 'waxes', return that row.
         - If caller supplies picture_id and stored row has NULL picture_id, attach it.
         - Shallow-merge metadata, and set email/source if provided (COALESCE-style).
      2) Else if (picture_id, email) already has a wax row:
         - If policy == "append": append new content (with separator) and update content_hash.
         - If policy == "overwrite": replace content and content_hash.
         - In both cases, shallow-merge metadata and update timestamps.
      3) Else: insert a fresh row.

    Notes:
      - This aligns with your "by picture" strategy while still deduping exact content globally.
      - Requires: connect(PICTURE_DB), _iso_now(), and _json_merge(...) already defined in your module.
    """
    import json, hashlib

    def _sha256(txt: str) -> str:
        return hashlib.sha256(txt.encode("utf-8")).hexdigest()

    if policy not in ("append", "overwrite"):
        raise ValueError("policy must be 'append' or 'overwrite'")

    now = _iso_now()
    meta_s_new = json.dumps(metadata or {}, ensure_ascii=False)
    new_hash = _sha256(content)

    conn = connect(PICTURE_DB)
    try:
        # ------------------------------------------------------------------ #
        # (1) Global exact-content de-dup (fast path)
        row = conn.execute(
            "SELECT id, picture_id, email, source, metadata FROM waxes WHERE content_hash = ? LIMIT 1",
            (new_hash,)
        ).fetchone()
        if row:
            wax_id, cur_pic, cur_email, cur_source, cur_meta = row

            # attach picture_id if empty and we have one
            set_picture = (picture_id is not None) and (cur_pic is None)

            # merge metadata shallowly
            merged_meta = _json_merge(cur_meta, meta_s_new) if (metadata is not None) else (cur_meta or meta_s_new)

            # update link/metadata if anything new was provided
            if set_picture or metadata is not None or email is not None or source is not None:
                conn.execute(
                    """
                    UPDATE waxes
                    SET picture_id = COALESCE(?, picture_id),
                        email      = COALESCE(?, email),
                        source     = COALESCE(?, source),
                        metadata   = ?,
                        updated_at = ?
                    WHERE id = ?
                    """,
                    (picture_id if set_picture else None, email, source, merged_meta, now, wax_id)
                )
                conn.commit()
            return wax_id

        # ------------------------------------------------------------------ #
        # Helper: find existing wax row by (picture_id, email)
        def _find_wax_id_by_picture_email(p_id: int, em: str | None):
            if p_id is None:
                return None
            if em is None:
                r = conn.execute(
                    "SELECT id FROM waxes WHERE picture_id = ? AND email IS NULL LIMIT 1",
                    (p_id,)
                ).fetchone()
            else:
                r = conn.execute(
                    "SELECT id FROM waxes WHERE picture_id = ? AND email = ? LIMIT 1",
                    (p_id, em)
                ).fetchone()
            return int(r[0]) if r else None

        # (2) If we have a picture, respect the (picture_id, email) policy
        existing_wax_id = _find_wax_id_by_picture_email(picture_id, email)
        if existing_wax_id is not None:
            # fetch current content + metadata
            row2 = conn.execute(
                "SELECT content, metadata FROM waxes WHERE id = ?",
                (existing_wax_id,)
            ).fetchone()
            cur_content, cur_meta = row2 if row2 else ("", None)

            if policy == "append":
                new_content = (cur_content or "")
                if content:
                    new_content = (new_content + (separator if new_content else "")) + content
            else:  # overwrite
                new_content = content

            merged_meta = _json_merge(cur_meta, meta_s_new)

            conn.execute(
                """
                UPDATE waxes
                SET title       = COALESCE(?, title),
                    content     = ?,
                    content_hash= ?,
                    email       = COALESCE(?, email),
                    source      = COALESCE(?, source),
                    metadata    = ?,
                    updated_at  = ?
                WHERE id = ?
                """,
                (
                    title,
                    new_content,
                    _sha256(new_content),
                    email,
                    source,
                    merged_meta,
                    now,
                    existing_wax_id
                )
            )
            conn.commit()
            return existing_wax_id

        # ------------------------------------------------------------------ #
        # (3) Fresh insert (no identical content, no existing (picture,email))
        cur = conn.execute(
            """
            INSERT INTO waxes
                (vision_id, picture_id, title, content, content_hash, email, source, metadata, created_at, updated_at)
            VALUES (?,         ?,          ?,     ?,       ?,            ?,     ?,      ?,        ?,          ?)
            """,
            (
                vision_id,
                picture_id,
                title,
                content,
                new_hash,
                email,
                source,
                meta_s_new,
                now,
                now
            )
        )
        conn.commit()
        return cur.lastrowid

    finally:
        conn.close()



def create_world(
    *,
    vision_id: int,
    wax_id: Optional[int],
    title: Optional[str],
    html: str,
    email: Optional[str],
    source: Optional[str] = "crayon",
    metadata: Optional[Dict[str, Any]] = None,
) -> int:
    conn = connect(PICTURE_DB)
    try:
        now = _iso_now()
        meta_s = json.dumps(metadata or {}, ensure_ascii=False)
        html_hash = _sha256(html)
        cur = conn.execute(
            """
            INSERT INTO worlds
                (vision_id, wax_id, title, html, html_hash, email, source, metadata, created_at, updated_at)
            VALUES (?,        ?,      ?,     ?,    ?,         ?,     ?,      ?,        ?,          ?)
            """,
            (vision_id, wax_id, title, html, html_hash, email, source, meta_s, now, now)
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()

def find_world_id_by_hash(html_hash: str) -> Optional[int]:
    conn = connect(PICTURE_DB)
    try:
        row = conn.execute("SELECT id FROM worlds WHERE html_hash = ?", (html_hash,)).fetchone()
        return int(row[0]) if row else None
    finally:
        conn.close()

def upsert_world_by_html(
    *,
    vision_id: int,
    wax_id: Optional[int],
    title: Optional[str],
    html: str,
    email: Optional[str],
    source: Optional[str] = "crayon",
    metadata: Optional[Dict[str, Any]] = None,
) -> int:
    h = _sha256(html)
    existing = find_world_id_by_hash(h)
    if existing:
        return existing
    return create_world(
        vision_id=vision_id, wax_id=watx_id if (watx_id:=wax_id) else None,
        title=title, html=html, email=email, source=source, metadata=metadata
    )



# --------- World lookup/update by (picture_id, email) with overwrite policy ----
from typing import Optional, Dict, Any
import json, hashlib

def _sha256(text: str) -> str:
    import hashlib
    return hashlib.sha256(text.encode("utf-8")).hexdigest()

def find_world_id_by_picture_email(*, picture_id: int, email: Optional[str]) -> Optional[int]:
    conn = connect(PICTURE_DB)
    try:
        if email is None:
            row = conn.execute(
                "SELECT id FROM worlds WHERE picture_id=? AND email IS NULL LIMIT 1",
                (picture_id,)
            ).fetchone()
        else:
            row = conn.execute(
                "SELECT id FROM worlds WHERE picture_id=? AND email=? LIMIT 1",
                (picture_id, email)
            ).fetchone()
        return int(row[0]) if row else None
    finally:
        conn.close()

def create_world_for_picture(
    *,
    vision_id: int,
    picture_id: int,
    wax_id: Optional[int],
    title: Optional[str],
    html: str,
    email: Optional[str],
    source: Optional[str] = "crayon",
    metadata: Optional[Dict[str, Any]] = None
) -> int:
    conn = connect(PICTURE_DB)
    try:
        now = _iso_now()
        meta_s = json.dumps(metadata or {}, ensure_ascii=False)
        html_hash = _sha256(html)
        cur = conn.execute(
            """
            INSERT INTO worlds
                (vision_id, picture_id, wax_id, title, html, html_hash, email, source, metadata, created_at, updated_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?)
            """,
            (vision_id, picture_id, wax_id, title, html, html_hash, email, source, meta_s, now, now)
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()

def update_world_overwrite(
    world_id: int,
    *,
    wax_id: Optional[int],
    html: str,
    email: Optional[str],
    source: Optional[str] = "crayon",
    metadata_merge: Optional[Dict[str, Any]] = None
) -> int:
    conn = connect(PICTURE_DB)
    try:
        row = conn.execute("SELECT metadata FROM worlds WHERE id=?", (world_id,)).fetchone()
        cur_meta = row[0] if row else None
        merged_meta = _json_merge(cur_meta, json.dumps(metadata_merge or {}, ensure_ascii=False))
        conn.execute(
            "UPDATE worlds SET wax_id=?, html=?, html_hash=?, email=?, source=?, metadata=?, updated_at=? WHERE id=?",
            (wax_id, html, _sha256(html), email, source, merged_meta, _iso_now(), world_id)
        )
        conn.commit()
        return world_id
    finally:
        conn.close()

def upsert_world_by_picture_overwrite(
    *,
    vision_id: int,
    picture_id: int,
    wax_id: Optional[int],
    title: Optional[str],
    html: str,
    email: Optional[str],
    source: Optional[str] = "crayon",
    metadata: Optional[Dict[str, Any]] = None
) -> int:
    existing = find_world_id_by_picture_email(picture_id=picture_id, email=email)
    if existing:
        return update_world_overwrite(
            existing,
            wax_id=wax_id,
            html=html,
            email=email,
            source=source,
            metadata_merge=metadata
        )
    return create_world_for_picture(
        vision_id=vision_id,
        picture_id=picture_id,
        wax_id=wax_id,
        title=title,
        html=html,
        email=email,
        source=source,
        metadata=metadata
    )
