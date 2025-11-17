# think.py

import json
import os
import sqlite3
from datetime import datetime, timezone
import threading
import time

from flask import Flask, request, jsonify, render_template


from prompts import (
    core_thought_architecture_builder_prompt,
    adjacent_thought_generator_prompt,
    core_thought_to_deep_expansion_prompt,
    core_idea_distiller_prompt,
    world_context_integrator_prompt,
    world_to_reality_bridge_generator_prompt,
)

import re

from openai import OpenAI
from db_shared import init_usage_db, log_usage, connect, USAGE_DB, PICTURE_DB, _iso_now


# -----------------------------
# Model + limits (mirrors jid)
# -----------------------------

DEFAULT_MODEL = os.getenv("THINK_LLM_MODEL") or os.getenv("JID_LLM_MODEL", "gpt-5")
DAILY_MAX_TOKENS_LIMIT = int(os.getenv("DAILY_MAX_TOKENS_LIMIT", "10000000"))

# Optional tokenizer
try:
    import tiktoken  # type: ignore
    _HAS_TIKTOKEN = True
except Exception:
    _HAS_TIKTOKEN = False

client = OpenAI()
init_usage_db()  # make sure llm_usage.db and tables exist


# -----------------------------
# Token + usage utilities
# -----------------------------

def _iso_today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def estimate_tokens(text: str, model: str = DEFAULT_MODEL) -> int:
    """
    Rough token estimate. If tiktoken is available, use it. Otherwise, heuristic.
    """
    if _HAS_TIKTOKEN:
        try:
            enc = tiktoken.get_encoding("cl100k_base")
            return len(enc.encode(text))
        except Exception:
            pass
    # Heuristic: ~4 chars per token
    return max(1, int(len(text) / 4))


def get_today_model_tokens(model: str) -> int:
    """
    Reads totals_daily for today+model from llm_usage.db (same as jid).
    """
    day = _iso_today()
    conn = connect(USAGE_DB)
    try:
        row = conn.execute(
            "SELECT total_tokens FROM totals_daily WHERE day=? AND model=?",
            (day, model),
        ).fetchone()
        return int(row[0]) if row else 0
    finally:
        conn.close()


def _usage_from_resp(resp) -> dict:
    """
    Mirrors jid/crayon: accept both {prompt,input}_tokens and {completion,output}_tokens.
    Returns {'input': int, 'output': int, 'total': int}
    """
    u = getattr(resp, "usage", None)

    def get(k: str):
        if u is None:
            return None
        if isinstance(u, dict):
            return u.get(k)
        return getattr(u, k, None)

    inp = get("prompt_tokens") or get("input_tokens") or 0
    outp = get("completion_tokens") or get("output_tokens") or 0
    tot = get("total_tokens") or (int(inp) + int(outp))
    return {"input": int(inp), "output": int(outp), "total": int(tot)}


# System message for JSON strictness (same spirit as jid)
SYSTEM_MSG_JSON = (
    "You are a precise JSON generator. Always return STRICT JSON with the exact keys requested. "
    "Do NOT include Markdown or explanations."
)






# =====================
# DB HELPERS
# =====================

def get_think_db_path():
    """
    Adjust this to match your existing data directory convention.

    If your crayon stack already sets DATA_DIR (env or config),
    you can reuse that here by exporting it before running think.py.
    """
    base_dir = os.environ.get("DATA_DIR")
    if not base_dir:
        base_dir = os.path.join(os.path.dirname(__file__), "/var/www/site/data")

    os.makedirs(base_dir, exist_ok=True)
    return os.path.join(base_dir, "think.db")


def get_db():
    db_path = get_think_db_path()
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn

def _get_picture_db_path() -> str:
    """
    Use the same convention as scribble.py for picture.db.
    """
    return os.getenv("PICTURE_DB", PICTURE_DB)


def get_picture_db():
    db_path = _get_picture_db_path()
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn

