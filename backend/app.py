# seeds_api.py
import sqlite3
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from datetime import datetime, timedelta

app = FastAPI()
DB_PATH = "/var/www/site/data/narratives_data.db"

@app.get("/api/seeds")
def api_seeds(days: int = 7):
    days = max(1, min(days, 30))
    cutoff = (datetime.utcnow() - timedelta(days=days)).isoformat(timespec="seconds")

    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()
    cur.execute("""
        SELECT s.id,
               s.narrative_id,
               s.title,
               s.description,
               COALESCE(s.problem, s.a, '') AS problem,
               COALESCE(s.objective, s.b, '') AS objective,
               COALESCE(s.link, s.href, '') AS link,
               s.created_at,
               n.title AS narrative_title
        FROM seeds s
        LEFT JOIN narratives n ON n.id = s.narrative_id
        WHERE COALESCE(s.created_at, '') >= ?
        ORDER BY COALESCE(s.created_at, '') DESC, s.id DESC
    """, (cutoff,))
    cols = [c[0] for c in cur.description]
    rows = [dict(zip(cols, r)) for r in cur.fetchall()]
    con.close()
    return JSONResponse(rows)
