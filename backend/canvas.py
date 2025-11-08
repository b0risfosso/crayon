# canvas.py
from __future__ import annotations

import os
import json
from datetime import datetime, timezone
from typing import Optional

from flask import Flask, request, jsonify

# OpenAI SDK
from openai import OpenAI

# Optional tokenizer (falls back to heuristic)
try:
    import tiktoken  # type: ignore
    _HAS_TIKTOKEN = True
except Exception:
    _HAS_TIKTOKEN = False

from db_shared import (
    init_picture_db, init_usage_db, log_usage, connect, USAGE_DB, PICTURE_DB, 
    upsert_vision_by_text_email,   # reuse your redundancy-aware vision upsert
    upsert_wax_by_content,
    upsert_world_by_html, find_or_create_picture_by_signature, upsert_wax_by_picture_append,
    upsert_world_by_picture_overwrite, update_world_overwrite, find_world_id_by_picture_email

)

from prompts import explain_picture_prompt

from prompts import wax_architect_prompt

from prompts import wax_worldwright_prompt

# NEW: collections runner
from prompts import PROMPT_COLLECTIONS  # expects a dict[str, list[dict]]
import sqlite3  # if not already imported

import threading

import threading
import queue
import uuid
import time
from typing import Optional, Dict, Any




DEFAULT_MODEL = os.getenv("LLM_MODEL", "gpt-5-mini-2025-08-07")
DAILY_MAX_TOKENS_LIMIT = int(os.getenv("DAILY_MAX_TOKENS_LIMIT", "10000000"))  # 10M
DEFAULT_ENDPOINT_NAME = "/canvas/initialize_canvas"



SYSTEM_MSG = (
    "You are a precise JSON generator. Always return STRICT JSON with the exact keys requested. "
    "Do NOT include Markdown or explanations."
)

_PROMPT_OUTPUTS_TABLE_READY = False
_PROMPT_OUTPUTS_TABLE_LOCK = threading.Lock()


app = Flask(__name__)
client = OpenAI()

# Init DBs
init_picture_db()
init_usage_db()

# --------- Async queue for /canvas/initialize_canvas --------------------------
class _InitCanvasTask:
    __slots__ = ("task_id", "payload", "result", "error", "event")
    def __init__(self, payload: dict):
        self.task_id = str(uuid.uuid4())
        self.payload = payload
        self.result = None        # tuple[int status, dict body]
        self.error = None         # Exception
        self.event = threading.Event()

class _TaskState:
    # queued -> running -> done | error
    def __init__(self, payload: dict, task_id: Optional[str] = None):
        self.task_id: str = task_id or str(uuid.uuid4())
        self.status: str = "queued"
        self.payload: dict = payload
        self.created_ts: float = time.time()
        self.started_ts: Optional[float] = None
        self.finished_ts: Optional[float] = None
        self.result: Optional[dict] = None
        self.http_status: Optional[int] = None
        self.error: Optional[str] = None

_TASKS: Dict[str, _TaskState] = {}
_TASKS_LOCK = threading.Lock()

def _register_task(payload: dict, task_id: Optional[str] = None) -> _TaskState:
    t = _TaskState(payload, task_id=task_id)
    with _TASKS_LOCK:
        _TASKS[t.task_id] = t
    return t

def _get_task(task_id: str) -> Optional[_TaskState]:
    with _TASKS_LOCK:
        return _TASKS.get(task_id)

_CREATE_INIT_Q: "queue.Queue[_InitCanvasTask]" = queue.Queue()
_CREATE_INIT_WORKERS = []
_CREATE_INIT_WORKER_COUNT = int(os.getenv("CANVAS_INIT_WORKERS", "2"))
_TASK_RETENTION_SECONDS = int(os.getenv("CANVAS_TASK_RETENTION_SEC", "86400"))  # 1 day

def _cleanup_tasks():
    now = time.time()
    with _TASKS_LOCK:
        doomed = [tid for tid, t in _TASKS.items()
                  if t.finished_ts and (now - t.finished_ts) > _TASK_RETENTION_SECONDS]
        for tid in doomed:
            _TASKS.pop(tid, None)

