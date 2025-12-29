from __future__ import annotations

import sqlite3
from flask import Flask, jsonify, request
from openai import OpenAI

DB_PATH = "/var/www/site/data/lang.db"

app = Flask(__name__)
client = OpenAI()


INSTRUCTION_TEMPLATE = """
Read the following text.
Text A: {text_a}

Draft a few ideas for the how the idea, system, or world in Text A can be built by, interacted with, influenced by, or be integrated into the concept, system, world found in Text B.

Text B: {text_b}
"""

def _get_db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


@app.post("/api/lang")
def run_lang():
    data = request.get_json(silent=True) or {}
    text_a = (data.get("text_a") or "").strip()
    text_b = (data.get("text_b") or "").strip()

    if not (text_a or text_b):
        return jsonify({"error": "text_a or text_b required"}), 400

    text_input = INSTRUCTION_TEMPLATE.format(
        text_a=text_a,
        text_b=text_b
    ).strip()

    response = client.responses.create(
        model="gpt-5-mini-2025-08-07",
        input=text_input,
    )

    output_text = getattr(response, "output_text", "")
    if not output_text:
        output_text = str(response)

    conn = _get_db()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO runs (instruction, text_a, text_b, prompt, response)
        VALUES (?, ?, ?, ?, ?)
        """,
        (INSTRUCTION_TEMPLATE, text_a, text_b, text_input, output_text),
    )
    conn.commit()
    run_id = cur.lastrowid
    conn.close()

    return jsonify({"id": run_id, "result": output_text})


@app.get("/api/lang")
def list_lang():
    conn = _get_db()
    rows = conn.execute(
        """
        SELECT id, instruction, text_a, text_b, response, created_at
        FROM runs
        ORDER BY id DESC
        """
    ).fetchall()
    conn.close()

    return jsonify([dict(row) for row in rows])


if __name__ == "__main__":
    app.run(debug=True)
