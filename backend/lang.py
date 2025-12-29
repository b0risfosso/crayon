from __future__ import annotations

import json
import queue
import sqlite3
import threading
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from flask import Flask, jsonify, request
from openai import OpenAI
from pydantic import BaseModel

DB_PATH = "/var/www/site/data/lang.db"

app = Flask(__name__)
client = OpenAI()

CONCURRENCY = 2
task_queue: queue.Queue[int] = queue.Queue()
task_lock = threading.Lock()
tasks: dict[int, "Task"] = {}
_workers_started = False
_next_task_id = 1


INSTRUCTION_TEMPLATE = """
Read the following text.
Text A: {text_a}

Draft a few ideas for the how the idea, system, or world in Text A can be built by, interacted with, influenced by, or be integrated into the concept, system, world found in Text B.

Text B: {text_b}
"""

class Idea(BaseModel):
    name: str
    desciription: str

class IdeaSet(BaseModel):
    ideas: list[Idea]

@dataclass
class Task:
    id: int
    text_a: str
    text_b: str
    status: str
    created_at: str
    started_at: str | None = None
    finished_at: str | None = None
    error: str | None = None
    run_id: int | None = None

def _get_db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")

def _next_id() -> int:
    global _next_task_id
    with task_lock:
        task_id = _next_task_id
        _next_task_id += 1
        return task_id

def _enqueue_task(text_a: str, text_b: str) -> int:
    task_id = _next_id()
    task = Task(
        id=task_id,
        text_a=text_a,
        text_b=text_b,
        status="queued",
        created_at=_now_iso(),
    )
    with task_lock:
        tasks[task_id] = task
    task_queue.put(task_id)
    return task_id

def _run_task(task: Task) -> None:
    text_input = INSTRUCTION_TEMPLATE.format(
        text_a=task.text_a,
        text_b=task.text_b,
    ).strip()

    response = client.responses.parse(
        model="gpt-5-mini-2025-08-07",
        input=[
            {"role": "system", "content": "You are an expert idea generator."},
            {
                "role": "user",
                "content": text_input,
            },
        ],
        text_format=IdeaSet,
    )

    event = response.output_parsed
    output_json = json.dumps(event.model_dump(), ensure_ascii=True)

    conn = _get_db()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO runs (instruction, text_a, text_b, prompt, response)
        VALUES (?, ?, ?, ?, ?)
        """,
        (INSTRUCTION_TEMPLATE, task.text_a, task.text_b, text_input, output_json),
    )
    conn.commit()
    task.run_id = cur.lastrowid
    conn.close()

def _worker_loop() -> None:
    while True:
        task_id = task_queue.get()
        try:
            with task_lock:
                task = tasks.get(task_id)
                if not task:
                    continue
                task.status = "running"
                task.started_at = _now_iso()

            try:
                _run_task(task)
                with task_lock:
                    task.status = "done"
                    task.finished_at = _now_iso()
            except Exception as exc:
                with task_lock:
                    task.status = "error"
                    task.error = str(exc)
                    task.finished_at = _now_iso()
        finally:
            task_queue.task_done()

def _ensure_workers() -> None:
    global _workers_started
    if _workers_started:
        return
    _workers_started = True
    for _ in range(CONCURRENCY):
        thread = threading.Thread(target=_worker_loop, daemon=True)
        thread.start()

@app.before_request
def _ensure_workers_for_request():
    _ensure_workers()


@app.post("/api/lang")
def run_lang():
    data = request.get_json(silent=True) or {}
    text_a = (data.get("text_a") or "").strip()
    text_b = (data.get("text_b") or "").strip()

    if not (text_a or text_b):
        return jsonify({"error": "text_a or text_b required"}), 400
    task_id = _enqueue_task(text_a=text_a, text_b=text_b)
    return jsonify({"task_id": task_id, "status": "queued"}), 202


@app.get("/api/lang")
def list_lang():
    conn = _get_db()
    rows = conn.execute(
        """
        SELECT id, instruction, text_a, text_b, response, created_at
        FROM runs
        ORDER BY id DESC
        """
    ).fetchall()
    conn.close()

    result = []
    for row in rows:
        item = dict(row)
        response_text = item.get("response") or ""
        try:
            item["response"] = json.loads(response_text)
        except json.JSONDecodeError:
            item["response"] = response_text
        result.append(item)

    return jsonify(result)

@app.get("/api/creations")
def list_creations():
    conn = _get_db()
    rows = conn.execute(
        """
        SELECT id, text_b, created_at
        FROM creations
        ORDER BY id DESC
        """
    ).fetchall()
    conn.close()
    return jsonify([dict(row) for row in rows])

@app.post("/api/creations")
def create_creation():
    data = request.get_json(silent=True) or {}
    text_b = (data.get("text_b") or "").strip()
    if not text_b:
        return jsonify({"error": "text_b required"}), 400
    conn = _get_db()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO creations (text_b)
        VALUES (?)
        """,
        (text_b,),
    )
    conn.commit()
    creation_id = cur.lastrowid
    conn.close()
    return jsonify({"id": creation_id, "text_b": text_b})

@app.delete("/api/creations/<int:creation_id>")
def delete_creation(creation_id: int):
    conn = _get_db()
    cur = conn.cursor()
    cur.execute(
        """
        DELETE FROM creations
        WHERE id = ?
        """,
        (creation_id,),
    )
    conn.commit()
    deleted = cur.rowcount
    conn.close()
    if not deleted:
        return jsonify({"error": "not found"}), 404
    return jsonify({"deleted": creation_id})

@app.get("/api/queue")
def queue_state():
    with task_lock:
        items = [asdict(task) for task in tasks.values()]

    queued = sum(1 for item in items if item["status"] == "queued")
    running = sum(1 for item in items if item["status"] == "running")

    return jsonify(
        {
            "concurrency": CONCURRENCY,
            "queued": queued,
            "running": running,
            "total": len(items),
            "tasks": sorted(items, key=lambda item: item["id"], reverse=True),
        }
    )

if __name__ == "__main__":
    _ensure_workers()
    app.run(debug=True)
