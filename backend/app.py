# app.py
import os
import sqlite3
import textwrap
from typing import Any, Dict
from flask import Flask, jsonify, request, g, Response

# -----------------------------
# Config
# -----------------------------
DB_PATH = os.environ.get("DB_PATH", "/var/www/site/data/narratives_data.db")

app = Flask(__name__)

# -----------------------------
# DB helpers
# -----------------------------
def get_db() -> sqlite3.Connection:
    if "db" not in g:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON;")
        g.db = conn
    return g.db

@app.teardown_appcontext
def close_db(exception):
    db: sqlite3.Connection = g.pop("db", None)
    if db is not None:
        db.close()

def init_db():
    db = get_db()
    db.executescript(
        """
        PRAGMA foreign_keys = ON;

        CREATE TABLE IF NOT EXISTS narratives (
          id          INTEGER PRIMARY KEY,
          title       TEXT NOT NULL,
          description TEXT,
          created_at  TEXT NOT NULL DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS narrative_dimensions (
          id           INTEGER PRIMARY KEY,
          narrative_id INTEGER NOT NULL,
          title        TEXT NOT NULL,
          description  TEXT,
          created_at   TEXT NOT NULL DEFAULT (datetime('now')),
          FOREIGN KEY (narrative_id) REFERENCES narratives(id) ON DELETE CASCADE
        );

        -- Seeds attach to a dimension (and thus indirectly to a narrative)
        CREATE TABLE IF NOT EXISTS narrative_seeds (
          id           INTEGER PRIMARY KEY,
          dimension_id INTEGER NOT NULL,
          title        TEXT NOT NULL,
          description  TEXT,
          created_at   TEXT NOT NULL DEFAULT (datetime('now')),
          FOREIGN KEY (dimension_id) REFERENCES narrative_dimensions(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS narrative_structures (
          id                 INTEGER PRIMARY KEY,
          narrative_id       INTEGER NOT NULL,
          narrative_seed_id  INTEGER NOT NULL,
          text               TEXT NOT NULL,
          created_at         TEXT NOT NULL DEFAULT (datetime('now')),
          FOREIGN KEY (narrative_id)      REFERENCES narratives(id)      ON DELETE CASCADE,
          FOREIGN KEY (narrative_seed_id) REFERENCES narrative_seeds(id) ON DELETE CASCADE
        );

        CREATE INDEX IF NOT EXISTS idx_dims_by_narr   ON narrative_dimensions(narrative_id);
        CREATE INDEX IF NOT EXISTS idx_seeds_by_dim   ON narrative_seeds(dimension_id);
        CREATE INDEX IF NOT EXISTS idx_struct_by_seed ON narrative_structures(narrative_seed_id);
        CREATE INDEX IF NOT EXISTS idx_struct_by_narr ON narrative_structures(narrative_id);
        """
    )
    db.commit()

with app.app_context():
    init_db()

# -----------------------------
# Utilities
# -----------------------------
def row_to_dict(row: sqlite3.Row) -> Dict[str, Any]:
    return {k: row[k] for k in row.keys()}

def error_json(message: str, status: int = 400):
    return jsonify({"error": message}), status

# -----------------------------
# Health
# -----------------------------
@app.get("/healthz")
def healthz():
    return jsonify({"ok": True})

# -----------------------------
# Narratives
# -----------------------------
@app.get("/api/narratives")
def list_narratives():
    db = get_db()
    rows = db.execute(
        "SELECT id, title, description, created_at FROM narratives ORDER BY id ASC"
    ).fetchall()
    return jsonify([row_to_dict(r) for r in rows])

@app.get("/api/narratives/<int:narrative_id>")
def get_narrative(narrative_id: int):
    db = get_db()
    row = db.execute(
        "SELECT id, title, description, created_at FROM narratives WHERE id = ?",
        (narrative_id,),
    ).fetchone()
    if not row:
        return error_json("narrative_not_found", 404)
    return jsonify(row_to_dict(row))

@app.post("/api/narratives")
def create_narrative():
    data = request.get_json(silent=True) or {}
    title = (data.get("title") or "").strip()
    description = (data.get("description") or "").strip()
    if not title:
        return error_json("missing_fields: title", 400)
    db = get_db()
    cur = db.execute(
        "INSERT INTO narratives (title, description) VALUES (?, ?)",
        (title, description or None),
    )
    db.commit()
    nid = cur.lastrowid
    row = db.execute(
        "SELECT id, title, description, created_at FROM narratives WHERE id = ?",
        (nid,),
    ).fetchone()
    return jsonify(row_to_dict(row)), 201

