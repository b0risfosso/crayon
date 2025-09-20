import os
from openai import OpenAI
import sqlite3
from flask import Flask, jsonify, abort, request, Response
from typing import List
from pydantic import BaseModel, Field
from openai import OpenAI, OpenAIError
# app.py (top-level, after Flask app creation)
import sqlite3, json, os
from contextlib import closing
import hashlib 
import re

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

        # === Box-of-Dirt artifacts tied to seeds ===
        con.execute("""
        CREATE TABLE IF NOT EXISTS seed_artifacts (
            id INTEGER PRIMARY KEY,
            seed_id INTEGER NOT NULL,
            kind TEXT NOT NULL DEFAULT 'box_of_dirt',      -- future-proof: other artifact kinds later
            title TEXT,
            html TEXT NOT NULL,                             -- full-page HTML or body-only HTML
            doc_format TEXT NOT NULL DEFAULT 'full'         -- 'full' or 'body'
                CHECK (doc_format IN ('full','body')),
            version INTEGER NOT NULL DEFAULT 1,             -- monotonically increasing per (seed_id, kind)
            is_published INTEGER NOT NULL DEFAULT 0,        -- 0/1
            checksum TEXT,                                  -- sha256(html) for cache/ETag
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now')),
            FOREIGN KEY (seed_id) REFERENCES narrative_seeds(id) ON DELETE CASCADE
        );
        """)

        # Useful indexes
        con.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS idx_seed_artifacts_unique
        ON seed_artifacts(seed_id, kind, version);
        """)
        con.execute("""
        CREATE INDEX IF NOT EXISTS idx_seed_artifacts_lookup
        ON seed_artifacts(seed_id, kind, is_published, version);
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

class NarrativePrototype(BaseModel):
    core_intent: str = Field(..., description="Smallest truth or principle tested")
    minimal_build: str = Field(..., description="Tiny computational/physical/narrative sketch")
    load_bearing_test: str = Field(..., description="What to show and the validating reaction")
    first_eyes: List[str] = Field(..., description="Who sees it first")
    why_box_of_dirt: str = Field(..., description="Why it's minimal, disposable, growth-inviting")

PROTOTYPE_SYS_MSG = """You are a narrative prototyper.
Your task is to translate narrative seeds into narrative prototypes.
A narrative prototype — also called a "box of dirt" — is a minimal, disposable artifact
that embodies the intent of the seed, tests whether the idea feels real, and invites growth into more complex systems.

Your output must include:
1. Core Intent — the smallest truth or principle the prototype tests.
2. Minimal Build — a storyboard, dashboard, flow chart, or pre-scripted simulation 
   that illustrates how the system would work. It should be conceptual and visualizable,
   not a functional application. No real backend, uploads, or panels — only mocked or prefilled flows.
3. Load-Bearing Test — what to show to first eyes and what reaction would validate it.
4. First Eyes — who to put it in front of first (supporters, skeptics, peers).
5. Why This is a Box of Dirt — how it is minimal, disposable, and growth-inviting.

Do not output code or a full implementation; focus only on the simplest conceptual sketch.

Return ONLY structured JSON in the provided schema.
"""


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

