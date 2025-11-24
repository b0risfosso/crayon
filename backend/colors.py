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
from prompts import digital_playground_bridge_sys, digital_playground_bridge_user  # type: ignore
from prompts import entities_prompt_sys, entities_prompt_user  # type: ignore
from prompts import bridge_simulation_prompt  # type: ignore
from prompts import theory_architecture_prompt  # type: ignore
from prompts import physical_world_bridge_prompt  # type: ignore
from prompts import math_bridge_prompt  # type: ignore
from prompts import language_bridge_prompt  # type: ignore
from prompts import data_bridge_prompt  # type: ignore
from prompts import computational_bridge_prompt  # type: ignore
from prompts import music_bridge_prompt  # type: ignore
from prompts import information_bridge_prompt  # type: ignore
from prompts import poetry_bridge_prompt  # type: ignore
from prompts import metaphysics_bridge_prompt  # type: ignore
from prompts import entity_bridge_relationship_prompt  # type: ignore










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

ENTITIES_KEYS = [
    "objects",
    "processes_interactions",
    "forces_drivers",
    "systems_structures",
    "variables_state_quantities",
    "agents_actors",
    "patterns_relationships",
    "constraints_boundary_conditions",
    "signals_information_flows",
    "phenomena",
    "values_goals_criteria",
    "failure_modes_edge_cases",
    "latent_opportunities_potentials",
    "questions_uncertainties",
]

def validate_entities_json(parsed: dict):
    """
    Now expects each key -> list of objects:
      {name:str, description:str, role_in_thought:str}
    """
    if not isinstance(parsed, dict):
        raise ValueError("entities JSON is not an object")

    for k in ENTITIES_KEYS:
        if k not in parsed or not isinstance(parsed[k], list):
            raise ValueError(f"entities JSON missing key or non-list: {k}")

        for i, ent in enumerate(parsed[k]):
            if not isinstance(ent, dict):
                raise ValueError(f"{k}[{i}] is not an object")
            for req in ("name", "description", "role_in_thought"):
                if req not in ent or not isinstance(ent[req], str):
                    raise ValueError(f"{k}[{i}] missing/invalid '{req}'")

def normalize_entities_json(parsed: dict) -> dict:
    """
    If any group contains strings, wrap them into objects.
    """
    for k in ENTITIES_KEYS:
        if k not in parsed or not isinstance(parsed[k], list):
            continue

        new_list = []
        for item in parsed[k]:
            if isinstance(item, dict):
                new_list.append(item)
            elif isinstance(item, str):
                new_list.append({
                    "name": item.strip(),
                    "description": "",
                    "role_in_thought": ""
                })
            else:
                # drop weird items
                continue
        parsed[k] = new_list
    return parsed



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

def run_simulation_seeds_from_color(task_id: str, color_id: int, model: str, temperature: float, user_metadata: dict, worker_id: int):
    db = get_art_db()
    color_row = db.execute(
        "SELECT id, art_id, output_text FROM colors WHERE id = ?",
        (color_id,)
    ).fetchone()

    if not color_row:
        db.close()
        raise ValueError("color not found")

    art_id = color_row["art_id"]
    thought_text = (color_row["output_text"] or "").strip()
    if not thought_text:
        db.close()
        raise ValueError("color output_text empty")

    system_prompt = digital_playground_bridge_sys
    user_prompt = digital_playground_bridge_user.format(thought=thought_text)

    t0 = time.time()
    resp = _client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
    )
    duration_ms = int((time.time() - t0) * 1000)
    output_text = (resp.choices[0].message.content or "").strip()

    # validate STRICT JSON
    parsed = json.loads(output_text)
    if "simulation_seeds" not in parsed or not isinstance(parsed["simulation_seeds"], list):
        db.close()
        raise ValueError("model returned invalid JSON (missing simulation_seeds list)")

    created_at = utc_now_iso()

    db.execute(
        """
        INSERT INTO simulation_seeds
          (color_id, art_id, input_text, seeds_json, model, temperature, created_at)
        VALUES (?,        ?,      ?,          ?,         ?,     ?,          ?)
        """,
        (
            color_id,
            art_id,
            thought_text,
            output_text,
            model,
            temperature,
            created_at,
        ),
    )
    db.commit()

    new_id = db.execute("SELECT last_insert_rowid()").fetchone()[0]
    saved_row = db.execute(
        "SELECT * FROM simulation_seeds WHERE id = ?",
        (new_id,)
    ).fetchone()
    db.close()

    usage = getattr(resp, "usage", None)
    usage_dict = usage.model_dump() if usage else None

    return {
        "color_id": color_id,
        "art_id": art_id,
        "input_text": thought_text,
        "seeds": parsed,
        "saved_simulation_seeds": dict(saved_row),
        "usage": usage_dict,
        "duration_ms": duration_ms,
        "worker_id": worker_id,
    }



