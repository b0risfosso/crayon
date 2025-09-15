import os
from openai import OpenAI
import sqlite3
from flask import Flask, jsonify, abort, request
from typing import List
from pydantic import BaseModel, Field
from openai import OpenAI, OpenAIError
# app.py (top-level, after Flask app creation)
import sqlite3, json, os
from contextlib import closing

app = Flask(__name__)

DB_PATH = "/var/www/site/data/narratives_data.db"  # keep consistent with your setup

def connect():
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    return con

def bootstrap_schema():
    with closing(connect()) as con, con:
        # Ensure narratives table exists (you already have this, but harmless)
        con.execute("""
        CREATE TABLE IF NOT EXISTS narratives (
            id INTEGER PRIMARY KEY,
            title TEXT NOT NULL,
            description TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            webpage TEXT
        );
        """)

        # Dimensions table (you already have this)
        con.execute("""
        CREATE TABLE IF NOT EXISTS narrative_dimensions (
            id INTEGER PRIMARY KEY,
            narrative_id INTEGER NOT NULL,
            title TEXT NOT NULL,
            description TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            -- optional new column added separately below
            FOREIGN KEY (narrative_id) REFERENCES narratives(id) ON DELETE CASCADE
        );
        """)

        # Add targets_json to narrative_dimensions if missing
        cols = [r["name"] for r in con.execute("PRAGMA table_info(narrative_dimensions)")]
        if "targets_json" not in cols:
            con.execute("ALTER TABLE narrative_dimensions ADD COLUMN targets_json TEXT")

        # Seeds table
        con.execute("""
        CREATE TABLE IF NOT EXISTS narrative_seeds (
            id INTEGER PRIMARY KEY,
            dimension_id INTEGER NOT NULL,
            problem TEXT NOT NULL,
            objective TEXT NOT NULL,
            solution TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            FOREIGN KEY (dimension_id) REFERENCES narrative_dimensions(id) ON DELETE CASCADE
        );
        """)

        # Helpful uniqueness to avoid dup spam
        con.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS idx_narratives_title ON narratives(title);
        """)
        con.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS idx_dim_unique ON narrative_dimensions(narrative_id, title);
        """)
        con.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS idx_seed_dedup ON narrative_seeds(dimension_id, problem, objective, solution);
        """)

bootstrap_schema()


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

def get_or_create_narrative(con, domain_title: str) -> int:
    row = con.execute("SELECT id FROM narratives WHERE title = ?", (domain_title,)).fetchone()
    if row:
        return row["id"]
    cur = con.execute("INSERT INTO narratives (title) VALUES (?)", (domain_title,))
    return cur.lastrowid

def upsert_dimension(con, narrative_id: int, name: str, thesis: str, targets: list) -> int:
    row = con.execute(
        "SELECT id FROM narrative_dimensions WHERE narrative_id=? AND title=?",
        (narrative_id, name)
    ).fetchone()
    targets_json = json.dumps(targets or [])
    if row:
        con.execute(
            "UPDATE narrative_dimensions SET description=?, targets_json=? WHERE id=?",
            (thesis, targets_json, row["id"])
        )
        return row["id"]
    cur = con.execute(
        "INSERT INTO narrative_dimensions (narrative_id, title, description, targets_json) VALUES (?,?,?,?)",
        (narrative_id, name, thesis, targets_json)
    )
    return cur.lastrowid

def insert_seed(con, dimension_id: int, problem: str, objective: str, solution: str):
    # dedup via unique index; ignore if exact duplicate
    con.execute("""
        INSERT OR IGNORE INTO narrative_seeds (dimension_id, problem, objective, solution)
        VALUES (?,?,?,?)
    """, (dimension_id, problem, objective, solution))

def find_dimension_id(con, narrative_id: int, dim_name: str):
    row = con.execute(
        "SELECT id FROM narrative_dimensions WHERE narrative_id=? AND title=?",
        (narrative_id, dim_name)
    ).fetchone()
    return row["id"] if row else None


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
            dims = parsed.model_dump()["dimensions"]

            # === NEW: save to DB ===
            with closing(connect()) as con, con:
                narrative_id = get_or_create_narrative(con, domain)
                saved = []
                for d in dims:
                    dim_id = upsert_dimension(
                        con,
                        narrative_id=narrative_id,
                        name=d["name"],
                        thesis=d["thesis"],
                        targets=d.get("targets") or []
                    )
                    saved.append({**d, "id": dim_id})

            return jsonify({
                "domain": domain,
                "model": os.getenv("OPENAI_MODEL", "gpt-5"),
                "dimensions": saved
            }), 200

        # fallback: parsing failed
        return jsonify({
            "domain": domain,
            "model": os.getenv("OPENAI_MODEL", "gpt-5"),
            "raw": parsed_resp.output_text,
            "note": "Parsing returned None; inspect 'raw'.",
        }), 200

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

        parsed = parsed_resp.output_parsed  # -> NarrativeSeeds | None
        if not parsed:
            # Return raw for debugging if parsing failed
            return jsonify({
                "domain": domain,
                "dimension": dimension,
                "raw": parsed_resp.output_text,
                "note": "Parsing failed, see raw output."
            }), 200

        seeds = parsed.model_dump()["seeds"]  # list of {problem, objective, solution}

        # === Persist to DB ===
        with closing(connect()) as con, con:
            # ensure domain exists
            narrative_id = get_or_create_narrative(con, domain)
            # ensure/refresh the dimension row with description+targets from the request
            dim_id = upsert_dimension(
                con,
                narrative_id=narrative_id,
                name=dimension,
                thesis=description,
                targets=targets
            )
            # insert seeds (dedup via UNIQUE index)
            for s in seeds:
                insert_seed(
                    con,
                    dimension_id=dim_id,
                    problem=(s.get("problem") or "").strip(),
                    objective=(s.get("objective") or "").strip(),
                    solution=(s.get("solution") or "").strip()
                )

            # return latest seeds from DB (with ids/timestamps)
            rows = con.execute("""
                SELECT id, problem, objective, solution, created_at
                FROM narrative_seeds
                WHERE dimension_id=?
                ORDER BY id DESC
                LIMIT 50
            """, (dim_id,)).fetchall()

        seeds_out = [
            {
                "id": r["id"],
                "problem": r["problem"],
                "objective": r["objective"],
                "solution": r["solution"],
                "created_at": r["created_at"]
            } for r in rows
        ]

        return jsonify({
            "domain": domain,
            "dimension": dimension,
            "dimension_id": dim_id,
            "seeds": seeds_out
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
