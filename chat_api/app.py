# app.py
import sqlite3, time, datetime as dt
from pathlib import Path
from flask import Flask, request, jsonify, Response

DB_PATH = Path(__file__).with_name("state.db")
app = Flask(__name__)

# --- DB helpers ---
def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def row_to_narrative(r):
    ts = r["start_ts"]
    start_iso_local = start_iso_utc = None
    if ts is not None:
        start_iso_local = dt.datetime.fromtimestamp(int(ts)).isoformat(timespec="seconds")
        start_iso_utc   = dt.datetime.utcfromtimestamp(int(ts)).replace(tzinfo=dt.timezone.utc).isoformat()
    return {
        "id": r["id"],
        "title": r["title"],
        "description": r["description"],
        "unit": r["unit"],
        "current": r["current"],
        "target": r["target"],
        "start_ts": ts,
        "start_iso_local": start_iso_local,
        "start_iso_utc": start_iso_utc,
    }

# --- API routes ---
@app.get("/api/narratives")
def get_narratives():
    conn = db()
    rows = conn.execute("SELECT * FROM narratives ORDER BY id").fetchall()
    conn.close()
    return jsonify([row_to_narrative(r) for r in rows])

@app.post("/api/narratives")
def add_narrative():
    p = request.get_json(force=True)
    # if no start_ts provided, set automatically to now (local time)
    ts = p.get("start_ts")
    if isinstance(ts, str) and ts.isdigit():
        ts = int(ts)
    elif isinstance(ts, int):
        ts = ts
    else:
        ts = int(time.time())

    values = (
        p.get("title"),
        p.get("description", ""),
        p.get("unit", ""),
        int(p.get("current", 0) or 0),
        int(p.get("target", 0) or 0),
        ts,
    )
    conn = db()
    cur = conn.execute(
        "INSERT INTO narratives (title, description, unit, current, target, start_ts) VALUES (?,?,?,?,?,?)",
        values,
    )
    conn.commit()
    row = conn.execute("SELECT * FROM narratives WHERE id=?", (cur.lastrowid,)).fetchone()
    conn.close()
    return jsonify(row_to_narrative(row)), 201

@app.put("/api/narratives/<int:narrative_id>")
def update_narrative(narrative_id: int):
    p = request.get_json(force=True)
    fields, values = [], []
    for col in ("title", "description", "unit", "current", "target"):
        if col in p:
            v = p[col]
            if col in ("current", "target") and v is not None:
                v = int(v)
            fields.append(f"{col}=?")
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
def delete_narrative(narrative_id: int):
    conn = db()
    conn.execute("DELETE FROM narratives WHERE id=?", (narrative_id,))
    conn.commit()
    conn.close()
    return jsonify(ok=True)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
