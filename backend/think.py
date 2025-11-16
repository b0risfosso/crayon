# think.py

import json
import os
import sqlite3
from datetime import datetime

from flask import Flask, request, jsonify, render_template, current_app

from prompts import (
    core_thought_architecture_builder_prompt,
    adjacent_thought_generator_prompt,
    core_thought_to_deep_expansion_prompt,
    core_idea_distiller_prompt,
    world_context_integrator_prompt,
    world_to_reality_bridge_generator_prompt,
)

import os
import re
import json
from datetime import datetime, timezone

from openai import OpenAI
from db_shared import init_usage_db, log_usage, connect, USAGE_DB


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
# FLASK APP
# =====================

app = Flask(__name__)


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


def init_think_db():
    conn = get_db()
    cur = conn.cursor()
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



# =====================
# APP STARTUP
# =====================

if __name__ == "__main__":
    init_think_db()
    # You can change host/port as needed
    app.run(host="0.0.0.0", port=8081, debug=True)
