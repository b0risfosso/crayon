# app.py
import os
import sqlite3, time, datetime as dt
from pathlib import Path
from flask import Flask, request, jsonify, send_from_directory

DB_PATH = Path(os.getenv("NARRATIVES_DB", Path(__file__).with_name("state.db")))
ROOT = Path(__file__).parent

app = Flask(__name__, static_folder=str(ROOT), static_url_path="")

# --- DB helpers ---
def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def dictrow(row):
    return {k: row[k] for k in row.keys()}

# --- Schema ---
# Desired final schema (after migration):
# narratives(id INTEGER PK, title TEXT NOT NULL, description TEXT DEFAULT '',
#            webpage TEXT DEFAULT '', start_ts INTEGER, start_iso_local TEXT)
DESIRED_COLS = ["id","title","description","webpage","start_ts","start_iso_local"]

def ensure_db():
    con = db()
    cur = con.cursor()
    cur.execute("PRAGMA foreign_keys = ON;")
    # find if table exists
    cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='narratives'")
    exists = cur.fetchone() is not None
    if not exists:
        cur.execute('''
            CREATE TABLE narratives(
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              title TEXT NOT NULL,
              description TEXT DEFAULT '',
              webpage TEXT DEFAULT '',
              start_ts INTEGER,
              start_iso_local TEXT
            )
        ''')
        con.commit()
        con.close()
        return
    
    # Check columns
    cur.execute("PRAGMA table_info(narratives)")
    cols = [r[1] for r in cur.fetchall()]
    # If columns already match (order doesn't matter), do nothing
    if set(cols) == set(DESIRED_COLS):
        con.close()
        return
    
    # If "webpage" is missing but other legacy cols exist, perform migration
    # Create a new table, copy over compatible columns, drop old, rename.
    cur.execute('''
        CREATE TABLE IF NOT EXISTS narratives_new(
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          title TEXT NOT NULL,
          description TEXT DEFAULT '',
          webpage TEXT DEFAULT '',
          start_ts INTEGER,
          start_iso_local TEXT
        )
    ''')
    # Copy data: title/description/start_ts/start_iso_local from old if present
    safe_cols = [c for c in ["id","title","description","start_ts","start_iso_local"] if c in cols]
    if safe_cols:
        cur.execute(f"INSERT INTO narratives_new ({','.join(safe_cols)}) SELECT {','.join(safe_cols)} FROM narratives")
    # Replace old table
    cur.execute("DROP TABLE narratives")
    cur.execute("ALTER TABLE narratives_new RENAME TO narratives")
    con.commit()
    con.close()

ensure_db()

# --- Routes ---
@app.get("/")
def index():
    # Serve the updated index.html from the same directory
    return send_from_directory(app.static_folder, "index.html")

@app.get("/api/narratives")
def list_narratives():
    con = db()
    rows = con.execute("SELECT id, title, description, webpage, start_ts, start_iso_local FROM narratives ORDER BY id ASC").fetchall()
    con.close()
    return jsonify([dictrow(r) for r in rows])

@app.post("/api/narratives")
def create_narrative():
    data = request.get_json(force=True, silent=True) or {}
    title = (data.get("title") or "").strip()
    description = (data.get("description") or "").strip()
    webpage = (data.get("webpage") or "").strip()
    if not title:
        return jsonify({"error":"title is required"}), 400
    now_ts = int(time.time())
    # Store an ISO string in local time for convenience (keeps previous behavior)
    local_iso = dt.datetime.fromtimestamp(now_ts).isoformat(timespec="seconds")
    con = db()
    cur = con.cursor()
    cur.execute(
        "INSERT INTO narratives(title, description, webpage, start_ts, start_iso_local) VALUES(?,?,?,?,?)",
        (title, description, webpage, now_ts, local_iso)
    )
    con.commit()
    nid = cur.lastrowid
    con.close()
    return jsonify({"id": nid}), 201

@app.put("/api/narratives/<int:nid>")
def update_narrative(nid):
    data = request.get_json(force=True, silent=True) or {}
    fields, vals = [], []
    for key in ("title","description","webpage"):
        if key in data:
            fields.append(f"{key}=?")
            vals.append((data.get(key) or "").strip())

    # Recalculate start_iso_local if start_ts exists but iso is null
    con = db()
    cur = con.execute("SELECT start_ts, start_iso_local FROM narratives WHERE id=?", (nid,))
    row = cur.fetchone()
    if row and row["start_ts"] and not row["start_iso_local"]:
        iso = dt.datetime.fromtimestamp(int(row["start_ts"])).isoformat(timespec="seconds")
        fields.append("start_iso_local=?")
        vals.append(iso)

    if not fields:
        con.close()
        return jsonify({"error":"no fields to update"}), 400

    vals.append(nid)
    con.execute(f"UPDATE narratives SET {', '.join(fields)} WHERE id=?", vals)
    con.commit()
    con.close()
    return jsonify({"ok": True})


@app.delete("/api/narratives/<int:nid>")
def delete_narrative(nid):
    con = db()
    con.execute("DELETE FROM narratives WHERE id=?", (nid,))
    con.commit()
    con.close()
    return jsonify({"ok": True})

if __name__ == "__main__":
    # For local testing
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "5000")), debug=True)
