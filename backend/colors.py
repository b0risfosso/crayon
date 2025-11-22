#!/usr/bin/env python3
"""
colors.py

Input: art_id from art table.
Process:
- load art.art text by id
- run build_thought_sys/build_thought_user with art text as {thought}
- save output to colors table in art.db
"""

from __future__ import annotations

import os
import json
import sqlite3
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Tuple

from flask import Flask, request, jsonify, abort

from prompts import build_thought_sys, build_thought_user  # type: ignore

try:
    from openai import OpenAI
    _client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))
except Exception as e:
    _client = None
    _client_err = e

import threading
import queue
import uuid
import time



MODEL_DEFAULT = os.environ.get("COLORS_MODEL", "gpt-5.1")
APP_PORT = int(os.environ.get("PORT", "9018"))
ART_DB_PATH = os.environ.get("ART_DB_PATH", "/var/www/site/data/art.db")
LLM_USAGE_DB_PATH = os.environ.get("LLM_USAGE_DB", "/var/www/site/data/llm_usage.db")
DAILY_LIMITS = json.loads(os.environ.get("COLORS_DAILY_TOKEN_LIMITS_JSON", "{}"))

LLM_USAGE_DB_PATH = os.environ.get("LLM_USAGE_DB", "/var/www/site/data/llm_usage.db")
DAILY_LIMITS = json.loads(os.environ.get("COLORS_DAILY_TOKEN_LIMITS_JSON", "{}"))


def usage_day_utc(ts_iso: str) -> str:
    # expects ISO like 2025-11-22T...
    return ts_iso[:10]


