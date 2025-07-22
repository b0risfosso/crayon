from collections import OrderedDict
from uuid import uuid4
from flask import Flask, request, jsonify
from dotenv import load_dotenv
from openai import OpenAI
import os

load_dotenv()
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o")
client = OpenAI()

app = Flask(__name__)

# --------------------------------------------------------------------
# In‑memory store  ----------------------------------------------------
MAX_HISTORY = 200
HISTORY: "OrderedDict[str, dict]" = OrderedDict()   # insertion‑ordered

# helpers -------------------------------------------------------------
def find_item(cid: str):
    """O(1) lookup; returns None if missing."""
    return HISTORY.get(cid)

def add_record(sys_msg: str, usr_msg: str, answer: str):
    rid = str(uuid4())
    if len(HISTORY) >= MAX_HISTORY:          # drop oldest
        HISTORY.popitem(last=False)
    HISTORY[rid] = {
        "id": rid,
        "parent": None,        # root
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
    answer = ask(data.get("system", ""), data.get("user", ""))
    add_record(data.get("system", ""), data.get("user", ""), answer)
    return jsonify({"answer": answer})

@app.route("/api/history", methods=["GET"])
def get_history():
    # keep client contract: flat list, newest last (OrderedDict preserves order)
    return jsonify(list(HISTORY.values()))

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