# crayon2.py
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

from prompts import wax_architect_prompt

from prompts import wax_worldwright_prompt

# NEW: collections runner
from prompts import PROMPT_COLLECTIONS  # expects a dict[str, list[dict]]
import sqlite3  # if not already imported

import threading







# On startup

DEFAULT_MODEL = os.getenv("LLM_MODEL", "gpt-5-mini-2025-08-07")
DAILY_MAX_TOKENS_LIMIT = int(os.getenv("DAILY_MAX_TOKENS_LIMIT", "10000000"))  # 10M
DEFAULT_ENDPOINT_NAME = "/crayon/create_wax"

SYSTEM_MSG = (
    "You are a precise JSON generator. Always return STRICT JSON with the exact keys requested. "
    "Do NOT include Markdown or explanations."
)

SYSTEM_MSG_WAX_HTML = (
    "You return ONLY a single valid HTML document. "
    "No commentary, no Markdown, no code fences. "
    "The HTML must be self-contained, with inline CSS and JS, and must run autonomously."
)

_PROMPT_OUTPUTS_TABLE_READY = False
_PROMPT_OUTPUTS_TABLE_LOCK = threading.Lock()


app = Flask(__name__)
client = OpenAI()

# Init DBs
init_picture_db()
init_usage_db()

# NEW: tiny safe helpers (won't clash with existing names)
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


# NEW: chat completion wrapper; uses your existing client/model if available
def _run_chat(system_text: str | None, user_text: str, *, model: Optional[str] = None):
    m = model or "gpt-5-mini-2025-08-07"
    messages = []
    resp = client.responses.create(
        model=model,
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
    return out, meta

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


# Example (adapt to your existing LLM call function and logging):
def run_wax_stack(
    vision: str,
    picture_short: str,
    picture_description: str,
    constraints: str = "",
    deployment_context: str = "",
    readiness_target: str = "",
    *,
    email: str | None = None,
    model: str = DEFAULT_MODEL,
    endpoint_name: str = "/crayon/wax_stack"
) -> str:


    """Generate structured text explanation (not JSON)."""
    today_tokens = get_today_model_tokens(model)
    if today_tokens >= DAILY_MAX_TOKENS_LIMIT:
        raise RuntimeError(
            f"Daily token limit reached for {model}: {today_tokens}/{DAILY_MAX_TOKENS_LIMIT}"
        )

    prompt_text = build_wax_architect_prompt(
        vision=vision,
        picture_short=picture_short,
        picture_description=picture_description,
        constraints=constraints,
        deployment_context=deployment_context,
        readiness_target=readiness_target,
    )

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
            app="crayon",
            model=model,
            tokens_in=usage_in,
            tokens_out=usage_out,
            endpoint=endpoint_name,
            email=email,
            request_id=request_id,
            duration_ms=0,
            meta={"purpose": "wax_stack"},
        )
    except Exception as e:
        print(f"[WARN] usage logging failed (explain_picture): {e}")

    return content.strip()

def run_wax_worldwright(
    *,
    vision: str,
    picture_short: str,
    picture_description: str,
    picture_explanation: str,
    constraints: str = "",
    deployment_context: str = "",
    readiness_target: str = "",
    wax_stack: str = "",
    email: Optional[str] = None,
    model: str = DEFAULT_MODEL,
    endpoint_name: str = "/crayon/wax_worldwright"
) -> str:
    """
    Returns a single self-contained HTML document as a string.
    Enforces daily token cap and logs usage.
    """
    today_tokens = get_today_model_tokens(model)
    if today_tokens >= DAILY_MAX_TOKENS_LIMIT:
        raise RuntimeError(
            f"Daily token limit reached for {model}: {today_tokens}/{DAILY_MAX_TOKENS_LIMIT}"
        )

    prompt_text = build_wax_worldwright_prompt(
        vision=vision,
        picture_short=picture_short,
        picture_description=picture_description,
        picture_explanation=picture_explanation,
        constraints=constraints,
        deployment_context=deployment_context,
        readiness_target=readiness_target,
        wax_stack=wax_stack,
    )

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

    html = resp.output_text

    # Basic sanity: ensure it looks like HTML; if not, wrap minimal boilerplate
    if "<html" not in html.lower():
        html = f"<!DOCTYPE html><html><head><meta charset='utf-8'><title>Wax World</title></head><body><pre>{html}</pre></body></html>"

    # Log usage (non-fatal)
    try:
        log_usage(
            app="crayon",
            model=model,
            tokens_in=usage_in,
            tokens_out=usage_out,
            endpoint=endpoint_name,
            email=email,
            request_id=request_id,
            duration_ms=0,
            cost_usd=0.0,
            meta={"purpose": "wax_worldwright"},
        )
    except Exception as e:
        print(f"[WARN] usage logging failed (wax_worldwright): {e}")

    return html.strip()


