#!/usr/bin/env python3

import os
import sqlite3
from typing import Optional

from flask import (
    Flask,
    g,
    request,
    jsonify,
    redirect,
    url_for,
    abort,
    render_template_string,
)

# === CONFIG ===
DATA_ROOT = "/var/www/site/data"
DB_PATH = os.path.join(DATA_ROOT, "dirt.db")

app = Flask(__name__)

# === DB CONNECTION HANDLING ===

def get_db():
    if "db" not in g:
        # ensure data dir exists
        os.makedirs(DATA_ROOT, exist_ok=True)
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        g.db = conn
    return g.db

def init_db():
    """Create tables and indexes if they don't exist."""
    conn = get_db()
    cur = conn.cursor()

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS boxes (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            slug       TEXT UNIQUE NOT NULL,
            title      TEXT,
            root_path  TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        """
    )

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS nodes (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            box_id      INTEGER NOT NULL,
            parent_id   INTEGER,
            name        TEXT NOT NULL,
            kind        TEXT NOT NULL,  -- 'box_root', 'chunk', 'particle'
            rel_path    TEXT NOT NULL,
            mime_type   TEXT,
            extension   TEXT,
            size_bytes  INTEGER,
            checksum    TEXT,
            depth       INTEGER NOT NULL,
            created_at  TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at  TEXT NOT NULL DEFAULT (datetime('now')),
            FOREIGN KEY (box_id) REFERENCES boxes(id) ON DELETE CASCADE,
            FOREIGN KEY (parent_id) REFERENCES nodes(id) ON DELETE CASCADE
        );
        """
    )

    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_nodes_box_parent ON nodes(box_id, parent_id);"
    )
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_nodes_box_relpath ON nodes(box_id, rel_path);"
    )
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_nodes_box_kind ON nodes(box_id, kind);"
    )

    conn.commit()


# Initialize DB once at startup
with app.app_context():
    init_db()




@app.teardown_appcontext
def close_db(exc):
    db = g.pop("db", None)
    if db is not None:
        db.close()



# === CORE LOGIC ===

def has_box_record(slug: str) -> bool:
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT 1 FROM boxes WHERE slug = ?", (slug,))
    return cur.fetchone() is not None



def generate_next_slug():
    conn = get_db()
    cur = conn.cursor()

    cur.execute("""
        SELECT slug FROM boxes
        WHERE slug LIKE 'box_%'
        ORDER BY slug DESC
        LIMIT 1;
    """)
    row = cur.fetchone()

    if not row:
        return "box_001"

    last_slug = row["slug"]  # e.g. "box_014"
    last_num = int(last_slug.split("_")[1])
    next_num = last_num + 1
    return f"box_{next_num:03d}"



def create_box(slug: str, title: Optional[str] = None):
    """
    Create a box of dirt:
    - Create /var/www/site/data/boxes/<slug> directory
    - Insert into boxes table
    - Insert root node into nodes table
    """
    conn = get_db()
    cur = conn.cursor()

    # paths
    boxes_root = os.path.join(DATA_ROOT, "boxes")
    box_dir = os.path.join(boxes_root, slug)
    root_path_rel = os.path.join("boxes", slug)  # relative to DATA_ROOT

    if not os.path.exists(DATA_ROOT):
        raise RuntimeError(f"DATA_ROOT does not exist: {DATA_ROOT}")

    os.makedirs(boxes_root, exist_ok=True)

    # check DB slug uniqueness
    if has_box_record(slug):
        raise ValueError(f"Box slug already exists: {slug}")

    # check filesystem directory
    if os.path.exists(box_dir):
        raise ValueError(f"Directory already exists for box: {box_dir}")

    try:
        # create directory on disk
        os.makedirs(box_dir, exist_ok=False)

        # insert box
        cur.execute(
            """
            INSERT INTO boxes (slug, title, root_path, created_at, updated_at)
            VALUES (?, ?, ?, datetime('now'), datetime('now'));
            """,
            (slug, title, root_path_rel),
        )
        box_id = cur.lastrowid

        # insert root node
        cur.execute(
            """
            INSERT INTO nodes (
                box_id, parent_id, name, kind, rel_path,
                mime_type, extension, size_bytes, checksum,
                depth, created_at, updated_at
            )
            VALUES (
                ?, NULL, ?, 'box_root', '',
                NULL, NULL, NULL, NULL,
                0, datetime('now'), datetime('now')
            );
            """,
            (box_id, slug),
        )

        conn.commit()

        return {
            "box_id": box_id,
            "slug": slug,
            "title": title,
            "dir": box_dir,
            "root_path": root_path_rel,
        }

    except Exception:
        conn.rollback()
        # best-effort cleanup if directory exists but DB insert failed
        if os.path.exists(box_dir) and not has_box_record(slug):
            try:
                os.rmdir(box_dir)
            except OSError:
                pass
        raise


# === ROUTES ===

@app.route("/")
def index():
    return redirect(url_for("list_boxes"))


@app.route("/boxes", methods=["GET"])
def list_boxes():
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM boxes ORDER BY created_at DESC;")
    rows = cur.fetchall()

    return jsonify(
        [
            {
                "id": row["id"],
                "slug": row["slug"],
                "title": row["title"],
                "root_path": row["root_path"],
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
            }
            for row in rows
        ]
    )



@app.route("/boxes/new", methods=["GET"])
def new_box():
    template = """
    <!doctype html>
    <html>
    <head>
      <title>Create Box of Dirt</title>
    </head>
    <body>
      <h1>Create a Box of Dirt</h1>
      <form method="post" action="{{ url_for('create_box_route') }}">
        <label>Slug (required):<br>
          <input type="text" name="slug" required>
        </label><br><br>
        <label>Title (optional):<br>
          <input type="text" name="title">
        </label><br><br>
        <button type="submit">Create Box</button>
      </form>
      <p><a href="{{ url_for('list_boxes') }}">Back to list</a></p>
    </body>
    </html>
    """
    return render_template_string(template)


@app.route("/boxes", methods=["POST"])
def create_box_route():
    if request.is_json:
        data = request.get_json(silent=True) or {}
        slug = (data.get("slug") or "").strip()
        title = (data.get("title") or "").strip() or None
    else:
        slug = (request.form.get("slug") or "").strip()
        title = (request.form.get("title") or "").strip() or None

    # If slug empty → auto-generate
    if not slug:
        slug = generate_next_slug()

    try:
        result = create_box(slug, title)
    except ValueError as ve:
        if request.is_json:
            return jsonify({"error": str(ve)}), 400
        abort(400, description=str(ve))
    except Exception as e:
        if request.is_json:
            return jsonify({"error": "internal error", "details": str(e)}), 500
        abort(500, description="internal error")

    if request.is_json:
        return jsonify(result), 201

    return redirect(url_for("list_boxes"))



# For running via `python app.py`
if __name__ == "__main__":
    # For production behind something like gunicorn or uWSGI, you won’t use this.
    app.run(host="0.0.0.0", port=5000, debug=True)
