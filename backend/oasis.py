#!/usr/bin/env python3
"""
Simple Flask API for entities.
- Stores entities in SQLite at /var/www/site/data/oasis.db
- Table name: entites
"""

from __future__ import annotations

import os
import queue
import sqlite3
import threading
from datetime import datetime, timezone
from typing import Any, Dict, Optional
from uuid import uuid4

from flask import Flask, g, request, jsonify, abort

from oasis_prompts import (
    BRIDGE_PROMPT,
    PROVENANCE_PROMPT,
    STORY_PROMPT,
    STORY_PROVENANCE_PROMPT,
    STORY_ORIGIN_PROMPT,
    STORY_CONFLICT_PROMPT,
    STORY_COORDINATION_PROMPT,
    RELATIONSHIP_PROMPT,
    RELATIONSHIP_SOURCES_PROMPT,
)

try:
    from openai import OpenAI
    _client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))
    _client_err: Optional[Exception] = None
except Exception as e:  # pragma: no cover - optional dependency
    _client = None
    _client_err = e

DB_PATH = "/var/www/site/data/oasis.db"
ART_DB_PATH = os.environ.get("ART_DB_PATH", "/var/www/site/data/art.db")
DIRT_DB_PATH = os.environ.get("DIRT_DB_PATH", "/var/www/site/data/dirt.db")
MODEL_DEFAULT = os.environ.get("OASIS_MODEL", "gpt-5-mini-2025-08-07")
CONCURRENCY = 2
LLM_LOCK = threading.Lock()

app = Flask(__name__)
app.config["JSON_SORT_KEYS"] = False
app.url_map.strict_slashes = False


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def get_db() -> sqlite3.Connection:
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


def run_llm(prompt_text: str, *, model: Optional[str] = None) -> tuple[str, Dict[str, Any], int, int, int]:
    if _client is None:
        raise RuntimeError(f"OpenAI client not initialized: {_client_err}")

    resolved_model = model or MODEL_DEFAULT
    with LLM_LOCK:
        response = _client.chat.completions.create(
            model=resolved_model,
            messages=[{"role": "user", "content": prompt_text}],
        )

    text = (response.choices[0].message.content or "").strip()
    usage = getattr(response, "usage", None)
    usage_dict = usage.model_dump() if usage else {}
    tokens_in = int(usage_dict.get("prompt_tokens", 0))
    tokens_out = int(usage_dict.get("completion_tokens", 0))
    total_tokens = int(usage_dict.get("total_tokens", tokens_in + tokens_out))

    return text, usage_dict, tokens_in, tokens_out, total_tokens


def fetch_entity(entity_id: int) -> Dict[str, Any]:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute("SELECT * FROM entites WHERE id = ?", (entity_id,)).fetchone()
        if row is None:
            raise ValueError("Entity not found")
        return dict(row)
    finally:
        conn.close()


def fetch_art_color_text(art_id: int, color_id: int) -> tuple[str, str]:
    conn = sqlite3.connect(ART_DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            """
            SELECT art.art AS art_text, colors.output_text AS color_text
            FROM art
            JOIN colors ON colors.art_id = art.id
            WHERE art.id = ? AND colors.id = ?
            """,
            (art_id, color_id),
        ).fetchone()
        if row is None:
            raise ValueError("art_id/color_id not found")
        return row["art_text"], row["color_text"]
    finally:
        conn.close()


def fetch_dirt_analysis(dirt_id: int) -> str:
    conn = sqlite3.connect(DIRT_DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            "SELECT content FROM nodes WHERE id = ?",
            (dirt_id,),
        ).fetchone()
        if row is None or row["content"] is None:
            raise ValueError("dirt_id not found or has no content")
        return row["content"]
    finally:
        conn.close()


def build_bridge_prompt(thought: str, analysis: str, entity_title: str, entity_description: str) -> str:
    return (
        f"{BRIDGE_PROMPT}\n"
        "\nINPUT A - Thought World\n"
        f"Thought:\n{thought}\n\n"
        f"Analysis:\n{analysis}\n\n"
        "INPUT B - Material / Energetic / Monetary System\n"
        f"Entity Title: {entity_title}\n"
        f"Entity Description: {entity_description}\n"
    )


def build_provenance_prompt(bridge_text: str) -> str:
    return f"{PROVENANCE_PROMPT}\n{bridge_text}\n"