# -----------------------------
# Narrative Dimensions
# -----------------------------
@app.get("/api/narrative-dimensions")
def list_dimensions():
    narrative_id = request.args.get("narrative", type=int)
    if not narrative_id:
        return error_json("missing_narrative_param", 400)

    limit = max(1, min(request.args.get("limit", 100, type=int), 500))
    offset = max(0, request.args.get("offset", 0, type=int))

    db = get_db()
    rows = db.execute(
        """
        SELECT id, narrative_id, title, description, created_at
        FROM narrative_dimensions
        WHERE narrative_id = ?
        ORDER BY id ASC
        LIMIT ? OFFSET ?
        """,
        (narrative_id, limit, offset),
    ).fetchall()
    return jsonify([row_to_dict(r) for r in rows])

@app.get("/api/narrative-dimensions/<int:dimension_id>")
def get_dimension(dimension_id: int):
    db = get_db()
    row = db.execute(
        "SELECT id, narrative_id, title, description, created_at FROM narrative_dimensions WHERE id = ?",
        (dimension_id,),
    ).fetchone()
    if not row:
        return error_json("dimension_not_found", 404)
    return jsonify(row_to_dict(row))

@app.post("/api/narrative-dimensions")
def create_dimension():
    data = request.get_json(silent=True) or {}
    narrative_id = data.get("narrative_id")
    title = (data.get("title") or "").strip()
    description = (data.get("description") or "").strip()

    if not narrative_id or not title:
        return error_json("missing_fields: narrative_id, title", 400)

    db = get_db()
    ok = db.execute("SELECT 1 FROM narratives WHERE id = ?", (narrative_id,)).fetchone()
    if not ok:
        return error_json("narrative_not_found", 404)

    cur = db.execute(
        "INSERT INTO narrative_dimensions (narrative_id, title, description) VALUES (?, ?, ?)",
        (narrative_id, title, description or None),
    )
    db.commit()
    did = cur.lastrowid
    row = db.execute(
        "SELECT id, narrative_id, title, description, created_at FROM narrative_dimensions WHERE id = ?",
        (did,),
    ).fetchone()
    return jsonify(row_to_dict(row)), 201

# -----------------------------
# Narrative Seeds (by Dimension)
# -----------------------------
@app.get("/api/narrative-seeds")
def list_seeds():
    dimension_id = request.args.get("dimension", type=int)
    if not dimension_id:
        return error_json("missing_dimension_param", 400)

    limit = max(1, min(request.args.get("limit", 100, type=int), 500))
    offset = max(0, request.args.get("offset", 0, type=int))

    db = get_db()
    rows = db.execute(
        """
        SELECT id, dimension_id, title, description, created_at
        FROM narrative_seeds
        WHERE dimension_id = ?
        ORDER BY id ASC
        LIMIT ? OFFSET ?
        """,
        (dimension_id, limit, offset),
    ).fetchall()
    return jsonify([row_to_dict(r) for r in rows])

@app.get("/api/narrative-seeds/<int:seed_id>")
def get_seed(seed_id: int):
    db = get_db()
    row = db.execute(
        "SELECT id, dimension_id, title, description, created_at FROM narrative_seeds WHERE id = ?",
        (seed_id,),
    ).fetchone()
    if not row:
        return error_json("seed_not_found", 404)
    return jsonify(row_to_dict(row))

@app.post("/api/narrative-seeds")
def create_seed():
    data = request.get_json(silent=True) or {}
    dimension_id = data.get("dimension_id")
    title = (data.get("title") or "").strip()
    description = (data.get("description") or "").strip()

    if not dimension_id or not title:
        return error_json("missing_fields: dimension_id, title", 400)

    db = get_db()
    ok = db.execute("SELECT 1 FROM narrative_dimensions WHERE id = ?", (dimension_id,)).fetchone()
    if not ok:
        return error_json("dimension_not_found", 404)

    cur = db.execute(
        "INSERT INTO narrative_seeds (dimension_id, title, description) VALUES (?, ?, ?)",
        (dimension_id, title, description or None),
    )
    db.commit()
    sid = cur.lastrowid
    row = db.execute(
        "SELECT id, dimension_id, title, description, created_at FROM narrative_seeds WHERE id = ?",
        (sid,),
    ).fetchone()
    return jsonify(row_to_dict(row)), 201

