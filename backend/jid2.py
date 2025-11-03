# jid2.py
from __future__ import annotations

import os
import re
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
    init_picture_db, init_usage_db, log_usage, connect, USAGE_DB,
    create_vision, create_picture, upsert_vision_by_text_email
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

def build_create_focuses_prompt(vision_text: str, count: str = "", must_include: str = "", exclude: str = "") -> str:
    return create_focuses_prompt.format(vision=vision_text.strip(), count=(count or ""), must_include=(must_include or ""), exclude=(exclude or ""))


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
    *, vision_text: str, picture_text: str, explanation_text: str, focus: str | None, email: str | None, source: str = "jid"
) -> dict:
    """
    Upsert vision by (text,email). Then add/append a picture row with explanation.
    """
    try:
        vision_id = upsert_vision_by_text_email(
            text=vision_text,
            email=email,
            focuses=None,
            explanation=None,  # leave vision.explanation empty here unless you want to copy explanation there as well
            source=source,
            metadata={"origin": "jid", "has_explanation": True},
        )

        # Title heuristic from picture_text
        raw = picture_text.strip()
        title = None
        for sep in [" — ", " - ", " —", ":", "–", "—", "|"]:
            if sep in raw:
                title = raw.split(sep, 1)[0].strip()
                break
        if not title:
            title = (raw[:64] + ("…" if len(raw) > 64 else "")).strip()

        picture_id = create_picture(
            vision_id=vision_id,
            focus=(focus or None),
            title=title,
            description=picture_text,
            function=None,
            explanation=explanation_text,     # store explanation on picture row
            email=email,
            order_index=0,
            status="draft",
            source=source,
            metadata={},
            assets={},
        )
        return {"vision_id": vision_id, "picture_id": picture_id}
    except Exception as e:
        print(f"[WARN] persist_explanation_to_db failed: {e}")
        return {}



@app.route("/jid/create_pictures", methods=["POST"])
def jid_create_pictures():
    payload = request.get_json(force=True) or {}
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

        return jsonify(payload_out), 200
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

        # Persist focuses inside a single vision row (metadata.focuses = [...])
        ids = persist_focuses_to_db(result, email=email, source="jid")
        payload_out = result.model_dump()
        payload_out.update(ids)  # adds vision_id if saved

        return jsonify(payload_out), 200
    except RuntimeError as e:
        return jsonify({"error": str(e)}), 429
    except Exception as e:
        return jsonify({"error": f"Unhandled error: {e}"}), 500


@app.route("/jid/explain_picture", methods=["POST"])
def jid_explain_picture():
    payload = request.get_json(force=True) or {}
    vision_text = (payload.get("vision") or "").strip()
    picture_text = (payload.get("picture") or "").strip()
    focus = (payload.get("focus") or "").strip()
    if not vision_text or not picture_text:
        return jsonify({"error": "Missing 'vision' or 'picture'"}), 400

    email = payload.get("email")
    try:
        result_text = run_explain_picture_llm(
            vision_text=vision_text,
            picture_text=picture_text,
            focus=focus,
            email=email,
            model=DEFAULT_MODEL,
            endpoint_name="/jid/explain_picture",
        )

        # Persist to DB (vision + single picture with explanation in metadata)
        ids = persist_explanation_to_db(
            vision_text=vision_text,
            picture_text=picture_text,
            explanation_text=result_text,
            focus=(focus or None),
            email=email,
            source="jid",
        )

        return jsonify({
            "vision": vision_text,
            "focus": focus,
            "picture": picture_text,
            "explanation": result_text,
            **ids  # includes vision_id, picture_id if saved
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




if __name__ == "__main__":
    port = int(os.getenv("PORT", "5000"))
    app.run(host="0.0.0.0", port=port, debug=True)