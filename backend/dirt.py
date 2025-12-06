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
from werkzeug.utils import secure_filename
import os
import shutil



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


import re

def sanitize_segment(display_name: str) -> str:
    """
    Turn a human label like 'seller (sales/marketing/business)'
    into a filesystem-safe single directory name like
    'seller-sales-marketing-business'.
    """
    # Replace slashes with hyphens
    s = display_name.replace("/", "-")
    # Optionally remove other nasty chars
    s = re.sub(r"[^A-Za-z0-9._ -]+", "", s)
    # Collapse whitespace to single dashes
    s = re.sub(r"\s+", "-", s).strip("-")
    # Fallback
    return s or "chunk"


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

def get_box_by_slug(slug: str):
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM boxes WHERE slug = ?", (slug,))
    row = cur.fetchone()
    return row


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



@app.route("/boxes/<slug>", methods=["GET"])
def get_box(slug):
    row = get_box_by_slug(slug)
    if row is None:
        abort(404, description="Box not found")

    return jsonify(
        {
            "id": row["id"],
            "slug": row["slug"],
            "title": row["title"],
            "root_path": row["root_path"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }
    )

@app.route("/boxes/<slug>/nodes", methods=["GET"])
def list_nodes(slug):
    box = get_box_by_slug(slug)
    if box is None:
        abort(404, description="Box not found")

    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        "SELECT * FROM nodes WHERE box_id = ? ORDER BY depth, rel_path;",
        (box["id"],),
    )
    rows = cur.fetchall()

    return jsonify(
        [
            {
                "id": r["id"],
                "box_id": r["box_id"],
                "parent_id": r["parent_id"],
                "name": r["name"],
                "kind": r["kind"],
                "rel_path": r["rel_path"],
                "mime_type": r["mime_type"],
                "extension": r["extension"],
                "size_bytes": r["size_bytes"],
                "checksum": r["checksum"],
                "depth": r["depth"],
                "created_at": r["created_at"],
                "updated_at": r["updated_at"],
            }
            for r in rows
        ]
    )



