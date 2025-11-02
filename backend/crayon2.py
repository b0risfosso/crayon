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
    init_picture_db, init_usage_db, log_usage, connect, USAGE_DB
)

from prompts import wax_architect_prompt

from prompts import wax_worldwright_prompt


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



app = Flask(__name__)
client = OpenAI()

# Init DBs
init_picture_db()
init_usage_db()



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
    wax_stack: str = ""
) -> str:
    """
    Builds the instruction prompt and injects a compact JSON 'spec_json' block
    that the model must embed into the final HTML inside #worldSpec.
    """
    spec = {
        "vision": (vision or "").strip(),
        "picture_short": (picture_short or "").strip(),
        "picture_description": (picture_description or "").strip(),
        "constraints": (constraints or "").strip(),
        "deployment_context": (deployment_context or "").strip(),
        "readiness_target": (readiness_target or "").strip(),
        "wax_stack": (wax_stack or "").strip(),
    }
    spec_json = json.dumps(spec, ensure_ascii=False, separators=(",", ":"))
    return wax_worldwright_prompt.format(spec_json=spec_json)



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
        return jsonify({
            "vision": vision,
            "picture_short": picture_short,
            "picture_description": picture_description,
            "constraints": constraints,
            "deployment_context": deployment_context,
            "readiness_target": readiness_target,
            "wax_stack": output
        }), 200
    except RuntimeError as e:
        return jsonify({"error": str(e)}), 429
    except Exception as e:
        return jsonify({"error": f"Unhandled error: {e}"}), 500


@app.route("/crayon/wax_worldwright", methods=["POST"])
def crayon_wax_worldwright():
    payload = request.get_json(force=True) or {}
    vision = (payload.get("vision") or "").strip()
    picture_short = (payload.get("picture_short") or "").strip()
    picture_description = (payload.get("picture_description") or "").strip()
    wax_stack = (payload.get("wax_stack") or "").strip()

    if not vision or not picture_short or not picture_description or not wax_stack:
        return jsonify({
            "error": "Missing required field(s). Required: 'vision', 'picture_short', 'picture_description', 'wax_stack'"
        }), 400

    constraints = (payload.get("constraints") or "").strip()
    deployment_context = (payload.get("deployment_context") or "").strip()
    readiness_target = (payload.get("readiness_target") or "").strip()
    email = payload.get("email")

    try:
        html = run_wax_worldwright(
            vision=vision,
            picture_short=picture_short,
            picture_description=picture_description,
            constraints=constraints,
            deployment_context=deployment_context,
            readiness_target=readiness_target,
            wax_stack=wax_stack,
            email=email,
            model=DEFAULT_MODEL,
            endpoint_name="/crayon/wax_worldwright",
        )
        # Return as JSON so clients can save to disk; alternatively set mimetype='text/html'
        return jsonify({
            "vision": vision,
            "picture_short": picture_short,
            "deployment_context": deployment_context,
            "readiness_target": readiness_target,
            "html": html
        }), 200
    except RuntimeError as e:
        return jsonify({"error": str(e)}), 429
    except Exception as e:
        return jsonify({"error": f"Unhandled error: {e}"}), 500

# -------------------------- If-main (local testing) ---------------------------
if __name__ == "__main__":
    # Minimal test server to exercise the endpoint without your full stack
    port = int(os.getenv("CRAYON_PORT", "5001"))
    print(f"[crayon] Starting test server on http://127.0.0.1:{port}")
    app.run(host="0.0.0.0", port=port, debug=True)

