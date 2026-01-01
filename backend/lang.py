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
USAGE_DB_PATH = "/var/www/site/data/llm_usage.db"

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

GARGANTUA_PROMPT_TEMPLATE = """
If we treat {gargantua} as the system/platform/entity that creates, operates, enacts, and interacts with things: What can go into and/or interact with {gargantua} (inputs), and what can come out and be used from {gargantua} (outputs) to support the creation, operation, enactment, and interaction with the following? What operations can be performed in/on {gargantua} to support the creation, operation, enactment, and interaction with the following?

{text_input}
""".strip()


class Idea(BaseModel):
    name: str
    desciription: str
    writing_id: int | None = None

class IdeaSet(BaseModel):
    ideas: list[Idea]

class GeneratedChild(BaseModel):
    title: str
    text: str

@dataclass
class Task:
    id: int
    kind: str  # "lang", "prompt_child", "gargantua_child"
    text_a: str
    text_b: str
    parent_writing_id: int | None
    status: str
    created_at: str
    # prompt-child fields
    prompt_id: int | None = None
    prompt_text: str | None = None
    output_type: str | None = None
    # NEW: gargantua
    gargantua_id: int | None = None
    # bookkeeping
    started_at: str | None = None
    finished_at: str | None = None
    error: str | None = None
    run_id: int | None = None



