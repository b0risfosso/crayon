#!/usr/bin/env python3

import os
import queue
import sqlite3
import threading
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from uuid import uuid4

from flask import (
    Flask,
    g,
    request,
    jsonify,
    redirect,
    url_for,
    abort,
    render_template_string,
)

# === CONFIG ===
DATA_ROOT = "/var/www/site/data"
DB_PATH = os.path.join(DATA_ROOT, "dirt.db")
LLM_MODEL = (
    os.environ.get("DECOMP_LLM_MODEL")
    or os.environ.get("LLM_MODEL")
    or "gpt-5-mini-2025-08-07"
)
LLM_SYSTEM_PROMPT = os.environ.get(
    "DECOMP_SYSTEM_PROMPT",
    "You are an analytical model tasked with decomposing ideas and systems.",
)
LLM_LOCK = threading.Lock()
DECOMPOSITION_PROMPT_TEMPLATE = """
You are given a thought that describes a world, system, scenario, or domain.
Your task is to extract and list:
Entities
– Concrete physical objects, materials, organisms, components, infrastructures, tools, or institutional structures that exist in the world implied by the thought.
– These are things you can point to in that reality.
Processes
– Physical, biological, mechanical, chemical, computational, economic, social, or regulatory operations that occur in that world.
– These can be transformations, workflows, interactions, or control loops.
Phenomena
– Observable behaviors, emergent patterns, system-level effects, constraints, failure modes, or opportunity patterns that arise from the entities and processes in that world.
– These describe how the world behaves.
Guidelines
Ground everything in the physical or material implications of the thought.
Avoid abstractions unless they correspond to real structures (e.g., “governance regime,” “feedback loop”).
Keep lists specific and concrete.
No summarization; just extract what exists.
Output Format
Provide the answer in three sections:
Entities:
…
Processes:
…
Phenomena:
…
Input Thought:
{element}

{element_description}
"""


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")

app = Flask(__name__)

try:
    from openai import OpenAI

    _client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))
    _client_err: Optional[Exception] = None
except Exception as e:  # pragma: no cover - optional dependency
    _client = None
    _client_err = e

# === LLM UTILITIES ===

def run_llm(prompt_text: str, *, model: Optional[str] = None):
    """
    Send prompt to configured LLM, returning text plus token accounting.
    """
    if _client is None:
        raise RuntimeError(f"OpenAI client not initialized: {_client_err}")

    resolved_model = model or LLM_MODEL
    with LLM_LOCK:
        start = time.time()
        response = _client.chat.completions.create(
            model=resolved_model,
            messages=[
                {"role": "system", "content": LLM_SYSTEM_PROMPT},
                {"role": "user", "content": prompt_text},
            ],
        )

    text = (response.choices[0].message.content or "").strip()
    usage = getattr(response, "usage", None)
    usage_dict = usage.model_dump() if usage else None
    tokens_in = int((usage_dict or {}).get("prompt_tokens", 0))
    tokens_out = int((usage_dict or {}).get("completion_tokens", 0))
    total_tokens = int((usage_dict or {}).get("total_tokens", tokens_in + tokens_out))
    duration_ms = int((time.time() - start) * 1000)

    return text, usage_dict, tokens_in, tokens_out, total_tokens, duration_ms, resolved_model


def build_decomposition_prompt(element: str, element_description: str) -> str:
    return DECOMPOSITION_PROMPT_TEMPLATE.format(
        element=element.strip() or "Unnamed element",
        element_description=element_description.strip() or "(no description provided)",
    )


# === LLM TASK QUEUE ===

LLM_QUEUE_CONCURRENCY = 2
LLM_TASK_HISTORY_LIMIT = 200
LLM_TASK_QUEUE: "queue.Queue[Dict[str, Any]]" = queue.Queue()
LLM_TASKS: Dict[str, Dict[str, Any]] = {}
LLM_TASK_ORDER: List[str] = []
LLM_TASKS_LOCK = threading.Lock()


def _add_llm_task(task: Dict[str, Any]) -> Dict[str, Any]:
    with LLM_TASKS_LOCK:
        LLM_TASKS[task["task_id"]] = task
        LLM_TASK_ORDER.append(task["task_id"])
        while len(LLM_TASK_ORDER) > LLM_TASK_HISTORY_LIMIT:
            oldest = LLM_TASK_ORDER.pop(0)
            LLM_TASKS.pop(oldest, None)
        return dict(task)


def _update_llm_task(task_id: str, **fields) -> None:
    with LLM_TASKS_LOCK:
        task = LLM_TASKS.get(task_id)
        if not task:
            return
        task.update(fields)


