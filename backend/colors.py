#!/usr/bin/env python3
"""
colors.py (simplified + create_dirt)

Functionality:

- build_thought:
    - Input: art_id (from art table).
    - Process:
        - load art.art text by id
        - run build_thought_sys / build_thought_user with art text as {thought}
        - save output to colors table in art.db

- create_dirt:
    - Input: color_id (from colors table), prompt_key (which create_dirt_* prompt to use)
    - Process:
        - load colors.output_text (origin = 'colors.build_thought') as {thought}
        - run the chosen create_dirt_* prompt with that thought
        - save output to dirt table in art.db

All other prompt families / endpoints are removed.
"""

from __future__ import annotations

import os
import json
import sqlite3
import threading
import queue
import uuid
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from flask import Flask, request, jsonify, abort

# --- OpenAI client ---------------------------------------------------------

try:
    from openai import OpenAI
    _client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))
    _client_err: Optional[Exception] = None
except Exception as e:
    _client = None
    _client_err = e

# Core build_thought prompts
from prompts import build_thought_sys, build_thought_user  # type: ignore

# create_dirt prompt templates (examples; more can be added)
from prompts import (  # type: ignore
    create_dirt_entities_processes_phenomena,
    create_dirt_abstractions_metaphors,
    create_dirt_processes_forces_interactions,
    create_dirt_theories,
    create_dirt_companies,
    create_dirt_historical_context,
    create_dirt_levers,
    create_dirt_intelligence,
    create_dirt_experiments,
    create_dirt_physical_build,
    create_dirt_codebases,
    create_dirt_datasets,

)

# Map prompt_key -> template string
DIRT_PROMPTS: Dict[str, str] = {
    # short keys
    "entities_processes_phenomena": create_dirt_entities_processes_phenomena,
    "abstractions_metaphors": create_dirt_abstractions_metaphors,
    "processes_forces_interactions": create_dirt_processes_forces_interactions,
    "theories": create_dirt_theories,
    "companies": create_dirt_companies,
    "historical_context": create_dirt_historical_context,
    "levers": create_dirt_levers,
    "intelligence": create_dirt_intelligence,
    "experiments": create_dirt_experiments,
    "physical_build": create_dirt_physical_build,
    "codebases": create_dirt_codebases,
    "datasets": create_dirt_datasets,
    
    # allow full variable-style names as aliases
    "create_dirt_entities_processes_phenomena": create_dirt_entities_processes_phenomena,
    "create_dirt_abstractions_metaphors": create_dirt_abstractions_metaphors,
    "create_dirt_processes_forces_interactions": create_dirt_processes_forces_interactions,
    "create_dirt_theories": create_dirt_theories,
    "create_dirt_companies": create_dirt_companies,
    "create_dirt_historical_context": create_dirt_historical_context,
    "create_dirt_levers": create_dirt_levers,
    "create_dirt_intelligence": create_dirt_intelligence,
    "create_dirt_experiments": create_dirt_experiments,
    "create_dirt_physical_build": create_dirt_physical_build,
    "create_dirt_codebases": create_dirt_codebases,
    "create_dirt_datasets": create_dirt_datasets,


}

# --- Config ---------------------------------------------------------------

MODEL_DEFAULT = os.environ.get("COLORS_MODEL", "gpt-5-mini-2025-08-07")
APP_PORT = int(os.environ.get("PORT", "9018"))
ART_DB_PATH = os.environ.get("ART_DB_PATH", "/var/www/site/data/art.db")
LLM_USAGE_DB_PATH = os.environ.get("LLM_USAGE_DB", "/var/www/site/data/llm_usage.db")
DAILY_LIMITS: Dict[str, int] = json.loads(
    os.environ.get("COLORS_DAILY_TOKEN_LIMITS_JSON", "{}")
)

app = Flask(__name__)

# --- DB helpers -----------------------------------------------------------


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


