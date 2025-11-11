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

from sqlite3 import connect
from models import CoreIdeasResponse
from prompts import core_ideas_prompt

from models import VisionsResponse  # new
from prompts import (
    visions_from_core_idea_prompt,
    play_visions_from_core_idea_prompt,
)


def _has_column(conn, table, col):
    cur = conn.execute(f"PRAGMA table_info({table})")
    return any(r[1] == col for r in cur.fetchall())

def ensure_visions_core_idea(conn):
    # Add the column only if missing (old SQLite-safe; no IF NOT EXISTS)
    if not _has_column(conn, "visions", "core_idea_id"):
        conn.execute(
            "ALTER TABLE visions "
            "ADD COLUMN core_idea_id INTEGER REFERENCES core_ideas(id) ON DELETE SET NULL"
        )
    # Index is safe to create repeatedly with IF NOT EXISTS
    conn.execute("CREATE INDEX IF NOT EXISTS idx_visions_core_idea ON visions(core_idea_id)")
    conn.commit()




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


def run_text_to_core_ideas_llm(
    text: str,
    *,
    email: Optional[str] = None,
    model: str = DEFAULT_MODEL,
    endpoint_name: str = "/jid/create_core_ideas",
) -> CoreIdeasResponse:
    """
    Extract core ideas from arbitrary text using a structured JSON schema.
    """
    # Daily limit pre-check
    today_tokens = get_today_model_tokens(model)
    if today_tokens >= DAILY_MAX_TOKENS_LIMIT:
        raise RuntimeError(
            f"Daily token limit reached for {model}: {today_tokens} / {DAILY_MAX_TOKENS_LIMIT}"
        )

    prompt_text = core_ideas_prompt.format(text=text)

    # Estimate tokens-in before call
    usage_in = 0
    usage_out = 0
    request_id = None

    try:
        resp = client.responses.parse(
            model=model,
            input=[
                {"role": "system", "content": "Return ONLY valid JSON matching the text_format."},
                {"role": "user", "content": prompt_text},
            ],
            text_format=CoreIdeasResponse,
        )
        u = _usage_from_resp(resp)
        usage_in = u.get("input", usage_in)
        usage_out = u.get("output", 0)

        parsed = getattr(resp, "output_parsed", None) or getattr(resp, "parsed", None)
        raw_text = getattr(resp, "output_text", None) or getattr(resp, "text", None) or ""

        if parsed is None:
            # Fallback: parse the raw text as JSON (strip code fences if present)
            m = re.search(r"```(?:json)?\s*(\{.*\})\s*```", raw_text, re.S)
            raw_json = m.group(1) if m else raw_text
            parsed = CoreIdeasResponse.model_validate(json.loads(raw_json))

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
        )
        return parsed
    except Exception:
        # still log approximate input usage
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
        )
        raise


def run_core_idea_visions_llm(core_idea: str, *, email: str | None, model: str, endpoint_name: str) -> VisionsResponse:
    prompt_text = visions_from_core_idea_prompt.format(core_idea=core_idea)
    usage_in = 0
    usage_out = 0

    try:
        resp = client.responses.parse(  # follows your existing JID structured calls
            model=model,
            input=[
                {"role": "system", "content": "Return ONLY valid JSON matching the text_format."},
                {"role": "user", "content": prompt_text},
            ],
            text_format=VisionsResponse,
        )
        u = _usage_from_resp(resp)
        usage_in = u.get("input", usage_in)
        usage_out = u.get("output", 0)
        log_usage(app="jid", model=model, tokens_in=usage_in, tokens_out=usage_out,
                  endpoint=endpoint_name, email=email, request_id=None, duration_ms=0, cost_usd=0.0)

        parsed = getattr(resp, "output_parsed", None) or getattr(resp, "parsed", None)
        raw_text = getattr(resp, "output_text", None) or getattr(resp, "text", None) or ""

        if parsed is None:
            # Fallback: parse the raw text as JSON (strip code fences if present)
            m = re.search(r"```(?:json)?\s*(\{.*\})\s*```", raw_text, re.S)
            raw_json = m.group(1) if m else raw_text
            parsed = VisionsResponse.model_validate(json.loads(raw_json))

        return resp.output_parsed
    except Exception:
        log_usage(app="jid", model=model, tokens_in=usage_in, tokens_out=usage_out,
                  endpoint=endpoint_name, email=email, request_id=None, duration_ms=0, cost_usd=0.0)
        raise