def _initialize_canvas_worker():
    while True:
        qtask = _CREATE_INIT_Q.get()
        try:
            state = _get_task(qtask.task_id)
            if state:
                state.status = "running"
                state.started_ts = time.time()

            status_code, body = _initialize_canvas_sync(qtask.payload)
            qtask.result = (status_code, body)

            if state:
                state.status = "done"
                state.http_status = status_code
                state.result = body
                state.finished_ts = time.time()
        except Exception as e:
            qtask.error = e
            state = _get_task(qtask.task_id)
            if state:
                state.status = "error"
                state.error = str(e)
                state.finished_ts = time.time()
        finally:
            qtask.event.set()
            _CREATE_INIT_Q.task_done()
            _cleanup_tasks()

def _start_initialize_canvas_workers_once():
    if _CREATE_INIT_WORKERS:
        return
    for _ in range(_CREATE_INIT_WORKER_COUNT):
        t = threading.Thread(target=_initialize_canvas_worker,
                             name="initialize_canvas_worker",
                             daemon=True)
        t.start()
        _CREATE_INIT_WORKERS.append(t)

_start_initialize_canvas_workers_once()


def _now_utc_iso():
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat(timespec="seconds")

def _safe_get_picture_db_path():
    # Reuse your configured DB path if you have one; otherwise fall back
    return os.environ.get("PICTURE_DB", "/var/www/site/data/picture.db")

def _maybe_connect(db_path: str):
    # If you already have a connect(...) helper, use that.
    if "connect" in globals() and callable(globals()["connect"]):
        return globals()["connect"](db_path)
    return sqlite3.connect(db_path)