def _get_db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def _get_usage_db() -> sqlite3.Connection:
    conn = sqlite3.connect(USAGE_DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")

def _today_utc() -> str:
    return datetime.now(timezone.utc).date().isoformat()

def _next_id() -> int:
    global _next_task_id
    with task_lock:
        task_id = _next_task_id
        _next_task_id += 1
        return task_id

def _enqueue_task(text_a: str, text_b: str, parent_writing_id: int | None) -> int:
    task_id = _next_id()
    created_at = _now_iso()
    task = Task(
        id=task_id,
        kind="lang",
        text_a=text_a,
        text_b=text_b,
        parent_writing_id=parent_writing_id,
        status="queued",
        created_at=created_at,
    )
    with task_lock:
        tasks[task_id] = task
    task_queue.put(task_id)
    return task_id


def _enqueue_prompt_task(
    *,
    writing_id: int,
    prompt_id: int | None,
    prompt_text: str,
    output_type: str | None,
) -> int:
    task_id = _next_id()
    created_at = _now_iso()
    task = Task(
        id=task_id,
        kind="prompt_child",
        text_a="",   # not used directly
        text_b="",   # not used directly
        parent_writing_id=writing_id,
        status="queued",
        created_at=created_at,
        prompt_id=prompt_id,
        prompt_text=prompt_text,
        output_type=output_type,
    )
    with task_lock:
        tasks[task_id] = task
    task_queue.put(task_id)
    return task_id

def _enqueue_gargantua_task(
    *,
    writing_id: int,
    gargantua_id: int,
) -> int:
    task_id = _next_id()
    created_at = _now_iso()
    task = Task(
        id=task_id,
        kind="gargantua_child",
        text_a="",
        text_b="",
        parent_writing_id=writing_id,
        status="queued",
        created_at=created_at,
        gargantua_id=gargantua_id,
    )
    with task_lock:
        tasks[task_id] = task
    task_queue.put(task_id)
    return task_id


def _run_task(task: Task) -> None:
    if task.kind == "prompt_child":
        _run_prompt_child_task(task)
    elif task.kind == "gargantua_child":
        _run_gargantua_child_task(task)
    else:
        _run_lang_task(task)


def _run_lang_task(task: Task) -> None:
    text_input = INSTRUCTION_TEMPLATE.format(
        text_a=task.text_a,
        text_b=task.text_b,
    ).strip()

    model_name = "gpt-5-mini-2025-08-07"
    response = client.responses.parse(
        model=model_name,
        input=[
            {"role": "system", "content": "You are an expert idea generator."},
            {"role": "user", "content": text_input},
        ],
        text_format=IdeaSet,
    )

    idea_set: IdeaSet = response.output_parsed
    _record_usage(model_name, response)

    conn = _get_db()
    cur = conn.cursor()

    # 1) Insert run row (prompt_id left NULL)
    cur.execute(
        """
        INSERT INTO runs (instruction, text_a, text_b, parent_writing_id, prompt, response)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (INSTRUCTION_TEMPLATE, task.text_a, task.text_b, task.parent_writing_id, text_input, None),
    )
    run_id = cur.lastrowid

    # 2) Create writings for each idea (this is what you already have)
    enriched_ideas: list[dict] = []
    for idea in idea_set.ideas:
        cur.execute(
            """
            INSERT INTO writings (
                name,
                description,
                parent_run_id,
                parent_text_a,
                parent_text_b,
                parent_writing_id,
                notes,
                type
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                idea.name,
                idea.desciription,
                run_id,
                task.text_a,
                task.text_b,
                task.parent_writing_id,
                "",
                "words",
            ),
        )
        writing_id = int(cur.lastrowid)
        idea_dict = idea.model_dump()
        idea_dict["writing_id"] = writing_id
        enriched_ideas.append(idea_dict)

    # 3) Store response JSON
    cur.execute(
        """
        UPDATE runs
        SET response = ?
        WHERE id = ?
        """,
        (json.dumps({"ideas": enriched_ideas}), run_id),
    )
    conn.commit()
    conn.close()

    task.run_id = run_id

def _first_line(text: str | None) -> str:
    if not text:
        return ""
    return (text.splitlines()[0] or "").strip()


def _run_prompt_child_task(task: Task) -> None:
    if task.parent_writing_id is None:
        raise ValueError("prompt_child task requires parent_writing_id (writing_id)")

    prompt_text = (task.prompt_text or "").strip()
    if not prompt_text:
        raise ValueError("prompt_text is required for prompt_child task")

    conn = _get_db()
    cur = conn.cursor()

    # 1) Load the input writing
    parent = conn.execute(
        """
        SELECT id, name, description, parent_text_a, parent_text_b
        FROM writings
        WHERE id = ?
        """,
        (task.parent_writing_id,),
    ).fetchone()
    if not parent:
        conn.close()
        raise ValueError(f"Writing {task.parent_writing_id} not found")

    writing_id = int(parent["id"])
    writing_name = parent["name"] or "(untitled)"
    writing_desc = parent["description"] or ""
    parent_text_a = parent["parent_text_a"] or ""
    parent_text_b = parent["parent_text_b"] or ""

    title_a = _first_line(parent_text_a)
    title_b = _first_line(parent_text_b)

    # 2) Build the final prompt to the LLM
    context_parts: list[str] = []
    if title_a:
        context_parts.append(f"{title_a}")
    if title_b:
        context_parts.append(f"{title_b}")
    context_parts.append(f"{writing_name}")
    if writing_desc:
        context_parts.append(f"\n{writing_desc}")

    context_block = "\n\n".join(context_parts)
    final_prompt = prompt_text.strip()
    if context_block:
        final_prompt = f"{final_prompt.strip()}\n\n---\n\nTEXT:\n\n{context_block}"

    model_name = "gpt-5-mini-2025-08-07"
    response = client.responses.parse(
        model=model_name,
        input=[
            {"role": "system", "content": "You are a expert. Complete the task as requested."},
            {"role": "user", "content": final_prompt},
        ],
        text_format=GeneratedChild,
    )

    output: GeneratedChild = response.output_parsed
    _record_usage(model_name, response)

    # 3) Insert a run row with prompt_id
    cur.execute(
        """
        INSERT INTO runs (
            instruction,
            text_a,
            text_b,
            parent_writing_id,
            prompt,
            response,
            prompt_id
        )
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "prompt_child",       # instruction label
            context_block,        # text_a = context we fed in
            "",                   # text_b unused
            writing_id,           # parent writing
            final_prompt,         # full prompt text actually sent to LLM
            None,                 # response will be filled below
            task.prompt_id,       # NEW: link back to prompts.id
        ),
    )
    run_id = int(cur.lastrowid)

    # 4) Create the child writing
    parent_text_a_for_child = f"{writing_name}\n\n{writing_desc}".strip()
    child_type = (task.output_type or "").strip() or "words"

    cur.execute(
        """
        INSERT INTO writings (
            name,
            description,
            parent_run_id,
            parent_text_a,
            parent_text_b,
            parent_writing_id,
            notes,
            type
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            output.title,
            output.text,
            run_id,
            parent_text_a_for_child,  # parent text A = name + description of input writing
            "",                       # parent text B = empty per your requirement
            writing_id,               # parent writing id
            "",
            child_type,
        ),
    )
    child_writing_id = int(cur.lastrowid)

    # 5) Create a writing_note pointing at this child writing
    note_content = f"{output.title}\n\n{output.text}".strip()
    cur.execute(
        """
        INSERT INTO writing_notes (writing_id, content, child_writing_id)
        VALUES (?, ?, ?)
        """,
        (writing_id, note_content, child_writing_id),
    )
    note_id = int(cur.lastrowid)  # not used further but useful to keep

    # 6) Save the structured response into the run
    cur.execute(
        """
        UPDATE runs
        SET response = ?
        WHERE id = ?
        """,
        (
            json.dumps(
                {
                    "title": output.title,
                    "text": output.text,
                    "child_writing_id": child_writing_id,
                    "note_id": note_id,
                }
            ),
            run_id,
        ),
    )

    conn.commit()
    conn.close()

    task.run_id = run_id


def _run_gargantua_child_task(task: Task) -> None:
    if task.parent_writing_id is None:
        raise ValueError("gargantua_child task requires parent_writing_id (writing_id)")
    if task.gargantua_id is None:
        raise ValueError("gargantua_child task requires gargantua_id")

    conn = _get_db()
    cur = conn.cursor()

    # 1) Load the input writing (same as prompt_child)
    parent = conn.execute(
        """
        SELECT id, name, description, parent_text_a, parent_text_b
        FROM writings
        WHERE id = ?
        """,
        (task.parent_writing_id,),
    ).fetchone()
    if not parent:
        conn.close()
        raise ValueError(f"Writing {task.parent_writing_id} not found")

    writing_id = int(parent["id"])
    writing_name = parent["name"] or "(untitled)"
    writing_desc = parent["description"] or ""
    parent_text_a = parent["parent_text_a"] or ""
    parent_text_b = parent["parent_text_b"] or ""

    title_a = _first_line(parent_text_a)
    title_b = _first_line(parent_text_b)

    # Build context_block exactly like before
    context_parts: list[str] = []
    if title_a:
        context_parts.append(f"{title_a}")
    if title_b:
        context_parts.append(f"{title_b}")
    context_parts.append(f"{writing_name}")
    if writing_desc:
        context_parts.append(f"\n{writing_desc}")

    context_block = "\n\n".join(context_parts)

    # 2) Load gargantua row
    garg = conn.execute(
        """
        SELECT id, name, text, type
        FROM gargantua
        WHERE id = ?
        """,
        (task.gargantua_id,),
    ).fetchone()
    if not garg:
        conn.close()
        raise ValueError(f"gargantua {task.gargantua_id} not found")

    garg_text = garg["text"] or ""
    garg_type = (garg["type"] or "").strip() or "words"

    # 3) Build final prompt using your template
    final_prompt = GARGANTUA_PROMPT_TEMPLATE.format(
        gargantua=garg_text,
        text_input=context_block,
    )

    model_name = "gpt-5-mini-2025-08-07"
    response = client.responses.parse(
        model=model_name,
        input=[
            {
                "role": "system",
                "content": "You are an expert. Complete the task as requested.",
            },
            {"role": "user", "content": final_prompt},
        ],
        text_format=GeneratedChild,
    )

    output: GeneratedChild = response.output_parsed
    _record_usage(model_name, response)

    # 4) Insert run row (recording context and gargantua text)
    cur.execute(
        """
        INSERT INTO runs (
            instruction,
            text_a,
            text_b,
            parent_writing_id,
            prompt,
            response,
            prompt_id
        )
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "gargantua_child",   # instruction
            context_block,       # text_a = context
            garg_text,           # text_b = gargantua definition
            writing_id,
            final_prompt,        # full prompt sent to LLM
            None,                # response JSON filled below
            None,                # no prompt_id (not from prompts table)
        ),
    )
    run_id = int(cur.lastrowid)

    # 5) Create the child writing; type from gargantua.type
    parent_text_a_for_child = f"{writing_name}\n\n{writing_desc}".strip()
    child_type = garg_type

    cur.execute(
        """
        INSERT INTO writings (
            name,
            description,
            parent_run_id,
            parent_text_a,
            parent_text_b,
            parent_writing_id,
            notes,
            type
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            output.title,
            output.text,
            run_id,
            parent_text_a_for_child,
            "",             # parent_text_b empty as before
            writing_id,
            "",
            child_type,     # <-- from gargantua.type
        ),
    )
    child_writing_id = int(cur.lastrowid)

    # 6) writing_note pointing at this child
    note_content = f"{output.title}\n\n{output.text}".strip()
    cur.execute(
        """
        INSERT INTO writing_notes (writing_id, content, child_writing_id)
        VALUES (?, ?, ?)
        """,
        (writing_id, note_content, child_writing_id),
    )
    note_id = int(cur.lastrowid)

    # 7) Save structured response JSON into run
    cur.execute(
        """
        UPDATE runs
        SET response = ?
        WHERE id = ?
        """,
        (
            json.dumps(
                {
                    "title": output.title,
                    "text": output.text,
                    "child_writing_id": child_writing_id,
                    "note_id": note_id,
                    "gargantua_id": task.gargantua_id,
                }
            ),
            run_id,
        ),
    )

    conn.commit()
    conn.close()

    task.run_id = run_id


def _extract_usage(response) -> tuple[int, int, int] | None:
    usage = getattr(response, "usage", None)
    if not usage:
        return None
    tokens_in = getattr(usage, "input_tokens", None)
    tokens_out = getattr(usage, "output_tokens", None)
    total_tokens = getattr(usage, "total_tokens", None)
    if tokens_in is None or tokens_out is None or total_tokens is None:
        return None
    return int(tokens_in), int(tokens_out), int(total_tokens)

def _record_usage(model_name: str, response) -> None:
    usage = _extract_usage(response)
    if not usage:
        return
    tokens_in, tokens_out, total_tokens = usage
    usage_date = _today_utc()
    conn = _get_usage_db()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO usage_log (usage_date, model, tokens_in, tokens_out, total_tokens)
        VALUES (?, ?, ?, ?, ?)
        """,
        (usage_date, model_name, tokens_in, tokens_out, total_tokens),
    )
    cur.execute(
        """
        INSERT INTO usage_daily (usage_date, model, tokens_in, tokens_out, total_tokens)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(usage_date, model) DO UPDATE SET
          tokens_in = tokens_in + excluded.tokens_in,
          tokens_out = tokens_out + excluded.tokens_out,
          total_tokens = total_tokens + excluded.total_tokens
        """,
        (usage_date, model_name, tokens_in, tokens_out, total_tokens),
    )
    cur.execute(
        """
        INSERT INTO usage_all_time (model, tokens_in, tokens_out, total_tokens)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(model) DO UPDATE SET
          tokens_in = tokens_in + excluded.tokens_in,
          tokens_out = tokens_out + excluded.tokens_out,
          total_tokens = total_tokens + excluded.total_tokens
        """,
        (model_name, tokens_in, tokens_out, total_tokens),
    )
    conn.commit()
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
    parent_writing_id = data.get("parent_writing_id")
    if parent_writing_id is not None:
        try:
            parent_writing_id = int(parent_writing_id)
        except (TypeError, ValueError):
            return jsonify({"error": "parent_writing_id must be an integer"}), 400

    if not (text_a or text_b):
        return jsonify({"error": "text_a or text_b required"}), 400
    task_id = _enqueue_task(text_a=text_a, text_b=text_b, parent_writing_id=parent_writing_id)
    return jsonify({"task_id": task_id, "status": "queued"}), 202


@app.get("/api/lang")
def list_lang():
    parent_writing_id = request.args.get("parent_writing_id", type=int)
    include_children = request.args.get("include_children")
    conn = _get_db()
    if parent_writing_id is None and not include_children:
        rows = conn.execute(
            """
            SELECT id, instruction, text_a, text_b, parent_writing_id, response, created_at
            FROM runs
            WHERE parent_writing_id IS NULL
            ORDER BY id DESC
            """
        ).fetchall()
    elif parent_writing_id is None:
        rows = conn.execute(
            """
            SELECT id, instruction, text_a, text_b, parent_writing_id, response, created_at
            FROM runs
            ORDER BY id DESC
            """
        ).fetchall()
    else:
        rows = conn.execute(
            """
            SELECT id, instruction, text_a, text_b, parent_writing_id, response, created_at
            FROM runs
            WHERE parent_writing_id = ?
            ORDER BY id DESC
            """,
            (parent_writing_id,),
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
    writing_id = request.args.get("writing_id", type=int)

    conn = _get_db()
    if writing_id:
        rows = conn.execute(
            """
            SELECT id,
                   name,
                   description,
                   description AS text_b,
                   created_at
            FROM writings
            WHERE type = 'creations' AND parent_writing_id = ?
            ORDER BY id DESC
            """,
            (writing_id,),
        ).fetchall()
    else:
        rows = conn.execute(
            """
            SELECT id,
                   name,
                   description,
                   description AS text_b,
                   created_at
            FROM writings
            WHERE type = 'creations'
            ORDER BY id DESC
            """
        ).fetchall()

    conn.close()
    return jsonify([dict(row) for row in rows])



@app.post("/api/creations")
def create_creation():
    data = request.get_json(silent=True) or {}
    name = (data.get("name") or "").strip()
    description = (data.get("description") or "").strip()
    parent_writing_id = data.get("writing_id") or data.get("parent_writing_id")

    if not name and not description:
        return jsonify({"error": "name or description required"}), 400

    # Default name if missing
    if not name:
        first_line = description.splitlines()[0].strip()
        name = first_line[:100] or "Creation"

    conn = _get_db()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO writings (
            name,
            description,
            parent_run_id,
            parent_text_a,
            parent_text_b,
            parent_writing_id,
            notes,
            type
        )
        VALUES (?, ?, NULL, '', '', ?, '', 'creations')
        """,
        (name, description, parent_writing_id),
    )
    conn.commit()
    creation_id = cur.lastrowid
    conn.close()
    return jsonify(
        {"id": creation_id, "name": name, "description": description, "text_b": description}
    )



