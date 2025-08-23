from flask import Flask, render_template, request, jsonify
from flask_socketio import SocketIO, emit
import time

app = Flask(__name__)
socketio = SocketIO(app, cors_allowed_origins="*")

# ---- Global stopwatch state ----
stopwatch = {
    "running": False,
    "start_time": None,   # monotonic time when run started
    "elapsed": 0.0        # accumulated seconds
}

def current_elapsed():
    if stopwatch["running"] and stopwatch["start_time"] is not None:
        return stopwatch["elapsed"] + (time.monotonic() - stopwatch["start_time"])
    return stopwatch["elapsed"]

def broadcast_state():
    socketio.emit("stopwatch", {
        "running": stopwatch["running"],
        "elapsed": int(current_elapsed())
    })

# ---- REST endpoints for control (called by JS buttons) ----
@app.route("/api/stopwatch/start", methods=["POST"])
def start():
    if not stopwatch["running"]:
        stopwatch["running"] = True
        stopwatch["start_time"] = time.monotonic()
    broadcast_state()
    return jsonify(ok=True, running=stopwatch["running"], elapsed=current_elapsed())

@app.route("/api/stopwatch/stop", methods=["POST"])
def stop():
    if stopwatch["running"]:
        stopwatch["elapsed"] += time.monotonic() - stopwatch["start_time"]
        stopwatch["running"] = False
        stopwatch["start_time"] = None
    broadcast_state()
    return jsonify(ok=True, running=stopwatch["running"], elapsed=current_elapsed())

@app.route("/api/stopwatch/reset", methods=["POST"])
def reset():
    stopwatch["running"] = False
    stopwatch["start_time"] = None
    stopwatch["elapsed"] = 0.0
    broadcast_state()
    return jsonify(ok=True, running=stopwatch["running"], elapsed=current_elapsed())

@app.route("/api/stopwatch", methods=["GET"])
def status():
    return jsonify(running=stopwatch["running"], elapsed=current_elapsed())

# ---- WebSocket ----
@socketio.on("connect")
def on_connect():
    emit("stopwatch", {
        "running": stopwatch["running"],
        "elapsed": int(current_elapsed())
    })

# background task: tick every second if running
@socketio.on("start_ticking")
def start_ticking():
    # client asks to start tick events
    pass

def ticker():
    while True:
        socketio.sleep(1)
        if stopwatch["running"]:
            broadcast_state()

socketio.start_background_task(ticker)

if __name__ == "__main__":
    socketio.run(app, host="0.0.0.0", port=5000)
