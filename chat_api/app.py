# pip install flask
import sqlite3, time
from pathlib import Path
from flask import Flask, request, jsonify

DB_PATH = Path(__file__).with_name("state.db")

app = Flask(__name__)

def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = db()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS goal_start (
            goal_id INTEGER PRIMARY KEY,
            start_ts INTEGER NOT NULL
        )
    """)
    conn.commit()
    conn.close()

init_db()

def get_all_starts():
    conn = db()
    rows = conn.execute("SELECT goal_id, start_ts FROM goal_start").fetchall()
    conn.close()
    return {int(r["goal_id"]): int(r["start_ts"]) for r in rows}

def get_start(goal_id):
    conn = db()
    row = conn.execute("SELECT start_ts FROM goal_start WHERE goal_id=?", (goal_id,)).fetchone()
    conn.close()
    return int(row["start_ts"]) if row else None

def set_start(goal_id, ts=None, force=False):
    now_ts = int(ts if ts is not None else time.time())
    existing = get_start(goal_id)
    if existing is not None and not force:
        return existing  # idempotent: keep original unless force=True
    conn = db()
    if existing is None:
        conn.execute("INSERT INTO goal_start(goal_id, start_ts) VALUES (?, ?)", (goal_id, now_ts))
    else:
        conn.execute("UPDATE goal_start SET start_ts=? WHERE goal_id=?", (now_ts, goal_id))
    conn.commit()
    conn.close()
    return now_ts

@app.get("/api/start-dates")
def api_all():
    """Return all start dates: { id: iso8601 or null }"""
    data = {}
    import datetime as dt
    tz = dt.timezone.utc  # store UTC; client can localize
    for gid, ts in get_all_starts().items():
        data[gid] = dt.datetime.fromtimestamp(ts, tz=tz).isoformat()
    return jsonify(data)

@app.get("/api/start/<int:goal_id>")
def api_get(goal_id):
    import datetime as dt
    ts = get_start(goal_id)
    if ts is None:
        return jsonify(start=None)
    return jsonify(start=dt.datetime.fromtimestamp(ts, tz=dt.timezone.utc).isoformat())

@app.post("/api/start/<int:goal_id>")
def api_set(goal_id):
    """Set start if not set. Optional JSON: {"force": true, "timestamp": 1710000000}"""
    payload = request.get_json(silent=True) or {}
    force = bool(payload.get("force", False))
    ts = payload.get("timestamp")
    if isinstance(ts, str) and ts.isdigit():
        ts = int(ts)
    elif not isinstance(ts, int):
        ts = None
    new_ts = set_start(goal_id, ts=ts, force=force)
    import datetime as dt
    return jsonify(
        goal_id=goal_id,
        start_ts=new_ts,
        start_iso=dt.datetime.fromtimestamp(new_ts, tz=dt.timezone.utc).isoformat(),
        forced=force
    )

@app.delete("/api/start/<int:goal_id>")
def api_delete(goal_id):
    """Optional helper to clear a start date."""
    conn = db()
    conn.execute("DELETE FROM goal_start WHERE goal_id=?", (goal_id,))
    conn.commit()
    conn.close()
    return jsonify(ok=True)

#if __name__ == "__main__":
    # Simple dev run; in prod put behind gunicorn/uvicorn + nginx
#    app.run(host="0.0.0.0", port=5000, debug=True)
