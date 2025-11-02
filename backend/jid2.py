# jid2.py
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

from models import PicturesResponse
from prompts import create_pictures_prompt
from db_shared import (
    init_picture_db, init_usage_db, log_usage, connect, USAGE_DB
)

from models import FocusesResponse
from prompts import create_focuses_prompt

from prompts import explain_picture_prompt



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
def build_create_focuses_prompt(vision_text: str,
                                count: str = "",
                                must_include: str = "",
                                exclude: str = "") -> str:
    # Ensure empty fields stay empty (prompt instructs model to ignore empties)
    return create_focuses_prompt.format(
        vision=vision_text.strip(),
        count=(count or ""),
        must_include=(must_include or ""),
        exclude=(exclude or "")

# --- Picture Explanation Prompt Builder ----------------------------------------
def build_explain_picture_prompt(vision_text: str, picture_text: str, focus: str = "") -> str:
    try:
        return explain_picture_prompt.format(
            vision=vision_text.strip(),
            picture=picture_text.strip(),
            focus=(focus or "")
        )
    except KeyError as ke:
        raise RuntimeError(
            f"Prompt formatting failed on placeholder {ke!r}. "
            "Ensure braces in prompts.py are doubled {{ like this }}."
        )



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

def run_explain_picture_llm(
    vision_text: str,
    picture_text: str,
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

    prompt_text = build_explain_picture_prompt(vision_text, picture_text, focus)

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
    )


@app.route("/jid/create_pictures", methods=["POST"])
def jid_create_pictures():
    payload = request.get_json(force=True) or {}
    vision_text = (payload.get("vision") or "").strip()
    if not vision_text:
        return jsonify({"error": "Missing 'vision'"}), 400

    email = payload.get("email")
    focus = (payload.get("focus") or "").strip()  # NEW

    try:
        result = run_vision_to_pictures_llm(
            vision_text=vision_text,
            email=email,
            model=DEFAULT_MODEL,
            endpoint_name=DEFAULT_ENDPOINT_NAME,
            focus=focus,  # NEW
        )
        # If you persist to DB after LLM, keep your current logic here unchanged.
        return jsonify(result.dict()), 200
    except RuntimeError as e:
        return jsonify({"error": str(e)}), 429
    except Exception as e:
        return jsonify({"error": f"Unhandled error: {e}"}), 500



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
        return jsonify(result.dict()), 200
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




if __name__ == "__main__":
    port = int(os.getenv("PORT", "5000"))
    app.run(host="0.0.0.0", port=port, debug=True)