def enqueue_llm_task(job_type: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    task = {
        "task_id": uuid4().hex,
        "job_type": job_type,
        "payload": payload,
        "status": "queued",
        "created_at": utc_now_iso(),
    }
    payload_copy = _add_llm_task(task)
    LLM_TASK_QUEUE.put(task)
    return payload_copy


def _process_llm_task(task: Dict[str, Any]):
    job_type = task.get("job_type")
    if job_type == "box_decomposition":
        return _handle_box_decomposition(task["payload"], request_id=task["task_id"])
    raise ValueError(f"Unknown job_type: {job_type}")


def _handle_box_decomposition(payload: Dict[str, Any], *, request_id: str):
    """
    payload: {box_slug: str}
    """
    slug = payload.get("box_slug")
    if not slug:
        raise ValueError("box_slug is required")

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    try:
        cur.execute("SELECT * FROM boxes WHERE slug = ?", (slug,))
        box = cur.fetchone()
        if box is None:
            raise ValueError("Box not found")

        element = box["title"] or box["slug"]
        description = box["description"] or ""

        prompt_text = build_decomposition_prompt(element, description)

        (
            output_text,
            usage_dict,
            tokens_in,
            tokens_out,
            total_tokens,
            duration_ms,
            resolved_model,
        ) = run_llm(prompt_text)

        # locate root node as parent (optional)
        cur.execute(
            "SELECT id FROM nodes WHERE box_id = ? AND kind = 'box_root' LIMIT 1;",
            (box["id"],),
        )
        root_row = cur.fetchone()
        parent_id = root_row["id"] if root_row else None

        rel_path = f"analysis/decomposition_{datetime.utcnow().strftime('%Y%m%dT%H%M%SZ')}"
        depth = rel_path.count("/") + 1

        cur.execute(
            """
            INSERT INTO nodes (
                box_id, parent_id, name, kind, rel_path,
                mime_type, extension, size_bytes, checksum,
                depth, content, created_at, updated_at
            )
            VALUES (?, ?, ?, 'analysis', ?, NULL, NULL, NULL, NULL,
                    ?, ?, datetime('now'), datetime('now'));
            """,
            (box["id"], parent_id, "decomposition", rel_path, depth, output_text),
        )
        conn.commit()
        node_id = cur.lastrowid

        return {
            "box_slug": slug,
            "node_id": node_id,
            "model": resolved_model,
            "tokens_in": tokens_in,
            "tokens_out": tokens_out,
            "total_tokens": total_tokens,
            "duration_ms": duration_ms,
            "usage_raw": usage_dict,
        }
    finally:
        conn.close()


def llm_worker_loop(worker_id: int):
    while True:
        task = LLM_TASK_QUEUE.get()
        task_id = task["task_id"]
        _update_llm_task(
            task_id,
            status="running",
            started_at=utc_now_iso(),
            worker_id=worker_id,
        )
        try:
            result = _process_llm_task(task)
            _update_llm_task(
                task_id,
                status="done",
                finished_at=utc_now_iso(),
                result=result,
            )
        except Exception as exc:
            _update_llm_task(
                task_id,
                status="error",
                finished_at=utc_now_iso(),
                error=str(exc),
            )
        finally:
            LLM_TASK_QUEUE.task_done()


for worker_index in range(LLM_QUEUE_CONCURRENCY):
    thread = threading.Thread(
        target=llm_worker_loop, args=(worker_index,), daemon=True
    )
    thread.start()
# === DB CONNECTION HANDLING ===

def get_db():
    if "db" not in g:
        os.makedirs(DATA_ROOT, exist_ok=True)
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        g.db = conn
    return g.db

def init_db():
    """Create tables and indexes if they don't exist."""
    conn = get_db()
    cur = conn.cursor()

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS boxes (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            slug       TEXT UNIQUE NOT NULL,
            title      TEXT,
            description TEXT,
            root_path  TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        """
    )

    # Backfill description column if existing DB lacks it
    cur.execute("PRAGMA table_info(boxes);")
    box_columns = [row[1] for row in cur.fetchall()]
    if "description" not in box_columns:
        cur.execute("ALTER TABLE boxes ADD COLUMN description TEXT;")

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS nodes (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            box_id      INTEGER NOT NULL,
            parent_id   INTEGER,
            name        TEXT NOT NULL,
            kind        TEXT NOT NULL,  -- 'box_root', 'chunk', 'particle', 'analysis'
            rel_path    TEXT NOT NULL,
            mime_type   TEXT,
            extension   TEXT,
            size_bytes  INTEGER,
            checksum    TEXT,
            depth       INTEGER NOT NULL,
            content     TEXT,
            created_at  TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at  TEXT NOT NULL DEFAULT (datetime('now')),
            FOREIGN KEY (box_id) REFERENCES boxes(id) ON DELETE CASCADE,
            FOREIGN KEY (parent_id) REFERENCES nodes(id) ON DELETE CASCADE
        );
        """
    )

    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_nodes_box_parent ON nodes(box_id, parent_id);"
    )
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_nodes_box_relpath ON nodes(box_id, rel_path);"
    )
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_nodes_box_kind ON nodes(box_id, kind);"
    )

    # Backfill content column if missing
    cur.execute("PRAGMA table_info(nodes);")
    node_columns = [row[1] for row in cur.fetchall()]
    if "content" not in node_columns:
        cur.execute("ALTER TABLE nodes ADD COLUMN content TEXT;")

    conn.commit()