def build_story_prompt(prompt_text: str, thought: str, entity_title: str, entity_description: str) -> str:
    return (
        f"{prompt_text}\n"
        "\nThought / World:\n"
        f"{thought}\n\n"
        "Material + Energetic System:\n"
        f"Entity Title: {entity_title}\n"
        f"Entity Description: {entity_description}\n"
    )


def build_sources_prompt(prompt_text: str, source_text: str) -> str:
    return f"{prompt_text}\n{source_text}\n"


def _require_entity(entity_id: int) -> sqlite3.Row:
    db = get_db()
    row = db.execute("SELECT * FROM entites WHERE id = ?", (entity_id,)).fetchone()
    if not row:
        abort(404, description="Entity not found")
    return row


TASK_QUEUE: "queue.Queue[dict]" = queue.Queue()
TASKS: Dict[str, Dict[str, Any]] = {}
TASKS_LOCK = threading.Lock()


def queue_stats() -> Dict[str, Any]:
    with TASKS_LOCK:
        counts = {"queued": 0, "running": 0, "done": 0, "error": 0}
        for t in TASKS.values():
            status = t.get("status")
            if status in counts:
                counts[status] += 1
    return {"queue_size": TASK_QUEUE.qsize(), "tasks": counts, "concurrency": CONCURRENCY}


def enqueue_bridge_task(payload: Dict[str, Any]) -> Dict[str, Any]:
    task = {
        "task_id": uuid4().hex,
        "task_type": "bridge_build",
        "payload": payload,
        "status": "queued",
        "created_at": utc_now_iso(),
    }
    with TASKS_LOCK:
        TASKS[task["task_id"]] = task
    TASK_QUEUE.put(task)
    return dict(task)


def enqueue_story_task(payload: Dict[str, Any]) -> Dict[str, Any]:
    task = {
        "task_id": uuid4().hex,
        "task_type": "story_build",
        "payload": payload,
        "status": "queued",
        "created_at": utc_now_iso(),
    }
    with TASKS_LOCK:
        TASKS[task["task_id"]] = task
    TASK_QUEUE.put(task)
    return dict(task)


def _store_bridge_run(
    *,
    entity_id: Optional[int],
    entity_title: str,
    entity_description: str,
    art_id: Optional[int],
    color_id: Optional[int],
    dirt_id: Optional[int],
    thought_text: str,
    analysis_text: str,
    bridge_text: str,
    sources_text: str,
    model_bridge: str,
    model_sources: str,
    tokens_in_bridge: int,
    tokens_out_bridge: int,
    total_tokens_bridge: int,
    tokens_in_sources: int,
    tokens_out_sources: int,
    total_tokens_sources: int,
) -> int:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            """
            INSERT INTO oasis_bridge_runs (
                entity_id,
                entity_title,
                entity_description,
                art_id,
                color_id,
                dirt_id,
                thought_text,
                analysis_text,
                bridge_text,
                sources_text,
                model_bridge,
                model_sources,
                tokens_in_bridge,
                tokens_out_bridge,
                total_tokens_bridge,
                tokens_in_sources,
                tokens_out_sources,
                total_tokens_sources,
                created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            RETURNING id
            """,
            (
                entity_id,
                entity_title,
                entity_description,
                art_id,
                color_id,
                dirt_id,
                thought_text,
                analysis_text,
                bridge_text,
                sources_text,
                model_bridge,
                model_sources,
                tokens_in_bridge,
                tokens_out_bridge,
                total_tokens_bridge,
                tokens_in_sources,
                tokens_out_sources,
                total_tokens_sources,
                utc_now_iso(),
            ),
        ).fetchone()
        conn.commit()
        return int(row["id"])
    finally:
        conn.close()


