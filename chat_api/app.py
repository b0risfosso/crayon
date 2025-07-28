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
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "o4-mini-2025-04-16")
client = OpenAI()

DB_FILE = Path(__file__).parent / "narratives_data.db"
NARRATIVES_FILE = Path(__file__).parent / "narratives.json"

conn = sqlite3.connect(DB_FILE, check_same_thread=False)
conn.row_factory = sqlite3.Row

with conn:
    conn.execute(
        """
    CREATE TABLE IF NOT EXISTS notes (
      id          TEXT PRIMARY KEY,
      narrative   TEXT,
      parent      TEXT,
      system      TEXT,
      user        TEXT,
      answer      TEXT,
      x           REAL DEFAULT 0,
      y           REAL DEFAULT 0,
      is_protocol INTEGER DEFAULT 0,
      created_at  DATETIME DEFAULT CURRENT_TIMESTAMP
    )
    """
    )

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
    if NARRATIVES_FILE.exists():
        with open(NARRATIVES_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return []


def save_narratives(objs):
    with open(NARRATIVES_FILE, "w", encoding="utf-8") as f:
        json.dump(objs, f, ensure_ascii=False, indent=2)


def slugify(s: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", s.lower())
    return re.sub(r"^-+|-+$", "", s)


def add_record(narrative, sys, user, answer, parent=None, is_protocol=False):
    rid = str(uuid4())
    with conn:
        conn.execute(
            "INSERT INTO notes(id,narrative,parent,system,user,answer,is_protocol) VALUES(?,?,?,?,?,?,?)",
            (rid, narrative, parent, sys, user, answer, int(is_protocol)),
        )
    return rid


def llm(system_msg, user_msg):
    resp = client.chat.completions.create(
        model=OPENAI_MODEL,
        messages=[{"role": "system", "content": system_msg}, {"role": "user", "content": user_msg}],
    )
    return resp.choices[0].message.content


app = Flask(__name__)
NARRATIVES = load_narratives() or [{"id": "default", "title": "Default"}]

# ───────────────────────────────────────────────────────────── Routes

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

    protocol = llm(SYSTEM_PROMPT, row["answer"])

    # store as protocol sub‑note
    add_record(row["narrative"], SYSTEM_PROMPT, row["answer"], protocol, parent=nid, is_protocol=True)
    return jsonify({"protocol": protocol})


# keep other endpoints (history, narratives, etc.) unchanged … (omitted for brevity)

if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=int(os.getenv("PORT", 8000)))
