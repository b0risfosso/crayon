import os
from openai import OpenAI
import sqlite3
from flask import Flask, jsonify, abort, request
from typing import List
from pydantic import BaseModel, Field
from openai import OpenAI, OpenAIError

app = Flask(__name__)

DB_PATH = "/var/www/site/data/narratives_data.db"  # keep consistent with your setup

client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))

class NarrativeDimension(BaseModel):
    name: str = Field(..., description="Short title for the dimension")
    thesis: str = Field(..., description="1–2 sentence distilled description")
    targets: List[str] = Field(..., description="3–6 concrete narrative targets")

class NarrativeDimensions(BaseModel):
    dimensions: List[NarrativeDimension]

class NarrativeSeed(BaseModel):
    problem: str = Field(..., description="A (Problem)")
    objective: str = Field(..., description="B (Objective)")
    solution: str = Field(..., description="Solution (Link)")

class NarrativeSeeds(BaseModel):
    seeds: List[NarrativeSeed]


DIM_SYS_MSG = """You are an assistant trained to generate narrative dimensions for any given domain.
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

SEED_SYS_MSG = """You are an assistant trained to generate Fantasiagenesis narrative seeds.
Input:
- A narrative domain (e.g., biotechnology).
- A single narrative dimension within that domain, including its thesis/description and narrative targets.

Output:
- 3–5 narrative seeds, each framed as an A→B arc in this structure:

A (Problem): [the tension, obstacle, or deficiency in the current state]  
B (Objective): [the desired outcome or state to reach]  
Solution (Link): [the mechanism, innovation, or transformation that connects A to B]

Seeds should tie directly to the narrative targets of the dimension where possible. 
Keep each seed concise, concrete, and imaginative.

Return ONLY structured JSON in the provided schema.
"""


@app.post("/api/narrative-dimensions")
# --- route ---
@app.post("/api/narrative-dimensions")
def generate_narrative_dimensions():
    data = request.get_json(silent=True) or {}
    domain = (data.get("domain") or "").strip()
    n = data.get("n")  # optional override 1..12

    if not domain:
        return jsonify({"error": "Missing 'domain'"}), 400

    count_hint = f" Generate exactly {int(n)} items." if isinstance(n, int) and 1 <= n <= 12 else ""
    usr_msg = f"Create narrative dimensions for the domain of {domain}.{count_hint}"

    try:
        # Use the parsing endpoint to coerce into our schema.
        parsed_resp = client.responses.parse(
            model=os.getenv("OPENAI_MODEL", "gpt-5"),
            input=[
                {"role": "system", "content": DIM_SYS_MSG},
                {"role": "user", "content": usr_msg},
            ],
            text_format=NarrativeDimensions,
        )

        parsed = parsed_resp.output_parsed  # → NarrativeDimensions | None
        if parsed is not None:
            # Pydantic → dict
            return jsonify({
                "domain": domain,
                "model": os.getenv("OPENAI_MODEL", "gpt-4o-2024-08-06"),
                **parsed.model_dump(),  # {"dimensions": [...]} with name/thesis/targets
            }), 200

        # Fallback: if parsing failed silently, return raw text to debug prompt/schema.
        return jsonify({
            "domain": domain,
            "model": os.getenv("OPENAI_MODEL", "gpt-4o-2024-08-06"),
            "raw": parsed_resp.output_text,
            "note": "Parsing returned None; inspect 'raw'.",
        }), 200

    except OpenAIError as e:
        return jsonify({"error": "OpenAI API error", "detail": str(e)}), 502
    except Exception as e:
        return jsonify({"error": "Internal Server Error", "detail": str(e)}), 500


@app.post("/api/narrative-seeds")
def generate_narrative_seeds():
    data = request.get_json(silent=True) or {}
    domain = (data.get("domain") or "").strip()
    dimension = (data.get("dimension") or "").strip()
    description = (data.get("description") or "").strip()
    targets = data.get("targets") or []

    if not (domain and dimension and description):
        return jsonify({"error": "Missing required fields: domain, dimension, description"}), 400

    usr_msg = (
        f"Domain: {domain}\n"
        f"Dimension: {dimension}\n"
        f"Description: {description}\n"
        f"Narrative Targets: {targets if targets else 'None provided'}\n\n"
        "Create A→B narrative seeds in this dimension."
    )

    try:
        parsed_resp = client.responses.parse(
            model=os.getenv("OPENAI_MODEL", "gpt-5"),
            input=[
                {"role": "system", "content": SEED_SYS_MSG},
                {"role": "user", "content": usr_msg},
            ],
            text_format=NarrativeSeeds,
        )

        parsed = parsed_resp.output_parsed
        if parsed:
            return jsonify({
                "domain": domain,
                "dimension": dimension,
                **parsed.model_dump()  # {"seeds": [...]}
            }), 200

        return jsonify({
            "domain": domain,
            "dimension": dimension,
            "raw": parsed_resp.output_text,
            "note": "Parsing failed, see raw output."
        }), 200

    except Exception as e:
        return jsonify({"error": "Internal Server Error", "detail": str(e)}), 500



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