@app.delete("/api/creations/<int:creation_id>")
def delete_creation(creation_id: int):
    conn = _get_db()
    cur = conn.cursor()
    cur.execute(
        """
        DELETE FROM writings
        WHERE id = ? AND type = 'creations'
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

@app.get("/api/usage")
def usage_state():
    conn = _get_usage_db()
    daily = conn.execute(
        """
        SELECT usage_date, model, tokens_in, tokens_out, total_tokens
        FROM usage_daily
        ORDER BY usage_date DESC, model ASC
        """
    ).fetchall()
    all_time = conn.execute(
        """
        SELECT model, tokens_in, tokens_out, total_tokens
        FROM usage_all_time
        ORDER BY model ASC
        """
    ).fetchall()
    conn.close()
    return jsonify(
        {
            "daily": [dict(row) for row in daily],
            "all_time": [dict(row) for row in all_time],
        }
    )

@app.get("/api/writings")
def list_writings():
    type_filter = (request.args.get("type") or "").strip() or None

    conn = _get_db()
    if type_filter:
        rows = conn.execute(
            """
            SELECT id, name, description, parent_run_id, parent_text_a, parent_text_b,
                   parent_writing_id, notes, type, created_at, updated_at
            FROM writings
            WHERE type = ?
            ORDER BY id DESC
            """,
            (type_filter,),
        ).fetchall()
    else:
        rows = conn.execute(
            """
            SELECT id, name, description, parent_run_id, parent_text_a, parent_text_b,
                   parent_writing_id, notes, type, created_at, updated_at
            FROM writings
            ORDER BY id DESC
            """
        ).fetchall()

    conn.close()
    return jsonify([dict(row) for row in rows])



@app.get("/api/writings/lookup")
def lookup_writing():
    run_id = request.args.get("run_id", type=int)
    name = (request.args.get("name") or "").strip()
    parent_text_a = (request.args.get("text_a") or "").strip()
    parent_text_b = (request.args.get("text_b") or "").strip()
    if not run_id or not name:
        return jsonify({"error": "run_id and name required"}), 400

    conn = _get_db()
    row = conn.execute(
        """
        SELECT id, name, description, parent_run_id, parent_text_a, parent_text_b,
               parent_writing_id, notes, type, created_at, updated_at
        FROM writings
        WHERE parent_run_id = ? AND name = ? AND parent_text_a = ? AND parent_text_b = ?
        ORDER BY id DESC
        LIMIT 1
        """,
        (run_id, name, parent_text_a, parent_text_b),
    ).fetchone()
    conn.close()
    if not row:
        return jsonify({}), 404
    return jsonify(dict(row))

@app.post("/api/writings")
def create_writing():
    data = request.get_json(silent=True) or {}
    name = (data.get("name") or "").strip()
    description = (data.get("description") or "").strip()
    parent_run_id = data.get("parent_run_id")
    parent_text_a = (data.get("parent_text_a") or "").strip()
    parent_text_b = (data.get("parent_text_b") or "").strip()
    parent_writing_id = data.get("parent_writing_id")
    notes = (data.get("notes") or "").strip()
    type_ = (data.get("type") or "").strip() or None  # NEW

    if not name:
        return jsonify({"error": "name required"}), 400

    conn = _get_db()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO writings (
            name, description, parent_run_id, parent_text_a, parent_text_b,
            parent_writing_id, notes, type
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            name,
            description,
            parent_run_id,
            parent_text_a,
            parent_text_b,
            parent_writing_id,
            notes,
            type_,   # NEW
        ),
    )
    conn.commit()
    writing_id = cur.lastrowid
    conn.close()
    return jsonify({"id": writing_id})


@app.patch("/api/writings/<int:writing_id>")
def update_writing(writing_id: int):
    data = request.get_json(silent=True) or {}
    notes = data.get("notes")
    if notes is None:
        return jsonify({"error": "notes required"}), 400
    conn = _get_db()
    cur = conn.cursor()
    cur.execute(
        """
        UPDATE writings
        SET notes = ?, updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """,
        (notes, writing_id),
    )
    conn.commit()
    updated = cur.rowcount
    conn.close()
    if not updated:
        return jsonify({"error": "not found"}), 404
    return jsonify({"updated": writing_id})

@app.delete("/api/writings/<int:writing_id>")
def delete_writing(writing_id: int):
    conn = _get_db()
    cur = conn.cursor()
    cur.execute(
        """
        DELETE FROM writings
        WHERE id = ?
        """,
        (writing_id,),
    )
    conn.commit()
    deleted = cur.rowcount
    conn.close()
    if not deleted:
        return jsonify({"error": "not found"}), 404
    return jsonify({"deleted": writing_id})


@app.get("/api/writings/<int:writing_id>/notes")
def list_notes(writing_id: int):
    conn = _get_db()
    rows = conn.execute(
        """
        SELECT
            n.id,
            n.writing_id,
            n.content,
            n.child_writing_id,
            n.created_at,
            n.updated_at,
            w.type AS child_type
        FROM writing_notes AS n
        LEFT JOIN writings AS w
          ON w.id = n.child_writing_id
        WHERE n.writing_id = ?
        ORDER BY n.id DESC
        """,
        (writing_id,),
    ).fetchall()
    conn.close()
    return jsonify([dict(row) for row in rows])




@app.post("/api/writings/<int:writing_id>/notes")
def create_note(writing_id: int):
    data = request.get_json(silent=True) or {}
    name = (data.get("name") or "").strip()
    description = (data.get("description") or "").strip()
    content = (data.get("content") or "").strip()
    type_value = (data.get("type") or "").strip() or None
    if not (name or description or content):
        return jsonify({"error": "content or name/description required"}), 400

    conn = _get_db()
    cur = conn.cursor()

    # 1) Load parent writing for context
    parent = conn.execute(
        """
        SELECT id, name, description, parent_run_id, parent_text_a, parent_text_b
        FROM writings
        WHERE id = ?
        """,
        (writing_id,),
    ).fetchone()
    if not parent:
        conn.close()
        return jsonify({"error": "parent writing not found"}), 404

    parent_run_id = parent["parent_run_id"]
    parent_text_a = parent["parent_text_a"]
    parent_text_b = parent["parent_text_b"]
    parent_name = parent["name"] or "(untitled)"

    # 2) Normalize name/description for child writing
    if not (name or description):
        lines = content.splitlines()
        name = (lines[0] if lines else "").strip()
        description = "\n".join(lines[1:]).strip()

    if name:
        child_name = name
    elif description:
        child_name = description.splitlines()[0].strip()
    else:
        child_name = f"Note on {parent_name}"

    if name and description:
        note_content = f"{name}\n\n{description}"
    else:
        note_content = name or description or content

    cur.execute(
        """
        INSERT INTO writings (
            name,
            description,
            parent_run_id,
            parent_text_a,
            parent_text_b,
            parent_writing_id,
            notes,
            type
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            child_name,
            description,
            parent_run_id,
            parent_text_a,
            parent_text_b,
            writing_id,
            "",
            type_value,
        ),
    )
    child_writing_id = int(cur.lastrowid)

    # 3) Create note row that points to the child writing
    cur.execute(
        """
        INSERT INTO writing_notes (writing_id, content, child_writing_id)
        VALUES (?, ?, ?)
        """,
        (writing_id, note_content, child_writing_id),
    )
    note_id = int(cur.lastrowid)

    conn.commit()
    conn.close()

    return jsonify({"id": note_id, "child_writing_id": child_writing_id})