def _store_story_run(
    *,
    entity_id: int,
    entity_title: str,
    entity_description: str,
    art_id: int,
    color_id: int,
    thought_text: str,
    story_text: str,
    sources_text: str,
    model_story: str,
    model_sources: str,
    prompt_type: str,
    tokens_in_story: int,
    tokens_out_story: int,
    total_tokens_story: int,
    tokens_in_sources: int,
    tokens_out_sources: int,
    total_tokens_sources: int,
) -> int:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            """
            INSERT INTO oasis_story_runs (
                entity_id,
                entity_title,
                entity_description,
                art_id,
                color_id,
                thought_text,
                story_text,
                sources_text,
                model_story,
                model_sources,
                prompt_type,
                tokens_in_story,
                tokens_out_story,
                total_tokens_story,
                tokens_in_sources,
                tokens_out_sources,
                total_tokens_sources,
                created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            RETURNING id
            """,
            (
                entity_id,
                entity_title,
                entity_description,
                art_id,
                color_id,
                thought_text,
                story_text,
                sources_text,
                model_story,
                model_sources,
                prompt_type,
                tokens_in_story,
                tokens_out_story,
                total_tokens_story,
                tokens_in_sources,
                tokens_out_sources,
                total_tokens_sources,
                utc_now_iso(),
            ),
        ).fetchone()
        conn.commit()
        return int(row["id"])
    finally:
        conn.close()


def _process_bridge_task(task: Dict[str, Any]) -> Dict[str, Any]:
    payload = task.get("payload", {})
    entity_id = payload.get("entity_id")
    entity_title = payload.get("entity_title")
    entity_description = payload.get("entity_description") or ""

    art_id = payload.get("art_id")
    color_id = payload.get("color_id")
    dirt_id = payload.get("dirt_id")

    thought_text = payload.get("thought")
    analysis_text = payload.get("analysis")

    model = payload.get("model") or MODEL_DEFAULT

    if entity_id is not None:
        try:
            entity_id = int(entity_id)
        except Exception as e:
            raise ValueError("entity_id must be an integer") from e
        row = fetch_entity(entity_id)
        entity_title = row.get("name") or entity_title
        entity_description = row.get("description") or entity_description

    any_db_inputs = any(x is not None for x in (art_id, color_id, dirt_id))
    use_db_inputs = all(x is not None for x in (art_id, color_id, dirt_id))
    if any_db_inputs and not use_db_inputs:
        raise ValueError("art_id, color_id, and dirt_id must be provided together")
    if use_db_inputs:
        try:
            art_id = int(art_id)
            color_id = int(color_id)
            dirt_id = int(dirt_id)
        except Exception as e:
            raise ValueError("art_id, color_id, and dirt_id must be integers") from e
        art_text, color_text = fetch_art_color_text(art_id, color_id)
        thought_text = f"{art_text}\n\n{color_text}"
        analysis_text = fetch_dirt_analysis(dirt_id)

    if not isinstance(thought_text, str) or not thought_text.strip():
        raise ValueError("thought text is required")
    if not isinstance(analysis_text, str) or not analysis_text.strip():
        raise ValueError("analysis text is required")
    if not isinstance(entity_title, str) or not entity_title.strip():
        raise ValueError("entity_title is required")
    if not isinstance(entity_description, str):
        raise ValueError("entity_description must be a string")

    bridge_prompt = build_bridge_prompt(
        thought_text.strip(),
        analysis_text.strip(),
        entity_title.strip(),
        entity_description.strip(),
    )
    bridge_text, _, tokens_in_bridge, tokens_out_bridge, total_tokens_bridge = run_llm(
        bridge_prompt,
        model=model,
    )

    provenance_prompt = build_provenance_prompt(bridge_text)
    sources_text, _, tokens_in_sources, tokens_out_sources, total_tokens_sources = run_llm(
        provenance_prompt,
        model=model,
    )

    run_id = _store_bridge_run(
        entity_id=entity_id,
        entity_title=entity_title.strip(),
        entity_description=entity_description.strip(),
        art_id=art_id,
        color_id=color_id,
        dirt_id=dirt_id,
        thought_text=thought_text.strip(),
        analysis_text=analysis_text.strip(),
        bridge_text=bridge_text,
        sources_text=sources_text,
        model_bridge=model,
        model_sources=model,
        tokens_in_bridge=tokens_in_bridge,
        tokens_out_bridge=tokens_out_bridge,
        total_tokens_bridge=total_tokens_bridge,
        tokens_in_sources=tokens_in_sources,
        tokens_out_sources=tokens_out_sources,
        total_tokens_sources=total_tokens_sources,
    )

    return {
        "run_id": run_id,
        "bridge_text": bridge_text,
        "sources_text": sources_text,
    }