@app.route("/crayon/wax_stack", methods=["POST"])
def crayon_wax_stack():
    payload = request.get_json(force=True) or {}
    vision = (payload.get("vision") or "").strip()
    picture_short = (payload.get("picture_short") or "").strip()
    picture_description = (payload.get("picture_description") or "").strip()

    if not vision or not picture_short or not picture_description:
        return jsonify({"error": "Missing one or more required fields: 'vision', 'picture_short', 'picture_description'"}), 400

    constraints = (payload.get("constraints") or "").strip()
    deployment_context = (payload.get("deployment_context") or "").strip()
    readiness_target = (payload.get("readiness_target") or "").strip()
    email = payload.get("email")

    try:
        output = run_wax_stack(
            vision=vision,
            picture_short=picture_short,
            picture_description=picture_description,
            constraints=constraints,
            deployment_context=deployment_context,
            readiness_target=readiness_target,
            email=email,
            model=DEFAULT_MODEL,
            endpoint_name="/crayon/wax_stack",
        )

        # --- Persist: upsert vision (same as before) --------------------------
        vision_id = upsert_vision_by_text_email(
            text=vision,
            email=email,
            source="crayon",
            metadata={
                "origin": "crayon",
                "picture_short": picture_short,
                "deployment_context": deployment_context,
                "readiness_target": readiness_target,
            },
        )

        # --- NEW: find or create the picture row ------------------------------
        picture_id = find_or_create_picture_by_signature(
            vision_id=vision_id,
            title=picture_short,
            description=picture_description,
            email=email,
            source="crayon",
            default_status="draft",
            metadata={"from": "wax_stack_endpoint"}
        )

        # --- NEW: upsert wax by (picture_id, email) with APPEND policy --------
        wax_title = f"Wax Stack for: {picture_short or vision}"
        wax_id = upsert_wax_by_picture_append(
            vision_id=vision_id,
            picture_id=picture_id,
            title=wax_title,
            content=output,
            email=email,
            source="crayon",
            metadata={
                "constraints": constraints,
                "deployment_context": deployment_context,
                "readiness_target": readiness_target
            },
        )

        return jsonify({
            "vision": vision,
            "vision_id": vision_id,
            "picture_short": picture_short,
            "picture_description": picture_description,
            "constraints": constraints,
            "deployment_context": deployment_context,
            "readiness_target": readiness_target,
            "wax_stack": output,
            "picture_id": picture_id,      # NEW
            "wax_id": wax_id               # same id if appending to existing wax row
        }), 200
    except RuntimeError as e:
        return jsonify({"error": str(e)}), 429
    except Exception as e:
        return jsonify({"error": f"Unhandled error: {e}"}), 500



