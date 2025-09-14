import os
from openai import OpenAI
import sqlite3
from flask import Flask, jsonify, abort, request

app = Flask(__name__)

DB_PATH = "/var/www/site/data/narratives_data.db"  # keep consistent with your setup

client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))


SYS_MSG = """You are an assistant trained to generate narrative dimensions for any given domain.
Each narrative dimension should have two parts:

1. A compressed, evocative description (1–2 sentences, almost like a thesis or proverb).
   It should feel like a distilled truth or lens, e.g., 
   "Energy is control. Empires rose with coal, oil wars redrew borders, battery supply chains shape the future."

2. A short list of concrete narrative targets that exist inside this dimension.
   These are examples, subtopics, or arenas where stories can be developed, e.g., 
   "geopolitics of oil/gas, rare earths, solar supply chains, energy security."

Output format:
[Number]. [Dimension Name] — [Thesis/Description]  
Narrative Targets: [list of 3–6 examples]

Generate 5–8 narrative dimensions unless otherwise requested.
"""

@app.post("/api/narrative-dimensions")
def generate_narrative_dimensions():
    data = request.get_json()
    domain = data.get("domain")
    if not domain:
        return jsonify({"error": "Missing 'domain' field"}), 400

    usr_msg = f"Create narrative dimensions for the domain of {domain}."

    resp = client.responses.create(
        model="gpt-5",
        input=[
            {"role": "system", "content": SYS_MSG},
            {"role": "user", "content": usr_msg},
                ],
        )
    output_text = resp.output_text
    return jsonify({"domain": domain, "dimensions": output_text})


def _query_all(sql, params=()):
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys=ON;")
    try:
        cur = con.execute(sql, params)
        rows = [dict(r) for r in cur.fetchall()]
        return rows
    finally:
        con.close()


@app.get("/api/narratives")
def api_narratives():
    rows = _query_all("""
        SELECT id, title, created_at
        FROM narratives
        WHERE COALESCE(title,'') <> ''
        ORDER BY COALESCE(created_at, '') DESC, id DESC
    """)
    return jsonify(rows)


@app.get("/api/narratives/<int:narrative_id>/dimensions")
def api_narrative_dimensions(narrative_id: int):
    # Optional: 404 if the narrative doesn't exist
    exists = _query_all("SELECT 1 AS ok FROM narratives WHERE id = ? LIMIT 1", (narrative_id,))
    if not exists:
        abort(404, description="Narrative not found")

    dims = _query_all("""
        SELECT id, narrative_id, title, description, created_at
        FROM narrative_dimensions
        WHERE narrative_id = ?
        ORDER BY id ASC
    """, (narrative_id,))
    return jsonify(dims)
