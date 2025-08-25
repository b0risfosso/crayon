# app.py
# pip install flask
import sqlite3, time, datetime as dt
from pathlib import Path
from flask import Flask, request, jsonify

DB_PATH = Path(__file__).with_name("state.db")

app = Flask(__name__)

# ---------- DB helpers ----------
def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def row_to_narrative(r: sqlite3.Row):
    ts = r["start_ts"]
    start_iso_local = start_iso_utc = None
    if ts is not None:
        # Local ISO (based on server local timezone)
        start_iso_local = dt.datetime.fromtimestamp(int(ts)).isoformat(timespec="seconds")
        # UTC ISO (explicit timezone)
        start_iso_utc = dt.datetime.utcfromtimestamp(int(ts)).replace(tzinfo=dt.timezone.utc).isoformat()
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

# ---------- CRUD ----------
@app.get("/api/narratives")
def get_narratives():
    conn = db()
    rows = conn.execute("SELECT * FROM narratives ORDER BY id").fetchall()
    conn.close()
    return jsonify([row_to_narrative(r) for r in rows])

@app.post("/api/narratives")
def add_narrative():
    p = request.get_json(force=True)
    values = (
        p.get("title"),
        p.get("description", ""),
        p.get("unit", ""),
        int(p.get("current", 0) or 0),
        int(p.get("target", 0) or 0),
        int(p["start_ts"]) if str(p.get("start_ts", "")).isdigit() else None,
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
    for col in ("title", "description", "unit", "current", "target", "start_ts"):
        if col in p:
            v = p[col]
            if col in ("current", "target", "start_ts") and v is not None:
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

# ---------- Start time (idempotent “set once”, local time baseline) ----------
@app.get("/api/start/<int:narrative_id>")
def get_start(narrative_id: int):
    conn = db()
    row = conn.execute("SELECT start_ts FROM narratives WHERE id=?", (narrative_id,)).fetchone()
    conn.close()
    if not row:
        return jsonify(error="narrative not found"), 404
    ts = row["start_ts"]
    if ts is None:
        return jsonify(start_ts=None, start_iso_local=None, start_iso_utc=None)
    return jsonify(
        start_ts=int(ts),
        start_iso_local=dt.datetime.fromtimestamp(int(ts)).isoformat(timespec="seconds"),
        start_iso_utc=dt.datetime.utcfromtimestamp(int(ts)).replace(tzinfo=dt.timezone.utc).isoformat(),
    )

@app.post("/api/start/<int:narrative_id>")
def set_start(narrative_id: int):
    """
    Set start_ts if not set. Will NOT overwrite unless {"force": true}.
    If no timestamp provided, uses current local time (server clock).
    Body JSON (optional): {"force": bool, "timestamp": <epoch_seconds>}
    """
    p = request.get_json(silent=True) or {}
    force = bool(p.get("force", False))
    ts = p.get("timestamp")
    if isinstance(ts, str) and ts.isdigit():
        ts = int(ts)
    elif not isinstance(ts, int):
        ts = int(time.time())  # local epoch seconds

    conn = db()
    row = conn.execute("SELECT start_ts FROM narratives WHERE id=?", (narrative_id,)).fetchone()
    if not row:
        conn.close()
        return jsonify(error="narrative not found"), 404

    current = row["start_ts"]
    if current is None or force:
        conn.execute("UPDATE narratives SET start_ts=? WHERE id=?", (ts, narrative_id))
        conn.commit()
        current = ts
    conn.close()

    return jsonify(
        narrative_id=narrative_id,
        start_ts=int(current),
        start_iso_local=dt.datetime.fromtimestamp(int(current)).isoformat(timespec="seconds"),
        start_iso_utc=dt.datetime.utcfromtimestamp(int(current)).replace(tzinfo=dt.timezone.utc).isoformat(),
        forced=force,
    )

# ---------- Run ----------
if __name__ == "__main__":
    # DB already initialized with init_db.py; just run the API
    app.run(host="0.0.0.0", port=5000, debug=True)
