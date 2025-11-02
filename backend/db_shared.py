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

# --- init ---

def init_picture_db(conn: Optional[sqlite3.Connection]=None) -> None:
    close = False
    if conn is None:
        conn, close = connect(PICTURE_DB), True
    try:
        conn.executescript(open("picture_schema.sql", "r", encoding="utf-8").read())
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
    Non-destructive update:
      - focuses: if target empty/NULL -> set; else if both look like JSON arrays, merge unique; else replace only if different.
      - explanation: set only if target empty/NULL, else keep existing.
      - metadata: shallow-merge (new keys overwrite).
      - title/status/tags/source: set if provided (overwrite).
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

        # focuses handling
        new_focuses = cur_focuses
        if focuses is not None:
            if not cur_focuses or cur_focuses.strip() == "":
                new_focuses = focuses
            else:
                # Try to merge JSON arrays
                try:
                    a = json.loads(cur_focuses)
                    b = json.loads(focuses)
                    if isinstance(a, list) and isinstance(b, list):
                        # de-dup by JSON string
                        seen = set()
                        merged = []
                        for x in a + b:
                            sig = json.dumps(x, sort_keys=True)
                            if sig not in seen:
                                seen.add(sig)
                                merged.append(x)
                        new_focuses = json.dumps(merged, ensure_ascii=False)
                except Exception:
                    # fallback: keep existing
                    new_focuses = cur_focuses

        # explanation handling: only set if empty
        new_expl = cur_expl
        if explanation is not None:
            if not cur_expl or cur_expl.strip() == "":
                new_expl = explanation

        # metadata shallow merge
        new_meta = _json_merge(cur_meta, json.dumps(metadata or {}, ensure_ascii=False))

        # columns to update
        sets = ["updated_at = ?"]
        vals = [ _iso_now() ]

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
        # metadata always writes merged string
        sets.append("metadata = ?"); vals.append(new_meta)

        if len(sets) > 1:  # something to update besides updated_at
            conn.execute(
                f"UPDATE visions SET {', '.join(sets)} WHERE id = ?",
                (*vals, vision_id)
            )
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
    title: Optional[str],
    content: str,
    email: Optional[str],
    source: Optional[str] = "crayon",
    metadata: Optional[Dict[str, Any]] = None,
) -> int:
    h = _sha256(content)
    existing = find_wax_id_by_hash(h)
    if existing:
        return existing
    return create_wax(
        vision_id=vision_id, title=title, content=content, email=email, source=source, metadata=metadata
    )

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
