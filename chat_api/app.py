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
      is_protocol INTEGER DEFAULT 0,        -- ⬅︎ NEW
      created_at  DATETIME DEFAULT CURRENT_TIMESTAMP
    )
    """)


SYSTEM_PROMPT = (
    "You are “Narrative‑to‑Protocol Designer,” an expert experimentalist.\n"
    "Goal: Convert the following narrative into a concrete, executable protocol\n"
    "that a competent researcher or maker can follow to realise the story’s outcome.\n\n"
    "INSTRUCTIONS\n"
    "1. Read the narrative once for context, once for key objectives.\n"
    "2. Extract the *core goal* (what must exist or happen when the protocol succeeds).\n"
    "3. Identify constraints, resources, and hints already present in the text.\n"
    "4. Decompose the goal into logical, testable sub‑objectives.\n"
    "5. Draft a protocol using the template below.  \n"
    "   • Be specific about amounts, durations, temperatures, instruments, software, etc.  \n"
    "   • Include controls, iterations, and decision points for optimisation.  \n"
    "   • Flag any assumptions or missing information as “TO‑DECIDE”.  \n"
    "   • Keep language concise, active, and free of narrative fluff.\n\n"
    "OUTPUT TEMPLATE\n=====================\n"
    "**Objective**  \nA one‑sentence statement of the end‑state the experiment must achieve.\n\n"
    "**Hypothesis / Rationale**  \nWhy these steps should reach the objective.\n\n"
    "**Materials & Reagents**  \nBulleted list with exact specs (grade, model, supplier).\n\n"
    "**Equipment**  \nBulleted list.\n\n"
    "**Variables to Optimise**  \nTable or bullet list (e.g., butter–flour ratio, baking temp, apple blend).\n\n"
    "**Protocol**  \nStep 1 …  \nStep 2 …  \n… (each with sub‑steps, timings, critical notes)\n\n"
    "**Controls / Benchmarks**  \nWhat to measure against (e.g., grandmother’s original pie, store‑bought crust).\n\n"
    "**Data Collection & Analysis**  \nHow to record observations, metrics, and acceptance criteria.\n\n"
    "**Safety & Risk Management**  \nRelevant hazards and mitigations.\n\n"
    "**Success Criteria**  \nQuantitative and qualitative tests the final product must pass.\n\n"
    "**Next Iterations / Scale‑up Plan**  \nHow to refine or industrialise if the first run works.\n=====================\n\n"
    "Respond **only** with the filled‑out template for the given narrative—no additional commentary, no code fences.\n"
    "Delimiter: ```---NARRATIVE---```"
)


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


def add_record(narrative, sys_msg, usr_msg, answer,
               parent=None, is_protocol=False):
    rid = str(uuid4())
    with conn:
        conn.execute("""
            INSERT INTO notes(id, narrative, parent, system,
                              user, answer, is_protocol)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (rid, narrative, parent, sys_msg,
              usr_msg, answer, int(is_protocol)))
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

app = Flask(__name__)
NARRATIVES = load_narratives() or [{"id":"default","title":"Default"}]

# --------------------------------------------------------------------
# Routes  -------------------------------------------------------------
@app.route("/api/note")
def get_note():
    nid = request.args.get("id")
    row = conn.execute("SELECT * FROM notes WHERE id=?", (nid,)).fetchone()
    return (jsonify(dict(row)) if row else (jsonify({"error": "not found"}), 404))


@app.route("/api/subnotes", methods=["GET", "POST"])
def subnotes():
    if request.method == "GET":
        parent = request.args.get("parent")
        cur = conn.execute("SELECT id,answer AS content,is_protocol FROM notes WHERE parent=? ORDER BY created_at", (parent,))
        return jsonify([dict(r) for r in cur.fetchall()])
    data = request.get_json(force=True)
    parent, content = data.get("parent"), data.get("content", "")
    prow = conn.execute("SELECT narrative FROM notes WHERE id=?", (parent,)).fetchone()
    if not prow:
        return jsonify({"error": "parent not found"}), 404
    rid = add_record(prow["narrative"], "", "", content, parent)
    return jsonify({"id": rid})


@app.route("/api/execute", methods=["POST"])
def execute():
    """Generate a protocol from a narrative and save as sub‑note."""
    data = request.get_json(force=True)
    nid = data.get("id")
    row = conn.execute("SELECT narrative,answer FROM notes WHERE id=?", (nid,)).fetchone()
    if not row:
        return jsonify({"error": "id not found"}), 404

    protocol = ask(SYSTEM_PROMPT, "---NARRATIVE---\n\n" + row["answer"])

    # store as protocol sub‑note
    add_record(row["narrative"], SYSTEM_PROMPT, row["answer"], protocol, parent=nid, is_protocol=True)
    return jsonify({"protocol": protocol})

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