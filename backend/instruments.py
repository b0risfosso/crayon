#!/usr/bin/env python3
"""
Simple Flask API for instruments.
- Stores instruments in SQLite at /var/www/site/data/instruments.db
- Supports create, list, read, update, delete
"""

from __future__ import annotations

import os
import sqlite3
import uuid
import threading
import queue
import time
import json
from datetime import datetime, timezone
from typing import Dict, Any, Optional, List

from flask import Flask, request, jsonify, g, abort

try:
    from openai import OpenAI
    _client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))
    _client_err: Optional[Exception] = None
except Exception as e:
    _client = None
    _client_err = e

from instrument_prompts import (
    OPERATOR_INSTRUCTION_COMPILER,
    SCENARIO_SYNTHESIZER,
    SYSTEMS_ANALYST,
    SYSTEMS_MEASUREMENT_ANALYST,
    GOAL_ORIENTED_PLANNER,
)  # type: ignore

DB_PATH = "/var/www/site/data/instruments.db"
ART_DB_PATH = os.environ.get("ART_DB_PATH", "/var/www/site/data/art.db")
MODEL_DEFAULT = os.environ.get("INSTRUMENT_MODEL", "gpt-5-mini-2025-08-07")

CONCURRENCY = 2
LLM_LOCK = threading.Lock()

app = Flask(__name__)
app.config["JSON_SORT_KEYS"] = False


def iso_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat() + "Z"


def init_db() -> None:
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    try:
        conn.execute("PRAGMA foreign_keys = ON;")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS instruments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                description TEXT DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS instrument_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                instrument_id INTEGER REFERENCES instruments(id) ON DELETE SET NULL,
                task_id TEXT,
                prompt_type TEXT DEFAULT 'instruction_compiler',
                scenario TEXT,
                system_description TEXT,
                system_feature TEXT,
                operator_name TEXT NOT NULL,
                operator_description TEXT,
                output_text TEXT,
                model TEXT,
                tokens_in INTEGER,
                tokens_out INTEGER,
                total_tokens INTEGER,
                status TEXT NOT NULL,
                error TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_instrument_runs_instrument ON instrument_runs(instrument_id, created_at DESC)"
        )
        conn.commit()
    finally:
        conn.close()


def get_db() -> sqlite3.Connection:
    if "db" not in g:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON;")
        g.db = conn
    return g.db


@app.teardown_appcontext
def close_db(_exc: BaseException | None) -> None:
    conn = g.pop("db", None)
    if conn is not None:
        conn.close()