# -----------------------------
# Narrative Structures (storage)
# -----------------------------
@app.post("/api/narrative-structures")
def create_structure():
    """
    JSON: { "narrative_id": int, "narrative_seed_id": int, "text": str }
    """
    data = request.get_json(silent=True) or {}
    narrative_id = data.get("narrative_id")
    narrative_seed_id = data.get("narrative_seed_id")
    text = (data.get("text") or "").strip()

    if not (narrative_id and narrative_seed_id and text):
        return error_json("missing_fields: narrative_id, narrative_seed_id, text", 400)

    db = get_db()
    n_ok = db.execute("SELECT 1 FROM narratives WHERE id = ?", (narrative_id,)).fetchone()
    if not n_ok:
        return error_json("narrative_not_found", 404)

    s_ok = db.execute("SELECT 1 FROM narrative_seeds WHERE id = ?", (narrative_seed_id,)).fetchone()
    if not s_ok:
        return error_json("seed_not_found", 404)

    cur = db.execute(
        "INSERT INTO narrative_structures (narrative_id, narrative_seed_id, text) VALUES (?, ?, ?)",
        (narrative_id, narrative_seed_id, text),
    )
    db.commit()
    struct_id = cur.lastrowid
    row = db.execute(
        "SELECT id, narrative_id, narrative_seed_id, text, created_at "
        "FROM narrative_structures WHERE id = ?",
        (struct_id,),
    ).fetchone()
    return jsonify(row_to_dict(row)), 201

@app.get("/api/narrative-structures/latest")
def get_latest_structure_for_seed():
    """
    GET /api/narrative-structures/latest?seed=<id>
    Returns the most recent structure as plain text.
    """
    seed_id = request.args.get("seed", type=int)
    if not seed_id:
        return Response("missing seed", status=400, mimetype="text/plain")

    db = get_db()
    row = db.execute(
        """
        SELECT text
        FROM narrative_structures
        WHERE narrative_seed_id = ?
        ORDER BY datetime(created_at) DESC, id DESC
        LIMIT 1
        """,
        (seed_id,),
    ).fetchone()

    if not row:
        return Response("No structure for this seed.", status=404, mimetype="text/plain")
    return Response(row["text"], mimetype="text/plain; charset=utf-8")

# -----------------------------
# Narrative Structure (on-the-fly generator)
# -----------------------------
@app.get("/api/narrative-structure/<int:seed_id>")
def generate_narrative_structure(seed_id: int):
    """
    Generate a plain-text template using:
    seed -> dimension -> narrative
    """
    db = get_db()
    seed = db.execute(
        """
        SELECT s.id AS seed_id, s.title AS seed_title, s.description AS seed_desc,
               d.id AS dimension_id, d.title AS dim_title, d.narrative_id AS narrative_id,
               n.title AS narrative_title
        FROM narrative_seeds s
        JOIN narrative_dimensions d ON d.id = s.dimension_id
        JOIN narratives n ON n.id = d.narrative_id
        WHERE s.id = ?
        """,
        (seed_id,),
    ).fetchone()
    if not seed:
        return Response("Seed not found.", status=404, mimetype="text/plain")

    title = seed["seed_title"] or "Untitled Seed"
    seed_desc = (seed["seed_desc"] or "").strip()
    narrative_title = seed["narrative_title"] or "Narrative"
    dim_title = seed["dim_title"] or "Dimension"

    objective = (
        seed_desc
        if seed_desc
        else f'Advance seed "{title}" within "{dim_title}" of {narrative_title}.'
    )
    hypothesis = (
        "If the correct levers (people, tools, systems) are mobilized in the right order, "
        "then the objective can be achieved with minimal waste, risk, and time."
    )

    materials = textwrap.dedent(
        """\
        Yourself (observer / operator)
        Notebook or notes app (logging facts, timestamps, decisions)
        Phone / terminal (to communicate, look up data, trigger systems)
        Map / coordinates of target environment
        Contacts / endpoints (to be enumerated for this seed)
        """
    )

    methods = textwrap.dedent(
        f"""\
        Scout & Observe
        - Verify ground truth relevant to "{title}" (what is happening, where, who/what is impacted).
        - Identify hazards and constraints (safety, legal, resource limits).

        Engage
        - Contact the relevant actors or systems (people, APIs, infrastructure).
        - Provide precise identifiers (addresses, IDs, coordinates, logs, photos if applicable).

        Execute
        - Trigger the minimal, reversible intervention first.
        - Escalate methodically to stronger interventions with clear rollback.

        Log & Iterate
        - Timestamp all actions and outcomes.
        - Capture metrics toward success criteria; adjust playbook if signals are weak.
        """
    )

    success = textwrap.dedent(
        """\
        - Objective achieved within the target time window.
        - No injuries, violations, or unacceptable side-effects.
        - System of record updated; follow-up tasks scheduled if needed.
        """
    )

    txt = textwrap.dedent(
        f"""\
        🌱 Narrative — {title}

        Objective
        {objective}

        Hypothesis
        {hypothesis}

        Materials / Resources
        {materials.rstrip()}

        Methods (Protocol)
        {methods.rstrip()}

        Success Criteria
        {success.rstrip()}
        """
    )

    return Response(txt, mimetype="text/plain; charset=utf-8")

# -----------------------------
# Main
# -----------------------------
if __name__ == "__main__":
    # Local testing; use gunicorn in production
    app.run(host="0.0.0.0", port=5000, debug=True)