@app.patch("/api/notes/<int:note_id>")
def update_note(note_id: int):
    data = request.get_json(silent=True) or {}
    name = data.get("name")
    description = data.get("description")
    content = data.get("content")
    if content is None and name is None and description is None:
        return jsonify({"error": "content or name/description required"}), 400

    if name is not None or description is not None:
        name_value = (name or "").strip()
        description_value = (description or "").strip()
        if not (name_value or description_value):
            return jsonify({"error": "name or description required"}), 400
        if name_value and description_value:
            content_value = f"{name_value}\n\n{description_value}"
        else:
            content_value = name_value or description_value
    else:
        if content is None:
            return jsonify({"error": "content required"}), 400
        content_value = content
    conn = _get_db()
    cur = conn.cursor()
    cur.execute(
        """
        UPDATE writing_notes
        SET content = ?, updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """,
        (content_value, note_id),
    )
    conn.commit()
    updated = cur.rowcount
    conn.close()
    if not updated:
        return jsonify({"error": "not found"}), 404
    return jsonify({"updated": note_id})

@app.delete("/api/notes/<int:note_id>")
def delete_note(note_id: int):
    conn = _get_db()
    cur = conn.cursor()
    cur.execute(
        """
        DELETE FROM writing_notes
        WHERE id = ?
        """,
        (note_id,),
    )
    conn.commit()
    deleted = cur.rowcount
    conn.close()
    if not deleted:
        return jsonify({"error": "not found"}), 404
    return jsonify({"deleted": note_id})

