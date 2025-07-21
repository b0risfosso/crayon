# ... top of file unchanged ...
from flask import Flask, request, jsonify
from collections import deque

load_dotenv()
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o")
client = OpenAI()

app = Flask(__name__)

# ---- new global store (keep last 100 interactions) ----
HISTORY = deque(maxlen=100)   # each item: {"system":..., "user":..., "answer":...}

def ask(sys_msg: str, usr_msg: str) -> str:
    resp = client.chat.completions.create(
        model=OPENAI_MODEL,
        messages=[
            {"role": "system", "content": sys_msg},
            {"role": "user",   "content": usr_msg}
        ]
    )
    return resp.choices[0].message.content

# -------- API routes --------
@app.route("/api/ask", methods=["POST"])
def handle_ask():
    data = request.get_json(force=True)
    sys_msg = data.get("system", "")
    usr_msg = data.get("user", "")
    answer  = ask(sys_msg, usr_msg)

    # save to history
    HISTORY.append({"system": sys_msg, "user": usr_msg, "answer": answer})
    return jsonify({"answer": answer})

@app.route("/api/history", methods=["GET"])
def handle_history():
    # newest → oldest
    return jsonify(list(reversed(HISTORY)))