class _DD(dict):
    def __missing__(self, k): return ""


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
    Reads totals_daily for today+model from llm_usage.db.
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
    Mirrors crayon.py: accept both {prompt,input}_tokens and {completion,output}_tokens.
    Returns {'input': int, 'output': int, 'total': int}
    """
    u = getattr(resp, "usage", None)
    get = (lambda k: (u.get(k) if isinstance(u, dict) else getattr(u, k, None)) if u else None)
    inp  = get("prompt_tokens")    or get("input_tokens")    or 0
    outp = get("completion_tokens") or get("output_tokens")  or 0
    tot  = get("total_tokens") or (int(inp) + int(outp))
    return {"input": int(inp), "output": int(outp), "total": int(tot)}



def build_wax_architect_prompt(
    *,
    vision: str,
    picture_short: str,
    picture_description: str,
    constraints: str = "",
    deployment_context: str = "",
    readiness_target: str = ""
) -> str:
    """
    Formats the Wax Architect prompt with provided fields.
    Safe to use with .format() since there are no stray braces in the template.
    """
    return wax_architect_prompt.format(
        vision=(vision or "").strip(),
        picture_short=(picture_short or "").strip(),
        picture_description=(picture_description or "").strip(),
        constraints=(constraints or "").strip(),
        deployment_context=(deployment_context or "").strip(),
        readiness_target=(readiness_target or "").strip(),
    )

def build_wax_worldwright_prompt(
    *,
    vision: str,
    picture_short: str,
    picture_description: str,
    constraints: str = "",
    deployment_context: str = "",
    readiness_target: str = "",
    wax_stack: str = "",
    picture_explanation: str = "",
) -> str:
    """
    Builds the instruction prompt and injects a compact JSON 'spec_json' block
    that the model must embed into the final HTML inside #worldSpec.
    """
    spec = {
        "vision": (vision or "").strip(),
        "picture_short": (picture_short or "").strip(),
        "picture_description": (picture_description or "").strip(),
        "picture_explanation": (picture_explanation or "").strip(),
    }
    spec_json = json.dumps(spec, ensure_ascii=False, separators=(",", ":"))
    return wax_worldwright_prompt.format(spec_json=spec_json)

def run_explain_picture(
    vision_text: str,
    picture_title: str,
    picture_description: str,
    picture_function: str,
    *,
    focus: str = "",
    email: Optional[str] = None,
    model: str = DEFAULT_MODEL,
    endpoint_name: str = "/jid/explain_picture",
) -> str:
    """Generate structured text explanation (not JSON)."""
    today_tokens = get_today_model_tokens(model)
    if today_tokens >= DAILY_MAX_TOKENS_LIMIT:
        raise RuntimeError(
            f"Daily token limit reached for {model}: {today_tokens}/{DAILY_MAX_TOKENS_LIMIT}"
        )

    prompt_text = build_explain_picture_prompt(vision_text, picture_title, picture_description, picture_function, focus)

    usage_in = 0
    usage_out = 0
    request_id = None

    resp = client.responses.create(
        model=model,
        input=prompt_text
    )

    usage = _usage_from_resp(resp)
    usage_in = usage["input"]
    usage_out = usage["output"]

    content = resp.output_text

    

    # Log usage
    try:
        log_usage(
            app="jid",
            model=model,
            tokens_in=usage_in,
            tokens_out=usage_out,
            endpoint=endpoint_name,
            email=email,
            request_id=request_id,
            duration_ms=0,
            cost_usd=0.0,
            meta={"purpose": "explain_picture"},
        )
    except Exception as e:
        print(f"[WARN] usage logging failed (explain_picture): {e}")

    return content.strip()

# NEW: chat completion wrapper; uses your existing client/model if available
def _run_chat(system_text: str | None, user_text: str, *, model: Optional[str] = None):
    m = model or "gpt-5-mini-2025-08-07"
    messages = []
    resp = client.responses.create(
        model=m,
        input=user_text
    )

    usage = _usage_from_resp(resp)
    usage_in = usage["input"]
    usage_out = usage["output"]

    content = resp.output_text
    meta = {
        "usage": getattr(resp, "usage", None) and resp.usage.model_dump(),
        "id": resp.id,
    }
    return content, meta

_PROMPT_OUTPUTS_TABLE_READY = False
_PROMPT_OUTPUTS_TABLE_LOCK = threading.Lock()

def _ensure_prompt_outputs_table(db_path: str):
    global _PROMPT_OUTPUTS_TABLE_READY
    if _PROMPT_OUTPUTS_TABLE_READY:
        return
    with _PROMPT_OUTPUTS_TABLE_LOCK:
        if _PROMPT_OUTPUTS_TABLE_READY:
            return
        with _maybe_connect(db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS prompt_outputs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    vision_id INTEGER,
                    picture_id INTEGER,
                    collection TEXT NOT NULL,
                    prompt_key TEXT NOT NULL,
                    prompt_text TEXT NOT NULL,
                    system_text TEXT,
                    output_text TEXT NOT NULL,
                    model TEXT,
                    email TEXT,
                    metadata TEXT,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (vision_id) REFERENCES visions(id) ON DELETE SET NULL,
                    FOREIGN KEY (picture_id) REFERENCES pictures(id) ON DELETE SET NULL
                )
            """)
            conn.commit()
        _PROMPT_OUTPUTS_TABLE_READY = True

def _store_prompt_output_row(
    db_path: str,
    *,
    vision_id: int | None,
    picture_id: int | None,
    collection: str,
    prompt_key: str,
    prompt_text: str,
    system_text: str | None,
    output_text: str,
    model: str | None,
    email: str | None,
    metadata: dict | None,
    created_at: str
) -> None:
    """Open, insert, commit, close (no persistent connection)."""
    with _maybe_connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO prompt_outputs
                (vision_id, picture_id, collection, prompt_key, prompt_text, system_text,
                 output_text, model, email, metadata, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                vision_id, picture_id, collection, prompt_key, prompt_text, system_text,
                output_text, model, email, json.dumps(metadata) if metadata else None, created_at
            )
        )
        conn.commit()