def init_think_db():
    conn = get_db()
    cur = conn.cursor()

    # Existing pipeline table
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS thought_pipelines (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            thought TEXT NOT NULL,
            adjacent_thoughts_json TEXT,
            core_thoughts_json TEXT,
            expanded_text TEXT,
            core_ideas_json TEXT,
            world_context_text TEXT,
            bridges_text TEXT,
            created_at TEXT NOT NULL
        )
        """
    )

    # New queue table for "core ideas + world" jobs
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS core_world_queue (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            thought_id INTEGER,
            thought_text TEXT NOT NULL,
            email TEXT,
            status TEXT NOT NULL DEFAULT 'pending',  -- pending | running | completed | error
            error TEXT,
            core_idea_id INTEGER,
            world_context_id INTEGER,
            created_at TEXT NOT NULL,
            started_at TEXT,
            finished_at TEXT
        )
        """
    )

    # On restart, re-queue any jobs that were "running"
    cur.execute(
        """
        UPDATE core_world_queue
        SET status='pending', started_at=NULL
        WHERE status = 'running'
        """
    )

    conn.commit()
    conn.close()




# =====================
# LLM CALL SHIMS
# =====================

def run_json_prompt(
    prompt_template: str,
    user_thought: str,
    *,
    model: str | None = None,
    endpoint_name: str = "/think/json_prompt",
    email: str | None = None,
) -> dict:
    """
    Take a prompt template and a user thought, call the model expecting JSON, and
    return a Python dict. Mirrors jid-style token accounting + logging.
    """
    model = (model or DEFAULT_MODEL).strip()

    # 1) Daily limit pre-check
    today_tokens = get_today_model_tokens(model)
    if today_tokens >= DAILY_MAX_TOKENS_LIMIT:
        raise RuntimeError(
            f"Daily token limit reached for {model}: {today_tokens} / {DAILY_MAX_TOKENS_LIMIT}"
        )

    # 2) Build user message
    # The template describes the task + JSON schema; we append the actual thought.
    user_msg = prompt_template.rstrip() + "\n\nThought:\n" + user_thought.strip()

    # 3) Estimate tokens-in before call (for early cut-off)
    est_in = estimate_tokens(user_msg, model=model)
    if today_tokens + est_in >= DAILY_MAX_TOKENS_LIMIT:
        raise RuntimeError(
            f"Daily token limit reached for {model} (estimate): "
            f"{today_tokens + est_in} / {DAILY_MAX_TOKENS_LIMIT}"
        )

    usage_in = 0
    usage_out = 0
    request_id = None

    try:
        # 4) LLM call
        resp = client.responses.create(
            model=model,
            input=[
                {"role": "system", "content": SYSTEM_MSG_JSON},
                {"role": "user", "content": user_msg},
            ],
        )

        # 5) Usage extraction
        u = _usage_from_resp(resp)
        usage_in = u.get("input", est_in)
        usage_out = u.get("output", 0)

        # 6) Extract raw text and parse JSON
        raw_text = (
            getattr(resp, "output_text", None)
            or getattr(resp, "text", None)
            or ""
        )
        if not raw_text:
            # Fallback to responses-style output structure
            try:
                out0 = resp.output[0].content[0]
                raw_text = getattr(out0, "text", None) or getattr(out0, "content", None) or ""
            except Exception:
                raw_text = ""

        # Strip ```json fences if present
        m = re.search(r"```(?:json)?\s*(\{.*\})\s*```", raw_text, re.S)
        raw_json = m.group(1) if m else raw_text

        parsed = json.loads(raw_json)
        if not isinstance(parsed, dict):
            raise ValueError("JSON root is not an object; got: " + type(parsed).__name__)

        # 7) Log and return
        log_usage(
            app="think",
            model=model,
            tokens_in=usage_in,
            tokens_out=usage_out,
            endpoint=endpoint_name,
            email=email,
            request_id=request_id,
            duration_ms=0,
            cost_usd=0.0,
            meta={"purpose": "json_prompt"},
        )
        return parsed

    except Exception:
        # Still log what we know
        try:
            log_usage(
                app="think",
                model=model,
                tokens_in=usage_in or est_in,
                tokens_out=usage_out,
                endpoint=endpoint_name,
                email=email,
                request_id=request_id,
                duration_ms=0,
                cost_usd=0.0,
                meta={"purpose": "json_prompt_error"},
            )
        except Exception:
            pass
        raise