@app.get("/api/writings/<int:writing_id>")
def get_writing(writing_id: int):
    conn = _get_db()
    row = conn.execute(
        """
        SELECT id, name, description, parent_run_id, parent_text_a, parent_text_b,
               parent_writing_id, notes, type, created_at, updated_at
        FROM writings
        WHERE id = ?
        """,
        (writing_id,),
    ).fetchone()
    conn.close()
    if not row:
        return jsonify({"error": "not found"}), 404
    return jsonify(dict(row))


@app.get("/api/writing-types")
def list_writing_types():
    conn = _get_db()
    rows = conn.execute(
        """
        SELECT DISTINCT type
        FROM writings
        WHERE type IS NOT NULL AND type <> ''
        ORDER BY type
        """
    ).fetchall()
    conn.close()
    return jsonify([row["type"] for row in rows])



@app.delete("/api/writings/<int:writing_id>/erase")
def erase_writing(writing_id: int):
    """
    Recursively delete a writing and all its descendants, plus related runs and notes.
    """
    conn = _get_db()
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    # 1) Build the tree of all descendant writings
    cur.execute(
        """
        WITH RECURSIVE tree(id) AS (
            SELECT id FROM writings WHERE id = ?
            UNION ALL
            SELECT w.id
            FROM writings w
            JOIN tree t ON w.parent_writing_id = t.id
        )
        SELECT id FROM tree
        """,
        (writing_id,),
    )
    rows = cur.fetchall()
    if not rows:
        conn.close()
        return jsonify({"error": "not found"}), 404

    ids = [row["id"] for row in rows]
    placeholders = ",".join("?" for _ in ids)

    # 2) Delete notes that either belong to or point to these writings
    cur.execute(
        f"""
        DELETE FROM writing_notes
        WHERE writing_id IN ({placeholders})
           OR child_writing_id IN ({placeholders})
        """,
        ids + ids,
    )

    # 3) Delete runs whose parent_writing_id is any of these writings
    cur.execute(
        f"""
        DELETE FROM runs
        WHERE parent_writing_id IN ({placeholders})
        """,
        ids,
    )

    # 4) Finally, delete the writings themselves
    cur.execute(
        f"""
        DELETE FROM writings
        WHERE id IN ({placeholders})
        """,
        ids,
    )

    conn.commit()
    conn.close()

    return jsonify({"deleted_ids": ids})


