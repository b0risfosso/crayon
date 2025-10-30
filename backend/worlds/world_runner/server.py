# server.py
from flask import Flask, jsonify, request
from subterranean_spinal_grid import SpinalGridWorld
import threading
import time

app = Flask(__name__)
world = SpinalGridWorld(v_dc=1000.0, seed=42)

# build initial graph same as __main__ demo:
def init_world(w: SpinalGridWorld):
    w.add_node("hospital_south", 500,1000,200,800,1500,0.9,0.7,0.8, True)
    w.add_node("transit_hub",   200,400,150,200, 800,0.8,0.6,0.6, False)
    w.add_node("shelter_west",  100,200, 80, 50, 300,0.7,0.5,0.7, False)
    w.add_node("mall_lowpri",    80,200,120,  0, 600,0.2,0.3,0.3, False)

    w.add_segment("segA","hospital_south","transit_hub",2.0)
    w.add_segment("segB","transit_hub","shelter_west",1.0)
    w.add_segment("segC","shelter_west","mall_lowpri",2.5)
    w.add_segment("segD","mall_lowpri","hospital_south",3.0)

init_world(world)

# background stepping thread
def loop():
    while True:
        world.step(dt=1.0)
        time.sleep(1.0)

threading.Thread(target=loop, daemon=True).start()

# ---- READ-ONLY TELEMETRY ENDPOINTS ----

@app.get("/api/snapshot")
def api_snapshot():
    """
    Return the latest world snapshot. Frontend polls this.
    """
    return jsonify(world.get_snapshot())

@app.post("/api/event/demand_surge")
def api_demand_surge():
    data = request.get_json(force=True)
    node_id = data.get("node_id")
    frac = float(data.get("frac", 0.2))
    world.inject_demand_surge(node_id, frac)
    return jsonify({"ok": True})

@app.post("/api/event/fault")
def api_fault():
    data = request.get_json(force=True)
    seg_id = data.get("seg_id")
    world.inject_fault_segment(seg_id, reason="ui")
    return jsonify({"ok": True})

if __name__ == "__main__":
    # run Flask
    app.run(host="0.0.0.0", port=5050, debug=False)
