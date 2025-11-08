# jid2.py
from __future__ import annotations

import os
import re
import json
from datetime import datetime, timezone
import time
from typing import Optional, Dict, Any

from flask import Flask, request, jsonify

# OpenAI SDK
from openai import OpenAI

# Optional tokenizer (falls back to heuristic)
try:
    import tiktoken  # type: ignore
    _HAS_TIKTOKEN = True
except Exception:
    _HAS_TIKTOKEN = False

from models import PicturesResponse
from prompts import create_pictures_prompt
from db_shared import (
    init_picture_db, init_usage_db, log_usage, connect, USAGE_DB, PICTURE_DB,
    create_vision, create_picture, upsert_vision_by_text_email, find_picture_id_by_signature, update_picture_fields, update_vision_fields
)

from models import FocusesResponse
from prompts import create_focuses_prompt

from prompts import explain_picture_prompt

import threading
import queue
import uuid




# ------------------------------------------------------------------------------
# Config
DEFAULT_MODEL = os.getenv("JID_LLM_MODEL", "gpt-5-mini-2025-08-07")
DAILY_MAX_TOKENS_LIMIT = int(os.getenv("DAILY_MAX_TOKENS_LIMIT", "10000000"))  # 10M
DEFAULT_ENDPOINT_NAME = "/jid/create_pictures"



SYSTEM_MSG = (
    "You are a precise JSON generator. Always return STRICT JSON with the exact keys requested. "
    "Do NOT include Markdown or explanations."
)

app = Flask(__name__)
client = OpenAI()

# Init DBs
init_picture_db()
init_usage_db()

# ---- Create-Pictures work queue ---------------------------------------------
_CREATE_PICTURES_Q: "queue.Queue[_CreatePicturesTask]" = queue.Queue()
_CREATE_PICTURES_WORKERS = []
_CREATE_PICTURES_WORKER_COUNT = int(os.getenv("CREATE_PICTURES_WORKERS", "2"))

class _CreatePicturesTask:
    __slots__ = ("task_id", "payload", "result", "error", "event")
    def __init__(self, payload: dict):
        self.task_id = str(uuid.uuid4())
        self.payload = payload
        self.result = None
        self.error = None
        self.event = threading.Event()


import json as _json
import dataclasses
try:
    from flask import Response as _FlaskResponse
except Exception:
    _FlaskResponse = None

def _to_plain(obj):
    """
    Coerce objects to JSON-serializable plain Python.
    - Flask Response  -> its JSON body if possible, else parsed text, else raw text
    - pydantic v2     -> model_dump()
    - dataclasses     -> asdict()
    - everything else -> obj (must already be serializable)
    """
    # Flask Response?
    if _FlaskResponse is not None and isinstance(obj, _FlaskResponse):
        # Try JSON
        try:
            js = obj.get_json(silent=True)
            if js is not None:
                return js
        except Exception:
            pass
        # Try parse text
        try:
            txt = obj.get_data(as_text=True)
            try:
                return _json.loads(txt)
            except Exception:
                return {"raw": txt}
        except Exception:
            return {"raw": "<unreadable response>"}

    # pydantic model?
    if hasattr(obj, "model_dump") and callable(getattr(obj, "model_dump")):
        try:
            return obj.model_dump()
        except Exception:
            pass

    # dataclass?
    try:
        if dataclasses.is_dataclass(obj):
            return dataclasses.asdict(obj)
    except Exception:
        pass

    return obj



def _create_pictures_worker():
    with app.app_context():
        while True:
            task = _CREATE_PICTURES_Q.get()
            state = None
            try:
                state = _get_task(task.task_id)
                if state:
                    state.status = "running"
                    state.started_ts = time.time()

                status_code, body = _create_pictures_sync(task.payload)

                # ⬇️ Ensure result is plain JSON-serializable data
                body_plain = _to_plain(body)

                task.result = (status_code, body_plain)

                if state:
                    state.status = "done"
                    state.http_status = status_code
                    state.result = body_plain
                    state.finished_ts = time.time()
            except Exception as e:
                task.error = e
                if state:
                    state.status = "error"
                    state.error = str(e)
                    state.finished_ts = time.time()
            finally:
                task.event.set()
                _CREATE_PICTURES_Q.task_done()
                _cleanup_tasks()



def _start_create_pictures_workers_once():
    # idempotent start
    if _CREATE_PICTURES_WORKERS:
        return
    for _ in range(_CREATE_PICTURES_WORKER_COUNT):
        t = threading.Thread(target=_create_pictures_worker, name="create_pictures_worker", daemon=True)
        t.start()
        _CREATE_PICTURES_WORKERS.append(t)

