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
from prompts2 import (
    build_abstractions_metaphors_prompt,
    build_decomposition_prompt,
    build_datasets_prompt,
    build_codebases_prompt,
    build_hardware_builds_prompt,
    build_experiments_prompt,
    build_intelligence_prompt,
    build_control_levers_prompt,
    build_companies_prompt,
    build_theories_prompt,
    build_historical_context_prompt,
    build_value_exchange_prompt,
    build_value_addition_prompt,
    build_scientific_substructure_prompt,
    build_spirit_soul_emotion_prompt,
    build_environment_prompt,
    build_imaginative_windows_prompt,
    build_musical_composition_prompt,
    build_infinity_prompt,
    build_computation_layer_prompt,
    build_computation_rules_prompt,
    build_computation_programs_prompt,
    build_computation_universe_prompt,
    build_computation_causal_prompt,
    build_state_transition_prompt,
    build_computation_primitives_prompt,
    build_computation_primitives_alt_prompt,
    build_computation_sublayers_prompt,
    build_computation_emergence_prompt,
    build_substrate_prompt,
    build_scaffolding_prompt,
    build_constraints_prompt,
    build_physical_substrate_prompt,
    build_physical_states_prompt,
    build_foundational_physics_prompt,
    build_tangibility_conservation_prompt,
    build_physical_subdomains_prompt,
    build_emergence_from_physics_prompt,
    build_observer_independent_prompt,
    build_sensory_profile_prompt,
    build_real_world_behavior_prompt,
    build_scenario_landscape_prompt,
    build_construction_reconstruction_prompt,
    build_thought_to_reality_prompt,
    build_processes_forces_interactions_prompt,
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


# === LLM TASK QUEUE ===

LLM_QUEUE_CONCURRENCY = 2
LLM_TASK_HISTORY_LIMIT = 1000
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
    if job_type == "box_abstractions_metaphors":
        return _handle_box_abstractions_metaphors(task["payload"], request_id=task["task_id"])
    if job_type == "box_processes_forces_interactions":
        return _handle_box_processes_forces_interactions(task["payload"], request_id=task["task_id"])
    if job_type == "box_datasets":
        return _handle_box_datasets(task["payload"], request_id=task["task_id"])
    if job_type == "box_codebases":
        return _handle_box_codebases(task["payload"], request_id=task["task_id"])
    if job_type == "box_hardware_builds":
        return _handle_box_hardware_builds(task["payload"], request_id=task["task_id"])
    if job_type == "box_experiments":
        return _handle_box_experiments(task["payload"], request_id=task["task_id"])
    if job_type == "box_intelligence":
        return _handle_box_intelligence(task["payload"], request_id=task["task_id"])
    if job_type == "box_control_levers":
        return _handle_box_control_levers(task["payload"], request_id=task["task_id"])
    if job_type == "box_companies":
        return _handle_box_companies(task["payload"], request_id=task["task_id"])
    if job_type == "box_theories":
        return _handle_box_theories(task["payload"], request_id=task["task_id"])
    if job_type == "box_historical_context":
        return _handle_box_historical_context(task["payload"], request_id=task["task_id"])
    if job_type == "box_value_exchange":
        return _handle_box_value_exchange(task["payload"], request_id=task["task_id"])
    if job_type == "box_value_addition":
        return _handle_box_value_addition(task["payload"], request_id=task["task_id"])
    if job_type == "box_scientific_substructure":
        return _handle_box_scientific_substructure(task["payload"], request_id=task["task_id"])
    if job_type == "box_spirit_soul_emotion":
        return _handle_box_spirit_soul_emotion(task["payload"], request_id=task["task_id"])
    if job_type == "box_environment":
        return _handle_box_environment(task["payload"], request_id=task["task_id"])
    if job_type == "box_imaginative_windows":
        return _handle_box_imaginative_windows(task["payload"], request_id=task["task_id"])
    if job_type == "box_musical_composition":
        return _handle_box_musical_composition(task["payload"], request_id=task["task_id"])
    if job_type == "box_infinity":
        return _handle_box_infinity(task["payload"], request_id=task["task_id"])
    if job_type == "box_computation_layer":
        return _handle_box_computation_layer(task["payload"], request_id=task["task_id"])
    if job_type == "box_computation_rules":
        return _handle_box_computation_rules(task["payload"], request_id=task["task_id"])
    if job_type == "box_computation_programs":
        return _handle_box_computation_programs(task["payload"], request_id=task["task_id"])
    if job_type == "box_computation_universe":
        return _handle_box_computation_universe(task["payload"], request_id=task["task_id"])
    if job_type == "box_computation_causal":
        return _handle_box_computation_causal(task["payload"], request_id=task["task_id"])
    if job_type == "box_state_transition":
        return _handle_box_state_transition(task["payload"], request_id=task["task_id"])
    if job_type == "box_computation_primitives":
        return _handle_box_computation_primitives(task["payload"], request_id=task["task_id"])
    if job_type == "box_computation_primitives_alt":
        return _handle_box_computation_primitives_alt(task["payload"], request_id=task["task_id"])
    if job_type == "box_computation_sublayers":
        return _handle_box_computation_sublayers(task["payload"], request_id=task["task_id"])
    if job_type == "box_computation_emergence":
        return _handle_box_computation_emergence(task["payload"], request_id=task["task_id"])
    if job_type == "box_substrate":
        return _handle_box_substrate(task["payload"], request_id=task["task_id"])
    if job_type == "box_scaffolding":
        return _handle_box_scaffolding(task["payload"], request_id=task["task_id"])
    if job_type == "box_constraints":
        return _handle_box_constraints(task["payload"], request_id=task["task_id"])
    if job_type == "box_physical_substrate":
        return _handle_box_physical_substrate(task["payload"], request_id=task["task_id"])
    if job_type == "box_physical_states":
        return _handle_box_physical_states(task["payload"], request_id=task["task_id"])
    if job_type == "box_foundational_physics":
        return _handle_box_foundational_physics(task["payload"], request_id=task["task_id"])
    if job_type == "box_tangibility_conservation":
        return _handle_box_tangibility_conservation(task["payload"], request_id=task["task_id"])
    if job_type == "box_physical_subdomains":
        return _handle_box_physical_subdomains(task["payload"], request_id=task["task_id"])
    if job_type == "box_emergence_from_physics":
        return _handle_box_emergence_from_physics(task["payload"], request_id=task["task_id"])
    if job_type == "box_observer_independent":
        return _handle_box_observer_independent(task["payload"], request_id=task["task_id"])
    if job_type == "box_sensory_profile":
        return _handle_box_sensory_profile(task["payload"], request_id=task["task_id"])
    if job_type == "box_real_world_behavior":
        return _handle_box_real_world_behavior(task["payload"], request_id=task["task_id"])
    if job_type == "box_scenario_landscape":
        return _handle_box_scenario_landscape(task["payload"], request_id=task["task_id"])
    if job_type == "box_construction_reconstruction":
        return _handle_box_construction_reconstruction(task["payload"], request_id=task["task_id"])
    if job_type == "box_thought_to_reality":
        return _handle_box_thought_to_reality(task["payload"], request_id=task["task_id"])
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


def _handle_box_abstractions_metaphors(payload: Dict[str, Any], *, request_id: str):
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

        prompt_text = build_abstractions_metaphors_prompt(element, description)

        (
            output_text,
            usage_dict,
            tokens_in,
            tokens_out,
            total_tokens,
            duration_ms,
            resolved_model,
        ) = run_llm(prompt_text)

        cur.execute(
            "SELECT id FROM nodes WHERE box_id = ? AND kind = 'box_root' LIMIT 1;",
            (box["id"],),
        )
        root_row = cur.fetchone()
        parent_id = root_row["id"] if root_row else None

        rel_path = f"analysis/abstractions_metaphors_{datetime.utcnow().strftime('%Y%m%dT%H%M%SZ')}"
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
            (box["id"], parent_id, "abstractions_metaphors", rel_path, depth, output_text),
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


def _handle_box_processes_forces_interactions(payload: Dict[str, Any], *, request_id: str):
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

        prompt_text = build_processes_forces_interactions_prompt(element, description)

        return _run_and_store(
            box,
            prompt_text,
            name="processes_forces_interactions",
            rel_suffix="processes_forces_interactions",
        )
    finally:
        conn.close()


def _run_and_store(box, prompt_text: str, *, name: str, rel_suffix: str):
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    try:
        (
            output_text,
            usage_dict,
            tokens_in,
            tokens_out,
            total_tokens,
            duration_ms,
            resolved_model,
        ) = run_llm(prompt_text)

        cur.execute(
            "SELECT id FROM nodes WHERE box_id = ? AND kind = 'box_root' LIMIT 1;",
            (box["id"],),
        )
        root_row = cur.fetchone()
        parent_id = root_row["id"] if root_row else None

        rel_path = f"analysis/{rel_suffix}_{datetime.utcnow().strftime('%Y%m%dT%H%M%SZ')}"
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
            (box["id"], parent_id, name, rel_path, depth, output_text),
        )
        conn.commit()
        node_id = cur.lastrowid

        return {
            "box_slug": box["slug"],
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


def _handle_box_datasets(payload: Dict[str, Any], *, request_id: str):
    slug = payload.get("box_slug")
    if not slug:
        raise ValueError("box_slug is required")

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("SELECT * FROM boxes WHERE slug = ?", (slug,))
    box = cur.fetchone()
    if box is None:
        conn.close()
        raise ValueError("Box not found")
    conn.close()

    element = box["title"] or box["slug"]
    description = box["description"] or ""
    prompt_text = build_datasets_prompt(element, description)
    return _run_and_store(box, prompt_text, name="datasets", rel_suffix="datasets")


def _handle_box_codebases(payload: Dict[str, Any], *, request_id: str):
    slug = payload.get("box_slug")
    if not slug:
        raise ValueError("box_slug is required")

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("SELECT * FROM boxes WHERE slug = ?", (slug,))
    box = cur.fetchone()
    if box is None:
        conn.close()
        raise ValueError("Box not found")
    conn.close()

    element = box["title"] or box["slug"]
    description = box["description"] or ""
    prompt_text = build_codebases_prompt(element, description)
    return _run_and_store(box, prompt_text, name="codebases", rel_suffix="codebases")


def _handle_box_hardware_builds(payload: Dict[str, Any], *, request_id: str):
    slug = payload.get("box_slug")
    if not slug:
        raise ValueError("box_slug is required")

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("SELECT * FROM boxes WHERE slug = ?", (slug,))
    box = cur.fetchone()
    if box is None:
        conn.close()
        raise ValueError("Box not found")
    conn.close()

    element = box["title"] or box["slug"]
    description = box["description"] or ""
    prompt_text = build_hardware_builds_prompt(element, description)
    return _run_and_store(box, prompt_text, name="hardware_builds", rel_suffix="hardware_builds")


def _handle_box_experiments(payload: Dict[str, Any], *, request_id: str):
    slug = payload.get("box_slug")
    if not slug:
        raise ValueError("box_slug is required")

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("SELECT * FROM boxes WHERE slug = ?", (slug,))
    box = cur.fetchone()
    if box is None:
        conn.close()
        raise ValueError("Box not found")
    conn.close()

    element = box["title"] or box["slug"]
    description = box["description"] or ""
    prompt_text = build_experiments_prompt(element, description)
    return _run_and_store(box, prompt_text, name="experiments", rel_suffix="experiments")


def _handle_box_intelligence(payload: Dict[str, Any], *, request_id: str):
    slug = payload.get("box_slug")
    if not slug:
        raise ValueError("box_slug is required")

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("SELECT * FROM boxes WHERE slug = ?", (slug,))
    box = cur.fetchone()
    if box is None:
        conn.close()
        raise ValueError("Box not found")
    conn.close()

    element = box["title"] or box["slug"]
    description = box["description"] or ""
    prompt_text = build_intelligence_prompt(element, description)
    return _run_and_store(box, prompt_text, name="intelligence", rel_suffix="intelligence")


def _handle_box_control_levers(payload: Dict[str, Any], *, request_id: str):
    slug = payload.get("box_slug")
    if not slug:
        raise ValueError("box_slug is required")

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("SELECT * FROM boxes WHERE slug = ?", (slug,))
    box = cur.fetchone()
    if box is None:
        conn.close()
        raise ValueError("Box not found")
    conn.close()

    element = box["title"] or box["slug"]
    description = box["description"] or ""
    prompt_text = build_control_levers_prompt(element, description)
    return _run_and_store(box, prompt_text, name="control_levers", rel_suffix="control_levers")


def _handle_box_companies(payload: Dict[str, Any], *, request_id: str):
    slug = payload.get("box_slug")
    if not slug:
        raise ValueError("box_slug is required")

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("SELECT * FROM boxes WHERE slug = ?", (slug,))
    box = cur.fetchone()
    if box is None:
        conn.close()
        raise ValueError("Box not found")
    conn.close()

    element = box["title"] or box["slug"]
    description = box["description"] or ""
    prompt_text = build_companies_prompt(element, description)
    return _run_and_store(box, prompt_text, name="companies", rel_suffix="companies")


def _handle_box_theories(payload: Dict[str, Any], *, request_id: str):
    slug = payload.get("box_slug")
    if not slug:
        raise ValueError("box_slug is required")

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("SELECT * FROM boxes WHERE slug = ?", (slug,))
    box = cur.fetchone()
    if box is None:
        conn.close()
        raise ValueError("Box not found")
    conn.close()

    element = box["title"] or box["slug"]
    description = box["description"] or ""
    prompt_text = build_theories_prompt(element, description)
    return _run_and_store(box, prompt_text, name="theories", rel_suffix="theories")


def _handle_box_historical_context(payload: Dict[str, Any], *, request_id: str):
    slug = payload.get("box_slug")
    if not slug:
        raise ValueError("box_slug is required")

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("SELECT * FROM boxes WHERE slug = ?", (slug,))
    box = cur.fetchone()
    if box is None:
        conn.close()
        raise ValueError("Box not found")
    conn.close()

    element = box["title"] or box["slug"]
    description = box["description"] or ""
    prompt_text = build_historical_context_prompt(element, description)
    return _run_and_store(box, prompt_text, name="historical_context", rel_suffix="historical_context")


def _handle_box_value_exchange(payload: Dict[str, Any], *, request_id: str):
    slug = payload.get("box_slug")
    if not slug:
        raise ValueError("box_slug is required")

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("SELECT * FROM boxes WHERE slug = ?", (slug,))
    box = cur.fetchone()
    if box is None:
        conn.close()
        raise ValueError("Box not found")
    conn.close()

    element = box["title"] or box["slug"]
    description = box["description"] or ""
    prompt_text = build_value_exchange_prompt(element, description)
    return _run_and_store(box, prompt_text, name="value_exchange", rel_suffix="value_exchange")


def _handle_box_value_addition(payload: Dict[str, Any], *, request_id: str):
    slug = payload.get("box_slug")
    if not slug:
        raise ValueError("box_slug is required")

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("SELECT * FROM boxes WHERE slug = ?", (slug,))
    box = cur.fetchone()
    if box is None:
        conn.close()
        raise ValueError("Box not found")
    conn.close()

    element = box["title"] or box["slug"]
    description = box["description"] or ""
    prompt_text = build_value_addition_prompt(element, description)
    return _run_and_store(box, prompt_text, name="value_addition", rel_suffix="value_addition")


def _handle_box_scientific_substructure(payload: Dict[str, Any], *, request_id: str):
    slug = payload.get("box_slug")
    if not slug:
        raise ValueError("box_slug is required")

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("SELECT * FROM boxes WHERE slug = ?", (slug,))
    box = cur.fetchone()
    if box is None:
        conn.close()
        raise ValueError("Box not found")
    conn.close()

    element = box["title"] or box["slug"]
    description = box["description"] or ""
    prompt_text = build_scientific_substructure_prompt(element, description)
    return _run_and_store(box, prompt_text, name="scientific_substructure", rel_suffix="scientific_substructure")


def _handle_box_spirit_soul_emotion(payload: Dict[str, Any], *, request_id: str):
    slug = payload.get("box_slug")
    if not slug:
        raise ValueError("box_slug is required")

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("SELECT * FROM boxes WHERE slug = ?", (slug,))
    box = cur.fetchone()
    if box is None:
        conn.close()
        raise ValueError("Box not found")
    conn.close()

    element = box["title"] or box["slug"]
    description = box["description"] or ""
    prompt_text = build_spirit_soul_emotion_prompt(element, description)
    return _run_and_store(box, prompt_text, name="spirit_soul_emotion", rel_suffix="spirit_soul_emotion")


def _handle_box_environment(payload: Dict[str, Any], *, request_id: str):
    slug = payload.get("box_slug")
    if not slug:
        raise ValueError("box_slug is required")

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("SELECT * FROM boxes WHERE slug = ?", (slug,))
    box = cur.fetchone()
    if box is None:
        conn.close()
        raise ValueError("Box not found")
    conn.close()

    element = box["title"] or box["slug"]
    description = box["description"] or ""
    prompt_text = build_environment_prompt(element, description)
    return _run_and_store(box, prompt_text, name="environment", rel_suffix="environment")


def _handle_box_imaginative_windows(payload: Dict[str, Any], *, request_id: str):
    slug = payload.get("box_slug")
    if not slug:
        raise ValueError("box_slug is required")

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("SELECT * FROM boxes WHERE slug = ?", (slug,))
    box = cur.fetchone()
    if box is None:
        conn.close()
        raise ValueError("Box not found")
    conn.close()

    element = box["title"] or box["slug"]
    description = box["description"] or ""
    prompt_text = build_imaginative_windows_prompt(element, description)
    return _run_and_store(box, prompt_text, name="imaginative_windows", rel_suffix="imaginative_windows")


def _handle_box_musical_composition(payload: Dict[str, Any], *, request_id: str):
    slug = payload.get("box_slug")
    if not slug:
        raise ValueError("box_slug is required")

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("SELECT * FROM boxes WHERE slug = ?", (slug,))
    box = cur.fetchone()
    if box is None:
        conn.close()
        raise ValueError("Box not found")
    conn.close()

    element = box["title"] or box["slug"]
    description = box["description"] or ""
    prompt_text = build_musical_composition_prompt(element, description)
    return _run_and_store(box, prompt_text, name="musical_composition", rel_suffix="musical_composition")


def _handle_box_infinity(payload: Dict[str, Any], *, request_id: str):
    slug = payload.get("box_slug")
    if not slug:
        raise ValueError("box_slug is required")

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("SELECT * FROM boxes WHERE slug = ?", (slug,))
    box = cur.fetchone()
    if box is None:
        conn.close()
        raise ValueError("Box not found")
    conn.close()

    element = box["title"] or box["slug"]
    description = box["description"] or ""
    prompt_text = build_infinity_prompt(element, description)
    return _run_and_store(box, prompt_text, name="infinity", rel_suffix="infinity")


def _handle_box_computation_layer(payload: Dict[str, Any], *, request_id: str):
    slug = payload.get("box_slug")
    if not slug:
        raise ValueError("box_slug is required")

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("SELECT * FROM boxes WHERE slug = ?", (slug,))
    box = cur.fetchone()
    if box is None:
        conn.close()
        raise ValueError("Box not found")
    conn.close()

    element = box["title"] or box["slug"]
    description = box["description"] or ""
    prompt_text = build_computation_layer_prompt(element, description)
    return _run_and_store(box, prompt_text, name="computation_layer", rel_suffix="computation_layer")


def _handle_box_computation_rules(payload: Dict[str, Any], *, request_id: str):
    slug = payload.get("box_slug")
    if not slug:
        raise ValueError("box_slug is required")

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("SELECT * FROM boxes WHERE slug = ?", (slug,))
    box = cur.fetchone()
    if box is None:
        conn.close()
        raise ValueError("Box not found")
    conn.close()

    element = box["title"] or box["slug"]
    description = box["description"] or ""
    prompt_text = build_computation_rules_prompt(element, description)
    return _run_and_store(box, prompt_text, name="computation_rules", rel_suffix="computation_rules")


def _handle_box_computation_programs(payload: Dict[str, Any], *, request_id: str):
    slug = payload.get("box_slug")
    if not slug:
        raise ValueError("box_slug is required")

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("SELECT * FROM boxes WHERE slug = ?", (slug,))
    box = cur.fetchone()
    if box is None:
        conn.close()
        raise ValueError("Box not found")
    conn.close()

    element = box["title"] or box["slug"]
    description = box["description"] or ""
    prompt_text = build_computation_programs_prompt(element, description)
    return _run_and_store(box, prompt_text, name="computation_programs", rel_suffix="computation_programs")


def _handle_box_computation_universe(payload: Dict[str, Any], *, request_id: str):
    slug = payload.get("box_slug")
    if not slug:
        raise ValueError("box_slug is required")

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("SELECT * FROM boxes WHERE slug = ?", (slug,))
    box = cur.fetchone()
    if box is None:
        conn.close()
        raise ValueError("Box not found")
    conn.close()

    element = box["title"] or box["slug"]
    description = box["description"] or ""
    prompt_text = build_computation_universe_prompt(element, description)
    return _run_and_store(box, prompt_text, name="computation_universe", rel_suffix="computation_universe")


def _handle_box_computation_causal(payload: Dict[str, Any], *, request_id: str):
    slug = payload.get("box_slug")
    if not slug:
        raise ValueError("box_slug is required")

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("SELECT * FROM boxes WHERE slug = ?", (slug,))
    box = cur.fetchone()
    if box is None:
        conn.close()
        raise ValueError("Box not found")
    conn.close()

    element = box["title"] or box["slug"]
    description = box["description"] or ""
    prompt_text = build_computation_causal_prompt(element, description)
    return _run_and_store(box, prompt_text, name="computation_causal", rel_suffix="computation_causal")


def _handle_box_state_transition(payload: Dict[str, Any], *, request_id: str):
    slug = payload.get("box_slug")
    if not slug:
        raise ValueError("box_slug is required")

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("SELECT * FROM boxes WHERE slug = ?", (slug,))
    box = cur.fetchone()
    if box is None:
        conn.close()
        raise ValueError("Box not found")
    conn.close()

    element = box["title"] or box["slug"]
    description = box["description"] or ""
    prompt_text = build_state_transition_prompt(element, description)
    return _run_and_store(box, prompt_text, name="state_transition", rel_suffix="state_transition")


def _handle_box_computation_primitives(payload: Dict[str, Any], *, request_id: str):
    slug = payload.get("box_slug")
    if not slug:
        raise ValueError("box_slug is required")

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("SELECT * FROM boxes WHERE slug = ?", (slug,))
    box = cur.fetchone()
    if box is None:
        conn.close()
        raise ValueError("Box not found")
    conn.close()

    element = box["title"] or box["slug"]
    description = box["description"] or ""
    prompt_text = build_computation_primitives_prompt(element, description)
    return _run_and_store(box, prompt_text, name="computation_primitives", rel_suffix="computation_primitives")


def _handle_box_computation_primitives_alt(payload: Dict[str, Any], *, request_id: str):
    slug = payload.get("box_slug")
    if not slug:
        raise ValueError("box_slug is required")

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("SELECT * FROM boxes WHERE slug = ?", (slug,))
    box = cur.fetchone()
    if box is None:
        conn.close()
        raise ValueError("Box not found")
    conn.close()

    element = box["title"] or box["slug"]
    description = box["description"] or ""
    prompt_text = build_computation_primitives_alt_prompt(element, description)
    return _run_and_store(box, prompt_text, name="computation_primitives_alt", rel_suffix="computation_primitives_alt")


def _handle_box_computation_sublayers(payload: Dict[str, Any], *, request_id: str):
    slug = payload.get("box_slug")
    if not slug:
        raise ValueError("box_slug is required")

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("SELECT * FROM boxes WHERE slug = ?", (slug,))
    box = cur.fetchone()
    if box is None:
        conn.close()
        raise ValueError("Box not found")
    conn.close()

    element = box["title"] or box["slug"]
    description = box["description"] or ""
    prompt_text = build_computation_sublayers_prompt(element, description)
    return _run_and_store(box, prompt_text, name="computation_sublayers", rel_suffix="computation_sublayers")


def _handle_box_computation_emergence(payload: Dict[str, Any], *, request_id: str):
    slug = payload.get("box_slug")
    if not slug:
        raise ValueError("box_slug is required")

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("SELECT * FROM boxes WHERE slug = ?", (slug,))
    box = cur.fetchone()
    if box is None:
        conn.close()
        raise ValueError("Box not found")
    conn.close()

    element = box["title"] or box["slug"]
    description = box["description"] or ""
    prompt_text = build_computation_emergence_prompt(element, description)
    return _run_and_store(box, prompt_text, name="computation_emergence", rel_suffix="computation_emergence")


def _handle_box_substrate(payload: Dict[str, Any], *, request_id: str):
    slug = payload.get("box_slug")
    if not slug:
        raise ValueError("box_slug is required")

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("SELECT * FROM boxes WHERE slug = ?", (slug,))
    box = cur.fetchone()
    if box is None:
        conn.close()
        raise ValueError("Box not found")
    conn.close()

    element = box["title"] or box["slug"]
    description = box["description"] or ""
    prompt_text = build_substrate_prompt(element, description)
    return _run_and_store(box, prompt_text, name="substrate", rel_suffix="substrate")


def _handle_box_scaffolding(payload: Dict[str, Any], *, request_id: str):
    slug = payload.get("box_slug")
    if not slug:
        raise ValueError("box_slug is required")

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("SELECT * FROM boxes WHERE slug = ?", (slug,))
    box = cur.fetchone()
    if box is None:
        conn.close()
        raise ValueError("Box not found")
    conn.close()

    element = box["title"] or box["slug"]
    description = box["description"] or ""
    prompt_text = build_scaffolding_prompt(element, description)
    return _run_and_store(box, prompt_text, name="scaffolding", rel_suffix="scaffolding")


def _handle_box_constraints(payload: Dict[str, Any], *, request_id: str):
    slug = payload.get("box_slug")
    if not slug:
        raise ValueError("box_slug is required")

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("SELECT * FROM boxes WHERE slug = ?", (slug,))
    box = cur.fetchone()
    if box is None:
        conn.close()
        raise ValueError("Box not found")
    conn.close()

    element = box["title"] or box["slug"]
    description = box["description"] or ""
    prompt_text = build_constraints_prompt(element, description)
    return _run_and_store(box, prompt_text, name="constraints", rel_suffix="constraints")


def _handle_box_physical_substrate(payload: Dict[str, Any], *, request_id: str):
    slug = payload.get("box_slug")
    if not slug:
        raise ValueError("box_slug is required")

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("SELECT * FROM boxes WHERE slug = ?", (slug,))
    box = cur.fetchone()
    if box is None:
        conn.close()
        raise ValueError("Box not found")
    conn.close()

    element = box["title"] or box["slug"]
    description = box["description"] or ""
    prompt_text = build_physical_substrate_prompt(element, description)
    return _run_and_store(box, prompt_text, name="physical_substrate", rel_suffix="physical_substrate")


def _handle_box_physical_states(payload: Dict[str, Any], *, request_id: str):
    slug = payload.get("box_slug")
    if not slug:
        raise ValueError("box_slug is required")

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("SELECT * FROM boxes WHERE slug = ?", (slug,))
    box = cur.fetchone()
    if box is None:
        conn.close()
        raise ValueError("Box not found")
    conn.close()

    element = box["title"] or box["slug"]
    description = box["description"] or ""
    prompt_text = build_physical_states_prompt(element, description)
    return _run_and_store(box, prompt_text, name="physical_states", rel_suffix="physical_states")


def _handle_box_foundational_physics(payload: Dict[str, Any], *, request_id: str):
    slug = payload.get("box_slug")
    if not slug:
        raise ValueError("box_slug is required")

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("SELECT * FROM boxes WHERE slug = ?", (slug,))
    box = cur.fetchone()
    if box is None:
        conn.close()
        raise ValueError("Box not found")
    conn.close()

    element = box["title"] or box["slug"]
    description = box["description"] or ""
    prompt_text = build_foundational_physics_prompt(element, description)
    return _run_and_store(box, prompt_text, name="foundational_physics", rel_suffix="foundational_physics")


def _handle_box_tangibility_conservation(payload: Dict[str, Any], *, request_id: str):
    slug = payload.get("box_slug")
    if not slug:
        raise ValueError("box_slug is required")

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("SELECT * FROM boxes WHERE slug = ?", (slug,))
    box = cur.fetchone()
    if box is None:
        conn.close()
        raise ValueError("Box not found")
    conn.close()

    element = box["title"] or box["slug"]
    description = box["description"] or ""
    prompt_text = build_tangibility_conservation_prompt(element, description)
    return _run_and_store(box, prompt_text, name="tangibility_conservation", rel_suffix="tangibility_conservation")


def _handle_box_physical_subdomains(payload: Dict[str, Any], *, request_id: str):
    slug = payload.get("box_slug")
    if not slug:
        raise ValueError("box_slug is required")

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("SELECT * FROM boxes WHERE slug = ?", (slug,))
    box = cur.fetchone()
    if box is None:
        conn.close()
        raise ValueError("Box not found")
    conn.close()

    element = box["title"] or box["slug"]
    description = box["description"] or ""
    prompt_text = build_physical_subdomains_prompt(element, description)
    return _run_and_store(box, prompt_text, name="physical_subdomains", rel_suffix="physical_subdomains")


def _handle_box_emergence_from_physics(payload: Dict[str, Any], *, request_id: str):
    slug = payload.get("box_slug")
    if not slug:
        raise ValueError("box_slug is required")

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("SELECT * FROM boxes WHERE slug = ?", (slug,))
    box = cur.fetchone()
    if box is None:
        conn.close()
        raise ValueError("Box not found")
    conn.close()

    element = box["title"] or box["slug"]
    description = box["description"] or ""
    prompt_text = build_emergence_from_physics_prompt(element, description)
    return _run_and_store(box, prompt_text, name="emergence_from_physics", rel_suffix="emergence_from_physics")


def _handle_box_observer_independent(payload: Dict[str, Any], *, request_id: str):
    slug = payload.get("box_slug")
    if not slug:
        raise ValueError("box_slug is required")

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("SELECT * FROM boxes WHERE slug = ?", (slug,))
    box = cur.fetchone()
    if box is None:
        conn.close()
        raise ValueError("Box not found")
    conn.close()

    element = box["title"] or box["slug"]
    description = box["description"] or ""
    prompt_text = build_observer_independent_prompt(element, description)
    return _run_and_store(box, prompt_text, name="observer_independent", rel_suffix="observer_independent")


def _handle_box_sensory_profile(payload: Dict[str, Any], *, request_id: str):
    slug = payload.get("box_slug")
    if not slug:
        raise ValueError("box_slug is required")

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("SELECT * FROM boxes WHERE slug = ?", (slug,))
    box = cur.fetchone()
    if box is None:
        conn.close()
        raise ValueError("Box not found")
    conn.close()

    element = box["title"] or box["slug"]
    description = box["description"] or ""
    prompt_text = build_sensory_profile_prompt(element, description)
    return _run_and_store(box, prompt_text, name="sensory_profile", rel_suffix="sensory_profile")


def _handle_box_real_world_behavior(payload: Dict[str, Any], *, request_id: str):
    slug = payload.get("box_slug")
    if not slug:
        raise ValueError("box_slug is required")

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("SELECT * FROM boxes WHERE slug = ?", (slug,))
    box = cur.fetchone()
    if box is None:
        conn.close()
        raise ValueError("Box not found")
    conn.close()

    element = box["title"] or box["slug"]
    description = box["description"] or ""
    prompt_text = build_real_world_behavior_prompt(element, description)
    return _run_and_store(box, prompt_text, name="real_world_behavior", rel_suffix="real_world_behavior")


def _handle_box_scenario_landscape(payload: Dict[str, Any], *, request_id: str):
    slug = payload.get("box_slug")
    if not slug:
        raise ValueError("box_slug is required")

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("SELECT * FROM boxes WHERE slug = ?", (slug,))
    box = cur.fetchone()
    if box is None:
        conn.close()
        raise ValueError("Box not found")
    conn.close()

    element = box["title"] or box["slug"]
    description = box["description"] or ""
    prompt_text = build_scenario_landscape_prompt(element, description)
    return _run_and_store(box, prompt_text, name="scenario_landscape", rel_suffix="scenario_landscape")


def _handle_box_construction_reconstruction(payload: Dict[str, Any], *, request_id: str):
    slug = payload.get("box_slug")
    if not slug:
        raise ValueError("box_slug is required")

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("SELECT * FROM boxes WHERE slug = ?", (slug,))
    box = cur.fetchone()
    if box is None:
        conn.close()
        raise ValueError("Box not found")
    conn.close()

    element = box["title"] or box["slug"]
    description = box["description"] or ""
    prompt_text = build_construction_reconstruction_prompt(element, description)
    return _run_and_store(box, prompt_text, name="construction_reconstruction", rel_suffix="construction_reconstruction")


def _handle_box_thought_to_reality(payload: Dict[str, Any], *, request_id: str):
    slug = payload.get("box_slug")
    if not slug:
        raise ValueError("box_slug is required")

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("SELECT * FROM boxes WHERE slug = ?", (slug,))
    box = cur.fetchone()
    if box is None:
        conn.close()
        raise ValueError("Box not found")
    conn.close()

    element = box["title"] or box["slug"]
    description = box["description"] or ""
    prompt_text = build_thought_to_reality_prompt(element, description)
    return _run_and_store(box, prompt_text, name="thought_to_reality", rel_suffix="thought_to_reality")


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

    limit = request.args.get("limit", default=200, type=int)
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
    limit = request.args.get("limit", default=200, type=int)
    if not isinstance(limit, int):
        return jsonify({"error": "limit must be integer"}), 400
    limit = max(1, min(limit, LLM_TASK_HISTORY_LIMIT))

    status_filter = request.args.get("status")
    include_error = request.args.get("include_error") in ("1", "true", "yes")

    with LLM_TASKS_LOCK:
        counts = {"queued": 0, "running": 0, "done": 0, "error": 0}
        for t in LLM_TASKS.values():
            status = t.get("status", "queued")
            counts[status] = counts.get(status, 0) + 1

        ordered_ids = list(reversed(LLM_TASK_ORDER))
        filtered_ids = []
        for tid in ordered_ids:
            task = LLM_TASKS.get(tid)
            if not task:
                continue
            if status_filter and task.get("status") != status_filter:
                continue
            filtered_ids.append(tid)
            if len(filtered_ids) >= limit:
                break

        tasks: List[Dict[str, Any]] = []
        for tid in filtered_ids:
            t = dict(LLM_TASKS.get(tid, {}))
            if not include_error and "error" in t:
                t["has_error"] = True
                t.pop("error", None)
            tasks.append(t)

    return jsonify(
        {
            "queue": {
                "queue_size": LLM_TASK_QUEUE.qsize(),
                "concurrency": LLM_QUEUE_CONCURRENCY,
                "tasks": counts,
            },
            "count": len(tasks),
            "tasks": tasks,
        }
    )


@app.route("/llm/workers", methods=["GET"])
def list_llm_workers():
    limit = request.args.get("limit", default=50, type=int)
    if not isinstance(limit, int):
        return jsonify({"error": "limit must be integer"}), 400
    limit = max(1, min(limit, LLM_TASK_HISTORY_LIMIT))

    workers: Dict[int, List[Dict[str, Any]]] = {}
    with LLM_TASKS_LOCK:
        for task in LLM_TASKS.values():
            if task.get("status") != "running":
                continue
            worker_id = task.get("worker_id")
            if worker_id is None:
                continue
            workers.setdefault(int(worker_id), []).append(dict(task))
        # trim per worker
        for wid, tasks in workers.items():
            workers[wid] = tasks[:limit]

        counts = {"queued": 0, "running": 0, "done": 0, "error": 0}
        for t in LLM_TASKS.values():
            status = t.get("status", "queued")
            counts[status] = counts.get(status, 0) + 1

    return jsonify(
        {
            "queue": {
                "queue_size": LLM_TASK_QUEUE.qsize(),
                "concurrency": LLM_QUEUE_CONCURRENCY,
                "tasks": counts,
            },
            "workers": workers,
        }
    )


@app.route("/boxes/<slug>/abstractions", methods=["POST"])
def enqueue_abstractions(slug):
    box = get_box_by_slug(slug)
    if box is None:
        abort(404, description="Box not found")

    task = enqueue_llm_task(
        "box_abstractions_metaphors",
        {"box_slug": box["slug"]},
    )
    task["queue_size"] = LLM_TASK_QUEUE.qsize()
    return jsonify(task), 202


@app.route("/boxes/<slug>/processes", methods=["POST"])
def enqueue_processes_forces_interactions(slug):
    box = get_box_by_slug(slug)
    if box is None:
        abort(404, description="Box not found")

    task = enqueue_llm_task(
        "box_processes_forces_interactions",
        {"box_slug": box["slug"]},
    )
    task["queue_size"] = LLM_TASK_QUEUE.qsize()
    return jsonify(task), 202


@app.route("/boxes/<slug>/datasets", methods=["POST"])
def enqueue_datasets(slug):
    box = get_box_by_slug(slug)
    if box is None:
        abort(404, description="Box not found")

    task = enqueue_llm_task(
        "box_datasets",
        {"box_slug": box["slug"]},
    )
    task["queue_size"] = LLM_TASK_QUEUE.qsize()
    return jsonify(task), 202


@app.route("/boxes/<slug>/codebases", methods=["POST"])
def enqueue_codebases(slug):
    box = get_box_by_slug(slug)
    if box is None:
        abort(404, description="Box not found")

    task = enqueue_llm_task(
        "box_codebases",
        {"box_slug": box["slug"]},
    )
    task["queue_size"] = LLM_TASK_QUEUE.qsize()
    return jsonify(task), 202


@app.route("/boxes/<slug>/hardware_builds", methods=["POST"])
def enqueue_hardware_builds(slug):
    box = get_box_by_slug(slug)
    if box is None:
        abort(404, description="Box not found")

    task = enqueue_llm_task(
        "box_hardware_builds",
        {"box_slug": box["slug"]},
    )
    task["queue_size"] = LLM_TASK_QUEUE.qsize()
    return jsonify(task), 202


@app.route("/boxes/<slug>/experiments", methods=["POST"])
def enqueue_experiments(slug):
    box = get_box_by_slug(slug)
    if box is None:
        abort(404, description="Box not found")

    task = enqueue_llm_task(
        "box_experiments",
        {"box_slug": box["slug"]},
    )
    task["queue_size"] = LLM_TASK_QUEUE.qsize()
    return jsonify(task), 202


@app.route("/boxes/<slug>/intelligence", methods=["POST"])
def enqueue_intelligence(slug):
    box = get_box_by_slug(slug)
    if box is None:
        abort(404, description="Box not found")

    task = enqueue_llm_task(
        "box_intelligence",
        {"box_slug": box["slug"]},
    )
    task["queue_size"] = LLM_TASK_QUEUE.qsize()
    return jsonify(task), 202


@app.route("/boxes/<slug>/control_levers", methods=["POST"])
def enqueue_control_levers(slug):
    box = get_box_by_slug(slug)
    if box is None:
        abort(404, description="Box not found")

    task = enqueue_llm_task(
        "box_control_levers",
        {"box_slug": box["slug"]},
    )
    task["queue_size"] = LLM_TASK_QUEUE.qsize()
    return jsonify(task), 202


@app.route("/boxes/<slug>/companies", methods=["POST"])
def enqueue_companies(slug):
    box = get_box_by_slug(slug)
    if box is None:
        abort(404, description="Box not found")

    task = enqueue_llm_task(
        "box_companies",
        {"box_slug": box["slug"]},
    )
    task["queue_size"] = LLM_TASK_QUEUE.qsize()
    return jsonify(task), 202


@app.route("/boxes/<slug>/theories", methods=["POST"])
def enqueue_theories(slug):
    box = get_box_by_slug(slug)
    if box is None:
        abort(404, description="Box not found")

    task = enqueue_llm_task(
        "box_theories",
        {"box_slug": box["slug"]},
    )
    task["queue_size"] = LLM_TASK_QUEUE.qsize()
    return jsonify(task), 202


@app.route("/boxes/<slug>/historical_context", methods=["POST"])
def enqueue_historical_context(slug):
    box = get_box_by_slug(slug)
    if box is None:
        abort(404, description="Box not found")

    task = enqueue_llm_task(
        "box_historical_context",
        {"box_slug": box["slug"]},
    )
    task["queue_size"] = LLM_TASK_QUEUE.qsize()
    return jsonify(task), 202


@app.route("/boxes/<slug>/value_exchange", methods=["POST"])
def enqueue_value_exchange(slug):
    box = get_box_by_slug(slug)
    if box is None:
        abort(404, description="Box not found")

    task = enqueue_llm_task(
        "box_value_exchange",
        {"box_slug": box["slug"]},
    )
    task["queue_size"] = LLM_TASK_QUEUE.qsize()
    return jsonify(task), 202


@app.route("/boxes/<slug>/value_addition", methods=["POST"])
def enqueue_value_addition(slug):
    box = get_box_by_slug(slug)
    if box is None:
        abort(404, description="Box not found")

    task = enqueue_llm_task(
        "box_value_addition",
        {"box_slug": box["slug"]},
    )
    task["queue_size"] = LLM_TASK_QUEUE.qsize()
    return jsonify(task), 202


@app.route("/boxes/<slug>/science", methods=["POST"])
def enqueue_scientific_substructure(slug):
    box = get_box_by_slug(slug)
    if box is None:
        abort(404, description="Box not found")

    task = enqueue_llm_task(
        "box_scientific_substructure",
        {"box_slug": box["slug"]},
    )
    task["queue_size"] = LLM_TASK_QUEUE.qsize()
    return jsonify(task), 202


@app.route("/boxes/<slug>/spirit", methods=["POST"])
def enqueue_spirit_soul_emotion(slug):
    box = get_box_by_slug(slug)
    if box is None:
        abort(404, description="Box not found")

    task = enqueue_llm_task(
        "box_spirit_soul_emotion",
        {"box_slug": box["slug"]},
    )
    task["queue_size"] = LLM_TASK_QUEUE.qsize()
    return jsonify(task), 202


@app.route("/boxes/<slug>/environment", methods=["POST"])
def enqueue_environment(slug):
    box = get_box_by_slug(slug)
    if box is None:
        abort(404, description="Box not found")

    task = enqueue_llm_task(
        "box_environment",
        {"box_slug": box["slug"]},
    )
    task["queue_size"] = LLM_TASK_QUEUE.qsize()
    return jsonify(task), 202


@app.route("/boxes/<slug>/imagination", methods=["POST"])
def enqueue_imaginative_windows(slug):
    box = get_box_by_slug(slug)
    if box is None:
        abort(404, description="Box not found")

    task = enqueue_llm_task(
        "box_imaginative_windows",
        {"box_slug": box["slug"]},
    )
    task["queue_size"] = LLM_TASK_QUEUE.qsize()
    return jsonify(task), 202


@app.route("/boxes/<slug>/musical", methods=["POST"])
def enqueue_musical_composition(slug):
    box = get_box_by_slug(slug)
    if box is None:
        abort(404, description="Box not found")

    task = enqueue_llm_task(
        "box_musical_composition",
        {"box_slug": box["slug"]},
    )
    task["queue_size"] = LLM_TASK_QUEUE.qsize()
    return jsonify(task), 202


@app.route("/boxes/<slug>/infinity", methods=["POST"])
def enqueue_infinity(slug):
    box = get_box_by_slug(slug)
    if box is None:
        abort(404, description="Box not found")

    task = enqueue_llm_task(
        "box_infinity",
        {"box_slug": box["slug"]},
    )
    task["queue_size"] = LLM_TASK_QUEUE.qsize()
    return jsonify(task), 202


@app.route("/boxes/<slug>/computation_layer", methods=["POST"])
def enqueue_computation_layer(slug):
    box = get_box_by_slug(slug)
    if box is None:
        abort(404, description="Box not found")

    task = enqueue_llm_task(
        "box_computation_layer",
        {"box_slug": box["slug"]},
    )
    task["queue_size"] = LLM_TASK_QUEUE.qsize()
    return jsonify(task), 202


@app.route("/boxes/<slug>/computation_rules", methods=["POST"])
def enqueue_computation_rules(slug):
    box = get_box_by_slug(slug)
    if box is None:
        abort(404, description="Box not found")

    task = enqueue_llm_task(
        "box_computation_rules",
        {"box_slug": box["slug"]},
    )
    task["queue_size"] = LLM_TASK_QUEUE.qsize()
    return jsonify(task), 202


@app.route("/boxes/<slug>/computation_programs", methods=["POST"])
def enqueue_computation_programs(slug):
    box = get_box_by_slug(slug)
    if box is None:
        abort(404, description="Box not found")

    task = enqueue_llm_task(
        "box_computation_programs",
        {"box_slug": box["slug"]},
    )
    task["queue_size"] = LLM_TASK_QUEUE.qsize()
    return jsonify(task), 202


@app.route("/boxes/<slug>/computation_universe", methods=["POST"])
def enqueue_computation_universe(slug):
    box = get_box_by_slug(slug)
    if box is None:
        abort(404, description="Box not found")

    task = enqueue_llm_task(
        "box_computation_universe",
        {"box_slug": box["slug"]},
    )
    task["queue_size"] = LLM_TASK_QUEUE.qsize()
    return jsonify(task), 202


@app.route("/boxes/<slug>/computation_causal", methods=["POST"])
def enqueue_computation_causal(slug):
    box = get_box_by_slug(slug)
    if box is None:
        abort(404, description="Box not found")

    task = enqueue_llm_task(
        "box_computation_causal",
        {"box_slug": box["slug"]},
    )
    task["queue_size"] = LLM_TASK_QUEUE.qsize()
    return jsonify(task), 202


@app.route("/boxes/<slug>/state_transition", methods=["POST"])
def enqueue_state_transition(slug):
    box = get_box_by_slug(slug)
    if box is None:
        abort(404, description="Box not found")

    task = enqueue_llm_task(
        "box_state_transition",
        {"box_slug": box["slug"]},
    )
    task["queue_size"] = LLM_TASK_QUEUE.qsize()
    return jsonify(task), 202


@app.route("/boxes/<slug>/computation_primitives", methods=["POST"])
def enqueue_computation_primitives(slug):
    box = get_box_by_slug(slug)
    if box is None:
        abort(404, description="Box not found")

    task = enqueue_llm_task(
        "box_computation_primitives",
        {"box_slug": box["slug"]},
    )
    task["queue_size"] = LLM_TASK_QUEUE.qsize()
    return jsonify(task), 202


@app.route("/boxes/<slug>/computation_primitives_alt", methods=["POST"])
def enqueue_computation_primitives_alt(slug):
    box = get_box_by_slug(slug)
    if box is None:
        abort(404, description="Box not found")

    task = enqueue_llm_task(
        "box_computation_primitives_alt",
        {"box_slug": box["slug"]},
    )
    task["queue_size"] = LLM_TASK_QUEUE.qsize()
    return jsonify(task), 202


@app.route("/boxes/<slug>/computation_sublayers", methods=["POST"])
def enqueue_computation_sublayers(slug):
    box = get_box_by_slug(slug)
    if box is None:
        abort(404, description="Box not found")

    task = enqueue_llm_task(
        "box_computation_sublayers",
        {"box_slug": box["slug"]},
    )
    task["queue_size"] = LLM_TASK_QUEUE.qsize()
    return jsonify(task), 202


@app.route("/boxes/<slug>/computation_emergence", methods=["POST"])
def enqueue_computation_emergence(slug):
    box = get_box_by_slug(slug)
    if box is None:
        abort(404, description="Box not found")

    task = enqueue_llm_task(
        "box_computation_emergence",
        {"box_slug": box["slug"]},
    )
    task["queue_size"] = LLM_TASK_QUEUE.qsize()
    return jsonify(task), 202


@app.route("/boxes/<slug>/substrate", methods=["POST"])
def enqueue_substrate(slug):
    box = get_box_by_slug(slug)
    if box is None:
        abort(404, description="Box not found")

    task = enqueue_llm_task(
        "box_substrate",
        {"box_slug": box["slug"]},
    )
    task["queue_size"] = LLM_TASK_QUEUE.qsize()
    return jsonify(task), 202


@app.route("/boxes/<slug>/scaffolding", methods=["POST"])
def enqueue_scaffolding(slug):
    box = get_box_by_slug(slug)
    if box is None:
        abort(404, description="Box not found")

    task = enqueue_llm_task(
        "box_scaffolding",
        {"box_slug": box["slug"]},
    )
    task["queue_size"] = LLM_TASK_QUEUE.qsize()
    return jsonify(task), 202


@app.route("/boxes/<slug>/constraints", methods=["POST"])
def enqueue_constraints(slug):
    box = get_box_by_slug(slug)
    if box is None:
        abort(404, description="Box not found")

    task = enqueue_llm_task(
        "box_constraints",
        {"box_slug": box["slug"]},
    )
    task["queue_size"] = LLM_TASK_QUEUE.qsize()
    return jsonify(task), 202


@app.route("/boxes/<slug>/physical_substrate", methods=["POST"])
def enqueue_physical_substrate(slug):
    box = get_box_by_slug(slug)
    if box is None:
        abort(404, description="Box not found")

    task = enqueue_llm_task(
        "box_physical_substrate",
        {"box_slug": box["slug"]},
    )
    task["queue_size"] = LLM_TASK_QUEUE.qsize()
    return jsonify(task), 202


@app.route("/boxes/<slug>/physical_states", methods=["POST"])
def enqueue_physical_states(slug):
    box = get_box_by_slug(slug)
    if box is None:
        abort(404, description="Box not found")

    task = enqueue_llm_task(
        "box_physical_states",
        {"box_slug": box["slug"]},
    )
    task["queue_size"] = LLM_TASK_QUEUE.qsize()
    return jsonify(task), 202


@app.route("/boxes/<slug>/foundational_physics", methods=["POST"])
def enqueue_foundational_physics(slug):
    box = get_box_by_slug(slug)
    if box is None:
        abort(404, description="Box not found")

    task = enqueue_llm_task(
        "box_foundational_physics",
        {"box_slug": box["slug"]},
    )
    task["queue_size"] = LLM_TASK_QUEUE.qsize()
    return jsonify(task), 202


@app.route("/boxes/<slug>/tangibility_conservation", methods=["POST"])
def enqueue_tangibility_conservation(slug):
    box = get_box_by_slug(slug)
    if box is None:
        abort(404, description="Box not found")

    task = enqueue_llm_task(
        "box_tangibility_conservation",
        {"box_slug": box["slug"]},
    )
    task["queue_size"] = LLM_TASK_QUEUE.qsize()
    return jsonify(task), 202


@app.route("/boxes/<slug>/physical_subdomains", methods=["POST"])
def enqueue_physical_subdomains(slug):
    box = get_box_by_slug(slug)
    if box is None:
        abort(404, description="Box not found")

    task = enqueue_llm_task(
        "box_physical_subdomains",
        {"box_slug": box["slug"]},
    )
    task["queue_size"] = LLM_TASK_QUEUE.qsize()
    return jsonify(task), 202


@app.route("/boxes/<slug>/emergence_from_physics", methods=["POST"])
def enqueue_emergence_from_physics(slug):
    box = get_box_by_slug(slug)
    if box is None:
        abort(404, description="Box not found")

    task = enqueue_llm_task(
        "box_emergence_from_physics",
        {"box_slug": box["slug"]},
    )
    task["queue_size"] = LLM_TASK_QUEUE.qsize()
    return jsonify(task), 202


@app.route("/boxes/<slug>/observer_independent", methods=["POST"])
def enqueue_observer_independent(slug):
    box = get_box_by_slug(slug)
    if box is None:
        abort(404, description="Box not found")

    task = enqueue_llm_task(
        "box_observer_independent",
        {"box_slug": box["slug"]},
    )
    task["queue_size"] = LLM_TASK_QUEUE.qsize()
    return jsonify(task), 202


@app.route("/boxes/<slug>/sensory_profile", methods=["POST"])
def enqueue_sensory_profile(slug):
    box = get_box_by_slug(slug)
    if box is None:
        abort(404, description="Box not found")

    task = enqueue_llm_task(
        "box_sensory_profile",
        {"box_slug": box["slug"]},
    )
    task["queue_size"] = LLM_TASK_QUEUE.qsize()
    return jsonify(task), 202


@app.route("/boxes/<slug>/real_world_behavior", methods=["POST"])
def enqueue_real_world_behavior(slug):
    box = get_box_by_slug(slug)
    if box is None:
        abort(404, description="Box not found")

    task = enqueue_llm_task(
        "box_real_world_behavior",
        {"box_slug": box["slug"]},
    )
    task["queue_size"] = LLM_TASK_QUEUE.qsize()
    return jsonify(task), 202


@app.route("/boxes/<slug>/scenario_landscape", methods=["POST"])
def enqueue_scenario_landscape(slug):
    box = get_box_by_slug(slug)
    if box is None:
        abort(404, description="Box not found")

    task = enqueue_llm_task(
        "box_scenario_landscape",
        {"box_slug": box["slug"]},
    )
    task["queue_size"] = LLM_TASK_QUEUE.qsize()
    return jsonify(task), 202


@app.route("/boxes/<slug>/construction_reconstruction", methods=["POST"])
def enqueue_construction_reconstruction(slug):
    box = get_box_by_slug(slug)
    if box is None:
        abort(404, description="Box not found")

    task = enqueue_llm_task(
        "box_construction_reconstruction",
        {"box_slug": box["slug"]},
    )
    task["queue_size"] = LLM_TASK_QUEUE.qsize()
    return jsonify(task), 202


@app.route("/boxes/<slug>/thought_to_reality", methods=["POST"])
def enqueue_thought_to_reality(slug):
    box = get_box_by_slug(slug)
    if box is None:
        abort(404, description="Box not found")

    task = enqueue_llm_task(
        "box_thought_to_reality",
        {"box_slug": box["slug"]},
    )
    task["queue_size"] = LLM_TASK_QUEUE.qsize()
    return jsonify(task), 202
