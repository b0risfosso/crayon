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

def run_json_prompt(prompt_template: str, user_thought: str) -> dict:
    """
    TODO: Wire this into your existing LLM helper.

    Expected behavior:
    - Take `prompt_template` and the `user_thought`
    - Call your model so that it outputs JSON
    - Parse JSON to Python dict and return it

    Example (pseudo):

    from your_llm_helper import call_model_json

    return call_model_json(prompt_template, {"thought": user_thought})
    """
    raise NotImplementedError("Implement run_json_prompt using your LLM stack.")


def run_text_prompt(prompt_text: str, context_text: str) -> str:
    """
    TODO: Wire this into your existing LLM helper.

    Expected behavior:
    - `prompt_text` is a completed instruction (already contains the core thought, etc.)
    - `context_text` is just for logging/tracing if you like
    - Return the model's text output as a string
    """
    raise NotImplementedError("Implement run_text_prompt using your LLM stack.")


# =====================
# ROUTES
# =====================

@app.route("/")
def index():
    # Assumes templates/think.html
    return render_template("think.html")


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

    result = run_json_prompt(core_thought_architecture_builder_prompt, thought)
    return jsonify(result)


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
    thought_text = (data.get("thought") or "").strip()
    if not thought_text:
        return jsonify({"error": "Missing 'thought'"}), 400

    result = run_json_prompt(core_idea_distiller_prompt, thought_text)
    return jsonify(result)


@app.route("/think/world_context", methods=["POST"])
def generate_world_context():
    data = request.get_json(force=True)
    core_ideas = data.get("core_ideas", [])
    if not isinstance(core_ideas, list) or not core_ideas:
        return jsonify({"error": "Missing 'core_ideas' list"}), 400

    core_ideas_text = "\n".join(
        f"- {ci.get('text', '')}"
        for ci in core_ideas
        if ci.get("text")
    )

    prompt_text = world_context_integrator_prompt + "\n\nCore ideas:\n" + core_ideas_text
    world_context = run_text_prompt(prompt_text, core_ideas_text)

    return jsonify({
        "core_ideas": core_ideas,
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
    """
    Full pipeline:
    thought
      -> adjacent thoughts
      -> core thoughts
      -> expand first core thought
      -> core ideas (from expansion)
      -> world context
      -> bridges

    This endpoint also stores the pipeline in SQLite.
    """
    data = request.get_json(force=True)
    thought = (data.get("thought") or "").strip()
    if not thought:
        return jsonify({"error": "Missing 'thought'"}), 400

    # 1. Adjacent thoughts
    adjacent = run_json_prompt(adjacent_thought_generator_prompt, thought)

    # 2. Core thought architecture
    core_thoughts = run_json_prompt(core_thought_architecture_builder_prompt, thought)
    core_thought_list = core_thoughts.get("core_thoughts", [])
    core_to_expand = core_thought_list[0]["text"] if core_thought_list else thought

    # 3. Deep expansion
    prompt_text_expansion = core_thought_to_deep_expansion_prompt.replace("<CORE_THOUGHT_HERE>", core_to_expand)
    expansion = run_text_prompt(prompt_text_expansion, core_to_expand)

    # 4. Core ideas from expansion
    core_ideas = run_json_prompt(core_idea_distiller_prompt, expansion)
    core_ideas_list = core_ideas.get("core_ideas", [])

    # 5. World context from core ideas
    core_ideas_text = "\n".join(
        f"- {ci.get('text', '')}"
        for ci in core_ideas_list
        if ci.get("text")
    )
    prompt_text_world = world_context_integrator_prompt + "\n\nCore ideas:\n" + core_ideas_text
    world_context = run_text_prompt(prompt_text_world, core_ideas_text)

    # 6. Bridges from world context
    prompt_text_bridges = world_to_reality_bridge_generator_prompt + "\n\nWorld context:\n" + world_context
    bridges_text = run_text_prompt(prompt_text_bridges, world_context)

    # Store pipeline
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
            json.dumps(core_thoughts),
            expansion,
            json.dumps(core_ideas),
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
        "core_thoughts": core_thoughts,
        "core_thought_expanded": {
            "core_thought": core_to_expand,
            "expansion": expansion,
        },
        "core_ideas": core_ideas,
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