def run_core_idea_play_visions_llm(core_idea: str, *, email: str | None, model: str, endpoint_name: str) -> VisionsResponse:
    prompt_text = play_visions_from_core_idea_prompt.format(core_idea=core_idea)
    usage_in = 0
    usage_out = 0

    try:
        resp = client.responses.parse(
            model=model,
            input=[
                {"role": "system", "content": "Return ONLY valid JSON matching the text_format."},
                {"role": "user", "content": prompt_text},
            ],
            text_format=VisionsResponse,
        )
        u = _usage_from_resp(resp)
        usage_in = u.get("input", usage_in)
        usage_out = u.get("output", 0)
        log_usage(app="jid", model=model, tokens_in=usage_in, tokens_out=usage_out,
                  endpoint=endpoint_name, email=email, request_id=None, duration_ms=0, cost_usd=0.0)

        
        parsed = getattr(resp, "output_parsed", None) or getattr(resp, "parsed", None)
        raw_text = getattr(resp, "output_text", None) or getattr(resp, "text", None) or ""

        if parsed is None:
            # Fallback: parse the raw text as JSON (strip code fences if present)
            m = re.search(r"```(?:json)?\s*(\{.*\})\s*```", raw_text, re.S)
            raw_json = m.group(1) if m else raw_text
            parsed = VisionsResponse.model_validate(json.loads(raw_json))


        return resp.output_parsed
    except Exception:
        log_usage(app="jid", model=model, tokens_in=usage_in, tokens_out=usage_out,
                  endpoint=endpoint_name, email=email, request_id=None, duration_ms=0, cost_usd=0.0)
        raise



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



def persist_core_ideas_to_db(ideas: list[str], *, source: str, email: Optional[str], metadata: Optional[dict] = None, payload_origin: Optional[str] = "jid") -> dict:
    """
    Insert each idea into core_ideas(source, core_idea, email, metadata, created_at, updated_at).
    Assumes triggers set timestamps if present; otherwise we set them.
    """
    db = PICTURE_DB
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    meta_json = json.dumps(metadata or {}, ensure_ascii=False)

    conn = connect(db)
    try:
        conn.execute("""
        CREATE TABLE IF NOT EXISTS core_ideas (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source TEXT NOT NULL,
            core_idea TEXT NOT NULL,
            email TEXT,
            metadata TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        """)
        # If you want dedupe, uncomment the unique index below.
        # conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_core_ideas_dedupe ON core_ideas(source, core_idea, IFNULL(email,''));")

        cur = conn.cursor()
        for idea in ideas:
            cur.execute(
                "INSERT INTO core_ideas (source, core_idea, email, origin, metadata, created_at, updated_at) VALUES (?,?,?,?,?,?, ?)",
                (source, idea, email, payload_origin, meta_json, ts, ts),
            )
        conn.commit()
        return {"inserted": len(ideas)}
    finally:
        conn.close()

def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")

def fetch_core_idea_text(core_idea_id: int) -> str | None:
    conn = connect(PICTURE_DB)
    try:
        cur = conn.execute("SELECT core_idea FROM core_ideas WHERE id = ?", (core_idea_id,))
        row = cur.fetchone()
        return row[0] if row else None
    finally:
        conn.close()

