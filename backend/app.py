# app_narratives_api.py (merge into your Flask app)
import sqlite3
from flask import Flask, jsonify
app = Flask(__name__)

DB_PATH = "/var/www/site/data/narratives_data.db"  # adjust path

@app.get("/api/narratives")
def api_narratives():
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()
    cur.execute("""
        SELECT id, title, created_at
        FROM narratives
        WHERE COALESCE(title,'') <> ''
        ORDER BY COALESCE(created_at, '') DESC, id DESC
    """)
    cols = [c[0] for c in cur.description]
    rows = [dict(zip(cols, r)) for r in cur.fetchall()]
    con.close()
    return jsonify(rows)