def _run_collection_core(
    *,
    collection: str,
    inputs: dict,
    model: str | None,
    store: bool,
    email: str | None,
    vision_id: int | None,
    picture_id: int | None
) -> dict:
    """
    Internal helper that executes a named collection using the same logic currently
    inside /canvas/run_collection, and (optionally) stores outputs.
    Returns: { "collection": str, "results": [...], "stored": int, "store_errors": [...] }
    """
    if not collection:
        return {"error": "collection is required"}

    if collection not in PROMPT_COLLECTIONS:
        return {"error": f"unknown collection '{collection}'"}

    items = PROMPT_COLLECTIONS[collection]
    if not isinstance(items, list) or not all(isinstance(x, dict) and "key" in x for x in items):
        return {"error": f"collection '{collection}' has invalid structure"}

    results = []
    stored = 0
    store_errors = []
    now = _now_utc_iso()
    db_path = _safe_get_picture_db_path()

    # Ensure the output table exists (best effort)
    if store:
        try:
            _ensure_prompt_outputs_table(db_path)
        except Exception:
            store = False  # continue without storing

    for item in items:
        key = item["key"]
        system_tmpl = item.get("system")
        user_tmpl = item.get("template")
        if not user_tmpl:
            results.append({"key": key, "error": "missing 'template' in collection item"})
            continue

        try:
            system_text = system_tmpl.format_map(_DD(**inputs)) if isinstance(system_tmpl, str) else None
            prompt_text = user_tmpl.format_map(_DD(**inputs))
        except KeyError as ke:
            results.append({"key": key, "error": f"missing input variable: {ke}"})
            continue

        try:
            output_text, meta = _run_chat(system_text, prompt_text, model=model)
        except Exception as e:
            results.append({"key": key, "prompt": prompt_text, "system": system_text, "error": str(e)})
            continue

        rec = {
            "key": key,
            "prompt": prompt_text,
            "system": system_text,
            "output": output_text,
            "metadata": meta,
        }
        results.append(rec)

        if store:
            try:
                _store_prompt_output_row(
                    db_path,
                    vision_id=vision_id if isinstance(vision_id, int) else None,
                    picture_id=picture_id if isinstance(picture_id, int) else None,
                    collection=collection,
                    prompt_key=key,
                    prompt_text=prompt_text,
                    system_text=system_text,
                    output_text=output_text,
                    model=model or globals().get("DEFAULT_MODEL") or "gpt-5",
                    email=email,
                    metadata=rec.get("metadata"),
                    created_at=now,
                )
                stored += 1
            except Exception as e:
                store_errors.append(f"{key}: {type(e).__name__}: {e}")

    return {
        "collection": collection,
        "results": results,
        "stored": stored,
        "store_errors": store_errors,
    }


def _initialize_canvas_sync(payload: dict) -> tuple[int, dict]:
    """
    Synchronous core for one request:
      1) explain picture
      2) persist explanation (vision/picture/explanation)
      3) run collection (+ store outputs)
    Returns: (http_status, response_body)
    """
    # ---- normalize inputs (back-compat keys honored) -------------------------
    vision_text = (payload.get("vision") or "").strip()
    picture_title = (payload.get("picture_title") or "").strip() or (payload.get("picture_short") or "").strip()
    picture_desc  = (payload.get("picture_description") or "").strip() or (payload.get("picture_desc") or payload.get("description") or "").strip()
    picture_func  = (payload.get("picture_function") or "").strip()
    focus         = (payload.get("focus") or "").strip()
    email         = (payload.get("email") or "").strip().lower() or None

    if not vision_text or not (picture_title or picture_desc):
        return 400, {
            "ok": False,
            "error": "Missing required fields",
            "detail": "Provide 'vision' and at least one of 'picture_title' or 'picture_description'."
        }

    explain_model = payload.get("explain_model") or (globals().get("DEFAULT_MODEL") or "gpt-5")
    collection    = (payload.get("collection") or "").strip()
    model         = payload.get("model")
    store         = bool(payload.get("store", True))

    # respect explicit IDs, else fill later from persistence
    vision_id  = payload.get("vision_id") if isinstance(payload.get("vision_id"), int) else None
    picture_id = payload.get("picture_id") if isinstance(payload.get("picture_id"), int) else None

    # ---- step 1: explanation --------------------------------------------------
    try:
        explanation_text = run_explain_picture(
            vision_text=vision_text,
            picture_title=picture_title,
            picture_description=picture_desc,
            picture_function=picture_func,
            focus=focus,
            email=email,
            model=explain_model,
            endpoint_name="/canvas/initialize_canvas",
        ).strip()
    except RuntimeError as e:
        return 429, {"ok": False, "error": str(e)}
    except Exception as e:
        return 500, {"ok": False, "error": f"Unhandled error during explanation: {e}"}

    if not explanation_text:
        return 422, {"ok": False, "error": "No explanation produced"}

    # ---- step 2: persist explanation (best-effort; continue on failure) -------
    persist_warning = None
    ids = {}
    try:
        ids = persist_explanation_to_db(
            vision_text=vision_text,
            picture_title=picture_title,
            picture_text=picture_desc,
            explanation_text=explanation_text,
            focus=(focus or None),
            email=email,
            source="canvas",
        ) or {}
    except Exception as e:
        persist_warning = f"Failed to persist explanation: {type(e).__name__}: {e}"

    if vision_id is None:
        vision_id = ids.get("vision_id")
    if picture_id is None:
        picture_id = ids.get("picture_id")

    # ---- step 3: run collection (+ store) ------------------------------------
    picture_compound = f"{picture_title}\n\n{picture_desc}".strip()
    inputs = {
        "vision": vision_text,
        "picture": picture_compound,
        "picture_explanation": explanation_text,
        "constraints": payload.get("constraints", ""),
        "deployment_context": payload.get("deployment_context", ""),
        "readiness_target": payload.get("readiness_target", ""),
        # aliases used by some prompts
        "context": payload.get("deployment_context", ""),
        "integration_context": payload.get("deployment_context", ""),
        "integrations": payload.get("integrations", "")
    }

    rc = _run_collection_core(
        collection=collection,
        inputs=inputs,
        model=model,
        store=store,
        email=email,
        vision_id=vision_id if isinstance(vision_id, int) else None,
        picture_id=picture_id if isinstance(picture_id, int) else None,
    )

    if "error" in rc:
        body = {
            "ok": False,
            "explanation": explanation_text,
            "vision": vision_text,
            "focus": focus,
            "picture_title": picture_title,
            "picture_description": picture_desc,
            "vision_id": vision_id,
            "picture_id": picture_id,
            **rc
        }
        if persist_warning:
            body["persist_warning"] = persist_warning
        return 400, body

    body = {
        "ok": True,
        "explanation": explanation_text,
        "vision": vision_text,
        "focus": focus,
        "picture_title": picture_title,
        "picture_description": picture_desc,
        "vision_id": vision_id,
        "picture_id": picture_id,
        **rc
    }
    if persist_warning:
        body["persist_warning"] = persist_warning

    return 200, body




