#!/usr/bin/env python3
"""
colors.py

Input: art_id from art table.
Process:
- load art.art text by id
- run build_thought_sys/build_thought_user with art text as {thought}
- save output to colors table in art.db
"""

from __future__ import annotations

import os
import json
import sqlite3
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Tuple

from flask import Flask, request, jsonify, abort

from prompts import build_thought_sys, build_thought_user  # type: ignore

try:
    from openai import OpenAI
    _client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))
except Exception as e:
    _client = None
    _client_err = e

MODEL_DEFAULT = os.environ.get("COLORS_MODEL", "gpt-5.1")
APP_PORT = int(os.environ.get("PORT", "9018"))
ART_DB_PATH = os.environ.get("ART_DB_PATH", "/var/www/site/data/art.db")

app = Flask(__name__)
app.config["JSON_SORT_KEYS"] = False


# -------------------------
# Helpers
# -------------------------

def utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def require_json() -> Dict[str, Any]:
    if not request.is_json:
        abort(400, description="Request must be application/json")
    payload = request.get_json(silent=True)
    if payload is None:
        abort(400, description="Invalid JSON body")
    return payload


def get_art_db() -> sqlite3.Connection:
    conn = sqlite3.connect(ART_DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn


def fetch_art_text(art_id: int) -> Dict[str, Any]:
    db = get_art_db()
    row = db.execute("SELECT * FROM art WHERE id = ?", (art_id,)).fetchone()
    db.close()
    if row is None:
        abort(404, description=f"art id {art_id} not found")
    return dict(row)


def insert_color_row(
    art_id: int,
    input_art: str,
    output_text: str,
    model: str,
    usage: Optional[Dict[str, Any]],
    user_metadata: Dict[str, Any],
) -> Dict[str, Any]:
    created_at = utc_now_iso()
    updated_at = created_at

    md_obj = {
        "source": "colors.build_thought",
        "original_art_id": art_id,
        "usage": usage,
        "user_metadata": user_metadata,
    }
    md_str = json.dumps(md_obj, ensure_ascii=False)

    db = get_art_db()
    cur = db.execute(
        """
        INSERT INTO colors
          (art_id, input_art, output_text, model, provider, metadata, created_at, updated_at)
        VALUES (?,      ?,         ?,          ?,        ?,        ?,        ?,          ?)
        """,
        (
            art_id,
            input_art,
            output_text,
            model,
            "openai",
            md_str,
            created_at,
            updated_at,
        ),
    )
    db.commit()
    new_id = cur.lastrowid
    row = db.execute("SELECT * FROM colors WHERE id = ?", (new_id,)).fetchone()
    db.close()

    out = dict(row)
    try:
        out["metadata"] = json.loads(out.get("metadata") or "{}")
    except Exception:
        pass
    return out


# -------------------------
# Routes
# -------------------------

@app.get("/health")
def health():
    ok = _client is not None
    return jsonify({
        "ok": ok,
        "service": "colors",
        "model_default": MODEL_DEFAULT,
        "art_db_path": ART_DB_PATH,
        "time": utc_now_iso(),
        "error": None if ok else str(_client_err),
    })


@app.post("/colors/build_thought")
def colors_build_thought():
    """
    POST /colors/build_thought
    Body:
      {
        "art_id": 123,
        "model": "optional override",
        "metadata": {...} (optional passthrough)
      }

    Returns the saved colors row + expanded text.
    """
    if _client is None:
        abort(500, description=f"OpenAI client not initialized: {_client_err}")

    payload = require_json()
    art_id = payload.get("art_id")

    if not isinstance(art_id, int):
        abort(400, description="'art_id' is required and must be an integer")

    model = payload.get("model", MODEL_DEFAULT)
    user_metadata = payload.get("metadata") or {}

    # 1) load art row
    art_row = fetch_art_text(art_id)
    thought_text = (art_row.get("art") or "").strip()
    if not thought_text:
        abort(400, description=f"art id {art_id} has empty art text")

    # 2) format user prompt
    user_prompt = build_thought_user.format(thought=thought_text)

    try:
        # 3) call LLM
        resp = _client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": build_thought_sys},
                {"role": "user", "content": user_prompt},
            ],
        )
        expanded = (resp.choices[0].message.content or "").strip()

        usage = getattr(resp, "usage", None)
        usage_dict = usage.model_dump() if usage else None

        # 4) save to colors table
        colors_row = insert_color_row(
            art_id=art_id,
            input_art=thought_text,
            output_text=expanded,
            model=model,
            usage=usage_dict,
            user_metadata=user_metadata,
        )

        return jsonify({
            "art_id": art_id,
            "input_art": thought_text,
            "expanded_thought": expanded,
            "model": model,
            "usage": usage_dict,
            "saved_color": colors_row,
            "created_at": utc_now_iso(),
        }), 201

    except sqlite3.IntegrityError as e:
        # likely dedupe unique index hit
        abort(409, description=f"colors insert blocked by constraint: {e}")

    except Exception as e:
        abort(500, description=f"LLM call failed: {e}")


# Optional alias if you ever strip /colors/ in nginx
@app.post("/build_thought")
def build_thought_alias():
    return colors_build_thought()


@app.errorhandler(400)
@app.errorhandler(404)
@app.errorhandler(409)
@app.errorhandler(500)
def json_error(err):
    code = getattr(err, "code", 500)
    return jsonify({
        "error": True,
        "code": code,
        "message": getattr(err, "description", str(err)),
    }), code


@app.get("/colors/by_art/<int:art_id>")
def colors_by_art(art_id: int):
    """
    Return all color expansions for a given art_id.
    Output:
      [
        {
          "id": ...,
          "art_id": ...,
          "input_art": "...",
          "output_text": "...",
          "model": "...",
          "metadata": {...},
          "created_at": "...",
          "updated_at": "..."
        },
        ...
      ]
    """
    db = get_art_db()
    rows = db.execute(
        """
        SELECT * FROM colors
        WHERE art_id = ?
        ORDER BY created_at DESC
        """,
        (art_id,)
    ).fetchall()
    db.close()

    out = []
    for r in rows:
        row = dict(r)
        # parse metadata back to JSON
        try:
            row["metadata"] = json.loads(row.get("metadata") or "{}")
        except Exception:
            pass
        out.append(row)

    return jsonify(out)