def row_to_dict(row: sqlite3.Row) -> Dict[str, Any]:
    return {
        "id": row["id"],
        "name": row["name"],
        "description": row["description"] or "",
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def require_json() -> Dict[str, Any]:
    if not request.is_json:
        abort(400, description="Request must be application/json")
    payload = request.get_json(silent=True)
    if payload is None:
        abort(400, description="Invalid JSON body")
    return payload


def fetch_instrument_row(instrument_id: int) -> Optional[Dict[str, Any]]:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    try:
        row = conn.execute("SELECT * FROM instruments WHERE id = ?", (instrument_id,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


# --- LLM helpers / queue --------------------------------------------------

TASK_QUEUE: "queue.Queue[dict]" = queue.Queue()
TASKS: Dict[str, Dict[str, Any]] = {}
TASKS_LOCK = threading.Lock()


def iso_time_ms() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def queue_stats() -> Dict[str, Any]:
    with TASKS_LOCK:
        counts = {"queued": 0, "running": 0, "done": 0, "error": 0}
        for t in TASKS.values():
            status = t.get("status")
            if status in counts:
                counts[status] += 1
    return {"queue_size": TASK_QUEUE.qsize(), "tasks": counts, "concurrency": CONCURRENCY}


def render_instruction_prompt(scenario: str, operator_name: str, operator_description: str) -> str:
    return (
        OPERATOR_INSTRUCTION_COMPILER
        .replace("{{SCENARIO_TEXT}}", scenario)
        .replace("{{OPERATOR_NAME}}", operator_name)
        .replace("{{OPERATOR_DESCRIPTION}}", operator_description)
    )


def render_synth_prompt(
    system_description: str,
    system_feature: str,
    operator_name: str,
    operator_capabilities: str,
) -> str:
    return (
        SCENARIO_SYNTHESIZER
        .replace("{{SYSTEM_DESCRIPTION}}", system_description)
        .replace("{{SYSTEM_FEATURE_DESCRIPTION}}", system_feature)
        .replace("{{OPERATOR_NAME}}", operator_name)
        .replace("{{OPERATOR_CAPABILITIES}}", operator_capabilities)
    )


def render_systems_analyst_prompt(
    system_name: str,
    system_description: str,
) -> str:
    desc = system_description.strip() or "No description provided."
    return (
        f"{SYSTEMS_ANALYST}\n\n"
        "System Definition\n"
        "Object / System\n"
        f"{system_name.strip()}\n"
        "Components / attributes\n"
        f"{desc}\n"
        "Operational Capabilities\n"
        f"{desc}"
    )


def render_measurement_prompt(
    system_name: str,
    system_description: str,
    analyst_examples: str,
) -> str:
    desc = system_description.strip() or "No description provided."
    examples = analyst_examples.strip()
    return (
        f"{SYSTEMS_MEASUREMENT_ANALYST}\n\n"
        "System Definition\n"
        "Object / System\n"
        f"{system_name.strip()}\n"
        "Components / attributes\n"
        f"{desc}\n"
        "System Operational Capabilities\n"
        f"{desc}\n"
        "Example Operational Sequences\n"
        f"{examples}\n"
    )


def render_goal_planner_prompt(
    goal: str,
    operator_name: str,
    operator_capabilities: str,
) -> str:
    return (
        f"{GOAL_ORIENTED_PLANNER}\n\n"
        "Goal / Scenario\n"
        f"{goal.strip()}\n\n"
        "Operator / System\n"
        f"{operator_name.strip()}\n\n"
        "Operator Capabilities\n"
        f"{operator_capabilities.strip()}\n"
    )


def insert_run(
    *,
    instrument_id: Optional[int],
    task_id: str,
    prompt_type: str,
    scenario: Optional[str],
    system_description: Optional[str],
    system_feature: Optional[str],
    operator_name: str,
    operator_description: str,
    output_text: Optional[str],
    model: str,
    tokens_in: int,
    tokens_out: int,
    total_tokens: int,
    status: str,
    error: Optional[str],
) -> Dict[str, Any]:
    # Allow scenario to be empty/None by normalizing to empty string before insert
    if scenario is None:
        scenario = ""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    try:
        now = iso_time_ms()
        row = conn.execute(
            """
            INSERT INTO instrument_runs
              (instrument_id, task_id, prompt_type, scenario, system_description, system_feature,
               operator_name, operator_description,
               output_text, model, tokens_in, tokens_out, total_tokens, status, error,
               created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            RETURNING *
            """,
            (
                instrument_id,
                task_id,
                prompt_type,
                scenario,
                system_description,
                system_feature,
                operator_name,
                operator_description,
                output_text,
                model,
                tokens_in,
                tokens_out,
                total_tokens,
                status,
                error,
                now,
                now,
            ),
        ).fetchone()
        conn.commit()
        return dict(row)
    finally:
        conn.close()

def insert_brush_stroke(
    *,
    art_id: int,
    color_id: int,
    dirt_id: int,
    instrument_id: Optional[int],
    instrument_task_id: str,
    instrument_run_id: Optional[int],
    output_text: str,
    metadata: Optional[dict] = None,
) -> None:
    """
    Store a brush_stroke in art.db. Best-effort; failures are swallowed to avoid
    blocking synth completion.
    """
    try:
        print("insert_brush_stroke: connecting to art db")
        conn = sqlite3.connect(ART_DB_PATH)
        conn.row_factory = sqlite3.Row
        now = iso_time_ms()
        md_str = None
        if metadata is not None:
            md_str = json.dumps(metadata, ensure_ascii=False)
        print("insert_brush_stroke: inserting row")
        conn.execute(
            """
            INSERT INTO brush_strokes
              (art_id, color_id, dirt_id, instrument_id, instrument_task_id,
               instrument_run_id, output_text, metadata, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                art_id,
                color_id,
                dirt_id,
                instrument_id,
                instrument_task_id,
                instrument_run_id,
                output_text,
                md_str,
                now,
                now,
            ),
        )
        conn.commit()
        print("insert_brush_stroke: done")
    except Exception:
        print("insert_brush_stroke: exception occurred")
        # ignore errors to keep synth responses flowing
        try:
            conn.rollback()
        except Exception:
            pass
    finally:
        try:
            conn.close()
        except Exception:
            pass


def worker_loop(worker_id: int):
    while True:
        task = TASK_QUEUE.get()
        task_id = task["task_id"]
        with TASKS_LOCK:
            meta = TASKS.get(task_id)
            if meta:
                meta.update({"status": "running", "started_at": iso_time_ms(), "worker_id": worker_id})
        try:
            instrument_id = task.get("instrument_id")
            model = task["model"]
            task_type = task.get("task_type", "compile_plan")

            if _client is None:
                raise RuntimeError(f"OpenAI client not initialized: {_client_err}")

            if task_type == "compile_plan":
                scenario = task["scenario"]
                operator_name = task["operator_name"]
                operator_description = task["operator_description"]
                prompt = render_instruction_prompt(scenario, operator_name, operator_description)
                prompt_type = "instruction_compiler"
                system_description = None
                system_feature = None
            elif task_type == "synthesize_scenarios":
                system_description = task["system_description"]
                system_feature = task["system_feature"]
                operator_name = task["operator_name"]
                operator_description = task["operator_description"]
                scenario = ""  # synth tasks do not supply a scenario; keep schema happy
                prompt = render_synth_prompt(
                    system_description,
                    system_feature,
                    operator_name,
                    operator_description,
                )
                prompt_type = "scenario_synthesizer"
            elif task_type == "systems_analyst":
                operator_name = task["operator_name"]
                operator_description = task["operator_description"]
                scenario = ""
                system_description = None
                system_feature = None
                prompt = render_systems_analyst_prompt(operator_name, operator_description)
                prompt_type = "systems_analyst"
            elif task_type == "measurement_analyst":
                operator_name = task["operator_name"]
                operator_description = task["operator_description"]
                analyst_examples = task["analyst_examples"]
                scenario = ""
                system_description = None
                system_feature = None
                prompt = render_measurement_prompt(operator_name, operator_description, analyst_examples)
                prompt_type = "measurement_analyst"
            elif task_type == "goal_planner":
                scenario = task["goal"]
                operator_name = task["operator_name"]
                operator_description = task["operator_description"]
                system_description = None
                system_feature = None
                prompt = render_goal_planner_prompt(scenario, operator_name, operator_description)
                prompt_type = "goal_planner"
            else:
                raise ValueError(f"Unknown task_type '{task_type}'")

            with LLM_LOCK:
                t0 = time.time()
                resp = _client.chat.completions.create(
                    model=model,
                    messages=[{"role": "system", "content": prompt},],
                )
            output_text = (resp.choices[0].message.content or "").strip()

            usage = getattr(resp, "usage", None)
            usage_dict = usage.model_dump() if usage else {}
            tokens_in = int(usage_dict.get("prompt_tokens", 0))
            tokens_out = int(usage_dict.get("completion_tokens", 0))
            total_tokens = int(usage_dict.get("total_tokens", tokens_in + tokens_out))
            duration_ms = int((time.time() - t0) * 1000)

            run_row = insert_run(
                instrument_id=instrument_id,
                task_id=task_id,
                prompt_type=prompt_type,
                scenario=scenario,
                system_description=system_description,
                system_feature=system_feature,
                operator_name=operator_name,
                operator_description=operator_description,
                output_text=output_text,
                model=model,
                tokens_in=tokens_in,
                tokens_out=tokens_out,
                total_tokens=total_tokens,
                status="done",
                error=None,
            )

            # Best-effort save into brush_strokes if art/color/dirt are provided
            art_id = task.get("art_id")
            color_id = task.get("color_id")
            dirt_id = task.get("dirt_id")
            if art_id and color_id and dirt_id:
                print("running insert_brush_stroke")
                try:
                    insert_brush_stroke(
                        art_id=int(art_id),
                        color_id=int(color_id),
                        dirt_id=int(dirt_id),
                        instrument_id=instrument_id,
                        instrument_task_id=task_id,
                        instrument_run_id=run_row.get("id") if isinstance(run_row, dict) else None,
                        output_text=output_text,
                        metadata={
                            "instrument_name": operator_name,
                            "instrument_description": operator_description,
                            "tokens": usage_dict,
                        },
                    )
                    print("insert_brush_stroke done")
                except Exception:
                    print("insert_brush_stroke failed")
                    pass

            result = {
                "output": output_text,
                "usage": usage_dict,
                "duration_ms": duration_ms,
                "run": run_row,
            }

            with TASKS_LOCK:
                meta = TASKS.get(task_id)
                if meta:
                    meta.update(
                        {
                            "status": "done",
                            "finished_at": iso_time_ms(),
                            "result": result,
                        }
                    )

        except Exception as e:
            with TASKS_LOCK:
                meta = TASKS.get(task_id)
                if meta:
                    meta.update(
                        {
                            "status": "error",
                            "finished_at": iso_time_ms(),
                            "error": str(e),
                        }
                    )
            try:
                insert_run(
                    instrument_id=task.get("instrument_id"),
                    task_id=task_id,
                    prompt_type=task.get("task_type", "unknown"),
                    scenario=task.get("scenario"),
                    system_description=task.get("system_description"),
                    system_feature=task.get("system_feature"),
                    operator_name=task.get("operator_name", ""),
                    operator_description=task.get("operator_description", ""),
                    output_text=None,
                    model=task.get("model", MODEL_DEFAULT),
                    tokens_in=0,
                    tokens_out=0,
                    total_tokens=0,
                    status="error",
                    error=str(e),
                )
            except Exception:
                pass
        finally:
            TASK_QUEUE.task_done()


# start worker threads
for wid in range(CONCURRENCY):
    t = threading.Thread(target=worker_loop, args=(wid,), daemon=True)
    t.start()


@app.get("/instruments/health")
def health() -> Any:
    return jsonify({"ok": True, "db_path": DB_PATH})


@app.get("/instruments")
def list_instruments() -> Any:
    db = get_db()
    rows = db.execute("SELECT * FROM instruments ORDER BY created_at DESC, id DESC").fetchall()
    return jsonify([row_to_dict(r) for r in rows])


@app.post("/instruments")
def create_instrument() -> Any:
    payload = request.get_json(silent=True) or {}
    name = payload.get("name")
    description = payload.get("description") or ""

    if not isinstance(name, str) or not name.strip():
        abort(400, description="'name' is required and must be a non-empty string")

    now = iso_now()
    db = get_db()
    cur = db.execute(
        """
        INSERT INTO instruments (name, description, created_at, updated_at)
        VALUES (?, ?, ?, ?)
        """,
        (name.strip(), description.strip(), now, now),
    )
    db.commit()

    row = db.execute("SELECT * FROM instruments WHERE id = ?", (cur.lastrowid,)).fetchone()
    return jsonify(row_to_dict(row)), 201


def _require_instrument(instrument_id: int) -> sqlite3.Row:
    db = get_db()
    row = db.execute("SELECT * FROM instruments WHERE id = ?", (instrument_id,)).fetchone()
    if not row:
        abort(404, description="Instrument not found")
    return row


@app.get("/instruments/<int:instrument_id>")
def get_instrument(instrument_id: int) -> Any:
    row = _require_instrument(instrument_id)
    return jsonify(row_to_dict(row))


@app.put("/instruments/<int:instrument_id>")
@app.patch("/instruments/<int:instrument_id>")
def update_instrument(instrument_id: int) -> Any:
    payload = request.get_json(silent=True) or {}
    updates = {}

    if "name" in payload:
        name = payload.get("name")
        if not isinstance(name, str) or not name.strip():
            abort(400, description="'name' must be a non-empty string when provided")
        updates["name"] = name.strip()

    if "description" in payload:
        desc = payload.get("description")
        if desc is None:
            desc = ""
        elif not isinstance(desc, str):
            abort(400, description="'description' must be a string when provided")
        updates["description"] = desc.strip()

    if not updates:
        abort(400, description="No valid fields to update")

    db = get_db()
    _require_instrument(instrument_id)

    updates["updated_at"] = iso_now()
    sets = ", ".join(f"{k} = ?" for k in updates.keys())
    args = list(updates.values()) + [instrument_id]
    db.execute(f"UPDATE instruments SET {sets} WHERE id = ?", args)
    db.commit()

    row = db.execute("SELECT * FROM instruments WHERE id = ?", (instrument_id,)).fetchone()
    return jsonify(row_to_dict(row))


@app.delete("/instruments/<int:instrument_id>")
def delete_instrument(instrument_id: int) -> Any:
    db = get_db()
    _require_instrument(instrument_id)
    db.execute("DELETE FROM instruments WHERE id = ?", (instrument_id,))
    db.commit()
    return jsonify({"ok": True, "deleted_id": instrument_id})


@app.get("/instruments/<int:instrument_id>/runs")
def list_runs_for_instrument(instrument_id: int) -> Any:
    db = get_db()
    rows = db.execute(
        """
        SELECT *
        FROM instrument_runs
        WHERE instrument_id = ?
        ORDER BY created_at DESC
        LIMIT 200
        """,
        (instrument_id,),
    ).fetchall()
    return jsonify([dict(r) for r in rows])


@app.delete("/instruments/runs/<int:run_id>")
def delete_run(run_id: int) -> Any:
    db = get_db()
    row = db.execute("SELECT id FROM instrument_runs WHERE id = ?", (run_id,)).fetchone()
    if not row:
        abort(404, description="run not found")
    db.execute("DELETE FROM instrument_runs WHERE id = ?", (run_id,))
    db.commit()
    return jsonify({"ok": True, "deleted_id": run_id})


@app.post("/instruments/compile")
def enqueue_compile() -> Any:
    if _client is None:
        abort(500, description=f"OpenAI client not initialized: {_client_err}")

    payload = require_json()
    scenario = payload.get("scenario")
    if not isinstance(scenario, str) or not scenario.strip():
        abort(400, description="'scenario' is required and must be a non-empty string")

    instrument_id = payload.get("instrument_id")
    if instrument_id is not None and not isinstance(instrument_id, int):
        abort(400, description="'instrument_id' must be an integer when provided")

    operator_name = payload.get("instrument_name")
    operator_description = payload.get("instrument_description")
    art_id = payload.get("art_id")
    color_id = payload.get("color_id")
    dirt_id = payload.get("dirt_id")
    for fname, fval in (("art_id", art_id), ("color_id", color_id), ("dirt_id", dirt_id)):
        if fval is not None and not isinstance(fval, int):
            abort(400, description=f"'{fname}' must be an integer when provided")
    art_id = payload.get("art_id")
    color_id = payload.get("color_id")
    dirt_id = payload.get("dirt_id")

    for field_name, field_val in [("art_id", art_id), ("color_id", color_id), ("dirt_id", dirt_id)]:
        if field_val is not None and not isinstance(field_val, int):
            abort(400, description=f"'{field_name}' must be an integer when provided")

    if instrument_id is not None:
        row = fetch_instrument_row(instrument_id)
        if not row:
            abort(404, description=f"Instrument id {instrument_id} not found")
        operator_name = operator_name or row.get("name")
        operator_description = operator_description or row.get("description")

    if not isinstance(operator_name, str) or not operator_name.strip():
        abort(400, description="'instrument_name' is required (or present in instrument_id)")
    if operator_description is None:
        operator_description = ""
    if not isinstance(operator_description, str):
        abort(400, description="'instrument_description' must be a string")

    model = payload.get("model", MODEL_DEFAULT)
    task_id = str(uuid.uuid4())

    with TASKS_LOCK:
        TASKS[task_id] = {
            "task_id": task_id,
            "task_type": "compile_plan",
            "status": "queued",
            "created_at": iso_time_ms(),
            "instrument_id": instrument_id,
            "model": model,
            "scenario": scenario.strip(),
            "operator_name": operator_name.strip(),
            "operator_description": operator_description.strip(),
        }

    TASK_QUEUE.put(
        {
            "task_id": task_id,
            "task_type": "compile_plan",
            "instrument_id": instrument_id,
            "model": model,
            "scenario": scenario.strip(),
            "operator_name": operator_name.strip(),
            "operator_description": operator_description.strip(),
        }
    )

    return (
        jsonify(
            {
                "task_id": task_id,
                "status": "queued",
                "queue": queue_stats(),
            }
        ),
        202,
    )


@app.post("/instruments/analyze")
def enqueue_analysis() -> Any:
    if _client is None:
        abort(500, description=f"OpenAI client not initialized: {_client_err}")

    payload = require_json()
    instrument_id = payload.get("instrument_id")
    if instrument_id is not None and not isinstance(instrument_id, int):
        abort(400, description="'instrument_id' must be an integer when provided")

    instrument_name = payload.get("instrument_name")
    instrument_description = payload.get("instrument_description")

    if instrument_id is not None:
        row = fetch_instrument_row(instrument_id)
        if not row:
            abort(404, description=f"Instrument id {instrument_id} not found")
        instrument_name = instrument_name or row.get("name")
        instrument_description = instrument_description or row.get("description")

    if not isinstance(instrument_name, str) or not instrument_name.strip():
        abort(400, description="'instrument_name' is required (or present in instrument_id)")
    if instrument_description is None:
        instrument_description = ""
    if not isinstance(instrument_description, str):
        abort(400, description="'instrument_description' must be a string")

    model = payload.get("model", MODEL_DEFAULT)
    task_id = str(uuid.uuid4())

    with TASKS_LOCK:
        TASKS[task_id] = {
            "task_id": task_id,
            "task_type": "systems_analyst",
            "status": "queued",
            "created_at": iso_time_ms(),
            "instrument_id": instrument_id,
            "model": model,
            "operator_name": instrument_name.strip(),
            "operator_description": instrument_description.strip(),
        }

    TASK_QUEUE.put(
        {
            "task_id": task_id,
            "task_type": "systems_analyst",
            "instrument_id": instrument_id,
            "model": model,
            "operator_name": instrument_name.strip(),
            "operator_description": instrument_description.strip(),
        }
    )

    return (
        jsonify(
            {
                "task_id": task_id,
                "status": "queued",
                "queue": queue_stats(),
            }
        ),
        202,
    )


@app.post("/instruments/measure")
def enqueue_measurement() -> Any:
    if _client is None:
        abort(500, description=f"OpenAI client not initialized: {_client_err}")

    payload = require_json()
    instrument_id = payload.get("instrument_id")
    if instrument_id is not None and not isinstance(instrument_id, int):
        abort(400, description="'instrument_id' must be an integer when provided")

    instrument_name = payload.get("instrument_name")
    instrument_description = payload.get("instrument_description")
    analyst_run_id = payload.get("analyst_run_id")
    analyst_output = payload.get("analyst_output")

    if analyst_run_id is not None and not isinstance(analyst_run_id, int):
        abort(400, description="'analyst_run_id' must be an integer when provided")
    if analyst_output is not None and not isinstance(analyst_output, str):
        abort(400, description="'analyst_output' must be a string when provided")

    if instrument_id is not None:
        row = fetch_instrument_row(instrument_id)
        if not row:
            abort(404, description=f"Instrument id {instrument_id} not found")
        instrument_name = instrument_name or row.get("name")
        instrument_description = instrument_description or row.get("description")

    if not isinstance(instrument_name, str) or not instrument_name.strip():
        abort(400, description="'instrument_name' is required (or present in instrument_id)")
    if instrument_description is None:
        instrument_description = ""
    if not isinstance(instrument_description, str):
        abort(400, description="'instrument_description' must be a string")

    examples_text = (analyst_output or "").strip()
    if not examples_text:
        if analyst_run_id is None:
            abort(400, description="Provide 'analyst_output' or 'analyst_run_id'")
        # fetch from DB
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON;")
        try:
            row = conn.execute(
                "SELECT output_text FROM instrument_runs WHERE id = ? AND prompt_type = 'systems_analyst'",
                (analyst_run_id,),
            ).fetchone()
            if not row or not row["output_text"]:
                abort(404, description="systems analyst run not found or has no output")
            examples_text = row["output_text"]
        finally:
            conn.close()

    model = payload.get("model", MODEL_DEFAULT)
    task_id = str(uuid.uuid4())

    with TASKS_LOCK:
        TASKS[task_id] = {
            "task_id": task_id,
            "task_type": "measurement_analyst",
            "status": "queued",
            "created_at": iso_time_ms(),
            "instrument_id": instrument_id,
            "model": model,
            "operator_name": instrument_name.strip(),
            "operator_description": instrument_description.strip(),
            "analyst_examples": examples_text,
        }

    TASK_QUEUE.put(
        {
            "task_id": task_id,
            "task_type": "measurement_analyst",
            "instrument_id": instrument_id,
            "model": model,
            "operator_name": instrument_name.strip(),
            "operator_description": instrument_description.strip(),
            "analyst_examples": examples_text,
        }
    )

    return (
        jsonify(
            {
                "task_id": task_id,
                "status": "queued",
                "queue": queue_stats(),
            }
        ),
        202,
    )


@app.post("/instruments/plan_goal")
def enqueue_goal_plan() -> Any:
    if _client is None:
        abort(500, description=f"OpenAI client not initialized: {_client_err}")

    payload = require_json()
    goal = payload.get("goal")
    if not isinstance(goal, str) or not goal.strip():
        abort(400, description="'goal' is required and must be a non-empty string")

    instrument_id = payload.get("instrument_id")
    if instrument_id is not None and not isinstance(instrument_id, int):
        abort(400, description="'instrument_id' must be an integer when provided")

    instrument_name = payload.get("instrument_name")
    instrument_description = payload.get("instrument_description")

    if instrument_id is not None:
        row = fetch_instrument_row(instrument_id)
        if not row:
            abort(404, description=f"Instrument id {instrument_id} not found")
        instrument_name = instrument_name or row.get("name")
        instrument_description = instrument_description or row.get("description")

    if not isinstance(instrument_name, str) or not instrument_name.strip():
        abort(400, description="'instrument_name' is required (or present in instrument_id)")
    if instrument_description is None:
        instrument_description = ""
    if not isinstance(instrument_description, str):
        abort(400, description="'instrument_description' must be a string")

    model = payload.get("model", MODEL_DEFAULT)
    task_id = str(uuid.uuid4())

    with TASKS_LOCK:
        TASKS[task_id] = {
            "task_id": task_id,
            "task_type": "goal_planner",
            "status": "queued",
            "created_at": iso_time_ms(),
            "instrument_id": instrument_id,
            "model": model,
            "goal": goal.strip(),
            "operator_name": instrument_name.strip(),
            "operator_description": instrument_description.strip(),
        }

    TASK_QUEUE.put(
        {
            "task_id": task_id,
            "task_type": "goal_planner",
            "instrument_id": instrument_id,
            "model": model,
            "goal": goal.strip(),
            "operator_name": instrument_name.strip(),
            "operator_description": instrument_description.strip(),
        }
    )

    return (
        jsonify(
            {
                "task_id": task_id,
                "status": "queued",
                "queue": queue_stats(),
            }
        ),
        202,
    )


@app.post("/instruments/synthesize")
def enqueue_synthesize() -> Any:
    if _client is None:
        abort(500, description=f"OpenAI client not initialized: {_client_err}")

    payload = require_json()
    system_description = payload.get("system_description")
    system_feature = payload.get("system_feature")
    if not isinstance(system_description, str) or not system_description.strip():
        abort(400, description="'system_description' is required and must be a non-empty string")
    if not isinstance(system_feature, str) or not system_feature.strip():
        abort(400, description="'system_feature' is required and must be a non-empty string")

    instrument_id = payload.get("instrument_id")
    if instrument_id is not None and not isinstance(instrument_id, int):
        abort(400, description="'instrument_id' must be an integer when provided")

    operator_name = payload.get("instrument_name")
    operator_description = payload.get("instrument_description")
    art_id = payload.get("art_id")
    color_id = payload.get("color_id")
    dirt_id = payload.get("dirt_id")
    for fname, fval in (("art_id", art_id), ("color_id", color_id), ("dirt_id", dirt_id)):
        if fval is not None and not isinstance(fval, int):
            abort(400, description=f"'{fname}' must be an integer when provided")

    if instrument_id is not None:
        row = fetch_instrument_row(instrument_id)
        if not row:
            abort(404, description=f"Instrument id {instrument_id} not found")
        operator_name = operator_name or row.get("name")
        operator_description = operator_description or row.get("description")

    if not isinstance(operator_name, str) or not operator_name.strip():
        abort(400, description="'instrument_name' is required (or present in instrument_id)")
    if operator_description is None:
        operator_description = ""
    if not isinstance(operator_description, str):
        abort(400, description="'instrument_description' must be a string")

    model = payload.get("model", MODEL_DEFAULT)
    task_id = str(uuid.uuid4())

    with TASKS_LOCK:
        TASKS[task_id] = {
            "task_id": task_id,
            "task_type": "synthesize_scenarios",
            "status": "queued",
            "created_at": iso_time_ms(),
            "instrument_id": instrument_id,
            "model": model,
            "system_description": system_description.strip(),
            "system_feature": system_feature.strip(),
            "operator_name": operator_name.strip(),
            "operator_description": operator_description.strip(),
            "art_id": art_id,
            "color_id": color_id,
            "dirt_id": dirt_id,
        }

    TASK_QUEUE.put(
        {
            "task_id": task_id,
            "task_type": "synthesize_scenarios",
            "instrument_id": instrument_id,
            "model": model,
            "system_description": system_description.strip(),
            "system_feature": system_feature.strip(),
            "operator_name": operator_name.strip(),
            "operator_description": operator_description.strip(),
            "art_id": art_id,
            "color_id": color_id,
            "dirt_id": dirt_id,
        }
    )

    return (
        jsonify(
            {
                "task_id": task_id,
                "status": "queued",
                "queue": queue_stats(),
            }
        ),
        202,
    )


@app.get("/instruments/llm/tasks/<task_id>")
def get_task(task_id: str) -> Any:
    with TASKS_LOCK:
        t = TASKS.get(task_id)
    if t is None:
        abort(404, description="task not found")
    return jsonify(t)


@app.get("/instruments/llm/tasks")
def list_tasks() -> Any:
    status = request.args.get("status")
    include_error = request.args.get("include_error") == "1"
    try:
        limit = int(request.args.get("limit", "200"))
    except ValueError:
        abort(400, description="'limit' must be an integer")

    with TASKS_LOCK:
        items = list(TASKS.values())

    if status:
        items = [t for t in items if t.get("status") == status]

    items.sort(key=lambda x: x.get("created_at", ""), reverse=True)
    if limit > 0:
        items = items[:limit]

    tasks_out = []
    for t in items:
        entry = dict(t)
        if not include_error and "error" in entry:
            entry["has_error"] = True
            entry.pop("error", None)
        tasks_out.append(entry)

    return jsonify({"queue": queue_stats(), "count": len(tasks_out), "tasks": tasks_out})


@app.get("/instruments/llm/workers")
def list_workers() -> Any:
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
                "instrument_id": t.get("instrument_id"),
                "created_at": t.get("created_at"),
                "started_at": t.get("started_at"),
            }
        )

    return jsonify({"queue": queue_stats(), "workers": workers})


# Ensure the DB exists when the module is imported.
init_db()


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