def run_text_prompt(
    prompt_text: str,
    context_text: str = "",
    *,
    model: str | None = None,
    endpoint_name: str = "/think/text_prompt",
    email: str | None = None,
    meta_purpose: str = "text_prompt",
) -> str:
    """
    Call the model with a completed instruction string and return text.
    Token counting + logging follow jid's pattern.
    `context_text` is only used for logging/meta if you want to track origin.
    """
    model = (model or DEFAULT_MODEL).strip()

    # 1) Daily limit pre-check
    today_tokens = get_today_model_tokens(model)
    if today_tokens >= DAILY_MAX_TOKENS_LIMIT:
        raise RuntimeError(
            f"Daily token limit reached for {model}: {today_tokens}/{DAILY_MAX_TOKENS_LIMIT}"
        )

    # 2) Estimate tokens-in before call
    est_in = estimate_tokens(prompt_text, model=model)

    if today_tokens + est_in >= DAILY_MAX_TOKENS_LIMIT:
        raise RuntimeError(
            f"Daily token limit reached for {model} (estimate): "
            f"{today_tokens + est_in}/{DAILY_MAX_TOKENS_LIMIT}"
        )

    usage_in = 0
    usage_out = 0
    request_id = None

    try:
        # 3) LLM call (plain text)
        resp = client.responses.create(
            model=model,
            input=prompt_text,
        )

        # 4) Usage extraction
        u = _usage_from_resp(resp)
        usage_in = u.get("input", est_in)
        usage_out = u.get("output", 0)

        # 5) Extract text
        raw_text = (
            getattr(resp, "output_text", None)
            or getattr(resp, "text", None)
            or ""
        )
        if not raw_text:
            try:
                out0 = resp.output[0].content[0]
                raw_text = getattr(out0, "text", None) or getattr(out0, "content", None) or ""
            except Exception:
                raw_text = ""

        # 6) Log and return
        log_usage(
            app="think",
            model=model,
            tokens_in=usage_in,
            tokens_out=usage_out,
            endpoint=endpoint_name,
            email=email,
            request_id=request_id,
            duration_ms=0,
            cost_usd=0.0,
            meta={"purpose": meta_purpose, "context_preview": context_text[:200]},
        )

        return raw_text

    except Exception:
        # Log approximate usage even on failure
        try:
            log_usage(
                app="think",
                model=model,
                tokens_in=usage_in or est_in,
                tokens_out=usage_out,
                endpoint=endpoint_name,
                email=email,
                request_id=request_id,
                duration_ms=0,
                cost_usd=0.0,
                meta={"purpose": meta_purpose + "_error", "context_preview": context_text[:200]},
            )
        except Exception:
            pass
        raise


# =====================
# CORE IDEAS + WORLD PIPELINE HELPERS
# =====================