# Initialize DB once at startup
with app.app_context():
    init_db()




@app.teardown_appcontext
def close_db(exc):
    db = g.pop("db", None)
    if db is not None:
        db.close()



# === CORE LOGIC ===


def has_box_record(slug: str) -> bool:
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT 1 FROM boxes WHERE slug = ?", (slug,))
    return cur.fetchone() is not None



def generate_next_slug():
    conn = get_db()
    cur = conn.cursor()

    cur.execute("""
        SELECT slug FROM boxes
        WHERE slug LIKE 'box_%'
        ORDER BY slug DESC
        LIMIT 1;
    """)
    row = cur.fetchone()

    if not row:
        return "box_001"

    last_slug = row["slug"]  # e.g. "box_014"
    last_num = int(last_slug.split("_")[1])
    next_num = last_num + 1
    return f"box_{next_num:03d}"



def create_box(slug: str, title: Optional[str] = None, description: Optional[str] = None):
    """
    Create a box of dirt:
    - Create /var/www/site/data/boxes/<slug> directory
    - Insert into boxes table
    - Insert root node into nodes table
    """
    conn = get_db()
    cur = conn.cursor()

    # paths
    boxes_root = os.path.join(DATA_ROOT, "boxes")
    box_dir = os.path.join(boxes_root, slug)
    root_path_rel = os.path.join("boxes", slug)  # relative to DATA_ROOT

    if not os.path.exists(DATA_ROOT):
        raise RuntimeError(f"DATA_ROOT does not exist: {DATA_ROOT}")

    os.makedirs(boxes_root, exist_ok=True)

    # check DB slug uniqueness
    if has_box_record(slug):
        raise ValueError(f"Box slug already exists: {slug}")

    # check filesystem directory
    if os.path.exists(box_dir):
        raise ValueError(f"Directory already exists for box: {box_dir}")

    try:
        # create directory on disk
        os.makedirs(box_dir, exist_ok=False)

        # insert box
        cur.execute(
            """
            INSERT INTO boxes (slug, title, description, root_path, created_at, updated_at)
            VALUES (?, ?, ?, ?, datetime('now'), datetime('now'));
            """,
            (slug, title, description, root_path_rel),
        )
        box_id = cur.lastrowid

        # insert root node
        cur.execute(
            """
            INSERT INTO nodes (
                box_id, parent_id, name, kind, rel_path,
                mime_type, extension, size_bytes, checksum,
                depth, content, created_at, updated_at
            )
            VALUES (
                ?, NULL, ?, 'box_root', '',
                NULL, NULL, NULL, NULL,
                0, NULL, datetime('now'), datetime('now')
            );
            """,
            (box_id, slug),
        )

        conn.commit()

        return {
            "box_id": box_id,
            "slug": slug,
            "title": title,
            "description": description,
            "dir": box_dir,
            "root_path": root_path_rel,
        }

    except Exception:
        conn.rollback()
        # best-effort cleanup if directory exists but DB insert failed
        if os.path.exists(box_dir) and not has_box_record(slug):
            try:
                os.rmdir(box_dir)
            except OSError:
                pass
        raise

def get_box_by_slug(slug: str):
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM boxes WHERE slug = ?", (slug,))
    row = cur.fetchone()
    return row
# === ROUTES ===

@app.route("/")
def index():
    return redirect(url_for("list_boxes"))


@app.route("/boxes", methods=["GET"])
def list_boxes():
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM boxes ORDER BY created_at DESC;")
    rows = cur.fetchall()

    return jsonify(
        [
            {
                "id": row["id"],
                "slug": row["slug"],
                "title": row["title"],
                "description": row["description"],
                "root_path": row["root_path"],
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
            }
            for row in rows
        ]
    )