# Call once during startup (e.g., right after app = Flask(__name__))
_start_create_pictures_workers_once()




# ---- Task registry (thread-safe) --------------------------------------------
class _TaskState:
    # queued -> running -> done | error
    def __init__(self, payload: dict):
        self.task_id: str = str(uuid.uuid4())
        self.status: str = "queued"
        self.payload: dict = payload
        self.created_ts: float = time.time()
        self.started_ts: Optional[float] = None
        self.finished_ts: Optional[float] = None
        self.result: Optional[dict] = None     # body from _create_pictures_sync
        self.http_status: Optional[int] = None  # HTTP code from sync fn
        self.error: Optional[str] = None

_TASKS: Dict[str, _TaskState] = {}
_TASKS_LOCK = threading.Lock()

def _register_task(payload: dict) -> _TaskState:
    t = _TaskState(payload)
    with _TASKS_LOCK:
        _TASKS[t.task_id] = t
    return t

def _get_task(task_id: str) -> Optional[_TaskState]:
    with _TASKS_LOCK:
        return _TASKS.get(task_id)

# Optional: automatic cleanup for finished tasks
_TASK_RETENTION_SECONDS = int(os.getenv("CREATE_PICTURES_RETENTION_SEC", "86400"))  # 1 day

def _cleanup_tasks():
    now = time.time()
    with _TASKS_LOCK:
        to_delete = []
        for tid, t in _TASKS.items():
            if t.finished_ts and (now - t.finished_ts) > _TASK_RETENTION_SECONDS:
                to_delete.append(tid)
        for tid in to_delete:
            _TASKS.pop(tid, None)


# ------------------------------------------------------------------------------
# Token utilities

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

# ------------------------------------------------------------------------------
# Prompt assembly

def build_create_pictures_prompt(vision_text: str, focus: str = "") -> str:
    try:
        return create_pictures_prompt.format(
            vision=vision_text.strip(),
            focus=(focus or "")
        )
    except KeyError as ke:
        raise RuntimeError(
            f"Prompt formatting failed on placeholder {ke!r}. "
            "Ensure all literal braces in prompts.py are doubled {{ like this }}."
        )

# --- Focuses: prompt assembly --------------------------------------------------

def build_create_focuses_prompt(vision_text: str, count: str = "", must_include: str = "", exclude: str = "") -> str:
    return create_focuses_prompt.format(vision=vision_text.strip(), count=(count or ""), must_include=(must_include or ""), exclude=(exclude or ""))


def _extract_title_from_picture_text(picture_text: str) -> str:
    raw = (picture_text or "").strip()
    for sep in [" — ", " - ", " —", ":", "–", "—", "|"]:
        if sep in raw:
            return raw.split(sep, 1)[0].strip()
    return (raw[:64] + ("…" if len(raw) > 64 else "")).strip()

# ------------------------------------------------------------------------------
# LLM call



def run_vision_to_pictures_llm(
    vision_text: str,
    *,
    email: Optional[str] = None,
    model: str = DEFAULT_MODEL,
    endpoint_name: str = DEFAULT_ENDPOINT_NAME,
    focus: str = "",  # NEW
) -> PicturesResponse:
    """
    Inserts `vision_text` into the structured prompt and calls the LLM.
    Enforces daily token cap for the given model.
    Validates output against PicturesResponse (Pydantic).
    Logs token usage into llm_usage.db.
    """
    # 1) Daily limit pre-check
    today_tokens = get_today_model_tokens(model)
    if today_tokens >= DAILY_MAX_TOKENS_LIMIT:
        raise RuntimeError(
            f"Daily token limit reached for {model}: {today_tokens} / {DAILY_MAX_TOKENS_LIMIT}"
        )

    # 2) Build prompt
    prompt_text = build_create_pictures_prompt(vision_text, focus=focus)

    # Prefer JSON output for reliable parsing. If your SDK supports `response_format={"type":"json_object"}`
    # or JSON schema, use that. Here we ask for JSON via system+user messages.
    system_msg = (
        "You are a precise JSON generator. Always return STRICT JSON with the exact keys requested. "
        "Do NOT include Markdown or explanations."
    )
    user_msg = prompt_text

    request_id = None

    usage_in = 0
    usage_out = 0

    try:
        # If your SDK supports "responses.create" with json response_format, you can switch to that.
        # Using chat.completions for broad compatibility:
        resp = client.responses.parse(
            model=model,
            input=[
                {"role": "system", "content": SYSTEM_MSG},
                {"role": "user", "content": user_msg},
            ],
            text_format=PicturesResponse,
        )

        parsed = getattr(resp, "output_parsed", None) or getattr(resp, "parsed", None)
        raw_text = getattr(resp, "output_text", None) or getattr(resp, "text", None) or ""

        if parsed is None:
            # Fallback: parse the raw text as JSON (strip code fences if present)
            m = re.search(r"```(?:json)?\s*(\{.*\})\s*```", raw_text, re.S)
            raw_json = m.group(1) if m else raw_text
            parsed = PicturesResponse.model_validate(json.loads(raw_json))

        usage = _usage_from_resp(resp)
        usage_in = usage["input"]
        usage_out = usage["output"]

        return parsed

    finally:
        # 6) Log usage
        total_tokens = int(usage_in) + int(usage_out)
        try:
            log_usage(
                app="jid",
                model=model,
                tokens_in=usage_in,
                tokens_out=usage_out,
                endpoint=endpoint_name,
                email=email,
                request_id=request_id,
                duration_ms=0,   # set your timing if you track it
                meta={"purpose": "vision_to_pictures"}
            )
        except Exception:
            # Avoid crashing app if logging fails
            pass