def worker_loop(worker_id: int):
    while True:
        task = TASK_QUEUE.get()  # blocks
        task_id = task["task_id"]
        task_type = task["task_type"]

        with TASKS_LOCK:
            TASKS[task_id]["status"] = "running"
            TASKS[task_id]["started_at"] = utc_now_iso()
            TASKS[task_id]["worker_id"] = worker_id

        try:
            # ============================================================
            #  TYPE 1: build_thought (original Colors expansion)
            # ============================================================
            if task_type == "build_thought":
                art_id = task["art_id"]
                model = task["model"]
                user_metadata = task["user_metadata"]

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
                )

                # 6) mark done
                with TASKS_LOCK:
                    TASKS[task_id]["status"] = "done"
                    TASKS[task_id]["finished_at"] = utc_now_iso()
                    TASKS[task_id]["result"] = {
                        "task_type": "build_thought",
                        "art_id": art_id,
                        "expanded_thought": expanded,
                        "usage": usage_dict,
                        "saved_color": colors_row,
                    }



            # ============================================================
            #  TYPE 2: simulation_seeds (NEW)
            # ============================================================
            elif task_type == "simulation_seeds":
                color_id = task["color_id"]
                model = task["model"]
                temperature = float(task["temperature"])
                user_metadata = task["user_metadata"]

                # Load color row
                db = get_art_db()
                row = db.execute(
                    "SELECT id, art_id, output_text FROM colors WHERE id = ?",
                    (color_id,)
                ).fetchone()
                if not row:
                    db.close()
                    raise ValueError(f"color id {color_id} not found")

                art_id = row["art_id"]
                thought_text = (row["output_text"] or "").strip()
                if not thought_text:
                    db.close()
                    raise ValueError(f"color {color_id} has empty output_text")

                system_prompt = digital_playground_bridge_sys
                user_prompt = digital_playground_bridge_user.format(thought=thought_text)

                projected_tokens = 4000
                enforce_daily_cap_or_429(model=model, projected_tokens=projected_tokens)

                # Call LLM
                with LLM_LOCK:
                    t0 = time.time()
                    resp = _client.chat.completions.create(
                        model=model,
                        messages=[
                            {"role": "system", "content": system_prompt},
                            {"role": "user", "content": user_prompt},
                        ],
                    )

                output_text = (resp.choices[0].message.content or "").strip()
                usage = getattr(resp, "usage", None)
                usage_dict = usage.model_dump() if usage else None
                tokens_in = int((usage_dict or {}).get("prompt_tokens", 0))
                tokens_out = int((usage_dict or {}).get("completion_tokens", 0))
                total_tokens = int((usage_dict or {}).get("total_tokens", tokens_in + tokens_out))
                duration_ms = int((time.time() - t0) * 1000)

                # STRICT JSON validation
                parsed = json.loads(output_text)
                if "simulation_seeds" not in parsed or not isinstance(parsed["simulation_seeds"], list):
                    db.close()
                    raise ValueError("model response missing simulation_seeds list")

                # Save to DB
                created_at = utc_now_iso()
                db.execute(
                    """
                    INSERT INTO simulation_seeds
                      (color_id, art_id, input_text, seeds_json, model, temperature, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        color_id,
                        art_id,
                        thought_text,
                        output_text,
                        model,
                        temperature,
                        created_at,
                    ),
                )
                db.commit()

                new_id = db.execute("SELECT last_insert_rowid()").fetchone()[0]
                saved_row = dict(
                    db.execute(
                        "SELECT * FROM simulation_seeds WHERE id = ?",
                        (new_id,)
                    ).fetchone()
                )
                db.close()

                # LLM usage logging
                log_llm_usage(
                    ts=utc_now_iso(),
                    app_name="colors",
                    model=model,
                    endpoint="/colors/simulation_seeds",
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
                        "worker_id": worker_id
                    },
                )

                with TASKS_LOCK:
                    TASKS[task_id]["status"] = "done"
                    TASKS[task_id]["finished_at"] = utc_now_iso()
                    TASKS[task_id]["result"] = {
                        "task_type": "simulation_seeds",
                        "color_id": color_id,
                        "art_id": art_id,
                        "seeds": parsed,
                        "saved_simulation_seeds": saved_row,
                        "usage": usage_dict,
                    }

            # ============================================================
            #  TYPE 3: entities (NEW)
            # ============================================================
            #elif task_type == "entities":
            # deleted
            elif task_type == "bridge_simulation":
                color_id = task["color_id"]
                model = task["model"]
                temperature = float(task["temperature"])
                user_metadata = task["user_metadata"]

                # Load color row
                db = get_art_db()
                row = db.execute(
                    "SELECT id, art_id, output_text FROM colors WHERE id = ?",
                    (color_id,)
                ).fetchone()
                if not row:
                    db.close()
                    raise ValueError(f"color id {color_id} not found")

                art_id = int(row["art_id"])
                thought_text = row["output_text"] or ""

                # Build prompt (single-message prompt stored in prompts.py)
                system_prompt = bridge_simulation_prompt.format(thought=thought_text)
                user_prompt = ""  # prompt is carried in system message

                projected_tokens = 5000
                enforce_daily_cap_or_429(model=model, projected_tokens=projected_tokens)

                # Call LLM
                with LLM_LOCK:
                    t0 = time.time()
                    resp = _client.chat.completions.create(
                        model=model,
                        messages=[
                            {"role": "system", "content": system_prompt},
                            {"role": "user", "content": user_prompt},
                        ],
                    )
                    duration_ms = int((time.time() - t0) * 1000)

                output_text = (resp.choices[0].message.content or "").strip()

                usage = getattr(resp, "usage", None)
                usage_dict = usage.model_dump() if usage else None
                tokens_in = int((usage_dict or {}).get("prompt_tokens", 0))
                tokens_out = int((usage_dict or {}).get("completion_tokens", 0))
                total_tokens = int((usage_dict or {}).get("total_tokens", tokens_in + tokens_out))
                duration_ms = int((time.time() - t0) * 1000)

                # Save to bridges table (NEW)
                created_at = utc_now_iso()
                db.execute(
                    """
                    INSERT INTO bridges
                      (color_id, art_id, input_text, bridge_text, bridge_type, model, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        color_id,
                        art_id,
                        thought_text,
                        output_text,
                        "simulation_architecture",
                        model,
                        created_at,
                    ),
                )
                db.commit()

                new_id = db.execute("SELECT last_insert_rowid()").fetchone()[0]
                saved_row = dict(
                    db.execute(
                        "SELECT * FROM bridges WHERE id = ?",
                        (new_id,)
                    ).fetchone()
                )
                db.close()

                # LLM usage logging
                log_llm_usage(
                    ts=utc_now_iso(),
                    app_name="colors",
                    model=model,
                    endpoint="/colors/bridge_simulation",
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
                        "user_metadata": user_metadata,
                    },
                )


                with TASKS_LOCK:
                    TASKS[task_id]["status"] = "done"
                    TASKS[task_id]["finished_at"] = utc_now_iso()
                    TASKS[task_id]["result"] = {
                        "task_type": "bridge_simulation",
                        "color_id": color_id,
                        "art_id": art_id,
                        "input_text": thought_text,
                        "output_text": output_text,
                        "model": model,
                        "temperature": temperature,
                        "usage": usage_dict,
                        "duration_ms": duration_ms,
                    }

                        # ============================================================
            #  TYPE 5: theory_architecture (NEW) -> saves to bridges
            # ============================================================
            elif task_type == "theory_architecture":
                color_id = task["color_id"]
                model = task["model"]
                temperature = float(task["temperature"])
                user_metadata = task["user_metadata"]

                # Load color row
                db = get_art_db()
                row = db.execute(
                    "SELECT id, art_id, output_text FROM colors WHERE id = ?",
                    (color_id,)
                ).fetchone()
                if not row:
                    db.close()
                    raise ValueError(f"color id {color_id} not found")

                art_id = row["art_id"]
                thought_text = (row["output_text"] or "").strip()
                if not thought_text:
                    db.close()
                    raise ValueError(f"color {color_id} has empty output_text")

                system_prompt = theory_architecture_prompt.format(thought=thought_text)
                user_prompt = ""

                projected_tokens = 5000
                enforce_daily_cap_or_429(model=model, projected_tokens=projected_tokens)

                with LLM_LOCK:
                    t0 = time.time()
                    resp = _client.chat.completions.create(
                        model=model,
                        messages=[
                            {"role": "system", "content": system_prompt},
                            {"role": "user", "content": user_prompt},
                        ],
                    )

                output_text = (resp.choices[0].message.content or "").strip()

                usage = getattr(resp, "usage", None)
                usage_dict = usage.model_dump() if usage else None
                tokens_in = int((usage_dict or {}).get("prompt_tokens", 0))
                tokens_out = int((usage_dict or {}).get("completion_tokens", 0))
                total_tokens = int((usage_dict or {}).get("total_tokens", tokens_in + tokens_out))
                duration_ms = int((time.time() - t0) * 1000)

                # Save to bridges table (same as simulation_architecture)
                created_at = utc_now_iso()
                db.execute(
                    """
                    INSERT INTO bridges
                      (color_id, art_id, input_text, bridge_text, bridge_type, model, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        color_id,
                        art_id,
                        thought_text,
                        output_text,
                        "theory_architecture",
                        model,
                        created_at,
                    ),
                )
                db.commit()

                new_id = db.execute("SELECT last_insert_rowid()").fetchone()[0]
                saved_row = dict(
                    db.execute(
                        "SELECT * FROM bridges WHERE id = ?",
                        (new_id,)
                    ).fetchone()
                )
                db.close()

                log_llm_usage(
                    ts=utc_now_iso(),
                    app_name="colors",
                    model=model,
                    endpoint="/colors/theory_architecture",
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
                        "user_metadata": user_metadata,
                    },
                )

                with TASKS_LOCK:
                    TASKS[task_id]["status"] = "done"
                    TASKS[task_id]["finished_at"] = utc_now_iso()
                    TASKS[task_id]["result"] = {
                        "task_type": "theory_architecture",
                        "color_id": color_id,
                        "art_id": art_id,
                        "theory_text": output_text,
                        "saved_bridge": saved_row,
                        "usage": usage_dict,
                    }

                        # ============================================================
            #  TYPE 6: physical_world_bridge (NEW) -> saves to bridges
            # ============================================================
            elif task_type == "physical_world_bridge":
                color_id = task["color_id"]
                model = task["model"]
                temperature = float(task["temperature"])
                user_metadata = task["user_metadata"]

                db = get_art_db()
                row = db.execute(
                    "SELECT id, art_id, output_text FROM colors WHERE id = ?",
                    (color_id,)
                ).fetchone()
                if not row:
                    db.close()
                    raise ValueError(f"color id {color_id} not found")

                art_id = row["art_id"]
                thought_text = (row["output_text"] or "").strip()
                if not thought_text:
                    db.close()
                    raise ValueError(f"color {color_id} has empty output_text")

                system_prompt = physical_world_bridge_prompt.format(thought=thought_text)
                user_prompt = ""

                projected_tokens = 5000
                enforce_daily_cap_or_429(model=model, projected_tokens=projected_tokens)

                with LLM_LOCK:
                    t0 = time.time()
                    resp = _client.chat.completions.create(
                        model=model,
                        temperature=temperature,
                        messages=[
                            {"role": "system", "content": system_prompt},
                            {"role": "user", "content": user_prompt},
                        ],
                    )

                output_text = (resp.choices[0].message.content or "").strip()

                usage = getattr(resp, "usage", None)
                usage_dict = usage.model_dump() if usage else None
                tokens_in = int((usage_dict or {}).get("prompt_tokens", 0))
                tokens_out = int((usage_dict or {}).get("completion_tokens", 0))
                total_tokens = int((usage_dict or {}).get("total_tokens", tokens_in + tokens_out))
                duration_ms = int((time.time() - t0) * 1000)

                # Save to bridges with bridge_type
                created_at = utc_now_iso()
                db.execute(
                    """
                    INSERT INTO bridges
                      (color_id, art_id, input_text, bridge_text, bridge_type, model, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        color_id,
                        art_id,
                        thought_text,
                        output_text,
                        "physical_world_bridge",
                        model,
                        created_at,
                    ),
                )
                db.commit()

                new_id = db.execute("SELECT last_insert_rowid()").fetchone()[0]
                saved_row = dict(
                    db.execute(
                        "SELECT * FROM bridges WHERE id = ?",
                        (new_id,)
                    ).fetchone()
                )
                db.close()

                log_llm_usage(
                    ts=utc_now_iso(),
                    app_name="colors",
                    model=model,
                    endpoint="/colors/physical_world_bridge",
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
                        "user_metadata": user_metadata,
                    },
                )

                with TASKS_LOCK:
                    TASKS[task_id]["status"] = "done"
                    TASKS[task_id]["finished_at"] = utc_now_iso()
                    TASKS[task_id]["result"] = {
                        "task_type": "physical_world_bridge",
                        "color_id": color_id,
                        "art_id": art_id,
                        "bridge_text": output_text,
                        "saved_bridge": saved_row,
                        "usage": usage_dict,
                    }
                        # ============================================================
            #  TYPE: math_bridge (NEW)
            # ============================================================
            elif task_type == "math_bridge":
                color_id = task["color_id"]
                model = task["model"]
                temperature = float(task["temperature"])
                user_metadata = task["user_metadata"]

                db = get_art_db()
                row = db.execute(
                    "SELECT id, art_id, output_text FROM colors WHERE id = ?",
                    (color_id,)
                ).fetchone()

                if not row:
                    db.close()
                    raise ValueError(f"color id {color_id} not found")

                art_id = row["art_id"]
                thought_text = (row["output_text"] or "").strip()

                if not thought_text:
                    db.close()
                    raise ValueError(f"color {color_id} has empty output_text")

                system_prompt = math_bridge_prompt.format(thought=thought_text)
                user_prompt = ""

                projected_tokens = 5000
                enforce_daily_cap_or_429(model=model, projected_tokens=projected_tokens)

                with LLM_LOCK:
                    t0 = time.time()
                    resp = _client.chat.completions.create(
                        model=model,
                        temperature=temperature,
                        messages=[
                            {"role": "system", "content": system_prompt},
                            {"role": "user", "content": user_prompt},
                        ],
                    )

                output_text = (resp.choices[0].message.content or "").strip()

                usage = getattr(resp, "usage", None)
                usage_dict = usage.model_dump() if usage else None
                tokens_in = int((usage_dict or {}).get("prompt_tokens", 0))
                tokens_out = int((usage_dict or {}).get("completion_tokens", 0))
                total_tokens = int((usage_dict or {}).get("total_tokens", tokens_in + tokens_out))
                duration_ms = int((time.time() - t0) * 1000)

                created_at = utc_now_iso()

                # --- SAVE INTO BRIDGES ---
                db.execute(
                    """
                    INSERT INTO bridges
                    (color_id, art_id, input_text, bridge_text, bridge_type, model, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        color_id,
                        art_id,
                        thought_text,
                        output_text,
                        "math_bridge",
                        model,
                        created_at,
                    ),
                )
                db.commit()

                new_id = db.execute("SELECT last_insert_rowid()").fetchone()[0]
                saved_row = dict(
                    db.execute("SELECT * FROM bridges WHERE id = ?", (new_id,)).fetchone()
                )
                db.close()

                log_llm_usage(
                    ts=utc_now_iso(),
                    app_name="colors",
                    model=model,
                    endpoint="/colors/math_bridge",
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
                        "user_metadata": user_metadata,
                    },
                )

                with TASKS_LOCK:
                    TASKS[task_id]["status"] = "done"
                    TASKS[task_id]["finished_at"] = utc_now_iso()
                    TASKS[task_id]["result"] = {
                        "task_type": "math_bridge",
                        "color_id": color_id,
                        "art_id": art_id,
                        "bridge_text": output_text,
                        "saved_bridge": saved_row,
                        "usage": usage_dict,
                    }
                        # ============================================================
            #  TYPE: language_bridge (NEW)
            # ============================================================
            elif task_type == "language_bridge":
                color_id = task["color_id"]
                model = task["model"]
                temperature = float(task["temperature"])
                user_metadata = task["user_metadata"]

                db = get_art_db()
                row = db.execute(
                    "SELECT id, art_id, output_text FROM colors WHERE id = ?",
                    (color_id,)
                ).fetchone()

                if not row:
                    db.close()
                    raise ValueError(f"color id {color_id} not found")

                art_id = row["art_id"]
                thought_text = (row["output_text"] or "").strip()

                if not thought_text:
                    db.close()
                    raise ValueError(f"color {color_id} has empty output_text")

                system_prompt = language_bridge_prompt.format(thought=thought_text)
                user_prompt = ""

                projected_tokens = 5000
                enforce_daily_cap_or_429(model=model, projected_tokens=projected_tokens)

                with LLM_LOCK:
                    t0 = time.time()
                    resp = _client.chat.completions.create(
                        model=model,
                        temperature=temperature,
                        messages=[
                            {"role": "system", "content": system_prompt},
                            {"role": "user", "content": user_prompt},
                        ],
                    )

                output_text = (resp.choices[0].message.content or "").strip()

                usage = getattr(resp, "usage", None)
                usage_dict = usage.model_dump() if usage else None
                tokens_in = int((usage_dict or {}).get("prompt_tokens", 0))
                tokens_out = int((usage_dict or {}).get("completion_tokens", 0))
                total_tokens = int((usage_dict or {}).get("total_tokens", tokens_in + tokens_out))
                duration_ms = int((time.time() - t0) * 1000)

                created_at = utc_now_iso()

                # --- SAVE INTO bridges WITH TYPE ---
                db.execute(
                    """
                    INSERT INTO bridges
                    (color_id, art_id, input_text, bridge_text, bridge_type, model, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        color_id,
                        art_id,
                        thought_text,
                        output_text,
                        "language_bridge",
                        model,
                        created_at,
                    ),
                )
                db.commit()

                new_id = db.execute("SELECT last_insert_rowid()").fetchone()[0]
                saved_row = dict(
                    db.execute("SELECT * FROM bridges WHERE id = ?", (new_id,)).fetchone()
                )
                db.close()

                log_llm_usage(
                    ts=utc_now_iso(),
                    app_name="colors",
                    model=model,
                    endpoint="/colors/language_bridge",
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
                        "user_metadata": user_metadata,
                    },
                )

                with TASKS_LOCK:
                    TASKS[task_id]["status"] = "done"
                    TASKS[task_id]["finished_at"] = utc_now_iso()
                    TASKS[task_id]["result"] = {
                        "task_type": "language_bridge",
                        "color_id": color_id,
                        "art_id": art_id,
                        "bridge_text": output_text,
                        "saved_bridge": saved_row,
                        "usage": usage_dict,
                    }
                        # ============================================================
            #  TYPE: data_bridge (NEW)
            # ============================================================
            elif task_type == "data_bridge":
                color_id = task["color_id"]
                model = task["model"]
                temperature = float(task["temperature"])
                user_metadata = task["user_metadata"]

                db = get_art_db()
                row = db.execute(
                    "SELECT id, art_id, output_text FROM colors WHERE id = ?",
                    (color_id,)
                ).fetchone()

                if not row:
                    db.close()
                    raise ValueError(f"color id {color_id} not found")

                art_id = row["art_id"]
                thought_text = (row["output_text"] or "").strip()

                if not thought_text:
                    db.close()
                    raise ValueError(f"color {color_id} has empty output_text")

                system_prompt = data_bridge_prompt.format(thought=thought_text)
                user_prompt = ""

                projected_tokens = 5000
                enforce_daily_cap_or_429(model=model, projected_tokens=projected_tokens)

                with LLM_LOCK:
                    t0 = time.time()
                    resp = _client.chat.completions.create(
                        model=model,
                        temperature=temperature,
                        messages=[
                            {"role": "system", "content": system_prompt},
                            {"role": "user", "content": user_prompt},
                        ],
                    )

                output_text = (resp.choices[0].message.content or "").strip()

                usage = getattr(resp, "usage", None)
                usage_dict = usage.model_dump() if usage else None
                tokens_in = int((usage_dict or {}).get("prompt_tokens", 0))
                tokens_out = int((usage_dict or {}).get("completion_tokens", 0))
                total_tokens = int((usage_dict or {}).get("total_tokens", tokens_in + tokens_out))
                duration_ms = int((time.time() - t0) * 1000)

                created_at = utc_now_iso()

                # --- SAVE INTO bridges WITH TYPE ---
                db.execute(
                    """
                    INSERT INTO bridges
                    (color_id, art_id, input_text, bridge_text, bridge_type, model, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        color_id,
                        art_id,
                        thought_text,
                        output_text,
                        "data_bridge",
                        model,
                        created_at,
                    ),
                )
                db.commit()

                new_id = db.execute("SELECT last_insert_rowid()").fetchone()[0]
                saved_row = dict(
                    db.execute("SELECT * FROM bridges WHERE id = ?", (new_id,)).fetchone()
                )
                db.close()

                log_llm_usage(
                    ts=utc_now_iso(),
                    app_name="colors",
                    model=model,
                    endpoint="/colors/data_bridge",
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
                        "user_metadata": user_metadata,
                    },
                )

                with TASKS_LOCK:
                    TASKS[task_id]["status"] = "done"
                    TASKS[task_id]["finished_at"] = utc_now_iso()
                    TASKS[task_id]["result"] = {
                        "task_type": "data_bridge",
                        "color_id": color_id,
                        "art_id": art_id,
                        "bridge_text": output_text,
                        "saved_bridge": saved_row,
                        "usage": usage_dict,
                    }
                        # ============================================================
            #  TYPE: computational_bridge (NEW)
            # ============================================================
            elif task_type == "computational_bridge":
                color_id = task["color_id"]
                model = task["model"]
                temperature = float(task["temperature"])
                user_metadata = task["user_metadata"]

                db = get_art_db()
                row = db.execute(
                    "SELECT id, art_id, output_text FROM colors WHERE id = ?",
                    (color_id,)
                ).fetchone()

                if not row:
                    db.close()
                    raise ValueError(f"color id {color_id} not found")

                art_id = row["art_id"]
                thought_text = (row["output_text"] or "").strip()

                if not thought_text:
                    db.close()
                    raise ValueError(f"color {color_id} has empty output_text")

                system_prompt = computational_bridge_prompt.format(thought=thought_text)
                user_prompt = ""

                projected_tokens = 6000
                enforce_daily_cap_or_429(model=model, projected_tokens=projected_tokens)

                with LLM_LOCK:
                    t0 = time.time()
                    resp = _client.chat.completions.create(
                        model=model,
                        temperature=temperature,
                        messages=[
                            {"role": "system", "content": system_prompt},
                            {"role": "user", "content": user_prompt},
                        ],
                    )

                output_text = (resp.choices[0].message.content or "").strip()

                usage = getattr(resp, "usage", None)
                usage_dict = usage.model_dump() if usage else None
                tokens_in = int((usage_dict or {}).get("prompt_tokens", 0))
                tokens_out = int((usage_dict or {}).get("completion_tokens", 0))
                total_tokens = int((usage_dict or {}).get("total_tokens", tokens_in + tokens_out))
                duration_ms = int((time.time() - t0) * 1000)

                created_at = utc_now_iso()

                # SAVE INTO bridges WITH TYPE
                db.execute(
                    """
                    INSERT INTO bridges
                    (color_id, art_id, input_text, bridge_text, bridge_type, model, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        color_id,
                        art_id,
                        thought_text,
                        output_text,
                        "computational_bridge",
                        model,
                        created_at,
                    ),
                )
                db.commit()

                new_id = db.execute("SELECT last_insert_rowid()").fetchone()[0]
                saved_row = dict(
                    db.execute("SELECT * FROM bridges WHERE id = ?", (new_id,)).fetchone()
                )
                db.close()

                log_llm_usage(
                    ts=utc_now_iso(),
                    app_name="colors",
                    model=model,
                    endpoint="/colors/computational_bridge",
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
                        "user_metadata": user_metadata,
                    },
                )

                with TASKS_LOCK:
                    TASKS[task_id]["status"] = "done"
                    TASKS[task_id]["finished_at"] = utc_now_iso()
                    TASKS[task_id]["result"] = {
                        "task_type": "computational_bridge",
                        "color_id": color_id,
                        "art_id": art_id,
                        "bridge_text": output_text,
                        "saved_bridge": saved_row,
                        "usage": usage_dict,
                    }
                        # ============================================================
            #  TYPE: music_bridge (NEW)
            # ============================================================
            elif task_type == "music_bridge":
                color_id = task["color_id"]
                model = task["model"]
                temperature = float(task["temperature"])
                user_metadata = task["user_metadata"]

                db = get_art_db()
                row = db.execute(
                    "SELECT id, art_id, output_text FROM colors WHERE id = ?",
                    (color_id,)
                ).fetchone()

                if not row:
                    db.close()
                    raise ValueError(f"color id {color_id} not found")

                art_id = row["art_id"]
                thought_text = (row["output_text"] or "").strip()
                if not thought_text:
                    db.close()
                    raise ValueError(f"color {color_id} has empty output_text")

                system_prompt = music_bridge_prompt.format(thought=thought_text)
                user_prompt = ""

                projected_tokens = 6000
                enforce_daily_cap_or_429(model=model, projected_tokens=projected_tokens)

                with LLM_LOCK:
                    t0 = time.time()
                    resp = _client.chat.completions.create(
                        model=model,
                        temperature=temperature,
                        messages=[
                            {"role": "system", "content": system_prompt},
                            {"role": "user", "content": user_prompt},
                        ],
                    )

                output_text = (resp.choices[0].message.content or "").strip()

                usage = getattr(resp, "usage", None)
                usage_dict = usage.model_dump() if usage else None
                tokens_in = int((usage_dict or {}).get("prompt_tokens", 0))
                tokens_out = int((usage_dict or {}).get("completion_tokens", 0))
                total_tokens = int((usage_dict or {}).get("total_tokens", tokens_in + tokens_out))
                duration_ms = int((time.time() - t0) * 1000)

                created_at = utc_now_iso()

                # SAVE INTO bridges WITH TYPE
                db.execute(
                    """
                    INSERT INTO bridges
                    (color_id, art_id, input_text, bridge_text, bridge_type, model, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        color_id,
                        art_id,
                        thought_text,
                        output_text,
                        "music_bridge",
                        model,
                        created_at,
                    ),
                )
                db.commit()

                new_id = db.execute("SELECT last_insert_rowid()").fetchone()[0]
                saved_row = dict(
                    db.execute("SELECT * FROM bridges WHERE id = ?", (new_id,)).fetchone()
                )
                db.close()

                log_llm_usage(
                    ts=utc_now_iso(),
                    app_name="colors",
                    model=model,
                    endpoint="/colors/music_bridge",
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
                        "user_metadata": user_metadata,
                    },
                )

                with TASKS_LOCK:
                    TASKS[task_id]["status"] = "done"
                    TASKS[task_id]["finished_at"] = utc_now_iso()
                    TASKS[task_id]["result"] = {
                        "task_type": "music_bridge",
                        "color_id": color_id,
                        "art_id": art_id,
                        "bridge_text": output_text,
                        "saved_bridge": saved_row,
                        "usage": usage_dict,
                    }
                        # ============================================================
            #  TYPE: information_bridge (NEW)
            # ============================================================
            elif task_type == "information_bridge":
                color_id = task["color_id"]
                model = task["model"]
                temperature = float(task["temperature"])
                user_metadata = task["user_metadata"]

                db = get_art_db()
                row = db.execute(
                    "SELECT id, art_id, output_text FROM colors WHERE id = ?",
                    (color_id,)
                ).fetchone()

                if not row:
                    db.close()
                    raise ValueError(f"color id {color_id} not found")

                art_id = row["art_id"]
                thought_text = (row["output_text"] or "").strip()
                if not thought_text:
                    db.close()
                    raise ValueError(f"color {color_id} has empty output_text")

                system_prompt = information_bridge_prompt.format(thought=thought_text)
                user_prompt = ""

                projected_tokens = 6000
                enforce_daily_cap_or_429(model=model, projected_tokens=projected_tokens)

                with LLM_LOCK:
                    t0 = time.time()
                    resp = _client.chat.completions.create(
                        model=model,
                        temperature=temperature,
                        messages=[
                            {"role": "system", "content": system_prompt},
                            {"role": "user", "content": user_prompt}
                        ],
                    )

                output_text = (resp.choices[0].message.content or "").strip()

                usage = getattr(resp, "usage", None)
                usage_dict = usage.model_dump() if usage else None
                tokens_in = int((usage_dict or {}).get("prompt_tokens", 0))
                tokens_out = int((usage_dict or {}).get("completion_tokens", 0))
                total_tokens = int((usage_dict or {}).get("total_tokens", tokens_in + tokens_out))
                duration_ms = int((time.time() - t0)*1000)

                created_at = utc_now_iso()

                # save in bridges with bridge_type
                db.execute(
                    """
                    INSERT INTO bridges
                    (color_id, art_id, input_text, bridge_text, bridge_type, model, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        color_id,
                        art_id,
                        thought_text,
                        output_text,
                        "information_bridge",
                        model,
                        created_at,
                    ),
                )
                db.commit()

                new_id = db.execute("SELECT last_insert_rowid()").fetchone()[0]
                saved_row = dict(
                    db.execute("SELECT * FROM bridges WHERE id = ?", (new_id,)).fetchone()
                )
                db.close()

                log_llm_usage(
                    ts=utc_now_iso(),
                    app_name="colors",
                    model=model,
                    endpoint="/colors/information_bridge",
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
                        "user_metadata": user_metadata,
                    },
                )

                with TASKS_LOCK:
                    TASKS[task_id]["status"] = "done"
                    TASKS[task_id]["finished_at"] = utc_now_iso()
                    TASKS[task_id]["result"] = {
                        "task_type": "information_bridge",
                        "color_id": color_id,
                        "art_id": art_id,
                        "bridge_text": output_text,
                        "saved_bridge": saved_row,
                        "usage": usage_dict,
                    }
                        # ============================================================
            #  TYPE: poetry_bridge (NEW)
            # ============================================================
            elif task_type == "poetry_bridge":
                color_id = task["color_id"]
                model = task["model"]
                temperature = float(task["temperature"])
                user_metadata = task["user_metadata"]

                db = get_art_db()
                row = db.execute(
                    "SELECT id, art_id, output_text FROM colors WHERE id = ?",
                    (color_id,)
                ).fetchone()

                if not row:
                    db.close()
                    raise ValueError(f"color id {color_id} not found")

                art_id = row["art_id"]
                thought_text = (row["output_text"] or "").strip()
                if not thought_text:
                    db.close()
                    raise ValueError(f"color {color_id} has empty output_text")

                system_prompt = poetry_bridge_prompt.format(thought=thought_text)
                user_prompt = ""

                projected_tokens = 6000
                enforce_daily_cap_or_429(model=model, projected_tokens=projected_tokens)

                with LLM_LOCK:
                    t0 = time.time()
                    resp = _client.chat.completions.create(
                        model=model,
                        temperature=temperature,
                        messages=[
                            {"role": "system", "content": system_prompt},
                            {"role": "user", "content": user_prompt},
                        ],
                    )

                output_text = (resp.choices[0].message.content or "").strip()

                usage = getattr(resp, "usage", None)
                usage_dict = usage.model_dump() if usage else None
                tokens_in = int((usage_dict or {}).get("prompt_tokens", 0))
                tokens_out = int((usage_dict or {}).get("completion_tokens", 0))
                total_tokens = int((usage_dict or {}).get("total_tokens", tokens_in + tokens_out))
                duration_ms = int((time.time() - t0) * 1000)

                created_at = utc_now_iso()

                # SAVE INTO bridges WITH TYPE
                db.execute(
                    """
                    INSERT INTO bridges
                    (color_id, art_id, input_text, bridge_text, bridge_type, model, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        color_id,
                        art_id,
                        thought_text,
                        output_text,
                        "poetry_bridge",
                        model,
                        created_at,
                    ),
                )
                db.commit()

                new_id = db.execute("SELECT last_insert_rowid()").fetchone()[0]
                saved_row = dict(
                    db.execute("SELECT * FROM bridges WHERE id = ?", (new_id,)).fetchone()
                )
                db.close()

                log_llm_usage(
                    ts=utc_now_iso(),
                    app_name="colors",
                    model=model,
                    endpoint="/colors/poetry_bridge",
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
                        "user_metadata": user_metadata,
                    },
                )

                with TASKS_LOCK:
                    TASKS[task_id]["status"] = "done"
                    TASKS[task_id]["finished_at"] = utc_now_iso()
                    TASKS[task_id]["result"] = {
                        "task_type": "poetry_bridge",
                        "color_id": color_id,
                        "art_id": art_id,
                        "bridge_text": output_text,
                        "saved_bridge": saved_row,
                        "usage": usage_dict,
                    }
                        # ============================================================
            #  TYPE: metaphysics_bridge (NEW)
            # ============================================================
            elif task_type == "metaphysics_bridge":
                color_id = task["color_id"]
                model = task["model"]
                temperature = float(task["temperature"])
                user_metadata = task["user_metadata"]

                db = get_art_db()
                row = db.execute(
                    "SELECT id, art_id, output_text FROM colors WHERE id = ?",
                    (color_id,)
                ).fetchone()

                if not row:
                    db.close()
                    raise ValueError(f"color id {color_id} not found")

                art_id = row["art_id"]
                thought_text = (row["output_text"] or "").strip()

                if not thought_text:
                    db.close()
                    raise ValueError(f"color {color_id} has empty output_text")

                system_prompt = metaphysics_bridge_prompt.format(thought=thought_text)
                user_prompt = ""

                projected_tokens = 6000
                enforce_daily_cap_or_429(model=model, projected_tokens=projected_tokens)

                with LLM_LOCK:
                    t0 = time.time()
                    resp = _client.chat.completions.create(
                        model=model,
                        temperature=temperature,
                        messages=[
                            {"role": "system", "content": system_prompt},
                            {"role": "user", "content": user_prompt},
                        ],
                    )

                output_text = (resp.choices[0].message.content or "").strip()

                usage = getattr(resp, "usage", None)
                usage_dict = usage.model_dump() if usage else None
                tokens_in = int((usage_dict or {}).get("prompt_tokens", 0))
                tokens_out = int((usage_dict or {}).get("completion_tokens", 0))
                total_tokens = tokens_in + tokens_out
                duration_ms = int((time.time() - t0) * 1000)

                created_at = utc_now_iso()

                # SAVE INTO bridges WITH TYPE
                db.execute(
                    """
                    INSERT INTO bridges
                      (color_id, art_id, input_text, bridge_text, bridge_type, model, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        color_id,
                        art_id,
                        thought_text,
                        output_text,
                        "metaphysics_bridge",
                        model,
                        created_at,
                    )
                )
                db.commit()

                new_id = db.execute("SELECT last_insert_rowid()").fetchone()[0]
                saved_row = dict(
                    db.execute("SELECT * FROM bridges WHERE id = ?", (new_id,)).fetchone()
                )
                db.close()

                log_llm_usage(
                    ts=utc_now_iso(),
                    app_name="colors",
                    model=model,
                    endpoint="/colors/metaphysics_bridge",
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
                        "user_metadata": user_metadata,
                    }
                )

                with TASKS_LOCK:
                    TASKS[task_id]["status"] = "done"
                    TASKS[task_id]["finished_at"] = utc_now_iso()
                    TASKS[task_id]["result"] = {
                        "task_type": "metaphysics_bridge",
                        "color_id": color_id,
                        "art_id": art_id,
                        "bridge_text": output_text,
                        "saved_bridge": saved_row,
                        "usage": usage_dict,
                    }
                        # ============================================================
            #  TYPE: thought_bridge (NO LLM: copies thought -> bridge_text)
            # ============================================================
            elif task_type == "thought_bridge":
                color_id = task["color_id"]
                model = task.get("model") or "none"
                user_metadata = task.get("user_metadata") or {}

                db = get_art_db()
                row = db.execute(
                    "SELECT id, art_id, output_text FROM colors WHERE id = ?",
                    (color_id,)
                ).fetchone()

                if not row:
                    db.close()
                    raise ValueError(f"color id {color_id} not found")

                art_id = row["art_id"]
                thought_text = (row["output_text"] or "").strip()

                if not thought_text:
                    db.close()
                    raise ValueError(f"color {color_id} has empty output_text")

                created_at = utc_now_iso()

                # Save the thought directly as a bridge
                db.execute(
                    """
                    INSERT INTO bridges
                      (color_id, art_id, input_text, bridge_text, bridge_type, model, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        color_id,
                        art_id,
                        thought_text,      # input_text
                        thought_text,      # bridge_text (copy)
                        "thought",         # bridge_type
                        model,             # "none" or whatever you want
                        created_at,
                    ),
                )
                db.commit()

                new_id = db.execute("SELECT last_insert_rowid()").fetchone()[0]
                saved_row = dict(
                    db.execute("SELECT * FROM bridges WHERE id = ?", (new_id,)).fetchone()
                )
                db.close()

                # Optional: still log usage as 0 tokens
                log_llm_usage(
                    ts=utc_now_iso(),
                    app_name="colors",
                    model=model,
                    endpoint="/colors/thought_bridge",
                    email=None,
                    request_id=task_id,
                    tokens_in=0,
                    tokens_out=0,
                    total_tokens=0,
                    duration_ms=0,
                    cost_usd=0.0,
                    meta_obj={
                        "color_id": color_id,
                        "art_id": art_id,
                        "worker_id": worker_id,
                        "user_metadata": user_metadata,
                        "no_llm": True,
                    },
                )

                with TASKS_LOCK:
                    TASKS[task_id]["status"] = "done"
                    TASKS[task_id]["finished_at"] = utc_now_iso()
                    TASKS[task_id]["result"] = {
                        "task_type": "thought_bridge",
                        "color_id": color_id,
                        "art_id": art_id,
                        "bridge_text": thought_text,
                        "saved_bridge": saved_row,
                        "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
                    }
            # ============================================================
            #  TYPE: brush_stroke_bridge
            # ============================================================

            elif task_type == "brush_stroke_bridge":
                bridge_id = task["bridge_id"]
                entity_text = task["entity_text"].strip()
                model = task["model"]
                temperature = float(task["temperature"])
                user_metadata = task["user_metadata"]

                db = get_art_db()
                try:
                    # Fetch bridge row
                    bridge_row = db.execute(
                        "SELECT * FROM bridges WHERE id = ?",
                        (bridge_id,),
                    ).fetchone()

                    if bridge_row is None:
                        raise ValueError(f"bridge id {bridge_id} not found")

                    bridge_row = dict(bridge_row)
                    color_id = bridge_row["color_id"]
                    art_id = bridge_row["art_id"]
                    orig_bridge_type = bridge_row["bridge_type"] or "bridge"
                    bridge_text = bridge_row["bridge_text"] or ""

                    # Fetch thought (optional)
                    color_row = db.execute(
                        "SELECT * FROM colors WHERE id = ?",
                        (color_id,),
                    ).fetchone()
                    thought_text = "NONE"
                    if color_row is not None:
                        thought_text = (dict(color_row).get("output_text") or "NONE")

                    # ---- ENTITY TABLE INSERTION / FETCH ----
                    now = utc_now_iso()

                    # 1. Check if entity already exists
                    existing_entity = db.execute(
                        "SELECT id FROM entities WHERE name = ?",
                        (entity_text,),
                    ).fetchone()

                    if existing_entity:
                        entity_id = existing_entity["id"]
                    else:
                        # 2. Insert new entity
                        db.execute(
                            """
                            INSERT INTO entities (name, canonical_name, created_at)
                            VALUES (?, ?, ?)
                            """,
                            (entity_text, entity_text, now),
                        )
                        entity_id = db.execute("SELECT last_insert_rowid()").fetchone()[0]

                    # After entity_id is known
                    db.execute(
                        """
                        INSERT INTO entity_instances (entity_id, color_id, bridge_id, origin, created_at)
                        VALUES (?, ?, ?, ?, ?)
                        """,
                        (entity_id, color_id, new_bridge_id, "brush_stroke", now)
                    )
                    # ---- LLM CALL ----
                    system_prompt = entity_bridge_relationship_prompt.format(
                        thought=thought_text,
                        bridge_type=orig_bridge_type,
                        bridge_text=bridge_text,
                        entity_text=entity_text,
                    )
                    user_prompt = ""

                    projected_tokens = 7000
                    enforce_daily_cap_or_429(model=model, projected_tokens=projected_tokens)

                    with LLM_LOCK:
                        t0 = time.time()
                        resp = _client.chat.completions.create(
                            model=model,
                            temperature=temperature,
                            messages=[
                                {"role": "system", "content": system_prompt},
                                {"role": "user", "content": user_prompt},
                            ],
                        )
                    output_text = (resp.choices[0].message.content or "").strip()

                    usage = getattr(resp, "usage", None)
                    usage_dict = {
                        "prompt_tokens": getattr(usage, "prompt_tokens", 0) if usage else 0,
                        "completion_tokens": getattr(usage, "completion_tokens", 0) if usage else 0,
                        "total_tokens": getattr(usage, "total_tokens", 0) if usage else 0,
                    }

                    tokens_in = int(usage_dict.get("prompt_tokens", 0))
                    tokens_out = int(usage_dict.get("completion_tokens", 0))
                    total_tokens = int(usage_dict.get("total_tokens", tokens_in + tokens_out))
                    duration_ms = int((time.time() - t0) * 1000)

                    # ---- SAVE NEW BRUSH-STROKE BRIDGE ----
                    new_bridge_type = f"{orig_bridge_type}_brush_stroke"
                    created_at = now

                    db.execute(
                        """
                        INSERT INTO bridges
                        (color_id, art_id, input_text, bridge_text, bridge_type, entity_id, model, created_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            color_id,
                            art_id,
                            entity_text,    # store entity in input_text for compatibility
                            output_text,
                            new_bridge_type,
                            entity_id,      # NEW: link to entity table
                            model,
                            created_at,
                        ),
                    )
                    new_id = db.execute("SELECT last_insert_rowid()").fetchone()[0]
                    db.commit()

                    saved_row = dict(
                        db.execute("SELECT * FROM bridges WHERE id = ?", (new_id,)).fetchone()
                    )

                    # ---- LLM USAGE LOG ----
                    log_llm_usage(
                        ts=created_at,
                        app_name="colors",
                        model=model,
                        endpoint="brush_stroke_bridge",
                        email=user_metadata.get("email") if isinstance(user_metadata, dict) else None,
                        request_id=task_id,
                        tokens_in=tokens_in,
                        tokens_out=tokens_out,
                        total_tokens=total_tokens,
                        duration_ms=duration_ms,
                        cost_usd=0.0,
                        meta_obj={
                            "color_id": color_id,
                            "art_id": art_id,
                            "bridge_id": bridge_id,
                            "entity": entity_text,
                            "entity_id": entity_id,
                            "orig_bridge_type": orig_bridge_type,
                            "new_bridge_type": new_bridge_type,
                            "worker_id": worker_id,
                            "user_metadata": user_metadata,
                        },
                    )

                    with TASKS_LOCK:
                        TASKS[task_id]["status"] = "done"
                        TASKS[task_id]["result"] = {
                            "task_type": "brush_stroke_bridge",
                            "bridge_id": bridge_id,
                            "new_bridge_entry_id": new_id,
                            "entity": entity_text,
                            "entity_id": entity_id,
                            "bridge_type": new_bridge_type,
                            "bridge_text": output_text,
                            "saved_bridge": saved_row,
                            "usage": usage_dict,
                        }

                except Exception as e:
                    with TASKS_LOCK:
                        TASKS[task_id]["status"] = "error"
                        TASKS[task_id]["error"] = str(e)
                finally:
                    db.close()

            # ============================================================
            # Unsupported task type
            # ============================================================
            else:
                raise ValueError(f"Unknown task_type: {task_type}")

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
            "task_type": "build_thought",
            "art_id": art_id,
            "status": "queued",
            "created_at": utc_now_iso(),
            "model": model,
        }


    TASK_QUEUE.put({
        "task_id": task_id,
        "task_type": "build_thought",
        "art_id": art_id,
        "model": model,
        "user_metadata": user_metadata,
    })


    return jsonify({
        "task_id": task_id,
        "status": "queued",
        "art_id": art_id,
        "model": model,
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


@app.post("/colors/simulation_seeds")
def enqueue_simulation_seeds():
    """
    Queue Simulation Seed Extractor on a color_id.

    Body:
      {
        "color_id": 123,
        "model": optional,
        "metadata": optional
      }

    Returns:
      { task_id, status:'queued' }
    """
    if _client is None:
        abort(500, description=f"OpenAI client not initialized: {_client_err}")

    payload = require_json()
    color_id = payload.get("color_id")
    if not isinstance(color_id, int):
        abort(400, description="'color_id' is required and must be an integer")

    model = payload.get("model", MODEL_DEFAULT)
    temperature = float(payload.get("temperature", 0.2))
    user_metadata = payload.get("metadata") or {}

    task_id = str(uuid.uuid4())

    with TASKS_LOCK:
        TASKS[task_id] = {
            "task_id": task_id,
            "task_type": "simulation_seeds",
            "color_id": color_id,
            "status": "queued",
            "created_at": utc_now_iso(),
            "model": model,
            "temperature": temperature,
        }

    TASK_QUEUE.put({
        "task_id": task_id,
        "task_type": "simulation_seeds",
        "color_id": color_id,
        "model": model,
        "temperature": temperature,
        "user_metadata": user_metadata,
    })

    return jsonify({
        "task_id": task_id,
        "status": "queued",
        "task_type": "simulation_seeds",
        "color_id": color_id,
    }), 202


@app.get("/colors/seeds/by_color/<int:color_id>")
def seeds_by_color(color_id: int):
    """
    Returns all simulation_seeds rows for a color_id, newest first.
    """
    db = get_art_db()
    rows = db.execute(
        """
        SELECT * FROM simulation_seeds
        WHERE color_id = ?
        ORDER BY created_at DESC
        """,
        (color_id,)
    ).fetchall()
    db.close()

    out = []
    for r in rows:
        d = dict(r)
        # try to parse seeds_json for convenience
        sj = d.get("seeds_json")
        if isinstance(sj, str):
            try:
                d["seeds_json"] = json.loads(sj)
            except Exception:
                pass
        out.append(d)

    return jsonify(out)


@app.post("/colors/entities")
def enqueue_entities():
    """
    Queue entity extraction on color_id.
    Body:
      {
        "color_id": 123,
        "model": optional,
        "temperature": optional,
        "metadata": optional
      }
    """
    if _client is None:
        abort(500, description=f"OpenAI client not initialized: {_client_err}")

    payload = require_json()
    color_id = payload.get("color_id")
    if not isinstance(color_id, int):
        abort(400, description="'color_id' is required and must be an integer")

    model = payload.get("model", MODEL_DEFAULT)
    temperature = float(payload.get("temperature", 0.2))
    user_metadata = payload.get("metadata") or {}

    task_id = str(uuid.uuid4())

    with TASKS_LOCK:
        TASKS[task_id] = {
            "task_id": task_id,
            "task_type": "entities",
            "color_id": color_id,
            "status": "queued",
            "created_at": utc_now_iso(),
            "model": model,
            "temperature": temperature,
        }

    TASK_QUEUE.put({
        "task_id": task_id,
        "task_type": "entities",
        "color_id": color_id,
        "model": model,
        "temperature": temperature,
        "user_metadata": user_metadata,
    })

    return jsonify({
        "task_id": task_id,
        "status": "queued",
        "task_type": "entities",
        "color_id": color_id,
    }), 202





@app.post("/colors/simulation_architecture")
def enqueue_bridge_simulation():
    """
    Queue Simulation Architecture prompt on a color_id.
    Body:
      {
        "color_id": 123,
        "model": optional,
        "temperature": optional,
        "metadata": optional
      }
    Returns:
      { task_id, status:'queued' }
    """
    if _client is None:
        abort(500, description=f"OpenAI client not initialized: {_client_err}")

    payload = require_json()
    color_id = payload.get("color_id")
    if not isinstance(color_id, int):
        abort(400, description="'color_id' is required and must be an integer")

    model = payload.get("model", MODEL_DEFAULT)
    temperature = float(payload.get("temperature", 0.2))
    user_metadata = payload.get("metadata") or {}

    task_id = str(uuid.uuid4())

    with TASKS_LOCK:
        TASKS[task_id] = {
            "task_id": task_id,
            "task_type": "bridge_simulation",
            "color_id": color_id,
            "status": "queued",
            "created_at": utc_now_iso(),
            "model": model,
            "temperature": temperature,
        }

    TASK_QUEUE.put({
        "task_id": task_id,
        "task_type": "bridge_simulation",
        "color_id": color_id,
        "model": model,
        "temperature": temperature,
        "user_metadata": user_metadata,
    })

    return jsonify({
        "task_id": task_id,
        "status": "queued",
        "task_type": "bridge_simulation",
        "color_id": color_id,
    }), 202


@app.get("/colors/bridges/by_color/<int:color_id>")
def bridges_by_color(color_id: int):
    """
    Returns all bridges rows for a color_id, newest first.
    """
    db = get_art_db()
    rows = db.execute(
        """
        SELECT * FROM bridges
        WHERE color_id = ?
        ORDER BY created_at DESC
        """,
        (color_id,)
    ).fetchall()
    db.close()

    return jsonify([dict(r) for r in rows])


@app.post("/colors/theory_architecture")
def enqueue_theory_architecture():
    """
    Queue theory architecture generation on a color_id.
    """
    if _client is None:
        abort(500, description=f"OpenAI client not initialized: {_client_err}")

    payload = require_json()
    color_id = payload.get("color_id")
    if not isinstance(color_id, int):
        abort(400, description="'color_id' is required and must be an integer")

    model = payload.get("model", MODEL_DEFAULT)
    temperature = float(payload.get("temperature", 0.2))
    user_metadata = payload.get("metadata") or {}

    task_id = str(uuid.uuid4())

    with TASKS_LOCK:
        TASKS[task_id] = {
            "task_id": task_id,
            "task_type": "theory_architecture",
            "color_id": color_id,
            "status": "queued",
            "created_at": utc_now_iso(),
            "model": model,
            "temperature": temperature,
        }

    TASK_QUEUE.put({
        "task_id": task_id,
        "task_type": "theory_architecture",
        "color_id": color_id,
        "model": model,
        "temperature": temperature,
        "user_metadata": user_metadata,
    })

    return jsonify({
        "task_id": task_id,
        "status": "queued",
        "task_type": "theory_architecture",
        "color_id": color_id,
    }), 202


@app.post("/colors/physical_world_bridge")
def enqueue_physical_world_bridge():
    """
    Queue physical-world bridge generation on a color_id.
    Body:
      {
        "color_id": 123,
        "model": optional,
        "temperature": optional,
        "metadata": optional
      }
    """
    if _client is None:
        abort(500, description=f"OpenAI client not initialized: {_client_err}")

    payload = require_json()
    color_id = payload.get("color_id")
    if not isinstance(color_id, int):
        abort(400, description="'color_id' is required and must be an integer")

    model = payload.get("model", MODEL_DEFAULT)
    temperature = float(payload.get("temperature", 0.2))
    user_metadata = payload.get("metadata") or {}

    task_id = str(uuid.uuid4())

    with TASKS_LOCK:
        TASKS[task_id] = {
            "task_id": task_id,
            "task_type": "physical_world_bridge",
            "color_id": color_id,
            "status": "queued",
            "created_at": utc_now_iso(),
            "model": model,
            "temperature": temperature,
        }

    TASK_QUEUE.put({
        "task_id": task_id,
        "task_type": "physical_world_bridge",
        "color_id": color_id,
        "model": model,
        "temperature": temperature,
        "user_metadata": user_metadata,
    })

    return jsonify({
        "task_id": task_id,
        "status": "queued",
        "task_type": "physical_world_bridge",
        "color_id": color_id,
    }), 202


@app.get("/colors/bridges/by_color/<int:color_id>/<bridge_type>")
def bridges_by_color_and_type(color_id: int, bridge_type: str):
    """
    Returns bridges rows for a color_id filtered by bridge_type, newest first.
    """
    db = get_art_db()
    rows = db.execute(
        """
        SELECT * FROM bridges
        WHERE color_id = ? AND bridge_type = ?
        ORDER BY created_at DESC
        """,
        (color_id, bridge_type)
    ).fetchall()
    db.close()
    return jsonify([dict(r) for r in rows])


@app.post("/colors/math_bridge")
def enqueue_math_bridge():
    if _client is None:
        abort(500, description=f"OpenAI client not initialized: {_client_err}")

    payload = require_json()
    color_id = payload.get("color_id")

    if not isinstance(color_id, int):
        abort(400, description="'color_id' must be an integer")

    model = payload.get("model", MODEL_DEFAULT)
    temperature = float(payload.get("temperature", 0.2))
    user_metadata = payload.get("metadata") or {}

    task_id = str(uuid.uuid4())

    with TASKS_LOCK:
        TASKS[task_id] = {
            "task_id": task_id,
            "task_type": "math_bridge",
            "color_id": color_id,
            "status": "queued",
            "created_at": utc_now_iso(),
            "model": model,
            "temperature": temperature,
        }

    TASK_QUEUE.put({
        "task_id": task_id,
        "task_type": "math_bridge",
        "color_id": color_id,
        "model": model,
        "temperature": temperature,
        "user_metadata": user_metadata,
    })

    return jsonify({
        "task_id": task_id,
        "status": "queued",
        "task_type": "math_bridge",
        "color_id": color_id,
    }), 202


@app.post("/colors/language_bridge")
def enqueue_language_bridge():
    if _client is None:
        abort(500, description=f"OpenAI client not initialized: {_client_err}")

    payload = require_json()
    color_id = payload.get("color_id")

    if not isinstance(color_id, int):
        abort(400, description="'color_id' must be an integer")

    model = payload.get("model", MODEL_DEFAULT)
    temperature = float(payload.get("temperature", 0.2))
    user_metadata = payload.get("metadata") or {}

    task_id = str(uuid.uuid4())

    with TASKS_LOCK:
        TASKS[task_id] = {
            "task_id": task_id,
            "task_type": "language_bridge",
            "color_id": color_id,
            "status": "queued",
            "created_at": utc_now_iso(),
            "model": model,
            "temperature": temperature,
        }

    TASK_QUEUE.put({
        "task_id": task_id,
        "task_type": "language_bridge",
        "color_id": color_id,
        "model": model,
        "temperature": temperature,
        "user_metadata": user_metadata,
    })

    return jsonify({
        "task_id": task_id,
        "status": "queued",
        "task_type": "language_bridge",
        "color_id": color_id,
    }), 202


@app.post("/colors/data_bridge")
def enqueue_data_bridge():
    if _client is None:
        abort(500, description=f"OpenAI client not initialized: {_client_err}")

    payload = require_json()
    color_id = payload.get("color_id")

    if not isinstance(color_id, int):
        abort(400, description="'color_id' must be an integer")

    model = payload.get("model", MODEL_DEFAULT)
    temperature = float(payload.get("temperature", 0.2))
    user_metadata = payload.get("metadata") or {}

    task_id = str(uuid.uuid4())

    with TASKS_LOCK:
        TASKS[task_id] = {
            "task_id": task_id,
            "task_type": "data_bridge",
            "color_id": color_id,
            "status": "queued",
            "created_at": utc_now_iso(),
            "model": model,
            "temperature": temperature,
        }

    TASK_QUEUE.put({
        "task_id": task_id,
        "task_type": "data_bridge",
        "color_id": color_id,
        "model": model,
        "temperature": temperature,
        "user_metadata": user_metadata,
    })

    return jsonify({
        "task_id": task_id,
        "status": "queued",
        "task_type": "data_bridge",
        "color_id": color_id,
    }), 202


@app.post("/colors/computational_bridge")
def enqueue_computational_bridge():
    if _client is None:
        abort(500, description=f"OpenAI client not initialized: {_client_err}")

    payload = require_json()
    color_id = payload.get("color_id")

    if not isinstance(color_id, int):
        abort(400, description="'color_id' must be an integer")

    model = payload.get("model", MODEL_DEFAULT)
    temperature = float(payload.get("temperature", 0.25))
    user_metadata = payload.get("metadata") or {}

    task_id = str(uuid.uuid4())

    with TASKS_LOCK:
        TASKS[task_id] = {
            "task_id": task_id,
            "task_type": "computational_bridge",
            "color_id": color_id,
            "status": "queued",
            "created_at": utc_now_iso(),
            "model": model,
            "temperature": temperature,
        }

    TASK_QUEUE.put({
        "task_id": task_id,
        "task_type": "computational_bridge",
        "color_id": color_id,
        "model": model,
        "temperature": temperature,
        "user_metadata": user_metadata,
    })

    return jsonify({
        "task_id": task_id,
        "status": "queued",
        "task_type": "computational_bridge",
        "color_id": color_id,
    }), 202


@app.post("/colors/music_bridge")
def enqueue_music_bridge():
    if _client is None:
        abort(500, description=f"OpenAI client not initialized: {_client_err}")

    payload = require_json()
    color_id = payload.get("color_id")
    if not isinstance(color_id, int):
        abort(400, description="'color_id' must be an integer")

    model = payload.get("model", MODEL_DEFAULT)
    temperature = float(payload.get("temperature", 0.25))
    user_metadata = payload.get("metadata") or {}

    task_id = str(uuid.uuid4())

    with TASKS_LOCK:
        TASKS[task_id] = {
            "task_id": task_id,
            "task_type": "music_bridge",
            "color_id": color_id,
            "status": "queued",
            "created_at": utc_now_iso(),
            "model": model,
            "temperature": temperature,
        }

    TASK_QUEUE.put({
        "task_id": task_id,
        "task_type": "music_bridge",
        "color_id": color_id,
        "model": model,
        "temperature": temperature,
        "user_metadata": user_metadata,
    })

    return jsonify({
        "task_id": task_id,
        "status": "queued",
        "task_type": "music_bridge",
        "color_id": color_id,
    }), 202


@app.post("/colors/information_bridge")
def enqueue_information_bridge():
    if _client is None:
        abort(500, description=f"OpenAI client not initialized: {_client_err}")

    payload = require_json()
    color_id = payload.get("color_id")

    if not isinstance(color_id, int):
        abort(400, description="'color_id' must be an integer")

    model = payload.get("model", MODEL_DEFAULT)
    temperature = float(payload.get("temperature", 0.25))
    user_metadata = payload.get("metadata") or {}

    task_id = str(uuid.uuid4())

    with TASKS_LOCK:
        TASKS[task_id] = {
            "task_id": task_id,
            "task_type": "information_bridge",
            "color_id": color_id,
            "status": "queued",
            "created_at": utc_now_iso(),
            "model": model,
            "temperature": temperature,
        }

    TASK_QUEUE.put({
        "task_id": task_id,
        "task_type": "information_bridge",
        "color_id": color_id,
        "model": model,
        "temperature": temperature,
        "user_metadata": user_metadata,
    })

    return jsonify({
        "task_id": task_id,
        "status": "queued",
        "task_type": "information_bridge",
        "color_id": color_id,
    }), 202


@app.post("/colors/poetry_bridge")
def enqueue_poetry_bridge():
    if _client is None:
        abort(500, description=f"OpenAI client not initialized: {_client_err}")

    payload = require_json()
    color_id = payload.get("color_id")
    if not isinstance(color_id, int):
        abort(400, description="'color_id' must be an integer")

    model = payload.get("model", MODEL_DEFAULT)
    temperature = float(payload.get("temperature", 0.25))
    user_metadata = payload.get("metadata") or {}

    task_id = str(uuid.uuid4())

    with TASKS_LOCK:
        TASKS[task_id] = {
            "task_id": task_id,
            "task_type": "poetry_bridge",
            "color_id": color_id,
            "status": "queued",
            "created_at": utc_now_iso(),
            "model": model,
            "temperature": temperature,
        }

    TASK_QUEUE.put({
        "task_id": task_id,
        "task_type": "poetry_bridge",
        "color_id": color_id,
        "model": model,
        "temperature": temperature,
        "user_metadata": user_metadata,
    })

    return jsonify({
        "task_id": task_id,
        "status": "queued",
        "task_type": "poetry_bridge",
        "color_id": color_id,
    }), 202


@app.post("/colors/metaphysics_bridge")
def enqueue_metaphysics_bridge():
    if _client is None:
        abort(500, description=f"OpenAI client not initialized: {_client_err}")

    payload = require_json()
    color_id = payload.get("color_id")

    if not isinstance(color_id, int):
        abort(400, description="'color_id' must be an integer")

    model = payload.get("model", MODEL_DEFAULT)
    temperature = float(payload.get("temperature", 0.25))
    user_metadata = payload.get("metadata") or {}

    task_id = str(uuid.uuid4())

    with TASKS_LOCK:
        TASKS[task_id] = {
            "task_id": task_id,
            "task_type": "metaphysics_bridge",
            "color_id": color_id,
            "status": "queued",
            "created_at": utc_now_iso(),
            "model": model,
            "temperature": temperature,
        }

    TASK_QUEUE.put({
        "task_id": task_id,
        "task_type": "metaphysics_bridge",
        "color_id": color_id,
        "model": model,
        "temperature": temperature,
        "user_metadata": user_metadata,
    })

    return jsonify({
        "task_id": task_id,
        "status": "queued",
        "task_type": "metaphysics_bridge",
        "color_id": color_id,
    }), 202


@app.post("/colors/thought_bridge")
def enqueue_thought_bridge():
    payload = require_json()
    color_id = payload.get("color_id")

    if not isinstance(color_id, int):
        abort(400, description="'color_id' must be an integer")

    model = payload.get("model", "none")
    user_metadata = payload.get("metadata") or {}

    task_id = str(uuid.uuid4())

    with TASKS_LOCK:
        TASKS[task_id] = {
            "task_id": task_id,
            "task_type": "thought_bridge",
            "color_id": color_id,
            "status": "queued",
            "created_at": utc_now_iso(),
            "model": model,
        }

    TASK_QUEUE.put({
        "task_id": task_id,
        "task_type": "thought_bridge",
        "color_id": color_id,
        "model": model,
        "user_metadata": user_metadata,
    })

    return jsonify({
        "task_id": task_id,
        "status": "queued",
        "task_type": "thought_bridge",
        "color_id": color_id,
    }), 202

@app.post("/colors/brush_stroke_bridge")
def enqueue_brush_stroke_bridge():
    if _client is None:
        abort(500, description=f"OpenAI client not initialized: {_client_err}")

    payload = require_json()
    bridge_id = payload.get("bridge_id")
    entity_text = payload.get("entity_text") or payload.get("entity")

    if not isinstance(bridge_id, int):
        abort(400, description="'bridge_id' must be an integer")
    if not isinstance(entity_text, str) or not entity_text.strip():
        abort(400, description="'entity_text' must be a non-empty string")

    model = payload.get("model", MODEL_DEFAULT)
    temperature = float(payload.get("temperature", 0.2))
    user_metadata = payload.get("metadata") or {}

    task_id = str(uuid.uuid4())
    created_at = utc_now_iso()

    with TASKS_LOCK:
        TASKS[task_id] = {
            "status": "queued",
            "created_at": created_at,
            "task_type": "brush_stroke_bridge",
        }

    TASK_QUEUE.put({
        "task_id": task_id,
        "task_type": "brush_stroke_bridge",
        "bridge_id": bridge_id,
        "entity_text": entity_text.strip(),
        "model": model,
        "temperature": temperature,
        "user_metadata": user_metadata,
    })

    return jsonify({
        "task_id": task_id,
        "status": "queued",
        "task_type": "brush_stroke_bridge",
        "bridge_id": bridge_id,
    }), 202


@app.get("/colors/bridges/by_entity/<int:entity_id>")
def get_bridges_by_entity(entity_id: int):
    """
    Returns bridges rows for a given entity_id, newest first.
    Also includes entity_name for convenience.
    """
    db = get_art_db()

    # optional: ensure entity exists (nice 404)
    ent = db.execute(
        "SELECT * FROM entities WHERE id = ?",
        (entity_id,)
    ).fetchone()
    if ent is None:
        db.close()
        abort(404, description=f"entity id {entity_id} not found")

    rows = db.execute(
        """
        SELECT b.*, e.name AS entity_name
        FROM bridges b
        LEFT JOIN entities e ON b.entity_id = e.id
        WHERE b.entity_id = ?
        ORDER BY b.created_at DESC
        """,
        (entity_id,)
    ).fetchall()
    db.close()

    return jsonify([dict(r) for r in rows])


@app.get("/colors/entities/search")
def search_entities():
    name = (request.args.get("name") or "").strip()
    if not name:
        return jsonify({"entities": []})

    db = get_art_db()
    rows = db.execute(
        """
        SELECT id, name, canonical_name, created_at
        FROM entities
        WHERE name LIKE ? OR canonical_name LIKE ?
        ORDER BY name ASC
        LIMIT 50
        """,
        (f"%{name}%", f"%{name}%"),
    ).fetchall()
    db.close()

    return jsonify({"entities": [dict(r) for r in rows]})

@app.get("/colors/entities/by_color/<int:color_id>")
def entities_by_color(color_id: int):
    db = get_art_db()
    rows = db.execute(
        """
        SELECT DISTINCT e.*
        FROM entities e
        JOIN entity_instances x ON x.entity_id = e.id
        WHERE x.color_id = ?
        ORDER BY e.name ASC
        """,
        (color_id,)
    ).fetchall()
    db.close()

    return jsonify([dict(r) for r in rows])