@app.route("/healthz")
def healthz():
    return jsonify({"status": "ok", "time": _now_utc_iso()}), 200


def _dictify(cur, row):
    return { d[0]: row[i] for i, d in enumerate(cur.description) }


@app.post("/canvas/run_collection")
# NEW ENDPOINT: run a named collection of prompts in one shot
def run_prompt_collection():
    data = request.get_json(force=True, silent=True) or {}
    collection = (data.get("collection") or "").strip()
    inputs = data.get("inputs") or {}
    model = data.get("model")
    store = bool(data.get("store", False))
    email = data.get("email")
    vision_id = data.get("vision_id")
    picture_id = data.get("picture_id")

    if not collection:
        return jsonify({"error": "collection is required"}), 400
    if collection not in PROMPT_COLLECTIONS:
        return jsonify({"error": f"unknown collection '{collection}'"}), 404

    items = PROMPT_COLLECTIONS[collection]
    if not isinstance(items, list) or not all(isinstance(x, dict) and "key" in x for x in items):
        return jsonify({"error": f"collection '{collection}' has invalid structure"}), 500

    results = []
    stored = 0
    store_errors = []  # <— add this
    now = _now_utc_iso()
    db_path = _safe_get_picture_db_path()

    # Ensure table exists once (short-lived connection under the hood)
    if store:
        try:
            _ensure_prompt_outputs_table(db_path)
        except Exception:
            # If table creation fails, we continue returning results but won’t store
            store = False

    for item in items:
        key = item["key"]
        system_tmpl = item.get("system")
        user_tmpl = item.get("template")
        if not user_tmpl:
            results.append({"key": key, "error": "missing 'template' in collection item"})
            continue

        # Soft-missing optional fields: switch to format_map if desired
        try:
            system_text = system_tmpl.format_map(_DD(**inputs)) if isinstance(system_tmpl, str) else None
            prompt_text = user_tmpl.format_map(_DD(**inputs))
        except KeyError as ke:
            results.append({"key": key, "error": f"missing input variable: {ke}"})
            continue

        try:
            output_text, meta = _run_chat(system_text, prompt_text, model=model)
        except Exception as e:
            results.append({"key": key, "prompt": prompt_text, "system": system_text, "error": str(e)})
            continue

        rec = {
            "key": key,
            "prompt": prompt_text,
            "system": system_text,
            "output": output_text,
            "metadata": meta,
        }
        results.append(rec)

        if store:
            try:
                _store_prompt_output_row(
                    db_path,
                    vision_id=vision_id if isinstance(vision_id, int) else None,
                    picture_id=picture_id if isinstance(picture_id, int) else None,
                    collection=collection,
                    prompt_key=key,
                    prompt_text=prompt_text,
                    system_text=system_text,
                    output_text=output_text,
                    model=model or globals().get("DEFAULT_MODEL") or "gpt-5",
                    email=email,
                    metadata=rec.get("metadata"),
                    created_at=now,
                )
                stored += 1
            except Exception as e:
                store_errors.append(f"{key}: {type(e).__name__}: {e}")  # <— capture the reason

    return jsonify({
        "collection": collection,
        "results": results,
        "stored": stored,
        "store_errors": store_errors
    })