def run_vision_to_focuses_llm(
    vision_text: str,
    *,
    email: Optional[str] = None,
    count: str = "",
    must_include: str = "",
    exclude: str = "",
    model: str = DEFAULT_MODEL,
    endpoint_name: str = "/jid/create_focuses",
) -> FocusesResponse:
    # Daily limit pre-check
    today_tokens = get_today_model_tokens(model)
    if today_tokens >= DAILY_MAX_TOKENS_LIMIT:
        raise RuntimeError(
            f"Daily token limit reached for {model}: {today_tokens} / {DAILY_MAX_TOKENS_LIMIT}"
        )

    # Build prompt
    prompt_text = build_create_focuses_prompt(
        vision_text=vision_text,
        count=count,
        must_include=must_include,
        exclude=exclude,
    )

    resp = client.responses.parse(
            model=model,
            input=[
                {"role": "system", "content": SYSTEM_MSG},
                {"role": "user", "content": prompt_text},
            ],
            text_format=FocusesResponse,
        )

    parsed = getattr(resp, "output_parsed", None) or getattr(resp, "parsed", None)
    raw_text = getattr(resp, "output_text", None) or getattr(resp, "text", None) or ""

    if parsed is None:
        # Fallback: parse the raw text as JSON (strip code fences if present)
        m = re.search(r"```(?:json)?\s*(\{.*\})\s*```", raw_text, re.S)
        raw_json = m.group(1) if m else raw_text
        parsed = FocusesResponse.model_validate(json.loads(raw_json))

    usage = _usage_from_resp(resp)
    usage_in = usage["input"]
    usage_out = usage["output"]

    request_id = None

    # Log usage (non-fatal if it fails)
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
            meta={"purpose": "vision_to_focuses"},
        )
    except Exception as e:
        print(f"[WARN] usage logging failed (focuses): {e}")

    return parsed

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



def create_pictures_from_vision(vision_text: str, user_email: Optional[str] = None) -> dict:
    """
    Convenience wrapper that returns a plain dict (already Pydantic-validated).
    """
    result = run_vision_to_pictures_llm(
        vision_text=vision_text,
        email=user_email,
        model=DEFAULT_MODEL,
        endpoint_name=DEFAULT_ENDPOINT_NAME,
    )
    return result.dict()


# -------------------- Redundancy-aware Persistence Helpers --------------------
import json

def _focus_string_to_json_array(focus_text: str) -> str:
    """
    Convert a single focus string (e.g., 'Biological Dimension — Microbial metabolism and electron flow.')
    into the same JSON-array-of-objects format used by /jid/create_focuses:
      [
        {"dimension": "Biological", "focus": "Microbial metabolism and electron flow.", "goal": null}
      ]
    Robust to separators: '—', '-', ':'. Falls back to {"dimension":"General","focus":<text>}.
    """
    s = (focus_text or "").strip()
    if not s:
        return json.dumps([], ensure_ascii=False)
    return json.dumps([{"dimension": None, "focus": s}], ensure_ascii=False)