@app.route("/crayon/wax_worldwright", methods=["POST"])
def crayon_wax_worldwright():
    payload = request.get_json(force=True) or {}
    
    vision              = (payload.get("vision") or "").strip()
    picture_short       = (payload.get("picture_short") or "").strip()
    picture_description = (payload.get("picture_description") or "").strip()


    wax_stack           = (payload.get("wax_stack") or "").strip()
    picture_explanation = (payload.get("picture_explanation") or "").strip()

    if not vision or not picture_short or not picture_description:
        return jsonify({"error": "Missing 'vision', 'picture_short', or 'picture_description'"}), 400

    if not wax_stack and not picture_explanation:
        return jsonify({"error": "Provide either 'wax_stack' or 'picture_explanation'"}), 400

    constraints         = (payload.get("constraints") or "").strip()
    deployment_context  = (payload.get("deployment_context") or "").strip()
    readiness_target    = (payload.get("readiness_target") or "").strip()
    email               = (payload.get("email") or None)

    try:
        html = run_wax_worldwright(
            vision=vision,
            picture_short=picture_short,
            picture_description=picture_description,
            wax_stack=wax_stack,
            picture_explanation=picture_explanation,   # <-- NEW
            constraints=constraints,
            deployment_context=deployment_context,
            readiness_target=readiness_target,
        )

        # --- Persist: upsert vision, upsert wax (from provided wax_stack), then upsert world by html hash
        vision_id = upsert_vision_by_text_email(
            text=vision,
            email=email,
            source="crayon",
            metadata={
                "origin": "crayon",
                "picture_short": picture_short,
                "deployment_context": deployment_context,
                "readiness_target": readiness_target,
            },
        )

        # --- NEW: find/create the picture row (by title+description+email) ----
        picture_id = find_or_create_picture_by_signature(
            vision_id=vision_id,
            title=picture_short,
            description=picture_description,
            email=email,
            source="crayon",
            default_status="draft",
            metadata={"from": "wax_worldwright_endpoint"}
        )

        wax_id = None
        if wax_stack:
            wax_title = f"Wax Stack for: {picture_short or vision}"
            wax_id = upsert_wax_by_content(
                vision_id=vision_id,
                picture_id=picture_id,
                title=wax_title,
                content=wax_stack,
                email=email,
                source="crayon",
                metadata={
                    "constraints": constraints,
                    "picture_description": picture_description,
                    "source_for_world": "wax_worldwright_input"
                },
            )

        world_title = f"World: {picture_short or vision}"
        world_id = upsert_world_by_picture_overwrite(
            vision_id=vision_id,
            picture_id=picture_id,
            wax_id=wax_id,   # may be None
            title=world_title,
            html=html,
            email=email,
            source="crayon",
            metadata={
                "constraints": constraints,
                "deployment_context": deployment_context,
                "readiness_target": readiness_target,
                "picture_explanation_present": bool(picture_explanation),
            },
        )

        # Return as JSON so clients can save to disk; alternatively set mimetype='text/html'
        return jsonify({
            "vision": vision,
            "vision_id": vision_id,
            "picture_short": picture_short,
            "picture_id": picture_id,             # NEW
            "deployment_context": deployment_context,
            "readiness_target": readiness_target,
            "wax_id": wax_id,
            "world_id": world_id,
            "html": html
        }), 200
    except RuntimeError as e:
        return jsonify({"error": str(e)}), 429
    except Exception as e:
        return jsonify({"error": f"Unhandled error: {e}"}), 500



# ------------------------------------------------------------------------------
# Health check and startup

@app.route("/healthz")
def healthz():
    return jsonify({"status": "ok", "time": _iso_now()}), 200


@app.route("/usage/today", methods=["GET"])
def usage_today():
    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    model = request.args.get("model", DEFAULT_MODEL)
    conn = connect(USAGE_DB)
    try:
        row = conn.execute(
            "SELECT tokens_in, tokens_out, total_tokens, calls FROM totals_daily WHERE day=? AND model=?",
            (day, model),
        ).fetchone()
        data = {"day": day, "model": model}
        if row:
            data.update({"tokens_in": row[0], "tokens_out": row[1], "total_tokens": row[2], "calls": row[3]})
        else:
            data.update({"tokens_in": 0, "tokens_out": 0, "total_tokens": 0, "calls": 0})
        return jsonify(data), 200
    finally:
        conn.close()

# --- Worlds lookup + HTML fetch ------------------------------------------------
from flask import request, jsonify

def _dictify(cur, row):
    return { d[0]: row[i] for i, d in enumerate(cur.description) }

@app.route("/crayon/worlds/lookup", methods=["POST"])
def crayon_worlds_lookup():
    """
    Batch lookup: given picture_ids, return the most recent world (if any) for each.
    Body: { "picture_ids": [1,2,3] }
    Resp: { "worlds": { "1": {"id": 10, "picture_id":1, "updated_at":"..."}, ... } }
    """
    payload = request.get_json(force=True) or {}
    ids = payload.get("picture_ids") or []
    if not isinstance(ids, list) or not ids:
        return jsonify({"worlds": {}})

    # sanitize to ints
    try:
        pic_ids = [int(x) for x in ids if str(x).strip() != ""]
    except Exception:
        return jsonify({"error": "picture_ids must be integers"}), 400

    if not pic_ids:
        return jsonify({"worlds": {}})

    conn = connect(PICTURE_DB)
    cur = conn.cursor()
    placeholders = ",".join("?" for _ in pic_ids)

    # get latest world per picture_id
    cur.execute(f"""
        SELECT w1.id, w1.picture_id, w1.updated_at
        FROM worlds w1
        JOIN (
          SELECT picture_id, MAX(COALESCE(updated_at, created_at)) AS mx
          FROM worlds
          WHERE picture_id IN ({placeholders})
          GROUP BY picture_id
        ) w2
        ON w1.picture_id = w2.picture_id
        AND COALESCE(w1.updated_at, w1.created_at) = w2.mx
    """, tuple(pic_ids))
    rows = cur.fetchall()

    out = {}
    for r in rows:
        d = _dictify(cur, r)
        out[str(d["picture_id"])] = {
            "id": d["id"],
            "picture_id": d["picture_id"],
            "updated_at": d.get("updated_at"),
        }

    return jsonify({"worlds": out})


