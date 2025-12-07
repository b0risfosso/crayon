#!/usr/bin/env python3

import os
import shutil
import sqlite3
import subprocess
import threading
import time
from typing import Optional
from uuid import uuid4

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

from db_shared import log_usage
from prompts2 import build_bridge_prompt

# === CONFIG ===
DATA_ROOT = "/var/www/site/data"
DB_PATH = os.path.join(DATA_ROOT, "dirt.db")
BRIDGE_LLM_MODEL = (
    os.environ.get("BRIDGE_LLM_MODEL")
    or os.environ.get("LLM_MODEL")
    or "gpt-5-mini-2025-08-07"
)
BRIDGE_LLM_SYSTEM_PROMPT = os.environ.get(
    "BRIDGE_SYSTEM_PROMPT",
    "You are an expert mediator who studies two documents and designs"
    " detailed value exchanges grounded in the provided text.",
)
LLM_LOCK = threading.Lock()

try:
    from openai import OpenAI

    _client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))
    _client_err: Optional[Exception] = None
except Exception as e:  # pragma: no cover - optional dependency
    _client = None
    _client_err = e

try:  # prefer PyPDF2 but fall back to the renamed pypdf package
    from PyPDF2 import PdfReader as _PdfReader  # type: ignore
except Exception:  # pragma: no cover
    try:
        from pypdf import PdfReader as _PdfReader  # type: ignore
    except Exception:
        _PdfReader = None

try:
    from pdfminer.high_level import extract_text as _pdfminer_extract_text  # type: ignore
except Exception:
    _pdfminer_extract_text = None

app = Flask(__name__)