def persist_visions_for_core_idea(
    core_idea_id: int,
    items: list[dict],
    *,
    email: str | None,
    origin: str = "jid",
) -> dict:
    """
    Save each generated vision into the visions table, linked by visions.core_idea_id.
    - title -> visions.title
    - text  -> a compact joined block of 'Vision' + 'Realization'
    - source -> origin (e.g., 'jid')
    - metadata -> JSON blob with original structured fields
    """
    ts = _utc_now()
    db = PICTURE_DB
    conn = connect(db)
    try:
        cur = conn.cursor()
        inserted = 0
        for v in items:
            title = (v.get("title") or "").strip() or None
            v_text = (v.get("vision") or "").strip()
            r_text = (v.get("realization") or "").strip()
            meta = json.dumps({"core_idea_id": core_idea_id, "structured": v}, ensure_ascii=False)
            cur.execute(
                """
                INSERT INTO visions (title, text, focus, email, status, priority, source, slug, metadata, created_at, updated_at, core_idea_id)
                VALUES (?, ?, ?, ?, 'draft', 0, ?, NULL, ?, ?, ?, ?)
                """,
                (title, v_text, r_text, email, origin, meta, ts, ts, core_idea_id),
            )
            inserted += 1
        conn.commit()
        return {"inserted": inserted}
    finally:
        conn.close()




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
    """
    Query status for one or more queued create_pictures tasks.

    Query params:
      - task_id=<uuid>           (single id)  OR
      - ids=<uuid1,uuid2,...>    (comma-separated)
      - include_result=true|false   (default false) — if true and task done/error,
                                    embed the (plain) result payload for each task.

    Response:
      200 {
        "tasks": [
          {
            "task_id": "...",
            "status": "queued|running|done|error|unknown",
            "created_ts": <unix>,
            "started_ts": <unix|null>,
            "finished_ts": <unix|null>,
            "http_status": <int|null>,
            "error": "<string|null>",
            "result": { ... }          # present only if include_result=true and done/error
          },
          ...
        ]
      }
      400 { "error": "..." }  on bad query
    """
    include_result = (request.args.get("include_result", "false").lower() == "true")

    ids_param = request.args.get("ids")
    single_id = request.args.get("task_id")

    if not ids_param and not single_id:
        return jsonify({"error": "provide task_id=<id> or ids=<id1,id2,...>"}), 400

    task_ids = []
    if single_id:
        task_ids.append(single_id.strip())
    if ids_param:
        task_ids.extend([s.strip() for s in ids_param.split(",") if s.strip()])

    if not task_ids:
        return jsonify({"error": "no valid task ids provided"}), 400

    def _serialize_state(state) -> dict:
        # state may be None (unknown task)
        if state is None:
            return {"task_id": None, "status": "unknown"}

        d = {
            "task_id": state.task_id,
            "status": state.status,               # queued | running | done | error
            "created_ts": state.created_ts,
            "started_ts": state.started_ts,
            "finished_ts": state.finished_ts,
            "http_status": state.http_status,
            "error": state.error,
        }
        if include_result and state.status in ("done", "error"):
            # Ensure result is plain JSON-serializable
            d["result"] = _to_plain(state.result)
        return d

    out = []
    for tid in task_ids:
        state = _get_task(tid)
        if state is None:
            out.append({"task_id": tid, "status": "unknown"})
        else:
            out.append(_serialize_state(state))

    return jsonify({"tasks": out}), 200


@app.route("/jid/create_pictures/result", methods=["GET"])
def create_pictures_result():
    """
    Fetch the final result for a single create_pictures task.

    Query params:
      - task_id=<uuid>    (required)
      - wrapper=true|false (default false)
          false -> return the original body exactly (status code = stored http_status or 200)
          true  -> return a wrapper with metadata + result

    Responses:
      400 if task_id missing
      404 if unknown task_id
      202 while queued/running
      500 if task errored (includes error string)
      200 (or stored http_status) with final JSON body when done
    """
    tid = request.args.get("task_id", "").strip()
    if not tid:
        return jsonify({"error": "task_id required"}), 400

    wrapper = (request.args.get("wrapper", "false").lower() == "true")

    state = _get_task(tid)
    if state is None:
        return jsonify({"error": "unknown task_id", "task_id": tid}), 404

    # Not ready yet
    if state.status in ("queued", "running"):
        return jsonify({
            "task_id": state.task_id,
            "status": state.status
        }), 202

    # Error case
    if state.status == "error":
        if wrapper:
            return jsonify({
                "task_id": state.task_id,
                "status": "error",
                "error": state.error
            }), 500
        else:
            return jsonify({"error": state.error or "task failed", "task_id": state.task_id}), 500

    # Done: return the original body with its stored HTTP status (default 200)
    body_plain = _to_plain(state.result)
    http_status = state.http_status or 200

    if wrapper:
        return jsonify({
            "task_id": state.task_id,
            "status": "done",
            "http_status": http_status,
            "result": body_plain
        }), 200

    # No wrapper: behave like the original synchronous endpoint
    return jsonify(body_plain), http_status


