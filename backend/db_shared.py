from __future__ import annotations
import sqlite3, json, os, datetime as dt
from typing import Optional, Dict, Any

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

def create_vision(text: str, email: Optional[str]=None, *, title: Optional[str]=None,
                  status: str="draft", priority: int=0, tags: Optional[str]=None,
                  source: Optional[str]=None, slug: Optional[str]=None,
                  metadata: Optional[Dict[str, Any]]=None) -> int:
    conn = connect(PICTURE_DB)
    now = _iso_now()
    meta_s = json.dumps(metadata or {})
    cur = conn.execute(
        """
        INSERT INTO visions (title, text, email, status, priority, tags, source, slug, metadata, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (title, text, email, status, priority, tags, source, slug, meta_s, now, now)
    )
    vid = cur.lastrowid
    conn.close()
    return vid

def create_picture(vision_id: int, *, subtext: Optional[str]=None, title: Optional[str]=None,
                   description: Optional[str]=None, function: Optional[str]=None,
                   email: Optional[str]=None, order_index: int=0, status: str="draft",
                   source: Optional[str]=None, slug: Optional[str]=None,
                   metadata: Optional[Dict[str, Any]]=None, assets: Optional[Dict[str, Any]]=None) -> int:
    conn = connect(PICTURE_DB)
    now = _iso_now()
    meta_s = json.dumps(metadata or {})
    assets_s = json.dumps(assets or {})
    cur = conn.execute(
        """
        INSERT INTO pictures (vision_id, subtext, title, description, function, email, order_index,
                              status, source, slug, metadata, assets, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (vision_id, subtext, title, description, function, email, order_index,
         status, source, slug, meta_s, assets_s, now, now)
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