def get_usage_db() -> sqlite3.Connection:
    conn = sqlite3.connect(LLM_USAGE_DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn


@contextmanager
def open_db():
    db = get_art_db()
    try:
        yield db
        db.commit()
    finally:
        db.close()


def fetch_one(sql: str, params=()) -> Dict[str, Any]:
    with open_db() as db:
        row = db.execute(sql, params).fetchone()
        return dict(row) if row else {}


def fetch_art_text(art_id: int) -> Dict[str, Any]:
    db = get_art_db()
    try:
        row = db.execute(
            "SELECT * FROM art WHERE id = ?",
            (art_id,),
        ).fetchone()
    finally:
        db.close()

    if row is None:
        abort(404, description=f"art id {art_id} not found")

    return dict(row)


def fetch_color_row(color_id: int) -> Dict[str, Any]:
    """
    Load a color row by id.

    Used by create_dirt to grab the build_thought expansion.
    """
    db = get_art_db()
    try:
        row = db.execute(
            "SELECT * FROM colors WHERE id = ?",
            (color_id,),
        ).fetchone()
    finally:
        db.close()

    if row is None:
        abort(404, description=f"color id {color_id} not found")

    return dict(row)


def insert_color_row(
    art_id: int,
    input_art: str,
    output_text: str,
    model: str,
    usage: Optional[Dict[str, Any]],
    user_metadata: Dict[str, Any],
    origin: str = "colors.build_thought",
) -> Dict[str, Any]:
    created_at = utc_now_iso()
    updated_at = created_at

    md_obj = {
        "source": origin,
        "original_art_id": art_id,
        "usage": usage,
        "user_metadata": user_metadata or {},
    }
    md_str = json.dumps(md_obj, ensure_ascii=False)

    db = get_art_db()
    try:
        row = db.execute(
            """
            INSERT INTO colors
              (art_id, input_art, output_text, model, provider,
               metadata, origin, created_at, updated_at)
            VALUES (?,      ?,         ?,          ?,     ?, 
                    ?,      ?,          ?,          ?)
            RETURNING *
            """,
            (
                art_id,
                input_art,
                output_text,
                model,
                "openai",
                md_str,
                origin,
                created_at,
                updated_at,
            ),
        ).fetchone()
        db.commit()
    finally:
        db.close()

    out = dict(row)
    try:
        out["metadata"] = json.loads(out.get("metadata") or "{}")
    except Exception:
        pass
    return out


def insert_dirt_row(
    *,
    color_row: Dict[str, Any],
    prompt_key: str,
    output_text: str,
    model: str,
    usage: Optional[Dict[str, Any]],
    user_metadata: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Insert a new row into the dirt table.

    Expected dirt schema (you create this in art.db):

        CREATE TABLE dirt (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          color_id INTEGER NOT NULL,
          art_id INTEGER NOT NULL,
          input_text TEXT NOT NULL,
          output_text TEXT NOT NULL,
          model TEXT NOT NULL,
          metadata TEXT,
          created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL
        );
    """
    created_at = utc_now_iso()
    updated_at = created_at

    color_id = int(color_row["id"])
    art_id = int(color_row["art_id"])
    input_text = color_row.get("output_text") or ""

    md_obj = {
        "source": "colors.create_dirt",
        "color_id": color_id,
        "art_id": art_id,
        "prompt_key": prompt_key,
        "usage": usage,
        "user_metadata": user_metadata or {},
    }
    md_str = json.dumps(md_obj, ensure_ascii=False)

    db = get_art_db()
    try:
        row = db.execute(
            """
            INSERT INTO dirt
              (color_id, art_id, input_text, output_text, model,
               metadata, created_at, updated_at)
            VALUES (?,        ?,      ?,          ?,          ?,
                    ?,        ?,          ?)
            RETURNING *
            """,
            (
                color_id,
                art_id,
                input_text,
                output_text,
                model,
                md_str,
                created_at,
                updated_at,
            ),
        ).fetchone()
        db.commit()
    finally:
        db.close()

    out = dict(row)
    try:
        out["metadata"] = json.loads(out.get("metadata") or "{}")
    except Exception:
        pass
    return out


# --- Usage logging / daily caps ------------------------------------------


def usage_day_utc(ts_iso: str) -> str:
    # expects ISO like 2025-11-22T...
    return ts_iso[:10]


def get_daily_tokens_for_model(model: str, day: str) -> int:
    db = get_usage_db()
    row = db.execute(
        "SELECT total_tokens FROM totals_daily WHERE day=? AND model=?",
        (day, model),
    ).fetchone()
    db.close()
    return int(row["total_tokens"]) if row else 0


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


def enforce_daily_cap_or_429(model: str, projected_tokens: int):
    limit = DAILY_LIMITS.get(model)
    if limit is None:
        return  # no cap

    now = utc_now_iso()
    day = usage_day_utc(now)
    used = get_daily_tokens_for_model(model, day)

    if used + projected_tokens > int(limit):
        abort(
            429,
            description=(
                f"Daily token cap exceeded for model '{model}'. "
                f"used={used}, projected={projected_tokens}, limit={limit}"
            ),
        )


# --- Queue + workers (build_thought + create_dirt) -----------------------

TASK_QUEUE: "queue.Queue[dict]" = queue.Queue()
TASKS: Dict[str, Dict[str, Any]] = {}
TASKS_LOCK = threading.Lock()

LLM_LOCK = threading.Lock()  # serialize client if needed for safety


def queue_stats() -> Dict[str, Any]:
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


def resolve_dirt_prompt_or_400(prompt_key: str) -> tuple[str, str]:
    """
    Normalize and resolve a dirt prompt key.

    Accepts either:
      - "entities_processes_phenomena"
      - "create_dirt_entities_processes_phenomena"
    etc.

    Returns (canonical_key, template_str)
    """
    if not prompt_key:
        abort(400, description="'prompt_key' is required")

    key = prompt_key.strip()
    if key.startswith("create_dirt_"):
        key = key[len("create_dirt_") :]

    # try short key first
    template = DIRT_PROMPTS.get(key)
    if template is None:
        # fall back to original if caller already used full variable name
        template = DIRT_PROMPTS.get(prompt_key)

    if template is None:
        abort(400, description=f"Unknown prompt_key '{prompt_key}'")

    return key, template


def worker_loop(worker_id: int):
    while True:
        task = TASK_QUEUE.get()  # blocks
        task_id = task["task_id"]
        task_type = task["task_type"]

        with TASKS_LOCK:
            meta = TASKS.get(task_id)
            if meta is not None:
                meta["status"] = "running"
                meta["started_at"] = utc_now_iso()
                meta["worker_id"] = worker_id

        try:
            if task_type == "build_thought":
                # ----------------------------------------------------------
                # build_thought flow
                # ----------------------------------------------------------
                art_id = task["art_id"]
                model = task["model"]
                user_metadata = task["user_metadata"]

                # 1) load art text
                art_row = fetch_art_text(art_id)
                thought_text = (art_row.get("art") or "").strip()
                if not thought_text:
                    raise ValueError(f"art id {art_id} has empty art text")

                # 2) format prompts
                user_prompt = build_thought_user.format(thought=thought_text)

                projected_tokens = 2000
                enforce_daily_cap_or_429(model=model, projected_tokens=projected_tokens)

                # 3) LLM call
                if _client is None:
                    raise RuntimeError(f"OpenAI client not initialized: {_client_err}")

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
                total_tokens = int(
                    (usage_dict or {}).get("total_tokens", tokens_in + tokens_out)
                )
                duration_ms = int((time.time() - t0) * 1000)

                # 4) log usage
                log_llm_usage(
                    ts=utc_now_iso(),
                    app_name="colors",
                    model=model,
                    endpoint="/colors/build_thought",
                    email=None,
                    request_id=task_id,
                    tokens_in=tokens_in,
                    tokens_out=tokens_out,
                    total_tokens=total_tokens,
                    duration_ms=duration_ms,
                    cost_usd=0.0,
                    meta_obj={"art_id": art_id, "worker_id": worker_id},
                )

                # 5) save into colors table
                colors_row = insert_color_row(
                    art_id=art_id,
                    input_art=thought_text,
                    output_text=expanded,
                    model=model,
                    usage=usage_dict,
                    user_metadata=user_metadata,
                    origin="colors.build_thought",
                )

                result = {
                    "task_type": "build_thought",
                    "art_id": art_id,
                    "expanded_thought": expanded,
                    "usage": usage_dict,
                    "saved_color": colors_row,
                }

            elif task_type == "create_dirt":
                # ----------------------------------------------------------
                # create_dirt flow
                # ----------------------------------------------------------
                color_id = task["color_id"]
                model = task["model"]
                user_metadata = task["user_metadata"]
                prompt_key = task["prompt_key"]

                canonical_key, prompt_template = resolve_dirt_prompt_or_400(prompt_key)

                # 1) load color row & ensure it is a build_thought result
                color_row = fetch_color_row(color_id)

                origin = color_row.get("origin") or ""
                if origin != "colors.build_thought":
                    raise ValueError(
                        f"color id {color_id} has origin '{origin}', "
                        "expected 'colors.build_thought'"
                    )

                thought_text = (color_row.get("output_text") or "").strip()
                if not thought_text:
                    raise ValueError(
                        f"color id {color_id} has empty output_text for create_dirt"
                    )

                art_id = int(color_row["art_id"])

                # 2) format prompt: treat the create_dirt_* string as user instructions
                user_prompt = prompt_template.format(thought=thought_text)

                projected_tokens = 2000
                enforce_daily_cap_or_429(model=model, projected_tokens=projected_tokens)

                # 3) LLM call
                if _client is None:
                    raise RuntimeError(f"OpenAI client not initialized: {_client_err}")

                with LLM_LOCK:
                    t0 = time.time()
                    resp = _client.chat.completions.create(
                        model=model,
                        messages=[
                            {
                                "role": "system",
                                "content": (
                                    "You are a precise analytical assistant. "
                                    "Follow the user instructions exactly."
                                ),
                            },
                            {"role": "user", "content": user_prompt},
                        ],
                    )
                dirt_text = (resp.choices[0].message.content or "").strip()

                usage = getattr(resp, "usage", None)
                usage_dict = usage.model_dump() if usage else None
                tokens_in = int((usage_dict or {}).get("prompt_tokens", 0))
                tokens_out = int((usage_dict or {}).get("completion_tokens", 0))
                total_tokens = int(
                    (usage_dict or {}).get("total_tokens", tokens_in + tokens_out)
                )
                duration_ms = int((time.time() - t0) * 1000)

                # 4) log usage
                log_llm_usage(
                    ts=utc_now_iso(),
                    app_name="colors",
                    model=model,
                    endpoint="/colors/create_dirt",
                    email=None,
                    request_id=task_id,
                    tokens_in=tokens_in,
                    tokens_out=tokens_out,
                    total_tokens=total_tokens,
                    duration_ms=duration_ms,
                    cost_usd=0.0,
                    meta_obj={
                        "color_id": color_id,
                        "art_id": art_id,
                        "worker_id": worker_id,
                        "prompt_key": canonical_key,
                    },
                )

                # 5) save into dirt table
                dirt_row = insert_dirt_row(
                    color_row=color_row,
                    prompt_key=canonical_key,
                    output_text=dirt_text,
                    model=model,
                    usage=usage_dict,
                    user_metadata=user_metadata,
                )

                result = {
                    "task_type": "create_dirt",
                    "color_id": color_id,
                    "art_id": art_id,
                    "prompt_key": canonical_key,
                    "dirt_text": dirt_text,
                    "usage": usage_dict,
                    "saved_dirt": dirt_row,
                }

            else:
                # Unknown task_type in this simplified server
                raise ValueError(
                    f"Unsupported task_type in simplified colors.py: {task_type}"
                )

            # Write result back into TASKS
            with TASKS_LOCK:
                meta = TASKS.get(task_id)
                if meta is not None:
                    meta["status"] = "done"
                    meta["finished_at"] = utc_now_iso()
                    meta["result"] = result

        except Exception as e:
            with TASKS_LOCK:
                meta = TASKS.get(task_id)
                if meta is not None:
                    meta["status"] = "error"
                    meta["finished_at"] = utc_now_iso()
                    meta["error"] = str(e)

        finally:
            TASK_QUEUE.task_done()


# start 2 background workers when module loads
for wid in range(2):
    t = threading.Thread(target=worker_loop, args=(wid,), daemon=True)
    t.start()


# --- HTTP endpoints -------------------------------------------------------


@app.get("/health")
def health():
    ok = _client is not None
    return jsonify(
        {
            "ok": ok,
            "service": "colors",
            "model_default": MODEL_DEFAULT,
            "art_db_path": ART_DB_PATH,
            "time": utc_now_iso(),
            "error": None if ok else str(_client_err),
        }
    )


@app.post("/colors/build_thought")
def colors_build_thought():
    """
    Enqueue a build_thought expansion job.

    Body:
      {
        "art_id": 123,
        "model": "...optional...",
        "temperature": 0.6,   # ignored in simplified version
        "metadata": {...}     # arbitrary dict, stored in metadata
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
    # temperature is accepted but not currently used in the call
    _temperature = float(payload.get("temperature", 0.6))
    user_metadata = payload.get("metadata") or {}

    task_id = str(uuid.uuid4())

    # record task
    with TASKS_LOCK:
        TASKS[task_id] = {
            "task_id": task_id,
            "task_type": "build_thought",
            "art_id": art_id,
            "status": "queued",
            "created_at": utc_now_iso(),
            "model": model,
            "user_metadata": user_metadata,
        }

    TASK_QUEUE.put(
        {
            "task_id": task_id,
            "task_type": "build_thought",
            "art_id": art_id,
            "model": model,
            "user_metadata": user_metadata,
        }
    )

    return (
        jsonify(
            {
                "task_id": task_id,
                "status": "queued",
                "art_id": art_id,
                "model": model,
                "queue": queue_stats(),
            }
        ),
        202,
    )


# Optional alias if you ever strip /colors/ in nginx
@app.post("/build_thought")
def build_thought_alias():
    return colors_build_thought()


@app.post("/colors/create_dirt")
def colors_create_dirt():
    """
    Enqueue a create_dirt job.

    Body:
      {
        "color_id": 456,
        "prompt_key": "entities_processes_phenomena",  # or any mapped key
        "model": "...optional...",
        "metadata": {...}  # arbitrary dict, stored in dirt.metadata
      }

    Notes:
      - color_id must refer to a colors row whose origin is 'colors.build_thought'
      - prompt_key selects which create_dirt_* prompt to use
    """
    if _client is None:
        abort(500, description=f"OpenAI client not initialized: {_client_err}")

    payload = require_json()
    color_id = payload.get("color_id")
    if not isinstance(color_id, int):
        abort(400, description="'color_id' is required and must be an integer")

    prompt_key_raw = payload.get("prompt_key")
    if not isinstance(prompt_key_raw, str):
        abort(400, description="'prompt_key' is required and must be a string")

    # Validate that the color row exists and is a build_thought result.
    color_row = fetch_color_row(color_id)
    origin = color_row.get("origin") or ""
    if origin != "colors.build_thought":
        abort(
            400,
            description=(
                f"color id {color_id} has origin '{origin}', "
                "expected 'colors.build_thought'"
            ),
        )

    canonical_key, _ = resolve_dirt_prompt_or_400(prompt_key_raw)

    model = payload.get("model", MODEL_DEFAULT)
    user_metadata = payload.get("metadata") or {}

    task_id = str(uuid.uuid4())

    # record task
    with TASKS_LOCK:
        TASKS[task_id] = {
            "task_id": task_id,
            "task_type": "create_dirt",
            "color_id": color_id,
            "prompt_key": canonical_key,
            "status": "queued",
            "created_at": utc_now_iso(),
            "model": model,
            "user_metadata": user_metadata,
        }

    TASK_QUEUE.put(
        {
            "task_id": task_id,
            "task_type": "create_dirt",
            "color_id": color_id,
            "prompt_key": canonical_key,
            "model": model,
            "user_metadata": user_metadata,
        }
    )

    return (
        jsonify(
            {
                "task_id": task_id,
                "status": "queued",
                "color_id": color_id,
                "prompt_key": canonical_key,
                "model": model,
                "queue": queue_stats(),
            }
        ),
        202,
    )


# Optional alias
@app.post("/create_dirt")
def create_dirt_alias():
    return colors_create_dirt()


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


@app.get("/colors/queue/tasks")
def list_queue_tasks():
    """
    Inspect in-memory TASKS.

    Query params (all optional):
      - status: queued|running|done|error
      - task_type: 'build_thought' or 'create_dirt'
      - limit: max number of tasks to return (default 200)
      - include_error: "1" to include full error text if present
    """
    status = request.args.get("status")
    task_type = request.args.get("task_type")
    include_error = request.args.get("include_error") == "1"

    try:
        limit = int(request.args.get("limit", "200"))
    except ValueError:
        abort(400, description="'limit' must be an integer")

    with TASKS_LOCK:
        items = []
        for tid, meta in TASKS.items():
            if status and meta.get("status") != status:
                continue
            if task_type and meta.get("task_type") != task_type:
                continue

            entry = dict(meta)
            entry["task_id"] = tid

            if not include_error and "error" in entry:
                entry["has_error"] = True
                entry.pop("error", None)

            items.append(entry)

    items.sort(key=lambda x: x.get("created_at", ""), reverse=True)

    if limit > 0:
        items = items[:limit]

    return jsonify(
        {
            "queue": queue_stats(),
            "count": len(items),
            "tasks": items,
        }
    )


@app.get("/colors/queue/workers")
def get_queue_workers():
    """
    Snapshot of what each worker is currently running,
    derived from TASKS entries with status == 'running'.
    """
    with TASKS_LOCK:
        running = [t for t in TASKS.values() if t.get("status") == "running"]

    workers: Dict[str, list[Dict[str, Any]]] = {}
    for t in running:
        wid = t.get("worker_id")
        if wid is None:
            continue
        wid_str = str(wid)
        workers.setdefault(wid_str, []).append(
            {
                "task_id": t.get("task_id"),
                "task_type": t.get("task_type"),
                "status": t.get("status"),
                "art_id": t.get("art_id") if t.get("task_type") == "build_thought" else None,
                "color_id": t.get("color_id") if t.get("task_type") == "create_dirt" else None,
                "created_at": t.get("created_at"),
                "started_at": t.get("started_at"),
            }
        )

    return jsonify(
        {
            "queue": queue_stats(),
            "workers": workers,
        }
    )


@app.get("/colors/by_art/<int:art_id>")
def colors_by_art(art_id: int):
    """
    Return all color expansions for a given art_id.

    Optional query param:
      ?origin=colors.build_thought
    """
    origin = request.args.get("origin", type=str)

    db = get_art_db()
    if origin:
        rows = db.execute(
            """
            SELECT *
            FROM colors
            WHERE art_id = ?
              AND origin = ?
            ORDER BY created_at DESC
            """,
            (art_id, origin),
        ).fetchall()
    else:
        rows = db.execute(
            """
            SELECT *
            FROM colors
            WHERE art_id = ?
            ORDER BY created_at DESC
            """,
            (art_id,),
        ).fetchall()
    db.close()

    out = []
    for r in rows:
        row = dict(r)
        try:
            row["metadata"] = json.loads(row.get("metadata") or "{}")
        except Exception:
            pass
        out.append(row)

    return jsonify(out)


@app.get("/dirt/by_color/<int:color_id>")
def dirt_by_color(color_id: int):
    """
    Return all dirt expansions for a given color_id.

    Example:
      GET /dirt/by_color/123

    Response:
      [
        {
          "id": ...,
          "color_id": ...,
          "art_id": ...,
          "input_text": "...",
          "output_text": "...",
          "model": "...",
          "metadata": {...},   # parsed JSON
          "created_at": "...",
          "updated_at": "..."
        },
        ...
      ]
    """
    db = get_art_db()
    try:
        rows = db.execute(
            """
            SELECT *
            FROM dirt
            WHERE color_id = ?
            ORDER BY created_at DESC
            """,
            (color_id,),
        ).fetchall()
    finally:
        db.close()

    out = []
    for r in rows:
        row = dict(r)
        # parse metadata JSON if present
        try:
            row["metadata"] = json.loads(row.get("metadata") or "{}")
        except Exception:
            pass
        out.append(row)

    return jsonify(out)


@app.get("/colors/dirt/by_color/<int:color_id>")
def colors_dirt_by_color_alias(color_id: int):
    return dirt_by_color(color_id)


# --- Error handlers -------------------------------------------------------


@app.errorhandler(400)
@app.errorhandler(404)
@app.errorhandler(429)
@app.errorhandler(500)
def handle_error(err):
    code = getattr(err, "code", 500)
    return (
        jsonify(
            {
                "error": True,
                "status": code,
                "message": getattr(err, "description", str(err)),
            }
        ),
        code,
    )


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=APP_PORT, debug=False)