@app.route("/boxes/<slug>/nodes", methods=["POST"])
def create_node(slug):
    box = get_box_by_slug(slug)
    if box is None:
        abort(404, description="Box not found")

    conn = get_db()
    cur = conn.cursor()

    box_root_fs = os.path.join(DATA_ROOT, box["root_path"])  # e.g. /var/www/site/data/boxes/box_001
    os.makedirs(box_root_fs, exist_ok=True)

    # Common fields
    if request.is_json:
        data = request.get_json() or {}
        kind = (data.get("kind") or "").strip()
        parent_rel_path = (data.get("parent_rel_path") or "").strip()
    else:
        kind = (request.form.get("kind") or "").strip()
        parent_rel_path = (request.form.get("parent_rel_path") or "").strip()

    # Find parent node
    if parent_rel_path == "":
        # parent is box root node
        cur.execute(
            "SELECT id FROM nodes WHERE box_id = ? AND kind = 'box_root' AND rel_path = ''",
            (box["id"],),
        )
    else:
        cur.execute(
            "SELECT id FROM nodes WHERE box_id = ? AND rel_path = ?",
            (box["id"], parent_rel_path),
        )
    parent_row = cur.fetchone()
    parent_id = parent_row["id"] if parent_row else None

    # CHUNK: JSON body with name + no file
    if kind == "chunk":
        if request.is_json:
            data = request.get_json() or {}
            display_name = (data.get("name") or "").strip()
        else:
            display_name = (request.form.get("name") or "").strip()

        if not display_name:
            return jsonify({"error": "name is required for chunk"}), 400

        # Filesystem-safe segment (no '/')
        fs_segment = sanitize_segment(display_name)

        # Build filesystem rel_path using safe segment
        if parent_rel_path:
            rel_path = f"{parent_rel_path}/{fs_segment}"
        else:
            rel_path = fs_segment

        target_dir = os.path.join(box_root_fs, rel_path)

        try:
            os.makedirs(target_dir, exist_ok=False)
        except FileExistsError:
            return jsonify({"error": "chunk already exists at that path"}), 400

        depth = rel_path.count("/") + 1

        # Store human label in `name`, filesystem path in `rel_path`
        cur.execute(
            """
            INSERT INTO nodes (
                box_id, parent_id, name, kind, rel_path,
                mime_type, extension, size_bytes, checksum,
                depth, created_at, updated_at
            )
            VALUES (?, ?, ?, 'chunk', ?, NULL, NULL, NULL, NULL,
                    ?, datetime('now'), datetime('now'));
            """,
            (box["id"], parent_id, display_name, rel_path, depth),
        )
        conn.commit()
        node_id = cur.lastrowid
        return jsonify({"id": node_id, "kind": "chunk", "rel_path": rel_path}), 201


    # PARTICLE: multipart/form-data with file
    if kind == "particle":
        file = request.files.get("file")
        if not file:
            return jsonify({"error": "file is required for particle"}), 400

        filename = secure_filename(file.filename)
        if not filename:
            return jsonify({"error": "invalid filename"}), 400

        if parent_rel_path:
            rel_path = f"{parent_rel_path}/{filename}"
        else:
            rel_path = filename

        target_dir = os.path.join(box_root_fs, parent_rel_path) if parent_rel_path else box_root_fs
        os.makedirs(target_dir, exist_ok=True)
        target_path = os.path.join(target_dir, filename)

        file.save(target_path)
        size_bytes = os.path.getsize(target_path)
        ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else None
        mime_type = file.mimetype

        depth = rel_path.count("/") + 1
        cur.execute(
            """
            INSERT INTO nodes (
                box_id, parent_id, name, kind, rel_path,
                mime_type, extension, size_bytes, checksum,
                depth, created_at, updated_at
            )
            VALUES (?, ?, ?, 'particle', ?, ?, ?, ?, NULL,
                    ?, datetime('now'), datetime('now'));
            """,
            (box["id"], parent_id, filename, rel_path, mime_type, ext, size_bytes, depth),
        )
        conn.commit()
        node_id = cur.lastrowid
        return jsonify({"id": node_id, "kind": "particle", "rel_path": rel_path}), 201

    return jsonify({"error": "invalid kind; expected 'chunk' or 'particle'"}), 400

@app.route("/boxes/<slug>/nodes/<int:node_id>", methods=["DELETE"])
def delete_node(slug, node_id):
    box = get_box_by_slug(slug)
    if box is None:
        abort(404, description="Box not found")

    conn = get_db()
    cur = conn.cursor()

    # Fetch node
    cur.execute(
        "SELECT * FROM nodes WHERE id = ? AND box_id = ?",
        (node_id, box["id"]),
    )
    node = cur.fetchone()
    if node is None:
        return jsonify({"error": "Node not found"}), 404

    if node["kind"] == "box_root":
        return jsonify({"error": "Cannot delete box root"}), 400

    box_root_fs = os.path.join(DATA_ROOT, box["root_path"])
    rel_path = node["rel_path"]
    target_path = os.path.join(box_root_fs, rel_path)

    try:
        if node["kind"] == "chunk":
            # Delete directory tree from disk
            if os.path.exists(target_path):
                shutil.rmtree(target_path)

            # Delete this chunk AND all descendants in DB
            cur.execute(
                """
                DELETE FROM nodes
                WHERE box_id = ?
                  AND (rel_path = ? OR rel_path LIKE ?)
                """,
                (box["id"], rel_path, rel_path + "/%"),
            )

        elif node["kind"] == "particle":
            # Delete single file
            if os.path.exists(target_path):
                try:
                    os.remove(target_path)
                except IsADirectoryError:
                    shutil.rmtree(target_path)

            cur.execute(
                "DELETE FROM nodes WHERE id = ? AND box_id = ?",
                (node_id, box["id"]),
            )

        else:
            return jsonify({"error": "Unsupported node kind"}), 400

        conn.commit()
        return jsonify({"status": "deleted", "id": node_id}), 200

    except Exception as e:
        conn.rollback()
        return jsonify({"error": "Failed to delete node", "details": str(e)}), 500