def build_explain_picture_prompt(vision_text: str, picture_title: str, picture_description: str, picture_function: str, focus: str = "") -> str:
    try:
        return explain_picture_prompt.format(
            vision=vision_text.strip(),
            picture_title=picture_title.strip(),
            picture_description=picture_description.strip(),
            picture_function=picture_function.strip(),
            focus=(focus or "")
        )
    except KeyError as ke:
        raise RuntimeError(
            f"Prompt formatting failed on placeholder {ke!r}. "
            "Ensure braces in prompts.py are doubled {{ like this }}."
        )


@app.route("/canvas/explain_picture", methods=["POST"])
def canvas_explain_picture():
    payload = request.get_json(force=True) or {}
    vision_text = (payload.get("vision") or "").strip()
    picture_title = (payload.get("picture_title") or "").strip()
    picture_desc = (payload.get("picture_description") or "").strip()
    picture_func = (payload.get("picture_function") or "").strip()
    focus = (payload.get("focus") or "").strip()


        # Back-compat shims (if callers still send old keys)
    if not picture_title:
        picture_title = (payload.get("picture_short") or "").strip()
    if not picture_desc:
        picture_desc = (payload.get("picture_desc") or payload.get("description") or "").strip()

    # Minimal validation (require a vision and at least some picture context)
    if not vision_text or not (picture_title or picture_desc):
        return jsonify({
            "error": "Missing required fields",
            "detail": "Provide 'vision' and at least one of 'picture_title' or 'picture_description'."
        }), 400


    email = payload.get("email")
    try:
        result_text = run_explain_picture(
            vision_text=vision_text,
            picture_title=picture_title,
            picture_description=picture_desc,
            picture_function=picture_func,
            focus=focus,
            email=email,
            model=DEFAULT_MODEL,
            endpoint_name="/canvas/explain_picture",
        )

        # Persist to DB (vision + single picture with explanation in metadata)
        ids = persist_explanation_to_db(
            vision_text=vision_text,
            picture_title=picture_title,
            picture_text=picture_desc,
            explanation_text=result_text,
            focus=(focus or None),
            email=email,
            source="canvas",
        )

        return jsonify({
            "vision": vision_text,
            "focus": focus,
            "picture": picture_desc,
            "explanation": result_text,
            **ids  # includes vision_id, picture_id if saved
        }), 200
    except RuntimeError as e:
        return jsonify({"error": str(e)}), 429
    except Exception as e:
        return jsonify({"error": f"Unhandled error: {e}"}), 500