def generate_core_ideas_and_world(thought: str) -> tuple[str, str]:
    """
    Given a single thought text, generate:
      - core_ideas_text  (TEXT BLOCK)
      - world_context    (TEXT BLOCK)

    This mirrors the pipeline: thought -> core ideas (text) -> world context.
    """
    thought = (thought or "").strip()
    if not thought:
        raise ValueError("Thought text is empty")

    # 1) Core ideas (TEXT BLOCK) directly from the thought
    prompt_text_core_ideas = core_idea_distiller_prompt.rstrip() + "\n\nThought:\n" + thought
    core_ideas_text = run_text_prompt(
        prompt_text_core_ideas,
        context_text=thought,
        endpoint_name="/think/core_ideas",
        meta_purpose="core_idea_distiller_from_thought",
    )
    core_ideas_text = (core_ideas_text or "").strip()
    if not core_ideas_text:
        raise RuntimeError("Core ideas generation returned empty text")

    # 2) World context from that core_ideas_text
    prompt_text_world = world_context_integrator_prompt + "\n\nCore ideas (text block):\n" + core_ideas_text
    world_context = run_text_prompt(
        prompt_text_world,
        context_text=core_ideas_text,
        endpoint_name="/think/world_context",
        meta_purpose="world_context_from_block",
    )
    world_context = (world_context or "").strip()
    if not world_context:
        raise RuntimeError("World context generation returned empty text")

    return core_ideas_text, world_context


def save_core_ideas_and_world_to_picture(
    thought_id: int | None,
    core_ideas_text: str,
    world_context: str,
    email: str | None = None,
) -> tuple[int | None, int | None]:
    """
    Insert:
      - a core_ideas row for the given thought
      - a world_context row linked to that core_idea

    Returns (core_idea_id, world_context_id).

    If thought_id is None, nothing is saved and (None, None) is returned.
    """
    if not thought_id:
        return None, None

    db = get_picture_db()
    try:
        cur = db.cursor()

        # Ensure the thought exists so we don't attach to nothing
        cur.execute("SELECT 1 FROM thoughts WHERE id = ?", (thought_id,))
        if cur.fetchone() is None:
            raise ValueError(f"Thought id {thought_id} not found in picture.db")

        source = f"thought:{thought_id}"
        ts = _iso_now()
        email_val = (email or "").strip() or None
        metadata_str = None

        # Insert into core_ideas (same schema/columns as scribble.py)
        cur.execute(
            """
            INSERT INTO core_ideas (
                source,
                core_idea,
                email,
                origin,
                metadata,
                created_at,
                updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (source, core_ideas_text, email_val, "think_pipeline", metadata_str, ts, ts),
        )
        core_idea_id = cur.lastrowid

        # Insert into world_contexts (triggers handle created_at/updated_at)
        cur.execute(
            """
            INSERT INTO world_contexts (core_idea_id, text, email, metadata)
            VALUES (?, ?, ?, ?)
            """,
            (core_idea_id, world_context, email_val, metadata_str),
        )
        world_context_id = cur.lastrowid

        db.commit()
        return core_idea_id, world_context_id
    finally:
        db.close()


# =====================
# QUEUE + WORKERS
# =====================

CORE_WORLD_WORKER_CONCURRENCY = int(os.getenv("CORE_WORLD_WORKER_CONCURRENCY", "2"))
CORE_WORLD_QUEUE_POLL_INTERVAL = float(os.getenv("CORE_WORLD_QUEUE_POLL_INTERVAL", "2.0"))

_queue_lock = threading.Lock()
_workers_started = False


def enqueue_core_world_job(thought_id, thought_text: str, email: str | None) -> int:
    """
    Insert a new row into core_world_queue and return its ID.
    """
    conn = get_db()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO core_world_queue (
                thought_id,
                thought_text,
                email,
                status,
                created_at
            )
            VALUES (?, ?, ?, ?, ?)
            """,
            (thought_id, thought_text, (email or "").strip() or None, "pending", datetime.utcnow().isoformat()),
        )
        job_id = cur.lastrowid
        conn.commit()
        return job_id
    finally:
        conn.close()


def _claim_next_job() -> int | None:
    """
    Atomically fetch the next pending job and mark it 'running'.
    Returns the job_id or None if no pending job exists.
    """
    conn = get_db()
    try:
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        with _queue_lock:
            row = cur.execute(
                """
                SELECT id FROM core_world_queue
                WHERE status = 'pending'
                ORDER BY created_at ASC
                LIMIT 1
                """
            ).fetchone()
            if not row:
                return None

            job_id = row["id"]
            cur.execute(
                """
                UPDATE core_world_queue
                SET status = 'running', started_at = ?
                WHERE id = ?
                """,
                (datetime.utcnow().isoformat(), job_id),
            )
            conn.commit()
            return job_id
    finally:
        conn.close()


