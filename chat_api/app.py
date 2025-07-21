# app.py
from dotenv import load_dotenv
from openai import OpenAI, OpenAIError
from flask import Flask, request, jsonify
from collections import deque
import os, logging
from uuid import uuid4

load_dotenv()
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o")
client = OpenAI()

app = Flask(__name__)

# ---- new global store (keep last 100 interactions) ----

def ask(sys_msg: str, usr_msg: str) -> str:
    resp = client.chat.completions.create(
        model=OPENAI_MODEL,
        messages=[
            {"role": "system", "content": sys_msg},
            {"role": "user",   "content": usr_msg}
        ]
    )
    return resp.choices[0].message.content

# --------------------------------------------------------------------
# In‑memory store
HISTORY = deque(maxlen=200)  # each: {"id":str, "parent":str|None, "system":…, "user":…, "answer":…}

def add_record(sys_msg, usr_msg, answer):
    HISTORY.append({
        "id": str(uuid4()),
        "parent": None,            # root by default
        "system": sys_msg,
        "user": usr_msg,
        "answer": answer
    })

# -------- API routes --------
@app.route("/api/ask", methods=["POST"])
def handle_ask():
    data = request.get_json(force=True)
    sys_msg = data.get("system", "")
    usr_msg = data.get("user", "")
    answer  = ask(sys_msg, usr_msg)
    add_record(sys_msg, usr_msg, answer)
    return jsonify({"answer": answer})

@app.route("/api/history", methods=["GET"])
def get_history():
    return jsonify(list(HISTORY))              # flat list

@app.route("/api/reparent", methods=["POST"])
def reparent():
    data = request.get_json(force=True)        # {"id":child, "parent":newParentOrNone}
    cid   = data.get("id")
    new_parent = data.get("parent")            # None for root
    for item in HISTORY:
        if item["id"] == cid:
            item["parent"] = new_parent
            return jsonify({"status": "ok"})
    return jsonify({"error": "id not found"}), 404