@app.get("/api/prompts/input-types")
def list_prompt_input_types():
    conn = _get_db()
    rows = conn.execute(
        """
        SELECT DISTINCT input_type
        FROM prompts
        WHERE input_type IS NOT NULL AND input_type <> ''
        ORDER BY input_type
        """
    ).fetchall()
    conn.close()
    return jsonify([row["input_type"] for row in rows])


@app.get("/api/prompts")
def list_prompts():
    input_type = (request.args.get("input_type") or "").strip() or None
    conn = _get_db()
    if input_type:
        rows = conn.execute(
            """
            SELECT id, input_type, prompt_text, output_type
            FROM prompts
            WHERE input_type = ?
            ORDER BY id
            """,
            (input_type,),
        ).fetchall()
    else:
        rows = conn.execute(
            """
            SELECT id, input_type, prompt_text, output_type
            FROM prompts
            ORDER BY id
            """
        ).fetchall()
    conn.close()
    return jsonify([dict(row) for row in rows])


@app.post("/api/writings/<int:writing_id>/prompt-run")
def run_prompt_for_writing(writing_id: int):
    data = request.get_json(silent=True) or {}
    prompt_id = data.get("prompt_id")
    if prompt_id is not None:
        try:
            prompt_id = int(prompt_id)
        except (TypeError, ValueError):
            return jsonify({"error": "prompt_id must be an integer or null"}), 400

    prompt_text = (data.get("prompt_text") or "").strip()
    output_type = (data.get("output_type") or "").strip() or None

    if not prompt_text:
        return jsonify({"error": "prompt_text is required"}), 400

    # Verify writing exists
    conn = _get_db()
    row = conn.execute(
        "SELECT id FROM writings WHERE id = ?",
        (writing_id,),
    ).fetchone()
    conn.close()
    if not row:
        return jsonify({"error": "writing not found"}), 404

    task_id = _enqueue_prompt_task(
        writing_id=writing_id,
        prompt_id=prompt_id,
        prompt_text=prompt_text,
        output_type=output_type,
    )

    return jsonify({"task_id": task_id, "status": "queued"}), 202


