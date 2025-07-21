# app.py
from dotenv import load_dotenv
from openai import OpenAI, OpenAIError
from flask import Flask, request, jsonify
import os, logging

load_dotenv()                    # loads .env in this folder
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o")
client = OpenAI()

app = Flask(__name__)
logging.basicConfig(level=logging.INFO)

def ask(sys_msg: str, usr_msg: str) -> str:
    try:
        resp = client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=[
                {"role": "system", "content": sys_msg},
                {"role": "user",   "content": usr_msg}
            ]
        )
        return resp.choices[0].message.content
    except OpenAIError as e:
        logging.exception("OpenAI API error")
        return f"ERROR: {e}"

@app.route("/api/ask", methods=["POST"])
def handle_ask():
    data = request.get_json(force=True)
    sys_msg = data.get("system", "")
    usr_msg = data.get("user", "")
    return jsonify({"answer": ask(sys_msg, usr_msg)})