def persist_pictures_to_db(result, *, email: str | None, source: str = "jid") -> dict:
    """
    Upsert a vision by (text,email). Then append N picture rows.
    - visions.focuses: store as JSON array-of-objects (same format as /jid/create_focuses).
    - pictures.focus: keep the original single-line focus string for convenience.
    """
    try:
        # original single-line focus string from the request/model (may be None)
        focus_line = getattr(result, "focus", None) or None

        # JSON array-of-objects for the vision row (match /jid/create_focuses)
        focuses_json = _focus_string_to_json_array(focus_line) if focus_line else json.dumps([], ensure_ascii=False)

        vision_id = upsert_vision_by_text_email(
            text=result.vision,
            email=email,
            focuses=focuses_json if focus_line else None,   # only set/merge if we got a focus
            explanation=None,
            source=source,
            metadata={"origin": "jid", "pictures_count": len(result.pictures)},
        )

        picture_ids = []
        for idx, item in enumerate(result.pictures):
            pid = create_picture(
                vision_id=vision_id,
                focus=focus_line,                # store the readable single-line focus on each picture (TEXT)
                title=item.title,
                description=item.picture,
                function=item.function,
                explanation=None,
                email=email,
                order_index=idx,
                status="draft",
                source=source,
                metadata={},
                assets={},
            )
            picture_ids.append(pid)
        return {"vision_id": vision_id, "picture_ids": picture_ids}
    except Exception as e:
        print(f"[WARN] persist_pictures_to_db failed: {e}")
        return {}

def persist_focuses_to_db(result, *, email: str | None, source: str = "jid") -> dict:
    """
    Upsert vision by (text,email). Merge focuses JSON array onto visions.focuses.
    """
    try:
        focuses_payload = [
            {"dimension": f.dimension, "focus": f.focus}
            for f in result.focuses
        ]
        vision_id = upsert_vision_by_text_email(
            text=result.vision,
            email=email,
            focuses=json.dumps(focuses_payload, ensure_ascii=False),
            explanation=None,
            source=source,
            metadata={"origin": "jid", "focuses_count": len(focuses_payload)},
        )
        return {"vision_id": vision_id}
    except Exception as e:
        print(f"[WARN] persist_focuses_to_db failed: {e}")
        return {}

def persist_explanation_to_db(
    *,
    vision_text: str,
    picture_title: str,
    picture_text: str,
    explanation_text: str,
    focus: str | None,
    email: str | None,
    source: str = "jid",
) -> dict:
    """
    Policy:
      - Vision: upsert by (text,email). If 'focus' provided as string, normalize to JSON-array and MERGE into visions.focuses.
      - Picture: match by (vision_id, title, description, email). If exists, OVERWRITE explanation; else INSERT new row.
    """
    try:
        # 1) Ensure we have a vision row, then (optionally) merge focus into focuses array.
        vision_id = upsert_vision_by_text_email(
            text=vision_text,
            email=email,
            source=source,
            metadata={"origin": "jid", "has_explanation": True},
        )

        if (focus or "").strip():
            focuses_json = _focus_string_to_json_array(focus)  # JSON array string
            update_vision_fields(
                vision_id,
                focuses=focuses_json,  # will be normalized+merged inside update_vision_fields
                # explanation: leave None so we don't touch vision.explanation here
                metadata={},            # no-op merge
            )

        # 2) Title + picture dedup / update
        title = picture_title
        existing_pid = find_picture_id_by_signature(
            vision_id=vision_id,
            title=title,
            description=picture_text.strip(),
            email=email,
        )
        if existing_pid:
            # overwrite explanation; optionally update focus if provided
            update_picture_fields(
                existing_pid,
                explanation=explanation_text,
                focus=((focus or "").strip() or None),
                metadata_merge={"source_last": source},
                status=None,
            )
            return {"vision_id": vision_id, "picture_id": existing_pid}

        # 3) Insert new picture with explanation
        picture_id = create_picture(
            vision_id=vision_id,
            focus=(focus or None),
            title=title,
            description=picture_text,
            function=None,
            explanation=explanation_text,
            email=email,
            order_index=0,
            status="draft",
            source=source,
            metadata={"source_created": source},
            assets={},
        )
        return {"vision_id": vision_id, "picture_id": picture_id}
    except Exception as e:
        print(f"[WARN] persist_explanation_to_db failed: {e}")
        return {}