@app.route("/crayon/world/html", methods=["GET"])
def crayon_world_html():
    """
    Fetch HTML for a specific world id.
    Query: ?id=<world_id>
    Resp: { "id": 10, "html": "<!doctype html>..." }
    """
    wid = (request.args.get("id") or "").strip()
    if not wid.isdigit():
        return jsonify({"error": "id is required (integer)"}), 400

    conn = connect(PICTURE_DB)
    cur = conn.cursor()
    cur.execute("SELECT id, html FROM worlds WHERE id = ?", (int(wid),))
    row = cur.fetchone()
    if not row:
        return jsonify({"error": "world not found"}), 404

    d = _dictify(cur, row)
    return jsonify({"id": d["id"], "html": d["html"]})


@app.post("/crayon/run_collection")
# NEW ENDPOINT: run a named collection of prompts in one shot@app.post("/crayon/run_collection")
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
            except Exception:
                # Don’t fail the request on a write error
                pass

    return jsonify({
        "collection": collection,
        "results": results,
        "stored": stored
    })


@app.get("/crayon/architectures")
def list_architectures():
    """
    Query params:
      picture_id (int)               -- required unless vision_id provided
      vision_id  (int)               -- optional
      collection (str)               -- optional filter (e.g., 'duet_worldwright_x_wax')
      include_body (0|1)             -- include output_text (default 0)
      limit (int)                    -- default 50
    Returns: { items: [{ id, collection, prompt_key, created_at, model, email, ...(maybe output_text) }], count }
    """
    pic_id = request.args.get("picture_id", type=int)
    vis_id = request.args.get("vision_id", type=int)
    if not pic_id and not vis_id:
        return jsonify({"error": "picture_id or vision_id is required"}), 400

    collection = (request.args.get("collection") or "").strip() or None
    include_body = bool(int(request.args.get("include_body", "0")))
    limit = request.args.get("limit", default=50, type=int)
    db_path = _safe_get_picture_db_path()

    # Ensure table (no-op if already created)
    try:
        _ensure_prompt_outputs_table(db_path)
    except Exception:
        return jsonify({"items": [], "count": 0})

    cols = "id, collection, prompt_key, created_at, model, email"
    if include_body:
        cols += ", output_text"

    where = []
    args = []
    if pic_id:
        where.append("picture_id = ?")
        args.append(pic_id)
    if vis_id:
        where.append("vision_id = ?")
        args.append(vis_id)
    if collection:
        where.append("collection = ?")
        args.append(collection)

    sql = f"SELECT {cols} FROM prompt_outputs"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY created_at DESC, id DESC LIMIT ?"
    args.append(limit)

    items = []
    with _maybe_connect(db_path) as conn:
        cur = conn.execute(sql, tuple(args))
        cols_list = [d[0] for d in cur.description]
        for row in cur.fetchall():
            items.append({k: v for k, v in zip(cols_list, row)})
    return jsonify({"items": items, "count": len(items)})


@app.get("/crayon/architecture/<int:arch_id>")
def get_architecture(arch_id: int):
    db_path = _safe_get_picture_db_path()
    try:
        _ensure_prompt_outputs_table(db_path)
    except Exception:
        return jsonify({"error": "not found"}), 404

    with _maybe_connect(db_path) as conn:
        cur = conn.execute("""
            SELECT id, vision_id, picture_id, collection, prompt_key, prompt_text, system_text,
                   output_text, model, email, metadata, created_at
            FROM prompt_outputs
            WHERE id = ?
        """, (arch_id,))
        row = cur.fetchone()
    if not row:
        return jsonify({"error": "not found"}), 404

    cols = [d[0] for d in cur.description]
    rec = {k: v for k, v in zip(cols, row)}
    # Try to parse metadata
    try:
        if rec.get("metadata"):
            rec["metadata"] = json.loads(rec["metadata"])
    except Exception:
        pass
    return jsonify(rec)

