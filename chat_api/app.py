# app.py
# pip install flask
import sqlite3, time, datetime as dt
from pathlib import Path
from flask import Flask, request, jsonify

DB_PATH = Path(__file__).with_name("narratives.db")

app = Flask(__name__)

def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = db()
    conn.execute("""
      CREATE TABLE IF NOT EXISTS narratives (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        title TEXT NOT NULL,
        description TEXT,
        unit TEXT,
        current INTEGER DEFAULT 0,
        target INTEGER,
        start_ts INTEGER
      )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_narratives_start_ts ON narratives(start_ts)")
    conn.commit()
    conn.close()
    seed_narratives_if_empty()

def seed_narratives_if_empty():
    """Seed once, only if table is empty."""
    conn = db()
    n = conn.execute("SELECT COUNT(*) AS c FROM narratives").fetchone()["c"]
    if n and n > 0:
        conn.close(); return

    seed = [
        { "title": "Harness Energy", "description": "Capture energy for use.", "unit": "MJ", "current": 0, "target": 1 },
        { "title": "Make Revenue", "description": "Generate initial capital for scaling builds.", "unit": "$", "current": 0, "target": 10000 },
        { "title": "Produce Food", "description": "Grow corn.", "unit": "acre", "current": 0, "target": 1 },
        { "title": "Generate Drinkable Water", "description": "Purify or condense potable water.", "unit": "liters", "current": 0, "target": 10000 },
        { "title": "Build Houses", "description": "Design and construct dwellings.", "unit": "houses", "current": 0, "target": 100 },
        { "title": "Construct WiFi Router", "description": "Connectivity to information", "unit": "routers", "current": 0, "target": 100 },
        { "title": "Build a Transportation System", "description": "Build 10 bikes.", "unit": "bikes", "current": 0, "target": 10 },
        { "title": "Create a Waste → Resource Loop", "description": "Convert waste streams into usable resources: recycled materials for reuse.", "unit": "recycled_tons", "current": 0, "target": 1 },
        { "title": "Attain Resting Heart Rate <50 bpm", "description": "Condition the body to achieve an athletic resting heart rate.", "unit": "individuals", "current": 0, "target": 10 },
        { "title": "Meditation", "description": "Meditate for 1000 hours.", "unit": "hours", "current": 0, "target": 1000 },
        { "title": "Train a Domain-Specific AI Model", "description": "Gather data and train a machine learning model specialized for one area.", "unit": "trained models", "current": 0, "target": 10 },
    ]
    conn.executemany(
        "INSERT INTO narratives (title, description, unit, current, target, start_ts) VALUES (?,?,?,?,?,NULL)",
        [(s["title"], s["description"], s["unit"], s["current"], s["target"]) for s in seed]
    )
    conn.commit()
    conn.close()

def row_to_narrative(r):
    iso = None
    if r["start_ts"] is not None:
        iso = dt.datetime.fromtimestamp(int(r["start_ts"]), tz=dt.timezone.utc).isoformat()
    return {
        "id": r["id"], "title": r["title"], "description": r["description"], "unit": r["unit"],
        "current": r["current"], "target": r["target"], "start_ts": r["start_ts"], "start_iso": iso
    }

# -------- CRUD --------
@app.get("/api/narratives")
def get_narratives():
    conn = db()
    rows = conn.execute("SELECT * FROM narratives ORDER BY id").fetchall()
    conn.close()
    return jsonify([row_to_narrative(r) for r in rows])

@app.post("/api/narratives")
def add_narrative():
    p = request.get_json(force=True)
    conn = db()
    cur = conn.execute("""
      INSERT INTO narratives (title, description, unit, current, target, start_ts)
      VALUES (?,?,?,?,?,?)
    """, (
      p.get("title"),
      p.get("description",""),
      p.get("unit",""),
      int(p.get("current",0) or 0),
      int(p.get("target",0) or 0),
      int(p["start_ts"]) if str(p.get("start_ts","")).isdigit() else None
    ))
    conn.commit()
    row = conn.execute("SELECT * FROM narratives WHERE id=?", (cur.lastrowid,)).fetchone()
    conn.close()
    return jsonify(row_to_narrative(row))

@app.put("/api/narratives/<int:narrative_id>")
def update_narrative(narrative_id):
    p = request.get_json(force=True)
    fields, values = [], []
    for col in ("title","description","unit","current","target","start_ts"):
        if col in p:
            fields.append(f"{col}=?")
            v = p[col]
            if col in ("current","target","start_ts") and v is not None:
                v = int(v)
            values.append(v)
    if not fields:
        return jsonify(ok=True, message="nothing to update")
    values.append(narrative_id)
    conn = db()
    conn.execute(f"UPDATE narratives SET {', '.join(fields)} WHERE id=?", values)
    conn.commit()
    row = conn.execute("SELECT * FROM narratives WHERE id=?", (narrative_id,)).fetchone()
    conn.close()
    return jsonify(row_to_narrative(row) if row else {"ok": True})

@app.delete("/api/narratives/<int:narrative_id>")
def delete_narrative(narrative_id):
    conn = db()
    conn.execute("DELETE FROM narratives WHERE id=?", (narrative_id,))
    conn.commit()
    conn.close()
    return jsonify(ok=True)

# -------- Start time (idempotent set) --------
@app.get("/api/start/<int:narrative_id>")
def get_start(narrative_id):
    conn = db()
    row = conn.execute("SELECT start_ts FROM narratives WHERE id=?", (narrative_id,)).fetchone()
    conn.close()
    if not row: return jsonify(error="narrative not found"), 404
    if row["start_ts"] is None: return jsonify(start_ts=None, start_iso=None)
    iso = dt.datetime.fromtimestamp(int(row["start_ts"]), tz=dt.timezone.utc).isoformat()
    return jsonify(start_ts=int(row["start_ts"]), start_iso=iso)

@app.post("/api/start/<int:narrative_id>")
def set_start(narrative_id):
    p = request.get_json(silent=True) or {}
    force = bool(p.get("force", False))
    ts = p.get("timestamp")
    if isinstance(ts, str) and ts.isdigit():
        ts = int(ts)
    elif not isinstance(ts, int):
        ts = int(time.time())
    conn = db()
    row = conn.execute("SELECT start_ts FROM narratives WHERE id=?", (narrative_id,)).fetchone()
    if not row:
        conn.close(); return jsonify(error="narrative not found"), 404
    cur = row["start_ts"]
    if cur is None or force:
        conn.execute("UPDATE narratives SET start_ts=? WHERE id=?", (ts, narrative_id))
        conn.commit()
        cur = ts
    conn.close()
    iso = dt.datetime.fromtimestamp(int(cur), tz=dt.timezone.utc).isoformat()
    return jsonify(narrative_id=narrative_id, start_ts=int(cur), start_iso=iso, forced=force)

#if __name__ == "__main__":
#    init_db()
#    app.run(host="0.0.0.0", port=5000, debug=True)