def process_core_world_job(job_id: int) -> None:
    """
    Worker-side processing of a single job:
      1. Read job
      2. Generate core ideas + world
      3. Save into picture.db
      4. Mark completed or error
    """
    # Read job row
    conn = get_db()
    try:
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        row = cur.execute(
            "SELECT * FROM core_world_queue WHERE id = ?",
            (job_id,),
        ).fetchone()
        if not row:
            return  # job might have been deleted

        thought_text = (row["thought_text"] or "").strip()
        thought_id = row["thought_id"]
        email = row["email"]
    finally:
        conn.close()

    try:
        core_ideas_text, world_context = generate_core_ideas_and_world(thought_text)
        core_idea_id, world_context_id = save_core_ideas_and_world_to_picture(
            thought_id,
            core_ideas_text,
            world_context,
            email=email,
        )

        # Mark completed
        conn = get_db()
        try:
            cur = conn.cursor()
            cur.execute(
                """
                UPDATE core_world_queue
                SET status = 'completed',
                    finished_at = ?,
                    error = NULL,
                    core_idea_id = ?,
                    world_context_id = ?
                WHERE id = ?
                """,
                (datetime.utcnow().isoformat(), core_idea_id, world_context_id, job_id),
            )
            conn.commit()
        finally:
            conn.close()
    except Exception as e:
        conn = get_db()
        try:
            cur = conn.cursor()
            cur.execute(
                """
                UPDATE core_world_queue
                SET status = 'error',
                    finished_at = ?,
                    error = ?
                WHERE id = ?
                """,
                (datetime.utcnow().isoformat(), str(e), job_id),
            )
            conn.commit()
        finally:
            conn.close()


def _core_world_worker_loop(worker_index: int) -> None:
    """
    Background worker loop: keep pulling pending jobs and processing them.
    """
    while True:
        job_id = _claim_next_job()
        if job_id is None:
            time.sleep(CORE_WORLD_QUEUE_POLL_INTERVAL)
            continue

        try:
            process_core_world_job(job_id)
        except Exception:
            # Avoid worker death on unexpected errors
            try:
                app.logger.exception(
                    "core_world worker %s crashed on job %s", worker_index, job_id
                )
            except Exception:
                # As a last resort, don't let logging kill the worker
                print(f"[core_world_worker_{worker_index}] crash on job {job_id}", flush=True)



def start_core_world_workers() -> None:
    """
    Start the background worker pool (concurrency = 2 by default).
    Safe to call multiple times; workers will only start once per process.
    """
    global _workers_started
    if _workers_started:
        return
    _workers_started = True

    for i in range(CORE_WORLD_WORKER_CONCURRENCY):
        t = threading.Thread(
            target=_core_world_worker_loop,
            args=(i,),
            daemon=True,
            name=f"core_world_worker_{i}",
        )
        t.start()


# =====================
# FLASK APP
# =====================

app = Flask(__name__)

init_think_db()
start_core_world_workers()

# =====================
# ROUTES
# =====================


@app.route("/think/adjacent", methods=["POST"])
def generate_adjacent_thoughts():
    data = request.get_json(force=True)
    thought = (data.get("thought") or "").strip()
    if not thought:
        return jsonify({"error": "Missing 'thought'"}), 400

    result = run_json_prompt(adjacent_thought_generator_prompt, thought)
    return jsonify(result)


