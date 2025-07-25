from uuid import uuid4
from flask import Flask, request, jsonify
from dotenv import load_dotenv
from openai import OpenAI
import os
import json
from pathlib import Path
import sqlite3
import re

load_dotenv()
#OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "o4-mini-2025-04-16")
client = OpenAI()

DB_FILE = Path(__file__).parent / "narratives_data.db"
NARRATIVES_FILE = Path(__file__).parent / "narratives.json"

# allow access from multiple Flask threads
conn = sqlite3.connect(DB_FILE, check_same_thread=False)
conn.row_factory = sqlite3.Row

with conn:
    conn.execute("""
    CREATE TABLE IF NOT EXISTS notes (
      id          TEXT PRIMARY KEY,
      narrative   TEXT,
      parent      TEXT,
      system      TEXT,
      user        TEXT,
      answer      TEXT,
      x           REAL DEFAULT 0,
      y           REAL DEFAULT 0,
      created_at  DATETIME DEFAULT CURRENT_TIMESTAMP
    )
    """)


def load_narratives():
    try:
        if NARRATIVES_FILE.exists():
            with open(NARRATIVES_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception as e:
        print("❌ Failed to load narratives.json:", e)
    return []

def save_narratives(list_of_dicts):
    try:
        with open(NARRATIVES_FILE, "w", encoding="utf-8") as f:
            json.dump(list_of_dicts, f, ensure_ascii=False, indent=2)
        print("✅ narratives.json updated")
    except Exception as e:
        print("❌ Failed to write narratives.json:", e)

app = Flask(__name__)
NARRATIVES = load_narratives() or [{"id":"default","title":"Default"}]
# ────────────────────────────────────────────────────────────


# helpers -------------------------------------------------------------
def add_record(narrative: str, sys_msg: str, usr_msg: str, answer: str):
    rid = str(uuid4())
    with conn:  # opens a transaction and commits
        conn.execute("""
            INSERT INTO notes(id, narrative, parent, system, user, answer)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (rid, narrative, None, sys_msg, usr_msg, answer))
    return rid

def ask(sys_msg: str, usr_msg: str) -> str:
    resp = client.chat.completions.create(
        model=OPENAI_MODEL,
        messages=[
            {"role": "system", "content": sys_msg},
            {"role": "user",   "content": usr_msg},
        ],
    )
    return resp.choices[0].message.content

def slugify(s: str) -> str:
    """Turn a title into a URL‑friendly lowercase slug."""
    s = s.lower()
    s = re.sub(r'[^a-z0-9]+', '-', s)
    return re.sub(r'^-+|-+$', '', s)

# --------------------------------------------------------------------
# Routes  -------------------------------------------------------------
@app.route("/api/ask", methods=["POST"])
def handle_ask():
    data = request.get_json(force=True)
    narrative = data.get("narrative", "hindgut")   # default or require it
    answer = ask(data.get("system", ""), data.get("user", ""))
    add_record(narrative, data.get("system", ""), data.get("user", ""), answer)
    return jsonify({"answer": answer})

@app.route("/api/history", methods=["GET"])
def get_history():
    narrative = request.args.get("narrative", "hindgut")
    cur = conn.execute("""
        SELECT id, narrative, parent, system, user, answer, x, y
          FROM notes
         WHERE narrative = ?
      ORDER BY created_at
    """, (narrative,))
    rows = [dict(row) for row in cur.fetchall()]
    return jsonify(rows)

@app.route("/api/positions", methods=["POST"])
def update_positions():
    data = request.get_json(force=True)
    positions = data.get("positions", {})  # expect { id: {x:…, y:…}, … }

    with conn:
        for cid, coords in positions.items():
            conn.execute(
              "UPDATE notes SET x = ?, y = ? WHERE id = ?",
              (coords.get("x", 0), coords.get("y", 0), cid)
            )
    return jsonify({"status":"ok"})



@app.route("/api/reparent", methods=["POST"])
def reparent():
    data = request.get_json(force=True)
    new_parent = data.get("parent")
    with conn:
        cur = conn.execute("UPDATE notes SET parent = ? WHERE id = ?", (new_parent, data["id"]))
    if cur.rowcount == 0:
        return jsonify({"error":"id not found"}), 404
    return jsonify({"status":"ok"})

@app.route("/api/delete", methods=["POST"])
def delete():
    data = request.get_json(force=True)
    cid = data.get("id","")
    # first pull its parent so children can be re‑adopted
    row = conn.execute("SELECT parent FROM notes WHERE id = ?", (cid,)).fetchone()
    if not row:
        return jsonify({"error":"id not found"}), 404
    old_parent = row["parent"]
    with conn:
        # delete the node
        conn.execute("DELETE FROM notes WHERE id = ?", (cid,))
        # reparent its children
        conn.execute("UPDATE notes SET parent = ? WHERE parent = ?", (old_parent, cid))
    return jsonify({"status":"deleted"})

@app.route("/api/edit", methods=["POST"])
def edit():
    data = request.get_json(force=True)
    fields = []
    vals   = []
    for key in ("system","user","answer"):
        if key in data:
            fields.append(f"{key} = ?")
            vals.append(data[key])
    if not fields:
        return jsonify({"error":"nothing to update"}), 400
    vals.append(data["id"])
    with conn:
        cur = conn.execute(f"""
            UPDATE notes SET {','.join(fields)} WHERE id = ?
        """, vals)
    if cur.rowcount == 0:
        return jsonify({"error":"id not found"}), 404
    return jsonify({"status":"edited"})

@app.route("/api/narratives", methods=["GET", "POST"])
def manage_narratives():
    global NARRATIVES

    if request.method == "GET":
        return jsonify(NARRATIVES)

    data  = request.get_json(force=True)
    title = data.get("title", "").strip()
    if not title:
        return jsonify({"error":"Title is required"}), 400

    base_id      = slugify(title)
    unique_id    = base_id
    existing_ids = {n["id"] for n in NARRATIVES}
    suffix       = 1
    # bump the slug until it's not already taken
    while unique_id in existing_ids:
        unique_id = f"{base_id}-{suffix}"
        suffix += 1

    new_item = {"id": unique_id, "title": title}
    NARRATIVES.append(new_item)

    try:
        save_narratives(NARRATIVES)
    except Exception:
        return jsonify({"error":"Could not persist narratives"}), 500

    return jsonify(new_item), 201