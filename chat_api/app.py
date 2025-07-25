from collections import OrderedDict
from uuid import uuid4
from flask import Flask, request, jsonify
from dotenv import load_dotenv
from openai import OpenAI
import os
import json
from pathlib import Path

load_dotenv()
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o")
client = OpenAI()

NARRATIVES_FILE = Path(__file__).parent / "narratives.json"

def load_narratives():
    if not NARRATIVES_FILE.exists():
        return []
    with open(NARRATIVES_FILE, "r", encoding="utf-8") as f:
        return json.load(f)

def save_narratives(list_of_dicts):
    with open(NARRATIVES_FILE, "w", encoding="utf-8") as f:
        # indent for readability
        json.dump(list_of_dicts, f, ensure_ascii=False, indent=2)

app = Flask(__name__)
NARRATIVES = load_narratives()

# --------------------------------------------------------------------
# In‑memory store  ----------------------------------------------------
MAX_HISTORY = 20000
HISTORY: "OrderedDict[str, dict]" = OrderedDict()   # flat, but each dict has .narrative
# ────────────────────────────────────────────────────────────


# helpers -------------------------------------------------------------
def find_item(cid: str):
    """O(1) lookup; returns None if missing."""
    return HISTORY.get(cid)

def add_record(narrative: str, sys_msg: str, usr_msg: str, answer: str):
    rid = str(uuid4())
    if len(HISTORY) >= MAX_HISTORY:
        HISTORY.popitem(last=False)
    HISTORY[rid] = {
        "id": rid,
        "narrative": narrative,   # ← NEW
        "parent": None,
        "system": sys_msg,
        "user": usr_msg,
        "answer": answer,
    }
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
    # keep insertion order but only those that match
    items = [rec for rec in HISTORY.values() if rec["narrative"] == narrative]
    return jsonify(items)

@app.route("/api/reparent", methods=["POST"])
def reparent():
    data = request.get_json(force=True)
    item = find_item(data.get("id", ""))
    if not item:
        return jsonify({"error": "id not found"}), 404
    item["parent"] = data.get("parent")
    return jsonify({"status": "ok"})

@app.route("/api/delete", methods=["POST"])
def delete():
    data = request.get_json(force=True)
    cid = data.get("id", "")
    victim = HISTORY.pop(cid, None)
    if victim is None:
        return jsonify({"error": "id not found"}), 404

    # adopt its children
    for rec in HISTORY.values():
        if rec["parent"] == cid:
            rec["parent"] = victim["parent"]

    return jsonify({"status": "deleted"})  # 200 OK

@app.route("/api/edit", methods=["POST"])
def edit():
    data = request.get_json(force=True)
    item = find_item(data.get("id", ""))
    if not item:
        return jsonify({"error": "id not found"}), 404
    for key in ("system", "user", "answer"):
        if key in data:
            item[key] = data[key]
    return jsonify({"status": "edited"})

@app.route("/api/narratives", methods=["GET", "POST"])
def manage_narratives():
    global NARRATIVES

    if request.method == "GET":
        return jsonify(NARRATIVES)

    # POST → add new
    data = request.get_json(force=True)
    nid   = data.get("id")
    title = data.get("title")

    if not nid or not title:
        return jsonify({"error":"Both id and title are required"}), 400
    if any(n["id"] == nid for n in NARRATIVES):
        return jsonify({"error":"Narrative already exists"}), 409

    new_item = {"id": nid, "title": title}
    NARRATIVES.append(new_item)
    save_narratives(NARRATIVES)
    return jsonify(new_item), 201