def _process_story_task(task: Dict[str, Any]) -> Dict[str, Any]:
    payload = task.get("payload", {})
    entity_id = payload.get("entity_id")
    art_id = payload.get("art_id")
    color_id = payload.get("color_id")
    model = payload.get("model") or MODEL_DEFAULT
    prompt_type = payload.get("prompt_type") or "connection"

    prompt_map = {
        "connection": STORY_PROMPT,
        "origin": STORY_ORIGIN_PROMPT,
        "conflict": STORY_CONFLICT_PROMPT,
        "coordination": STORY_COORDINATION_PROMPT,
        "relationship": RELATIONSHIP_PROMPT,
    }
    prompt_type = str(prompt_type)
    prompt_text = prompt_map.get(prompt_type)
    if prompt_text is None:
        raise ValueError("prompt_type must be 'connection', 'origin', 'conflict', 'coordination', or 'relationship'")

    try:
        entity_id = int(entity_id)
        art_id = int(art_id)
        color_id = int(color_id)
    except Exception as e:
        raise ValueError("entity_id, art_id, and color_id are required integers") from e

    entity = fetch_entity(entity_id)
    entity_title = entity.get("name") or ""
    entity_description = entity.get("description") or ""

    art_text, color_text = fetch_art_color_text(art_id, color_id)
    thought_text = f"{art_text}\n\n{color_text}".strip()

    if not thought_text:
        raise ValueError("thought text is required")
    if not entity_title:
        raise ValueError("entity title is required")

    story_prompt = build_story_prompt(prompt_text, thought_text, entity_title, entity_description)
    story_text, _, tokens_in_story, tokens_out_story, total_tokens_story = run_llm(
        story_prompt,
        model=model,
    )

    source_prompt_map = {
        "relationship": RELATIONSHIP_SOURCES_PROMPT,
    }
    source_prompt_text = source_prompt_map.get(prompt_type, STORY_PROVENANCE_PROMPT)
    provenance_prompt = build_sources_prompt(source_prompt_text, story_text)
    sources_text, _, tokens_in_sources, tokens_out_sources, total_tokens_sources = run_llm(
        provenance_prompt,
        model=model,
    )

    run_id = _store_story_run(
        entity_id=entity_id,
        entity_title=entity_title,
        entity_description=entity_description,
        art_id=art_id,
        color_id=color_id,
        thought_text=thought_text,
        story_text=story_text,
        sources_text=sources_text,
        model_story=model,
        model_sources=model,
        prompt_type=prompt_type,
        tokens_in_story=tokens_in_story,
        tokens_out_story=tokens_out_story,
        total_tokens_story=total_tokens_story,
        tokens_in_sources=tokens_in_sources,
        tokens_out_sources=tokens_out_sources,
        total_tokens_sources=total_tokens_sources,
    )

    return {
        "run_id": run_id,
        "story_text": story_text,
        "sources_text": sources_text,
    }


def worker_loop(worker_id: int) -> None:
    while True:
        task = TASK_QUEUE.get()
        task_id = task.get("task_id")
        with TASKS_LOCK:
            if task_id in TASKS:
                TASKS[task_id]["status"] = "running"
                TASKS[task_id]["started_at"] = utc_now_iso()
                TASKS[task_id]["worker_id"] = worker_id
        try:
            task_type = task.get("task_type")
            if task_type == "story_build":
                result = _process_story_task(task)
            else:
                result = _process_bridge_task(task)
            with TASKS_LOCK:
                if task_id in TASKS:
                    TASKS[task_id]["status"] = "done"
                    TASKS[task_id]["finished_at"] = utc_now_iso()
                    TASKS[task_id]["result"] = result
        except Exception as exc:
            with TASKS_LOCK:
                if task_id in TASKS:
                    TASKS[task_id]["status"] = "error"
                    TASKS[task_id]["finished_at"] = utc_now_iso()
                    TASKS[task_id]["error"] = str(exc)
        finally:
            TASK_QUEUE.task_done()


for wid in range(CONCURRENCY):
    t = threading.Thread(target=worker_loop, args=(wid,), daemon=True)
    t.start()


@app.get("/oasis/health")
def health() -> Any:
    return jsonify({"ok": True, "db_path": DB_PATH})


@app.get("/oasis")
def list_entities() -> Any:
    db = get_db()
    rows = db.execute("SELECT * FROM entites ORDER BY created_at DESC, id DESC").fetchall()
    return jsonify([row_to_dict(r) for r in rows])


