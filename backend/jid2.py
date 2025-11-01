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

import re
from pydantic import ValidationError


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
# Utils

def _iso_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

def _iso_today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")

def estimate_tokens(text: str, model: str = DEFAULT_MODEL) -> int:
    if _HAS_TIKTOKEN:
        try:
            enc = tiktoken.get_encoding("cl100k_base")
            return len(enc.encode(text))
        except Exception:
            pass
    # heuristic ~4 chars/token
    return max(1, len(text) // 4)

def get_today_model_tokens(model: str) -> int:
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

def build_create_pictures_prompt(vision_text: str) -> str:
    return create_pictures_prompt.format(vision=vision_text.strip())

JSON_OBJECT_RE = re.compile(r"\{(?:[^{}]|(?R))*\}", re.S)  # recursive-ish best-effort

def _strip_code_fences(s: str) -> str:
    # Remove ```json ... ``` fences if present
    s = s.strip()
    if s.startswith("```"):
        s = re.sub(r"^```(?:json)?\s*", "", s, flags=re.I)
        s = re.sub(r"\s*```$", "", s)
    return s.strip()

def _best_effort_json_parse(text: str) -> dict:
    """
    Be liberal in what we accept: strip fences, try full loads, or extract first JSON object.
    """
    s = _strip_code_fences(text)
    try:
        return json.loads(s)
    except json.JSONDecodeError:
        # Try to find the first JSON object in the string
        m = JSON_OBJECT_RE.search(s)
        if not m:
            raise
        return json.loads(m.group(0))


# ------------------------------------------------------------------------------
# Core LLM logic

def run_vision_to_pictures_llm(
    vision_text: str,
    *,
    email: Optional[str] = None,
    model: str = DEFAULT_MODEL,
    endpoint_name: str = DEFAULT_ENDPOINT_NAME,
    max_output_tokens: int = 2200,
) -> PicturesResponse:
    # Daily limit pre-check
    today_tokens = get_today_model_tokens(model)
    if today_tokens >= DAILY_MAX_TOKENS_LIMIT:
        raise RuntimeError(
            f"Daily token limit reached for {model}: {today_tokens} / {DAILY_MAX_TOKENS_LIMIT}"
        )

    # Build prompt
    prompt_text = build_create_pictures_prompt(vision_text)

    # Pre-call projection to avoid obvious overage
    prompt_tokens_est = estimate_tokens(prompt_text, model=model)
    projected_total_est = prompt_tokens_est + max_output_tokens
    if today_tokens + projected_total_est > DAILY_MAX_TOKENS_LIMIT:
        raise RuntimeError(
            f"Projected usage would exceed daily limit for {model}: "
            f"{today_tokens}+{projected_total_est} > {DAILY_MAX_TOKENS_LIMIT}"
        )

    # Call LLM (request JSON-only if supported)
    usage_in = prompt_tokens_est
    usage_out = 0
    request_id = None

    resp = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": SYSTEM_MSG},
            {"role": "user", "content": prompt_text},
        ],
        response_format={"type": "json_object"},  # ensure strict JSON if supported
    )

    content = resp.choices[0].message.content
    request_id = getattr(resp, "id", None)

    # Use actual usage if present
    if getattr(resp, "usage", None):
        usage_in = int(getattr(resp.usage, "prompt_tokens", usage_in) or usage_in)
        usage_out = int(getattr(resp.usage, "completion_tokens", 0) or 0)

    # Parse + validate with Pydantic
    try:
        data = _best_effort_json_parse(content)
    except json.JSONDecodeError as e:
        # Surface a helpful snippet
        snippet = content[:300].replace("\n", "\\n")
        raise RuntimeError(f"Model returned invalid JSON: {e}. Snippet: {snippet}")

    try:
        parsed = PicturesResponse(**data)
    except ValidationError as ve:
        # Show structured validation details
        raise RuntimeError(f"Pydantic validation failed: {ve.errors()}")

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
            meta={"purpose": "vision_to_pictures"},
        )
    except Exception as e:
        # Don't block on logging errors
        print(f"[WARN] usage logging failed: {e}")

    return parsed

# ------------------------------------------------------------------------------
# Flask endpoints

@app.route("/jid/create_pictures", methods=["POST"])
def jid_create_pictures():
    payload = request.get_json(force=True) or {}
    vision_text = (payload.get("vision") or "").strip()
    if not vision_text:
        return jsonify({"error": "Missing 'vision'"}), 400

    email = payload.get("email")
    try:
        result = run_vision_to_pictures_llm(
            vision_text=vision_text,
            email=email,
            model=DEFAULT_MODEL,
            endpoint_name=DEFAULT_ENDPOINT_NAME,
        )
        return jsonify(result.dict()), 200
    except RuntimeError as e:
        return jsonify({"error": str(e)}), 429
    except Exception as e:
        return jsonify({"error": f"Unhandled error: {e}"}), 500

@app.route("/healthz", methods=["GET"])
def healthz():
    return jsonify({"status": "ok", "time": _iso_now()}), 200

# ------------------------------------------------------------------------------
if __name__ == "__main__":
    port = int(os.getenv("PORT", "5000"))
    app.run(host="0.0.0.0", port=port, debug=True)