@app.route("/jid/create_core_ideas", methods=["POST"])
def jid_create_core_ideas():
    payload = request.get_json(force=True) or {}
    text = (payload.get("text") or "").strip()
    source = (payload.get("source") or "").strip()
    email = payload.get("email")

    if not text:
        return jsonify({"error": "Missing 'text'"}), 400
    if not source:
        return jsonify({"error": "Missing 'source' (to store in core_ideas.source)"}), 400

    try:
        result = run_text_to_core_ideas_llm(text, email=email)
        stats = persist_core_ideas_to_db(result.ideas, source=source, email=email, metadata={"origin": "jid"}, payload_origin="jid")
        return jsonify({"ideas": result.ideas, **stats}), 200
    except RuntimeError as e:
        return jsonify({"error": str(e)}), 429
    except Exception as e:
        return jsonify({"error": f"Unhandled error: {e}"}), 500


@app.route("/jid/generate_visions_from_core_idea", methods=["POST"])
def jid_generate_visions_from_core_idea():
    """
    Payload: { "core_idea_id": int, "email": "...", "model": "..." }
    Uses core_idea_id to fetch text, generates visions, saves them with visions.core_idea_id.
    """
    payload = request.get_json(force=True) or {}
    cid = payload.get("core_idea_id")
    email = (payload.get("email") or None)
    model = payload.get("model") or DEFAULT_MODEL

    if not isinstance(cid, int):
        return jsonify({"error": "core_idea_id (int) is required"}), 400

    core_text = fetch_core_idea_text(cid)
    if not core_text:
        return jsonify({"error": f"core_idea_id {cid} not found"}), 404

    try:
        result = run_core_idea_visions_llm(core_text, email=email, model=model,
                                           endpoint_name="/jid/generate_visions_from_core_idea")
        stats = persist_visions_for_core_idea(cid, [v.dict() for v in result.visions], email=email, origin="jid")
        return jsonify({"core_idea_id": cid, "visions": [v.dict() for v in result.visions], **stats})
    except Exception as e:
        return jsonify({"error": f"{e}"}), 500

@app.route("/jid/generate_play_visions_from_core_idea", methods=["POST"])
def jid_generate_play_visions_from_core_idea():
    """
    Payload: { "core_idea_id": int, "email": "...", "model": "..." }
    """
    payload = request.get_json(force=True) or {}
    cid = payload.get("core_idea_id")
    email = (payload.get("email") or None)
    model = payload.get("model") or DEFAULT_MODEL

    if not isinstance(cid, int):
        return jsonify({"error": "core_idea_id (int) is required"}), 400

    core_text = fetch_core_idea_text(cid)
    if not core_text:
        return jsonify({"error": f"core_idea_id {cid} not found"}), 404

    try:
        result = run_core_idea_play_visions_llm(core_text, email=email, model=model,
                                                endpoint_name="/jid/generate_play_visions_from_core_idea")
        stats = persist_visions_for_core_idea(cid, [v.dict() for v in result.visions], email=email, origin="jid")
        return jsonify({"core_idea_id": cid, "visions": [v.dict() for v in result.visions], **stats})
    except Exception as e:
        return jsonify({"error": f"{e}"}), 500