def run_bridge_llm(prompt_text: str, *, model: Optional[str] = None):
    """Send the bridge prompt to the configured LLM and return text + usage."""
    if _client is None:
        raise RuntimeError(f"OpenAI client not initialized: {_client_err}")

    resolved_model = model or BRIDGE_LLM_MODEL
    with LLM_LOCK:
        start = time.time()
        response = _client.chat.completions.create(
            model=resolved_model,
            messages=[
                {"role": "system", "content": BRIDGE_LLM_SYSTEM_PROMPT},
                {"role": "user", "content": prompt_text},
            ],
        )

    text = (response.choices[0].message.content or "").strip()
    usage = getattr(response, "usage", None)
    usage_dict = usage.model_dump() if usage else None
    tokens_in = int((usage_dict or {}).get("prompt_tokens", 0))
    tokens_out = int((usage_dict or {}).get("completion_tokens", 0))
    total_tokens = int(
        (usage_dict or {}).get("total_tokens", tokens_in + tokens_out)
    )
    duration_ms = int((time.time() - start) * 1000)

    return text, usage_dict, tokens_in, tokens_out, total_tokens, duration_ms, resolved_model

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

    # Bridge table: stores prompt + output for pairs of particle nodes
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS bridge (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            node_a_id   INTEGER NOT NULL,
            node_b_id   INTEGER NOT NULL,
            prompt      TEXT NOT NULL,
            output      TEXT,
            created_at  TEXT NOT NULL DEFAULT (datetime('now')),
            FOREIGN KEY (node_a_id) REFERENCES nodes(id) ON DELETE CASCADE,
            FOREIGN KEY (node_b_id) REFERENCES nodes(id) ON DELETE CASCADE
        );
        """
    )
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_bridge_nodes ON bridge(node_a_id, node_b_id);"
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

def read_particle_excerpt(node_row, max_chars: int = 10000) -> str:
    """
    Given a nodes row joined with boxes.root_path, read the corresponding file
    from /var/www/site/data and return up to max_chars of UTF-8 text.
    Assumes the row has columns: root_path and rel_path.
    """
    root_path = node_row["root_path"]   # e.g. 'boxes/box_001'
    rel_path = node_row["rel_path"]     # e.g. 'chunk_a/file.txt'
    abs_path = os.path.join(DATA_ROOT, root_path, rel_path)

    if not os.path.exists(abs_path) or not os.path.isfile(abs_path):
        raise FileNotFoundError(f"Particle file not found at {abs_path}")

    # Prefer structured extraction for PDFs to avoid binary gibberish in UI.
    if rel_path.lower().endswith(".pdf"):
        pdf_text = _extract_pdf_text(abs_path, max_chars)
        if pdf_text:
            return pdf_text

    with open(abs_path, "rb") as f:
        raw = f.read(50000)

    text = raw.decode("utf-8", errors="ignore")
    if text.startswith("%PDF"):
        fallback = _extract_pdf_text(abs_path, max_chars)
        if fallback:
            return fallback
        return "[pdf detected but text extraction unavailable]"
    return text[:max_chars]


def _extract_pdf_text(path: str, max_chars: int) -> Optional[str]:
    """Try several strategies to pull readable PDF text."""

    text = _extract_pdf_with_reader(path, max_chars)
    if text:
        return text

    text = _extract_pdf_with_pdfminer(path, max_chars)
    if text:
        return text

    text = _extract_pdf_with_cli(path, max_chars)
    if text:
        return text

    return None


def _extract_pdf_with_reader(path: str, max_chars: int) -> Optional[str]:
    if _PdfReader is None:
        return None
    try:
        reader = _PdfReader(path)
    except Exception:
        return None

    chunks = []
    total = 0
    for page in reader.pages:
        try:
            page_text = page.extract_text() or ""
        except Exception:
            page_text = ""
        if not page_text:
            continue
        needed = max_chars - total
        chunks.append(page_text[:needed])
        total += len(chunks[-1])
        if total >= max_chars:
            break
    combined = "".join(chunks).strip()
    return combined[:max_chars] if combined else None


def _extract_pdf_with_pdfminer(path: str, max_chars: int) -> Optional[str]:
    if _pdfminer_extract_text is None:
        return None
    try:
        text = _pdfminer_extract_text(path, maxpages=None)
    except Exception:
        return None
    if not text:
        return None
    return text[:max_chars]


def _extract_pdf_with_cli(path: str, max_chars: int) -> Optional[str]:
    exe = shutil.which("pdftotext")
    if not exe:
        return None
    try:
        proc = subprocess.run(
            [exe, "-layout", path, "-"],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=10,
        )
    except Exception:
        return None
    if proc.returncode != 0:
        return None
    try:
        text = proc.stdout.decode("utf-8", errors="ignore")
    except Exception:
        return None
    text = text.strip()
    return text[:max_chars] if text else None


@app.route("/particles/<int:node_id>/excerpt", methods=["GET"])
def particle_excerpt(node_id: int):
    """Return up to max_chars characters of the given particle's file."""
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT n.*, b.root_path
        FROM nodes n
        JOIN boxes b ON n.box_id = b.id
        WHERE n.id = ?
        """,
        (node_id,),
    )
    row = cur.fetchone()
    if row is None:
        return jsonify({"error": "Node not found"}), 404
    if row["kind"] != "particle":
        return jsonify({"error": "Node is not a particle"}), 400

    try:
        max_chars_param = request.args.get("max_chars")
        max_chars = int(max_chars_param) if max_chars_param else 10000
        max_chars = max(1, min(max_chars, 50000))
    except ValueError:
        return jsonify({"error": "max_chars must be an integer"}), 400

    try:
        excerpt = read_particle_excerpt(row, max_chars=max_chars)
    except FileNotFoundError as exc:
        return jsonify({"error": str(exc)}), 404
    except Exception as exc:
        return jsonify({"error": "Failed to read particle", "details": str(exc)}), 500

    return jsonify(
        {
            "node_id": node_id,
            "max_chars": max_chars,
            "excerpt": excerpt,
            "length": len(excerpt),
        }
    )



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


@app.route("/bridge", methods=["POST"])
def create_bridge():
    """
    Create a 'bridge' between two particle nodes.

    Request JSON:
    {
      "node_a_id": 123,
      "node_b_id": 456
    }

    Behavior:
    - Verify both nodes exist and are kind='particle'
    - Read their underlying files from /var/www/site/data/...
    - Take up to 10,000 characters from each document
    - Build the bridge prompt using prompts.build_bridge_prompt
    - Run the configured LLM to generate bridge output
    - Insert a row into the bridge table with prompt and output
    - Return the bridge row metadata + prompt
    """
    if not request.is_json:
        return jsonify({"error": "JSON body required"}), 400

    data = request.get_json() or {}
    try:
        node_a_id = int(data.get("node_a_id"))
        node_b_id = int(data.get("node_b_id"))
    except (TypeError, ValueError):
        return jsonify({"error": "node_a_id and node_b_id must be integers"}), 400

    if node_a_id == node_b_id:
        return jsonify({"error": "node_a_id and node_b_id must be different"}), 400

    conn = get_db()
    cur = conn.cursor()

    # Fetch both nodes and their box root_path
    cur.execute(
        """
        SELECT n.*, b.root_path
        FROM nodes n
        JOIN boxes b ON n.box_id = b.id
        WHERE n.id IN (?, ?);
        """,
        (node_a_id, node_b_id),
    )
    rows = cur.fetchall()
    if len(rows) != 2:
        return jsonify({"error": "One or both nodes not found"}), 404

    node_map = {row["id"]: row for row in rows}
    node_a = node_map.get(node_a_id)
    node_b = node_map.get(node_b_id)

    if node_a is None or node_b is None:
        return jsonify({"error": "One or both nodes not found"}), 404

    if node_a["kind"] != "particle" or node_b["kind"] != "particle":
        return jsonify({"error": "Both nodes must be particles"}), 400

    # Read up to 10,000 characters from each document
    try:
        doc_a_excerpt = read_particle_excerpt(node_a, max_chars=10000)
        doc_b_excerpt = read_particle_excerpt(node_b, max_chars=10000)
    except FileNotFoundError as e:
        return jsonify({"error": str(e)}), 500
    except Exception as e:
        return jsonify({"error": "Failed to read particle files", "details": str(e)}), 500

    # Build the prompt text
    prompt_text = build_bridge_prompt(doc_a_excerpt, doc_b_excerpt)

    try:
        (
            model_output,
            usage_dict,
            tokens_in,
            tokens_out,
            total_tokens,
            duration_ms,
            resolved_model,
        ) = run_bridge_llm(prompt_text)
    except RuntimeError as e:
        return jsonify({"error": "LLM unavailable", "details": str(e)}), 503
    except Exception as e:
        return jsonify({"error": "LLM call failed", "details": str(e)}), 502

    try:
        log_usage(
            app="dirt.bridge",
            model=resolved_model,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            endpoint="/bridge",
            request_id=f"bridge:{node_a_id}:{node_b_id}:{uuid4().hex}",
            duration_ms=duration_ms,
            meta={"node_a_id": node_a_id, "node_b_id": node_b_id},
        )
    except Exception:
        # Usage logging is best-effort; failures shouldn't block the request.
        pass

    usage_payload = {
        "model": resolved_model,
        "prompt_tokens": tokens_in,
        "completion_tokens": tokens_out,
        "total_tokens": total_tokens,
        "duration_ms": duration_ms,
    }
    if usage_dict is not None:
        usage_payload["raw"] = usage_dict

    try:
        cur.execute(
            """
            INSERT INTO bridge (node_a_id, node_b_id, prompt, output, created_at)
            VALUES (?, ?, ?, ?, datetime('now'));
            """,
            (node_a_id, node_b_id, prompt_text, model_output),
        )
        conn.commit()
        bridge_id = cur.lastrowid
    except Exception as e:
        conn.rollback()
        return jsonify({"error": "Failed to insert bridge row", "details": str(e)}), 500

    return jsonify(
        {
            "id": bridge_id,
            "node_a_id": node_a_id,
            "node_b_id": node_b_id,
            "prompt": prompt_text,
            "output": model_output,
            "usage": usage_payload,
        }
    ), 201