@app.post("/oasis")
def create_entity() -> Any:
    payload = require_json()
    name = payload.get("name")
    description = payload.get("description") or ""

    if not isinstance(name, str) or not name.strip():
        abort(400, description="'name' is required and must be a non-empty string")
    if description is not None and not isinstance(description, str):
        abort(400, description="'description' must be a string when provided")

    now = utc_now_iso()
    db = get_db()
    cur = db.execute(
        """
        INSERT INTO entites (name, description, created_at, updated_at)
        VALUES (?, ?, ?, ?)
        """,
        (name.strip(), description.strip(), now, now),
    )
    db.commit()

    row = db.execute("SELECT * FROM entites WHERE id = ?", (cur.lastrowid,)).fetchone()
    return jsonify(row_to_dict(row)), 201


@app.get("/oasis/<int:entity_id>")
def get_entity(entity_id: int) -> Any:
    row = _require_entity(entity_id)
    return jsonify(row_to_dict(row))


@app.delete("/oasis/<int:entity_id>")
def delete_entity(entity_id: int) -> Any:
    db = get_db()
    _require_entity(entity_id)
    db.execute("DELETE FROM entites WHERE id = ?", (entity_id,))
    db.commit()
    return jsonify({"ok": True, "deleted_id": entity_id})


@app.post("/oasis/bridge")
def enqueue_bridge() -> Any:
    if _client is None:
        abort(500, description=f"OpenAI client not initialized: {_client_err}")
    payload = require_json()
    task = enqueue_bridge_task(payload)
    task["queue_size"] = TASK_QUEUE.qsize()
    return jsonify(task), 202


@app.post("/oasis/story")
def enqueue_story() -> Any:
    if _client is None:
        abort(500, description=f"OpenAI client not initialized: {_client_err}")
    payload = require_json()
    for field in ("entity_id", "art_id", "color_id"):
        if field not in payload:
            abort(400, description=f"'{field}' is required")
    prompt_type = payload.get("prompt_type")
    if prompt_type is not None and prompt_type not in ("connection", "origin", "conflict", "coordination", "relationship"):
        abort(400, description="'prompt_type' must be 'connection', 'origin', 'conflict', 'coordination', or 'relationship' when provided")
    task = enqueue_story_task(payload)
    task["queue_size"] = TASK_QUEUE.qsize()
    return jsonify(task), 202


@app.get("/oasis/bridge/runs")
def list_bridge_runs() -> Any:
    entity_id = request.args.get("entity_id")
    if entity_id is None:
        abort(400, description="'entity_id' is required")
    try:
        entity_id_int = int(entity_id)
    except Exception:
        abort(400, description="'entity_id' must be an integer")

    limit = request.args.get("limit", default=50, type=int)
    limit = max(1, min(limit, 200))

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """
            SELECT id, entity_id, entity_title, entity_description, art_id, color_id, dirt_id,
                   bridge_text, sources_text, created_at
            FROM oasis_bridge_runs
            WHERE entity_id = ?
            ORDER BY created_at DESC, id DESC
            LIMIT ?
            """,
            (entity_id_int, limit),
        ).fetchall()
        return jsonify([dict(r) for r in rows])
    finally:
        conn.close()


@app.get("/oasis/story/runs")
def list_story_runs() -> Any:
    entity_id = request.args.get("entity_id")
    art_id = request.args.get("art_id")
    color_id = request.args.get("color_id")
    if art_id is None or color_id is None:
        abort(400, description="'art_id' and 'color_id' are required")
    try:
        art_id_int = int(art_id)
        color_id_int = int(color_id)
    except Exception:
        abort(400, description="'art_id' and 'color_id' must be integers")

    limit = request.args.get("limit", default=50, type=int)
    limit = max(1, min(limit, 200))

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        if entity_id is not None:
            try:
                entity_id_int = int(entity_id)
            except Exception:
                abort(400, description="'entity_id' must be an integer")
            rows = conn.execute(
                """
            SELECT id, entity_id, entity_title, entity_description, art_id, color_id,
                   story_text, sources_text, prompt_type, created_at
            FROM oasis_story_runs
            WHERE entity_id = ? AND art_id = ? AND color_id = ?
            ORDER BY created_at DESC, id DESC
            LIMIT ?
                """,
                (entity_id_int, art_id_int, color_id_int, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                """
            SELECT id, entity_id, entity_title, entity_description, art_id, color_id,
                   story_text, sources_text, prompt_type, created_at
            FROM oasis_story_runs
            WHERE art_id = ? AND color_id = ?
            ORDER BY created_at DESC, id DESC
            LIMIT ?
                """,
                (art_id_int, color_id_int, limit),
            ).fetchall()
        return jsonify([dict(r) for r in rows])
    finally:
        conn.close()


@app.delete("/oasis/story/runs/<int:run_id>")
def delete_story_run(run_id: int) -> Any:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            "SELECT id FROM oasis_story_runs WHERE id = ?",
            (run_id,),
        ).fetchone()
        if not row:
            abort(404, description="story run not found")
        conn.execute("DELETE FROM oasis_story_runs WHERE id = ?", (run_id,))
        conn.commit()
        return jsonify({"ok": True, "deleted_id": run_id})
    finally:
        conn.close()