def get_usage_db() -> sqlite3.Connection:
    conn = sqlite3.connect(LLM_USAGE_DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn


def get_daily_tokens_for_model(model: str, day: str) -> int:
    db = get_usage_db()
    row = db.execute(
        "SELECT total_tokens FROM totals_daily WHERE day=? AND model=?",
        (day, model)
    ).fetchone()
    db.close()
    return int(row["total_tokens"]) if row else 0


def enforce_daily_cap_or_429(model: str, projected_tokens: int):
    limit = DAILY_LIMITS.get(model)
    if limit is None:
        return  # no cap for this model

    now = utc_now_iso()
    day = usage_day_utc(now)
    used = get_daily_tokens_for_model(model, day)

    if used + projected_tokens > int(limit):
        abort(
            429,
            description=(
                f"Daily token cap exceeded for model '{model}'. "
                f"used={used}, projected={projected_tokens}, limit={limit}"
            )
        )


def log_llm_usage(
    *,
    ts: str,
    app_name: str,
    model: str,
    endpoint: str,
    email: Optional[str],
    request_id: str,
    tokens_in: int,
    tokens_out: int,
    total_tokens: int,
    duration_ms: int,
    cost_usd: float = 0.0,
    meta_obj: Optional[Dict[str, Any]] = None
):
    """
    Inserts a usage_events row and upserts totals_* tables
    in ONE transaction.
    """
    day = usage_day_utc(ts)
    meta_str = json.dumps(meta_obj or {}, ensure_ascii=False)

    db = get_usage_db()
    try:
        db.execute("BEGIN")

        # 1) usage_events
        db.execute(
            """
            INSERT INTO usage_events
              (ts, app, model, endpoint, email, request_id,
               tokens_in, tokens_out, total_tokens, duration_ms, cost_usd, meta)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                ts, app_name, model, endpoint, email, request_id,
                tokens_in, tokens_out, total_tokens, duration_ms, cost_usd, meta_str
            )
        )

        # 2) totals_all_time (single row id=1)
        db.execute(
            """
            INSERT INTO totals_all_time (id, tokens_in, tokens_out, total_tokens, calls, last_ts)
            VALUES (1, ?, ?, ?, 1, ?)
            ON CONFLICT(id) DO UPDATE SET
                tokens_in    = tokens_in + excluded.tokens_in,
                tokens_out   = tokens_out + excluded.tokens_out,
                total_tokens = total_tokens + excluded.total_tokens,
                calls        = calls + 1,
                last_ts      = excluded.last_ts
            """,
            (tokens_in, tokens_out, total_tokens, ts)
        )

        # 3) totals_by_model
        db.execute(
            """
            INSERT INTO totals_by_model
              (model, tokens_in, tokens_out, total_tokens, calls, first_ts, last_ts)
            VALUES (?, ?, ?, ?, 1, ?, ?)
            ON CONFLICT(model) DO UPDATE SET
                tokens_in    = tokens_in + excluded.tokens_in,
                tokens_out   = tokens_out + excluded.tokens_out,
                total_tokens = total_tokens + excluded.total_tokens,
                calls        = calls + 1,
                last_ts      = excluded.last_ts
            """,
            (model, tokens_in, tokens_out, total_tokens, ts, ts)
        )

        # 4) totals_daily
        db.execute(
            """
            INSERT INTO totals_daily
              (day, model, tokens_in, tokens_out, total_tokens, calls)
            VALUES (?, ?, ?, ?, ?, 1)
            ON CONFLICT(day, model) DO UPDATE SET
                tokens_in    = tokens_in + excluded.tokens_in,
                tokens_out   = tokens_out + excluded.tokens_out,
                total_tokens = total_tokens + excluded.total_tokens,
                calls        = calls + 1
            """,
            (day, model, tokens_in, tokens_out, total_tokens)
        )

        db.execute("COMMIT")
    except Exception:
        db.execute("ROLLBACK")
        raise
    finally:
        db.close()


app = Flask(__name__)
app.config["JSON_SORT_KEYS"] = False

# -------------------------
# Queue + workers (concurrency = 2)
# -------------------------
TASK_QUEUE: "queue.Queue[dict]" = queue.Queue()
TASKS: dict[str, dict] = {}
TASKS_LOCK = threading.Lock()

LLM_LOCK = threading.Lock()  # serialize client if needed for safety


def queue_stats() -> dict:
    with TASKS_LOCK:
        counts = {"queued": 0, "running": 0, "done": 0, "error": 0}
        for t in TASKS.values():
            s = t.get("status")
            if s in counts:
                counts[s] += 1
    return {
        "queue_size": TASK_QUEUE.qsize(),
        "tasks": counts,
        "concurrency": 2,
    }


def worker_loop(worker_id: int):
    while True:
        task = TASK_QUEUE.get()  # blocks until task available
        task_id = task["task_id"]
        art_id = task["art_id"]
        model = task["model"]
        user_metadata = task["user_metadata"]

        with TASKS_LOCK:
            TASKS[task_id]["status"] = "running"
            TASKS[task_id]["started_at"] = utc_now_iso()
            TASKS[task_id]["worker_id"] = worker_id

        try:
            # 1) load art text
            art_row = fetch_art_text(art_id)
            thought_text = (art_row.get("art") or "").strip()
            if not thought_text:
                raise ValueError(f"art id {art_id} has empty art text")

            # 2) format prompt
            user_prompt = build_thought_user.format(thought=thought_text)

            projected_tokens = 2000
            enforce_daily_cap_or_429(model=model, projected_tokens=projected_tokens)

            # 3) call LLM
            with LLM_LOCK:
                t0 = time.time()
                resp = _client.chat.completions.create(
                    model=model,
                    messages=[
                        {"role": "system", "content": build_thought_sys},
                        {"role": "user", "content": user_prompt},
                    ],
                )

            expanded = (resp.choices[0].message.content or "").strip()
            usage = getattr(resp, "usage", None)
            usage_dict = usage.model_dump() if usage else None

            tokens_in = int((usage_dict or {}).get("prompt_tokens", 0))
            tokens_out = int((usage_dict or {}).get("completion_tokens", 0))
            total_tokens = int((usage_dict or {}).get("total_tokens", tokens_in + tokens_out))

            duration_ms = int((time.time() - t0) * 1000)

            log_llm_usage(
                ts=utc_now_iso(),
                app_name="colors",
                model=model,
                endpoint="/colors/build_thought",
                email=None,                  # you don’t have email on colors call; OK
                request_id=task_id,          # your queued task_id
                tokens_in=tokens_in,
                tokens_out=tokens_out,
                total_tokens=total_tokens,
                duration_ms=duration_ms,
                cost_usd=0.0,
                meta_obj={
                    "art_id": art_id,
                    "worker_id": worker_id
                }
            )


            # 4) save to colors table
            colors_row = insert_color_row(
                art_id=art_id,
                input_art=thought_text,
                output_text=expanded,
                model=model,
                usage=usage_dict,
                user_metadata=user_metadata,
            )

            with TASKS_LOCK:
                TASKS[task_id]["status"] = "done"
                TASKS[task_id]["finished_at"] = utc_now_iso()
                TASKS[task_id]["result"] = {
                    "art_id": art_id,
                    "expanded_thought": expanded,
                    "usage": usage_dict,
                    "saved_color": colors_row,
                }

        except Exception as e:
            with TASKS_LOCK:
                TASKS[task_id]["status"] = "error"
                TASKS[task_id]["finished_at"] = utc_now_iso()
                TASKS[task_id]["error"] = str(e)

        finally:
            TASK_QUEUE.task_done()


# start 2 background workers when module loads
for wid in range(2):
    t = threading.Thread(target=worker_loop, args=(wid,), daemon=True)
    t.start()



# -------------------------
# Helpers
# -------------------------

def utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def require_json() -> Dict[str, Any]:
    if not request.is_json:
        abort(400, description="Request must be application/json")
    payload = request.get_json(silent=True)
    if payload is None:
        abort(400, description="Invalid JSON body")
    return payload


def get_art_db() -> sqlite3.Connection:
    conn = sqlite3.connect(ART_DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn


def fetch_art_text(art_id: int) -> Dict[str, Any]:
    db = get_art_db()
    row = db.execute("SELECT * FROM art WHERE id = ?", (art_id,)).fetchone()
    db.close()
    if row is None:
        abort(404, description=f"art id {art_id} not found")
    return dict(row)


def insert_color_row(
    art_id: int,
    input_art: str,
    output_text: str,
    model: str,
    usage: Optional[Dict[str, Any]],
    user_metadata: Dict[str, Any],
) -> Dict[str, Any]:
    created_at = utc_now_iso()
    updated_at = created_at

    md_obj = {
        "source": "colors.build_thought",
        "original_art_id": art_id,
        "usage": usage,
        "user_metadata": user_metadata,
    }
    md_str = json.dumps(md_obj, ensure_ascii=False)

    db = get_art_db()
    cur = db.execute(
        """
        INSERT INTO colors
          (art_id, input_art, output_text, model, provider, metadata, created_at, updated_at)
        VALUES (?,      ?,         ?,          ?,        ?,        ?,        ?,          ?)
        """,
        (
            art_id,
            input_art,
            output_text,
            model,
            "openai",
            md_str,
            created_at,
            updated_at,
        ),
    )
    db.commit()
    new_id = cur.lastrowid
    row = db.execute("SELECT * FROM colors WHERE id = ?", (new_id,)).fetchone()
    db.close()

    out = dict(row)
    try:
        out["metadata"] = json.loads(out.get("metadata") or "{}")
    except Exception:
        pass
    return out


# -------------------------
# Routes
# -------------------------

@app.get("/health")
def health():
    ok = _client is not None
    return jsonify({
        "ok": ok,
        "service": "colors",
        "model_default": MODEL_DEFAULT,
        "art_db_path": ART_DB_PATH,
        "time": utc_now_iso(),
        "error": None if ok else str(_client_err),
    })


@app.post("/colors/build_thought")
def colors_build_thought():
    """
    Enqueue an expansion job.

    Body:
      {
        "art_id": 123,
        "model": "...optional...",
        "temperature": 0.6 optional,
        "metadata": {...} optional
      }

    Returns immediately with task_id.
    """
    if _client is None:
        abort(500, description=f"OpenAI client not initialized: {_client_err}")

    payload = require_json()
    art_id = payload.get("art_id")
    if not isinstance(art_id, int):
        abort(400, description="'art_id' is required and must be an integer")

    model = payload.get("model", MODEL_DEFAULT)
    temperature = float(payload.get("temperature", 0.6))
    user_metadata = payload.get("metadata") or {}

    task_id = str(uuid.uuid4())

    # record task
    with TASKS_LOCK:
        TASKS[task_id] = {
            "task_id": task_id,
            "art_id": art_id,
            "status": "queued",
            "created_at": utc_now_iso(),
            "model": model,
            "temperature": temperature,
        }

    TASK_QUEUE.put({
        "task_id": task_id,
        "art_id": art_id,
        "model": model,
        "temperature": temperature,
        "user_metadata": user_metadata,
    })

    return jsonify({
        "task_id": task_id,
        "status": "queued",
        "art_id": art_id,
        "model": model,
        "temperature": temperature,
        "queue": queue_stats(),
    }), 202


@app.get("/colors/tasks/<task_id>")
def get_task(task_id: str):
    with TASKS_LOCK:
        t = TASKS.get(task_id)
    if t is None:
        abort(404, description="task not found")
    return jsonify(t)


@app.get("/colors/queue/stats")
def get_queue_stats():
    return jsonify(queue_stats())



# Optional alias if you ever strip /colors/ in nginx
@app.post("/build_thought")
def build_thought_alias():
    return colors_build_thought()


@app.errorhandler(400)
@app.errorhandler(404)
@app.errorhandler(409)
@app.errorhandler(500)
def json_error(err):
    code = getattr(err, "code", 500)
    return jsonify({
        "error": True,
        "code": code,
        "message": getattr(err, "description", str(err)),
    }), code


@app.get("/colors/by_art/<int:art_id>")
def colors_by_art(art_id: int):
    """
    Return all color expansions for a given art_id.
    Output:
      [
        {
          "id": ...,
          "art_id": ...,
          "input_art": "...",
          "output_text": "...",
          "model": "...",
          "metadata": {...},
          "created_at": "...",
          "updated_at": "..."
        },
        ...
      ]
    """
    db = get_art_db()
    rows = db.execute(
        """
        SELECT * FROM colors
        WHERE art_id = ?
        ORDER BY created_at DESC
        """,
        (art_id,)
    ).fetchall()
    db.close()

    out = []
    for r in rows:
        row = dict(r)
        # parse metadata back to JSON
        try:
            row["metadata"] = json.loads(row.get("metadata") or "{}")
        except Exception:
            pass
        out.append(row)

    return jsonify(out)