@app.route("/think/core_thoughts", methods=["POST"])
def generate_core_thoughts():
    data = request.get_json(force=True)
    thought = (data.get("thought") or "").strip()
    if not thought:
        return jsonify({"error": "Missing 'thought'"}), 400

    # Build the full prompt: instructions + the actual thought
    prompt_text = core_thought_architecture_builder_prompt.rstrip() + "\n\nThought:\n" + thought

    core_thoughts_text = run_text_prompt(
        prompt_text,
        context_text=thought,
        endpoint_name="/think/core_thoughts",
        meta_purpose="core_thought_architecture",
    )

    # Return a simple wrapper so front-ends can display it
    return jsonify({
        "thought": thought,
        "core_thoughts_text": core_thoughts_text,
    })


@app.route("/think/expand_core_thought", methods=["POST"])
def expand_core_thought():
    data = request.get_json(force=True)
    core_thought = (data.get("core_thought") or "").strip()
    if not core_thought:
        return jsonify({"error": "Missing 'core_thought'"}), 400

    # Insert core thought into template
    prompt_text = core_thought_to_deep_expansion_prompt.replace("<CORE_THOUGHT_HERE>", core_thought)
    expansion = run_text_prompt(prompt_text, core_thought)

    return jsonify({
        "core_thought": core_thought,
        "expansion": expansion,
    })


@app.route("/think/core_ideas", methods=["POST"])
def distill_core_ideas():
    data = request.get_json(force=True)
    source_text = (data.get("thought") or "").strip()  # can be original thought OR core-thought block
    if not source_text:
        return jsonify({"error": "Missing 'thought'"}), 400

    prompt_text = core_idea_distiller_prompt.rstrip() + "\n\nThought:\n" + source_text

    core_ideas_text = run_text_prompt(
        prompt_text,
        context_text=source_text,
        endpoint_name="/think/core_ideas",
        meta_purpose="core_idea_distiller",
    )

    return jsonify({
        "thought": source_text,
        "core_ideas_text": core_ideas_text,
    })


@app.route("/think/world_context", methods=["POST"])
def build_world_context():
    data = request.get_json(force=True)

    # Preferred: a single text block
    core_ideas_text = (data.get("core_ideas_text") or "").strip()

    # Backward-compatible: list of core_ideas with "text"
    if not core_ideas_text and isinstance(data.get("core_ideas"), list):
        lines = []
        for idx, item in enumerate(data["core_ideas"], start=1):
            t = (item.get("text") or "").strip()
            if t:
                lines.append(f"{idx}. {t}")
        core_ideas_text = "\n".join(lines).strip()

    if not core_ideas_text:
        return jsonify({
            "error": "Missing 'core_ideas_text' or non-empty 'core_ideas' array"
        }), 400

    prompt_text = world_context_integrator_prompt + "\n\nCore ideas (text block):\n" + core_ideas_text

    world_context = run_text_prompt(
        prompt_text,
        context_text=core_ideas_text,
        endpoint_name="/think/world_context",
        meta_purpose="world_context_from_block",
    )

    return jsonify({
        "core_ideas_text": core_ideas_text,
        "world_context": world_context,
    })



@app.route("/think/bridges", methods=["POST"])
def generate_bridges():
    data = request.get_json(force=True)
    world_context = (data.get("world_context") or "").strip()
    if not world_context:
        return jsonify({"error": "Missing 'world_context'"}), 400

    prompt_text = world_to_reality_bridge_generator_prompt + "\n\nWorld context:\n" + world_context
    bridges_text = run_text_prompt(prompt_text, world_context)

    return jsonify({
        "world_context": world_context,
        "bridges": bridges_text,
    })