@app.route("/boxes/new", methods=["GET"])
def new_box():
    template = """
    <!doctype html>
    <html>
    <head>
      <title>Create Box of Dirt</title>
    </head>
    <body>
      <h1>Create a Box of Dirt</h1>
      <form method="post" action="{{ url_for('create_box_route') }}">
        <label>Slug (required):<br>
          <input type="text" name="slug" required>
        </label><br><br>
        <label>Title (optional):<br>
          <input type="text" name="title">
        </label><br><br>
        <button type="submit">Create Box</button>
      </form>
      <p><a href="{{ url_for('list_boxes') }}">Back to list</a></p>
    </body>
    </html>
    """
    return render_template_string(template)


@app.route("/boxes", methods=["POST"])
def create_box_route():
    if request.is_json:
        data = request.get_json(silent=True) or {}
        slug = (data.get("slug") or "").strip()
        title = (data.get("title") or "").strip() or None
        description = (data.get("description") or "").strip() or None
    else:
        slug = (request.form.get("slug") or "").strip()
        title = (request.form.get("title") or "").strip() or None
        description = (request.form.get("description") or "").strip() or None

    # If slug empty -> auto-generate
    if not slug:
        slug = generate_next_slug()

    try:
        result = create_box(slug, title, description)
    except ValueError as ve:
        if request.is_json:
            return jsonify({"error": str(ve)}), 400
        abort(400, description=str(ve))
    except Exception as e:
        if request.is_json:
            return jsonify({"error": "internal error", "details": str(e)}), 500
        abort(500, description="internal error")

    if request.is_json:
        return jsonify(result), 201

    return redirect(url_for("list_boxes"))



@app.route("/boxes/<slug>", methods=["GET"])
def get_box(slug):
    row = get_box_by_slug(slug)
    if row is None:
        abort(404, description="Box not found")

    return jsonify(
        {
            "id": row["id"],
            "slug": row["slug"],
            "title": row["title"],
            "description": row["description"],
            "root_path": row["root_path"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }
    )


@app.route("/boxes/<slug>/description", methods=["GET", "POST"])
def box_description(slug):
    box = get_box_by_slug(slug)
    if box is None:
        abort(404, description="Box not found")

    if request.method == "GET":
        return jsonify({"slug": box["slug"], "description": box["description"]})

    data = request.get_json(silent=True) or {}
    if "description" not in data:
        return jsonify({"error": "description is required"}), 400

    description = (data.get("description") or "").strip() or None

    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        "UPDATE boxes SET description = ?, updated_at = datetime('now') WHERE id = ?;",
        (description, box["id"]),
    )
    conn.commit()

    return jsonify({
        "slug": box["slug"],
        "description": description,
        "updated_at": utc_now_iso(),
    })


@app.route("/boxes/<slug>/decompose", methods=["POST"])
def enqueue_decomposition(slug):
    box = get_box_by_slug(slug)
    if box is None:
        abort(404, description="Box not found")

    task = enqueue_llm_task(
        "box_decomposition",
        {"box_slug": box["slug"]},
    )
    task["queue_size"] = LLM_TASK_QUEUE.qsize()
    return jsonify(task), 202


@app.route("/boxes/<slug>/analyses", methods=["GET"])
def list_decompositions(slug):
    box = get_box_by_slug(slug)
    if box is None:
        abort(404, description="Box not found")

    limit = request.args.get("limit", default=20, type=int)
    limit = max(1, min(limit, 200))

    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT id, name, rel_path, content, created_at, updated_at
        FROM nodes
        WHERE box_id = ? AND kind = 'analysis'
        ORDER BY id DESC
        LIMIT ?
        """,
        (box["id"], limit),
    )
    rows = cur.fetchall()
    return jsonify(
        [
            {
                "id": row["id"],
                "name": row["name"],
                "rel_path": row["rel_path"],
                "content": row["content"],
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
            }
            for row in rows
        ]
    )


@app.route("/llm/tasks", methods=["GET"])
def list_llm_tasks():
    limit = request.args.get("limit", default=50, type=int)
    if not isinstance(limit, int):
        return jsonify({"error": "limit must be integer"}), 400
    limit = max(1, min(limit, LLM_TASK_HISTORY_LIMIT))

    with LLM_TASKS_LOCK:
        ordered_ids = list(reversed(LLM_TASK_ORDER))[:limit]
        tasks = [LLM_TASKS[tid] for tid in ordered_ids if tid in LLM_TASKS]
        counts = {"queued": 0, "running": 0, "done": 0, "error": 0}
        for t in LLM_TASKS.values():
            status = t.get("status", "queued")
            counts[status] = counts.get(status, 0) + 1

    return jsonify(
        {
            "queue_size": LLM_TASK_QUEUE.qsize(),
            "tasks": tasks,
            "counts": counts,
        }
    )