@app.post("/api/prompts")
def create_prompt():
    data = request.get_json(silent=True) or {}
    input_type = (data.get("input_type") or "").strip()
    prompt_text = (data.get("prompt_text") or "").strip()
    output_type = (data.get("output_type") or "").strip()

    if not input_type or not prompt_text or not output_type:
        return jsonify({"error": "input_type, prompt_text, and output_type are required"}), 400

    conn = _get_db()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO prompts (input_type, prompt_text, output_type)
        VALUES (?, ?, ?)
        """,
        (input_type, prompt_text, output_type),
    )
    prompt_id = cur.lastrowid
    row = conn.execute(
        "SELECT id, input_type, prompt_text, output_type FROM prompts WHERE id = ?",
        (prompt_id,),
    ).fetchone()
    conn.commit()
    conn.close()
    return jsonify(dict(row)), 201


@app.put("/api/prompts/<int:prompt_id>")
def update_prompt(prompt_id: int):
    data = request.get_json(silent=True) or {}
    input_type = (data.get("input_type") or "").strip()
    prompt_text = (data.get("prompt_text") or "").strip()
    output_type = (data.get("output_type") or "").strip()

    if not input_type or not prompt_text or not output_type:
        return jsonify({"error": "input_type, prompt_text, and output_type are required"}), 400

    conn = _get_db()
    cur = conn.cursor()
    cur.execute(
        """
        UPDATE prompts
        SET input_type = ?, prompt_text = ?, output_type = ?
        WHERE id = ?
        """,
        (input_type, prompt_text, output_type, prompt_id),
    )
    if cur.rowcount == 0:
        conn.close()
        return jsonify({"error": "prompt not found"}), 404

    row = conn.execute(
        "SELECT id, input_type, prompt_text, output_type FROM prompts WHERE id = ?",
        (prompt_id,),
    ).fetchone()
    conn.commit()
    conn.close()
    return jsonify(dict(row))


@app.delete("/api/prompts/<int:prompt_id>")
def delete_prompt(prompt_id: int):
    conn = _get_db()
    cur = conn.cursor()
    cur.execute("DELETE FROM prompts WHERE id = ?", (prompt_id,))
    if cur.rowcount == 0:
        conn.close()
        return jsonify({"error": "prompt not found"}), 404
    conn.commit()
    conn.close()
    return jsonify({"status": "deleted", "id": prompt_id})


@app.get("/api/gargantua")
def list_gargantua():
    conn = _get_db()
    rows = conn.execute(
        """
        SELECT id, name, text, type, created_at, updated_at
        FROM gargantua
        ORDER BY id DESC
        """
    ).fetchall()
    conn.close()
    return jsonify([dict(row) for row in rows])


@app.post("/api/gargantua")
def create_gargantua():
    data = request.get_json(silent=True) or {}
    name = (data.get("name") or "").strip()
    text = (data.get("text") or "").strip()
    type_ = (data.get("type") or "").strip()

    if not name or not text or not type_:
        return jsonify({"error": "name, text, and type are required"}), 400

    conn = _get_db()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO gargantua (name, text, type)
        VALUES (?, ?, ?)
        """,
        (name, text, type_),
    )
    gargantua_id = cur.lastrowid
    row = conn.execute(
        """
        SELECT id, name, text, type, created_at, updated_at
        FROM gargantua
        WHERE id = ?
        """,
        (gargantua_id,),
    ).fetchone()
    conn.commit()
    conn.close()
    return jsonify(dict(row)), 201


@app.post("/api/writings/<int:writing_id>/gargantua-run")
def run_gargantua_for_writing(writing_id: int):
    data = request.get_json(silent=True) or {}
    gargantua_id = data.get("gargantua_id")

    try:
        gargantua_id = int(gargantua_id)
    except (TypeError, ValueError):
        return jsonify({"error": "gargantua_id must be an integer"}), 400

    conn = _get_db()

    # verify writing exists
    row = conn.execute(
        "SELECT id FROM writings WHERE id = ?",
        (writing_id,),
    ).fetchone()
    if not row:
        conn.close()
        return jsonify({"error": "writing not found"}), 404

    # optional: verify gargantua exists now (errors faster)
    g_row = conn.execute(
        "SELECT id FROM gargantua WHERE id = ?",
        (gargantua_id,),
    ).fetchone()
    if not g_row:
        conn.close()
        return jsonify({"error": "gargantua entry not found"}), 404

    conn.close()

    task_id = _enqueue_gargantua_task(
        writing_id=writing_id,
        gargantua_id=gargantua_id,
    )

    return jsonify({"task_id": task_id, "status": "queued"}), 202



if __name__ == "__main__":
    _ensure_workers()
    app.run(debug=True)
