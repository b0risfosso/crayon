#!/usr/bin/env python3
from pathlib import Path
import json, sqlite3

# EDIT THESE LISTS ---------------------------------------------------
FANTASIA = [
    "humanity-s-imagination", "boris-stuff"
]
GENESIS  = [
    "cell-shape","cell-mechanics","cell-polarity","collective-cell-behavior",
    "cell-junctions","genetic-pathways","individual-cells","cell-systems",
    "nuclear-information","hindgut","genetic-engineered-microbes","code",
    "dirt", "rocks", "vegetation"
]
# --------------------------------------------------------------------

json_file = Path("narratives.json")
db_file   = Path("narratives_data.db")

# --- 1. rewrite narratives.json ------------------------------------
data = json.loads(json_file.read_text("utf-8"))

def rewrite_id(old_id):
    if old_id in FANTASIA: return f"fantasia-{old_id}"
    if old_id in GENESIS : return f"genesis-{old_id}"
    return old_id

for item in data:
    item["id"] = rewrite_id(item["id"])

json_file.write_text(json.dumps(data, ensure_ascii=False, indent=2), "utf-8")
print("✅ narratives.json updated")

# --- 2. patch SQLite -----------------------------------------------
con = sqlite3.connect(db_file)
with con:
    for cat in FANTASIA:
        con.execute("UPDATE notes SET narrative=? WHERE narrative=?",
                    (f"fantasia-{cat}", cat))
    for cat in GENESIS:
        con.execute("UPDATE notes SET narrative=? WHERE narrative=?",
                    (f"genesis-{cat}", cat))
print("✅ SQLite narratives patched")