@app.get("/oasis/story/runs/<int:run_id>")
def get_story_run(run_id: int) -> Any:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            """
            SELECT id, entity_id, entity_title, entity_description, art_id, color_id,
                   thought_text, story_text, sources_text, prompt_type, created_at
            FROM oasis_story_runs
            WHERE id = ?
            """,
            (run_id,),
        ).fetchone()
        if not row:
            abort(404, description="story run not found")
        return jsonify(dict(row))
    finally:
        conn.close()


@app.get("/oasis/story/runs/<int:run_id>/notes")
def list_story_notes(run_id: int) -> Any:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """
            SELECT id, story_run_id, note_text, created_at
            FROM oasis_story_notes
            WHERE story_run_id = ?
            ORDER BY created_at DESC, id DESC
            """,
            (run_id,),
        ).fetchall()
        return jsonify([dict(r) for r in rows])
    finally:
        conn.close()


@app.post("/oasis/story/runs/<int:run_id>/notes")
def create_story_note(run_id: int) -> Any:
    payload = require_json()
    note_text = payload.get("note_text")
    if not isinstance(note_text, str) or not note_text.strip():
        abort(400, description="'note_text' is required and must be a non-empty string")

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            """
            INSERT INTO oasis_story_notes (story_run_id, note_text, created_at)
            VALUES (?, ?, ?)
            RETURNING id, story_run_id, note_text, created_at
            """,
            (run_id, note_text.strip(), utc_now_iso()),
        ).fetchone()
        conn.commit()
        return jsonify(dict(row)), 201
    finally:
        conn.close()


@app.get("/oasis/story/runs/all")
def list_all_story_runs() -> Any:
    limit = request.args.get("limit", default=200, type=int)
    limit = max(1, min(limit, 1000))

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """
            SELECT id, entity_id, entity_title, entity_description, art_id, color_id,
                   story_text, sources_text, prompt_type, created_at
            FROM oasis_story_runs
            ORDER BY created_at DESC, id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return jsonify([dict(r) for r in rows])
    finally:
        conn.close()


@app.get("/oasis/llm/tasks/<task_id>")
def get_task(task_id: str) -> Any:
    with TASKS_LOCK:
        task = TASKS.get(task_id)
        if not task:
            abort(404, description="task not found")
        return jsonify(task)


@app.get("/oasis/llm/tasks")
def list_tasks() -> Any:
    status = request.args.get("status")
    task_type = request.args.get("task_type")
    include_error = request.args.get("include_error") == "1"

    limit = request.args.get("limit", default=200, type=int)
    limit = max(1, min(limit, 500))

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


@app.get("/oasis/llm/workers")
def get_queue_workers() -> Any:
    with TASKS_LOCK:
        running = [t for t in TASKS.values() if t.get("status") == "running"]

    workers: Dict[str, list[Dict[str, Any]]] = {}
    for t in running:
        wid = t.get("worker_id")
        if wid is None:
            continue
        wid_str = str(wid)
        payload = t.get("payload") or {}
        workers.setdefault(wid_str, []).append(
            {
                "task_id": t.get("task_id"),
                "task_type": t.get("task_type"),
                "status": t.get("status"),
                "entity_id": payload.get("entity_id"),
                "art_id": payload.get("art_id"),
                "color_id": payload.get("color_id"),
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


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