@app.route("/think/pipeline", methods=["POST"])
def run_full_pipeline():
    data = request.get_json(force=True)
    thought = (data.get("thought") or "").strip()
    if not thought:
        return jsonify({"error": "Missing 'thought'"}), 400

    # 1. Adjacent thoughts (JSON)
    adjacent = run_json_prompt(
        adjacent_thought_generator_prompt,
        thought,
        endpoint_name="/think/adjacent",
        email=None,
    )

    # 2. Core thoughts (TEXT BLOCK)
    prompt_text_core = core_thought_architecture_builder_prompt.rstrip() + "\n\nThought:\n" + thought
    core_thoughts_text = run_text_prompt(
        prompt_text_core,
        context_text=thought,
        endpoint_name="/think/core_thoughts",
        meta_purpose="core_thought_architecture",
    )

    # 3. Core ideas (TEXT BLOCK) — fed directly from core_thoughts_text
    prompt_text_core_ideas = core_idea_distiller_prompt.rstrip() + "\n\nThought:\n" + core_thoughts_text
    core_ideas_text = run_text_prompt(
        prompt_text_core_ideas,
        context_text=core_thoughts_text,
        endpoint_name="/think/core_ideas",
        meta_purpose="core_idea_distiller",
    )

    # 4. World context from core_ideas_text
    prompt_text_world = world_context_integrator_prompt + "\n\nCore ideas (text block):\n" + core_ideas_text
    world_context = run_text_prompt(
        prompt_text_world,
        context_text=core_ideas_text,
        endpoint_name="/think/world_context",
        meta_purpose="world_context_from_block",
    )

    # 5. Bridges from world context
    prompt_text_bridges = world_to_reality_bridge_generator_prompt + "\n\nWorld context:\n" + world_context
    bridges_text = run_text_prompt(
        prompt_text_bridges,
        context_text=world_context,
        endpoint_name="/think/bridges",
        meta_purpose="bridges_from_world_context",
    )

    # Persist pipeline; keep expanded_text column but store empty string / None
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO thought_pipelines
        (thought, adjacent_thoughts_json, core_thoughts_json, expanded_text,
         core_ideas_json, world_context_text, bridges_text, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            thought,
            json.dumps(adjacent),
            core_thoughts_text,   # text block
            "",                   # expanded_text no longer used
            core_ideas_text,      # text block
            world_context,
            bridges_text,
            datetime.utcnow().isoformat(),
        ),
    )
    pipeline_id = cur.lastrowid
    conn.commit()
    conn.close()

    return jsonify({
        "id": pipeline_id,
        "thought": thought,
        "adjacent": adjacent,
        "core_thoughts_text": core_thoughts_text,
        "core_ideas_text": core_ideas_text,
        "world_context": world_context,
        "bridges": bridges_text,
    })

@app.route("/think/queue_core_world", methods=["POST"])
def queue_core_ideas_and_world():
    """
    Enqueue a 'core ideas + world' job and return immediately.

    JSON payload:
      {
        "thought_id": 123,          # optional but strongly recommended (picture.db thoughts.id)
        "thought": "text of thought",  # required
        "email": "optional@email"
      }

    Response (202):
      {
        "task_id": <job_id>,
        "status": "pending"
      }
    """
    data = request.get_json(force=True) or {}
    thought_text = (data.get("thought") or "").strip()
    if not thought_text:
        return jsonify({"error": "Missing 'thought'"}), 400

    thought_id_raw = data.get("thought_id")
    thought_id = None
    if thought_id_raw is not None:
        try:
            thought_id = int(thought_id_raw)
        except Exception:
            return jsonify({"error": "Invalid 'thought_id'"}), 400

    email = (data.get("email") or "").strip() or None

    job_id = enqueue_core_world_job(thought_id, thought_text, email)

    # Workers will pick this up asynchronously
    return jsonify({"task_id": job_id, "status": "pending"}), 202

@app.get("/think/core_world_status/<int:job_id>")
def core_world_status(job_id: int):
    conn = get_db()
    try:
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        row = cur.execute(
            """
            SELECT id, thought_id, status, error,
                   core_idea_id, world_context_id,
                   created_at, started_at, finished_at
            FROM core_world_queue
            WHERE id = ?
            """,
            (job_id,),
        ).fetchone()
        if not row:
            return jsonify({"error": "job not found"}), 404
        return jsonify(dict(row)), 200
    finally:
        conn.close()