BODY_WRAPPER_STYLE = """
<style>
  :root { color-scheme: light dark; }
  body { font-family: system-ui, sans-serif; margin: 2rem; max-width: 1000px; }
  h1 { font-size: 1.6rem; margin-bottom: .5rem; }
  h2 { font-size: 1.2rem; margin: 1.2rem 0 .6rem; }
  .grid { display: grid; gap: .8rem; }
  .cols-2 { grid-template-columns: 1fr 1fr; }
  .cols-3 { grid-template-columns: 1fr 1fr 1fr; }
  @media (max-width: 800px){ .cols-2, .cols-3 { grid-template-columns: 1fr; } }
  .placeholder { height: 120px; border: 1px dashed #c8c8c8; border-radius: .5rem; display: grid; place-items: center; color: #666; background: #fff; }
  .card { border: 1px solid #d0d0d0; border-radius: .6rem; padding: .9rem 1rem; background: #fff; }
  .card h3 { margin: .2rem 0 .5rem; font-size: 1rem; }
  .thesis { margin: .4rem 0 .3rem; font-style: italic; }
  .muted { opacity: .7; font-size: .9rem; }
  .list-compact li { margin:.25rem 0; }
  .badge { display:inline-block; padding:.2rem .45rem; border:1px solid #d0d0d0; border-radius:.4rem; background:#fff; font-size:.8rem; }
</style>
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


def _prototype_user_msg(domain: str, dimension: str, problem: str, objective: str, solution: str) -> str:
    return (
        f"Narrative Domain: {domain}\n"
        f"Narrative Dimension: {dimension}\n"
        f"Narrative Seed:\n"
        f"A (Problem): {problem}\n"
        f"B (Objective): {objective}\n"
        f"Solution (Link): {solution}\n\n"
        "Construct a narrative prototype sketch following the format defined in the system message."
    )

def _artifact_next_version(con, seed_id: int, kind: str = "box_of_dirt") -> int:
    row = con.execute(
        "SELECT COALESCE(MAX(version), 0) AS v FROM seed_artifacts WHERE seed_id=? AND kind=?",
        (seed_id, kind)
    ).fetchone()
    return int(row["v"]) + 1

def _seed_exists(con, seed_id: int) -> bool:
    r = con.execute("SELECT 1 FROM narrative_seeds WHERE id=? LIMIT 1", (seed_id,)).fetchone()
    return bool(r)

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


@app.get("/api/dimensions/<int:dimension_id>/seeds")
def api_dimension_seeds(dimension_id: int):
    rows = _query_all("""
        SELECT id, problem, objective, solution, created_at
        FROM narrative_seeds
        WHERE dimension_id = ?
        ORDER BY id DESC
        LIMIT 200
    """, (dimension_id,))
    return jsonify({"ok": True, "dimension_id": dimension_id, "seeds": rows})


@app.post("/api/narrative-prototype")
def api_narrative_prototype():
    data = request.get_json(silent=True) or {}
    domain = (data.get("domain") or "").strip()
    dimension = (data.get("dimension") or "").strip()

    # Accept either flat fields or a nested seed object
    seed = data.get("seed") or {}
    problem = (data.get("problem") or seed.get("problem") or "").strip()
    objective = (data.get("objective") or seed.get("objective") or "").strip()
    solution = (data.get("solution") or seed.get("solution") or "").strip()

    # Validation
    missing = []
    if not domain: missing.append("domain")
    if not dimension: missing.append("dimension")
    if not problem: missing.append("problem")
    if not objective: missing.append("objective")
    if not solution: missing.append("solution")
    if missing:
        return jsonify({"error": f"Missing required fields: {', '.join(missing)}"}), 400

    usr_msg = _prototype_user_msg(domain, dimension, problem, objective, solution)

    try:
        parsed_resp = client.responses.parse(
            model=os.getenv("OPENAI_MODEL", "gpt-5"),
            input=[
                {"role": "system", "content": PROTOTYPE_SYS_MSG},
                {"role": "user", "content": usr_msg},
            ],
            text_format=NarrativePrototype,
        )

        parsed = parsed_resp.output_parsed  # -> NarrativePrototype | None
        if not parsed:
            # Helpful debug path if parsing fails
            return jsonify({
                "domain": domain,
                "dimension": dimension,
                "raw": parsed_resp.output_text,
                "note": "Parsing failed; 'raw' contains the unparsed model output."
            }), 200

        proto = parsed.model_dump()
        return jsonify({
            "ok": True,
            "domain": domain,
            "dimension": dimension,
            "prototype": proto
        }), 200

    except Exception as e:
        return jsonify({"error": "Internal Server Error", "detail": str(e)}), 500


@app.get("/api/seeds/<int:seed_id>/box")
def api_get_seed_box(seed_id: int):
    """
    Returns the latest published artifact for this seed (kind='box_of_dirt').
    Optional query params:
      - kind: override artifact kind (default box_of_dirt)
      - version: fetch a specific version (int)
      - draft=1: if set, prefer latest version even if not published
    """
    kind = (request.args.get("kind") or "box_of_dirt").strip()
    version = request.args.get("version")
    draft = request.args.get("draft") in ("1", "true", "yes")

    with closing(connect()) as con:
        if not _seed_exists(con, seed_id):
            abort(404, description="Seed not found")

        params = [seed_id, kind]
        if version is not None:
            row = con.execute("""
                SELECT id, seed_id, kind, title, html, doc_format, version, is_published, checksum,
                       created_at, updated_at
                FROM seed_artifacts
                WHERE seed_id=? AND kind=? AND version=?
                LIMIT 1
            """, (seed_id, kind, int(version))).fetchone()
        else:
            if draft:
                row = con.execute("""
                    SELECT id, seed_id, kind, title, html, doc_format, version, is_published, checksum,
                           created_at, updated_at
                    FROM seed_artifacts
                    WHERE seed_id=? AND kind=?
                    ORDER BY version DESC
                    LIMIT 1
                """, params).fetchone()
            else:
                row = con.execute("""
                    SELECT id, seed_id, kind, title, html, doc_format, version, is_published, checksum,
                           created_at, updated_at
                    FROM seed_artifacts
                    WHERE seed_id=? AND kind=? AND is_published=1
                    ORDER BY version DESC
                    LIMIT 1
                """, params).fetchone()

        if not row:
            abort(404, description="No artifact found for this seed/kind")

        payload = dict(row)

        # Simple ETag support for cache friendliness
        etag = payload.get("checksum") or ""
        if etag and request.headers.get("If-None-Match") == etag:
            return ("", 304, {"ETag": etag})

        resp = jsonify(payload)
        if etag:
            resp.headers["ETag"] = etag
        return resp


@app.post("/api/seeds/<int:seed_id>/box")
def api_create_seed_box(seed_id: int):
    """
    Create a new artifact version for a seed.
    Body JSON:
      - html (str, required)
      - title (str, optional)
      - kind (str, default 'box_of_dirt')
      - doc_format ('full'|'body', default matches table default)
      - publish (bool, default false)  # set is_published=1 on insert
    """
    data = request.get_json(silent=True) or {}
    html = (data.get("html") or "").strip()
    if not html:
        return jsonify({"error": "html is required"}), 400

    kind = (data.get("kind") or "box_of_dirt").strip()
    title = (data.get("title") or "").strip() or None
    doc_format = (data.get("doc_format") or "full").strip()
    if doc_format not in ("full", "body"):
        return jsonify({"error": "doc_format must be 'full' or 'body'"}), 400
    publish = bool(data.get("publish"))

    checksum = hashlib.sha256(html.encode("utf-8")).hexdigest()

    with closing(connect()) as con, con:
        if not _seed_exists(con, seed_id):
            return jsonify({"error": "Seed not found"}), 404

        version = _artifact_next_version(con, seed_id, kind)
        con.execute("""
            INSERT INTO seed_artifacts (seed_id, kind, title, html, doc_format, version, is_published, checksum, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
        """, (seed_id, kind, title, html, doc_format, version, 1 if publish else 0, checksum))

        new_id = con.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]

    return jsonify({
        "ok": True,
        "artifact_id": new_id,
        "seed_id": seed_id,
        "kind": kind,
        "version": version,
        "is_published": bool(publish),
        "checksum": checksum
    }), 201

@app.post("/api/seeds/<int:seed_id>/box/<int:version>/publish")
def api_publish_seed_box(seed_id: int, version: int):
    data = request.get_json(silent=True) or {}
    publish = bool(data.get("publish", True))  # default: publish
    kind = (data.get("kind") or "box_of_dirt").strip()

    with closing(connect()) as con, con:
        # Ensure the artifact exists
        row = con.execute("""
            SELECT id FROM seed_artifacts WHERE seed_id=? AND kind=? AND version=? LIMIT 1
        """, (seed_id, kind, version)).fetchone()
        if not row:
            return jsonify({"error": "Artifact version not found"}), 404

        con.execute("""
            UPDATE seed_artifacts
            SET is_published=?, updated_at=datetime('now')
            WHERE seed_id=? AND kind=? AND version=?
        """, (1 if publish else 0, seed_id, kind, version))

    return jsonify({"ok": True, "seed_id": seed_id, "version": version, "is_published": publish})

@app.get("/api/seeds/<int:seed_id>/boxes")
def api_list_seed_boxes(seed_id: int):
    kind = (request.query_string.decode() and request.args.get("kind")) or "box_of_dirt"
    rows = _query_all("""
        SELECT id, seed_id, kind, title, version, is_published, doc_format, checksum, created_at, updated_at
        FROM seed_artifacts
        WHERE seed_id = ? AND kind = ?
        ORDER BY version DESC
    """, (seed_id, kind))
    return jsonify(rows)


@app.get("/boxes/<int:seed_id>")
def public_box(seed_id: int):
    """
    Public, isolated viewer for a seed's Box of Dirt.
    Query params:
      - draft=1  -> show latest version regardless of publish state
      - version=INT -> fetch specific version
      - kind=... -> artifact kind (default 'box_of_dirt')
    """
    want_draft = request.args.get("draft") in ("1", "true", "yes")
    version = request.args.get("version")
    kind = (request.args.get("kind") or "box_of_dirt").strip()

    with closing(connect()) as con:
        # sanity: seed exists
        s = con.execute("SELECT 1 FROM narrative_seeds WHERE id=? LIMIT 1", (seed_id,)).fetchone()
        if not s:
            abort(404, description="Seed not found.")

        if version:
            row = con.execute("""
                SELECT title, html, doc_format, version, is_published, checksum, updated_at
                FROM seed_artifacts
                WHERE seed_id=? AND kind=? AND version=?
                LIMIT 1
            """, (seed_id, kind, int(version))).fetchone()
        else:
            if want_draft:
                row = con.execute("""
                    SELECT title, html, doc_format, version, is_published, checksum, updated_at
                    FROM seed_artifacts
                    WHERE seed_id=? AND kind=?
                    ORDER BY version DESC
                    LIMIT 1
                """, (seed_id, kind)).fetchone()
            else:
                row = con.execute("""
                    SELECT title, html, doc_format, version, is_published, checksum, updated_at
                    FROM seed_artifacts
                    WHERE seed_id=? AND kind=? AND is_published=1
                    ORDER BY version DESC
                    LIMIT 1
                """, (seed_id, kind)).fetchone()

    if not row:
        msg = "No published artifact found for this seed." if not want_draft else "No artifact found for this seed."
        abort(404, description=msg)

    html = (row["html"] or "")
    fmt = (row["doc_format"] or "full").lower()
    looks_full = bool(re.search(r"<!DOCTYPE|<html[^>]*>", html, re.I) or fmt == "full")

    # ETag for caching
    etag = row["checksum"] or None
    if etag and request.headers.get("If-None-Match") == etag:
        return ("", 304, {"ETag": etag})

    if looks_full:
        resp = Response(html, mimetype="text/html; charset=utf-8")
    else:
        wrapped = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>{row['title'] or 'Box of Dirt'}</title>
  {BODY_WRAPPER_STYLE}
</head>
<body>
{html}
</body>
</html>"""
        resp = Response(wrapped, mimetype="text/html; charset=utf-8")

    if etag:
        resp.headers["ETag"] = etag
    return resp