@app.post("/canvas/initialize_canvas")
def initialize_canvas():
    """
    One call that:
      1) gets PICTURE_EXPLANATION (via run_explain_picture)
      2) persists the explanation (vision/picture/explanation) to DB
      3) runs the prompt collection with that explanation
      4) stores collection outputs (short-lived DB connections per write)

    Expected JSON body (mirrors your example):
      {
        "vision": "...",
        "picture_title": "...",
        "picture_description": "...",
        "picture_function": "...",
        "focus": "...",
        "collection": "architects_all",
        "constraints": "...",
        "deployment_context": "...",
        "readiness_target": "...",
        "integrations": "",
        "email": "user@example.com",
        "picture_id": 123,          # optional (will be set from persistence if omitted)
        "vision_id": 456,           # optional (ditto)
        "model": null,              # collection model override
        "explain_model": null,      # optional override for the explanation call
        "explain_temperature": null,# if your _run_chat honors temperature; safe to ignore
        "store": true               # default True
      }
    """
    data = request.get_json(force=True, silent=True) or {}

    # 1) Explain the picture
    vision_text = (data.get("vision") or "").strip()
    picture_title = (data.get("picture_title") or "").strip() or (data.get("picture_short") or "").strip()
    picture_desc = (data.get("picture_description") or "").strip() or (data.get("picture_desc") or data.get("description") or "").strip()
    picture_func = (data.get("picture_function") or "").strip()
    focus = (data.get("focus") or "").strip()

    if not vision_text or not (picture_title or picture_desc):
        return jsonify({
            "ok": False,
            "error": "Missing required fields",
            "detail": "Provide 'vision' and at least one of 'picture_title' or 'picture_description'."
        }), 400

    email = (data.get("email") or "").strip().lower() or None
    explain_model = data.get("explain_model") or DEFAULT_MODEL

    try:
        explanation_text = run_explain_picture(
            vision_text=vision_text,
            picture_title=picture_title,
            picture_description=picture_desc,
            picture_function=picture_func,
            focus=focus,
            email=email,
            model=explain_model,
            endpoint_name="/canvas/initialize_canvas",
        ).strip()
    except RuntimeError as e:
        return jsonify({"ok": False, "error": str(e)}), 429
    except Exception as e:
        return jsonify({"ok": False, "error": f"Unhandled error during explanation: {e}"}), 500

    if not explanation_text:
        return jsonify({"ok": False, "error": "No explanation produced"}), 422

    # 2) Persist the explanation to DB (get/propagate IDs)
    try:
        ids = persist_explanation_to_db(
            vision_text=vision_text,
            picture_title=picture_title,
            picture_text=picture_desc,
            explanation_text=explanation_text,
            focus=(focus or None),
            email=email,
            source="canvas",
        ) or {}
    except Exception as e:
        # Non-fatal for running the collection; but return as warning
        ids = {}
        persist_warning = f"Failed to persist explanation: {type(e).__name__}: {e}"
    else:
        persist_warning = None

    # Respect explicit IDs if the client sent them; otherwise prefer persisted IDs
    vision_id = data.get("vision_id") if isinstance(data.get("vision_id"), int) else ids.get("vision_id")
    picture_id = data.get("picture_id") if isinstance(data.get("picture_id"), int) else ids.get("picture_id")

    # 3) Prepare inputs for the collection (matches your example contract)
    picture_compound = f"{picture_title}\n\n{picture_desc}".strip()
    inputs = {
        "vision": vision_text,
        "picture": picture_compound,
        "picture_explanation": explanation_text,
        "constraints": data.get("constraints", ""),
        "deployment_context": data.get("deployment_context", ""),
        "readiness_target": data.get("readiness_target", ""),
        # aliases used by some prompts
        "context": data.get("deployment_context", ""),
        "integration_context": data.get("deployment_context", ""),
        "integrations": data.get("integrations", "")
    }

    # 4) Run the collection + store outputs
    rc = _run_collection_core(
        collection=(data.get("collection") or "").strip(),
        inputs=inputs,
        model=data.get("model"),
        store=bool(data.get("store", True)),
        email=email,
        vision_id=vision_id if isinstance(vision_id, int) else None,
        picture_id=picture_id if isinstance(picture_id, int) else None,
    )

    if "error" in rc:
        return jsonify({"ok": False, "explanation": explanation_text, **rc}), 400

    resp = {
        "ok": True,
        "explanation": explanation_text,
        "vision": vision_text,
        "focus": focus,
        "picture_title": picture_title,
        "picture_description": picture_desc,
        "vision_id": vision_id,
        "picture_id": picture_id,
        **rc
    }
    if persist_warning:
        resp["persist_warning"] = persist_warning

    return jsonify(resp), 200