def _create_pictures_sync(payload: dict) -> tuple[int, dict]:
    """
    Pure, synchronous body that used to live inside the endpoint.
    Expects a single item payload: { vision, focus, email?, count?, must_include?, exclude?, ... }
    Returns (status_code, response_json_dict).
    """
    # --- example input normalization (keep exactly what your old code expects) ---
    # vision_text = payload["vision"]  # or however it was named
    # focus = payload.get("focus", "")
    # ... run your existing create pipeline ...
    # return 200, PicturesResponse(...).model_dump()

    vision_text = (payload.get("vision") or "").strip()
    if not vision_text:
        return jsonify({"error": "Missing 'vision'"}), 400

    email = payload.get("email")
    focus = (payload.get("focus") or "").strip()

    try:
        result = run_vision_to_pictures_llm(
            vision_text=vision_text,
            email=email,
            model=DEFAULT_MODEL,
            endpoint_name=DEFAULT_ENDPOINT_NAME,
            focus=focus,
        )

        # Persist to DB (non-fatal if it fails)
        ids = persist_pictures_to_db(result, email=email, source="jid")
        payload_out = result.dict()
        payload_out.update(ids)  # adds vision_id and picture_ids if saved

        return payload_out, 200
    except RuntimeError as e:
        return {"error": str(e)}, 429
    except Exception as e:
        return {"error": f"Unhandled error: {e}"}, 500



@app.route("/jid/create_pictures", methods=["POST"])
def create_pictures_submit():
    """
    Async submit-only:
      - Accepts single object, {items:[...]}, or a top-level list.
      - Enqueues each item.
      - Returns { task_ids: [ ... ] }
    """
    try:
        data = request.get_json(force=True, silent=False)
    except Exception:
        return jsonify({"error": "invalid JSON"}), 400

    if isinstance(data, list):
        items = data
    elif isinstance(data, dict) and "items" in data and isinstance(data["items"], list):
        items = data["items"]
    elif isinstance(data, dict):
        items = [data]
    else:
        return jsonify({"error": "expected object or list"}), 400

    if not items:
        return jsonify({"error": "no items to process"}), 400

    task_ids = []
    for payload in items:
        # make a registry state and a queue task that points to it
        state = _register_task(payload)
        qtask = _CreatePicturesTask(payload=payload)
        qtask.task_id = state.task_id   # ensure worker looks up the same id
        _CREATE_PICTURES_Q.put(qtask)
        task_ids.append(state.task_id)

    return jsonify({"task_ids": task_ids}), 202




@app.route("/jid/create_focuses", methods=["POST"])
def jid_create_focuses():
    payload = request.get_json(force=True) or {}
    vision_text = (payload.get("vision") or "").strip()
    if not vision_text:
        return jsonify({"error": "Missing 'vision'"}), 400

    email = payload.get("email")
    count = (payload.get("count") or "").strip()
    must_include = (payload.get("must_include") or "").strip()
    exclude = (payload.get("exclude") or "").strip()

    try:
        result = run_vision_to_focuses_llm(
            vision_text=vision_text,
            email=email,
            count=count,
            must_include=must_include,
            exclude=exclude,
            model=DEFAULT_MODEL,
            endpoint_name="/jid/create_focuses",
        )

        # Persist focuses inside a single vision row (metadata.focuses = [...])
        ids = persist_focuses_to_db(result, email=email, source="jid")
        payload_out = result.model_dump()
        payload_out.update(ids)  # adds vision_id if saved

        return jsonify(payload_out), 200
    except RuntimeError as e:
        return jsonify({"error": str(e)}), 429
    except Exception as e:
        return jsonify({"error": f"Unhandled error: {e}"}), 500


def _row_to_dict(cursor, row):
    # convenience to get dict rows without changing global connection settings
    return { d[0]: row[i] for i, d in enumerate(cursor.description) }

# ------------------------------------------------------------------------------
# Health check and startup

@app.route("/jid/healthz")
def healthz():
    return jsonify({"status": "ok", "time": _iso_today()}), 200


def _serialize_state(t: _TaskState) -> dict:
    return {
        "task_id": t.task_id,
        "status": t.status,  # queued | running | done | error
        "created_ts": t.created_ts,
        "started_ts": t.started_ts,
        "finished_ts": t.finished_ts,
        "http_status": t.http_status,
        "error": t.error,
        # Keep result lightweight here (None unless include_result=true)
    }

@app.route("/jid/create_pictures/status", methods=["GET"])
def create_pictures_status():
    include_result = request.args.get("include_result", "false").lower() == "true"
    # ... build ids ...
    out = []
    for tid in ids:
        t = _get_task(tid)
        if not t:
            out.append({"task_id": tid, "status": "unknown"})
            continue
        d = {
            "task_id": t.task_id,
            "status": t.status,
            "created_ts": t.created_ts,
            "started_ts": t.started_ts,
            "finished_ts": t.finished_ts,
            "http_status": t.http_status,
            "error": t.error,
        }
        if include_result and t.status in ("done", "error"):
            d["result"] = _to_plain(t.result)
        out.append(d)
    return jsonify({"tasks": out}), 200


