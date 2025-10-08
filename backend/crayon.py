from __future__ import annotations
import os
from openai import OpenAI
import sqlite3
from flask import Flask, jsonify, abort, request, Response
from typing import List, Optional, Literal, Any, Dict, Tuple
from pydantic import BaseModel, Field, conlist
from openai import OpenAI, OpenAIError
# app.py (top-level, after Flask app creation)
import sqlite3, json, os
from contextlib import closing
import hashlib 
import re
import json
from pathlib import Path
from datetime import datetime, timezone
from xai_sdk import Client as XAIClient
from xai_sdk.chat import system as xai_system, user as xai_user
from google import genai
import uuid
import threading
import time
from datetime import datetime
from crayon_prompts import (
    DIM_SYS_MSG,
    DIM_USER_TEMPLATE,
    DOMAIN_ARCHITECT_SYS_MSG,
    DOMAIN_ARCHITECT_USER_TEMPLATE,
)



DB_PATH = "/var/www/site/data/crayon_data.db"
DATA_DIR = str(Path(DB_PATH).parent)

app = Flask(__name__)


#!/usr/bin/env python3
# crayon.py
# Minimal Flask service + SQLite initializer for the new Fantasia schema.

DB_PATH = "/var/www/site/data/crayon_data.db"
DATA_DIR = str(Path(DB_PATH).parent)

app = Flask(__name__)

def connect(db_path: str = DB_PATH) -> sqlite3.Connection:
    Path(DATA_DIR).mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    # Sensible pragmas for durability + read performance
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    return conn

SCHEMA = [
    # --- meta for migrations ---
    """
    CREATE TABLE IF NOT EXISTS schema_migrations (
        id INTEGER PRIMARY KEY,
        name TEXT NOT NULL UNIQUE,
        applied_at TEXT NOT NULL DEFAULT (datetime('now'))
    );
    """,

    # --- users (old: people) ---
    # From old 'people' concept: email, name, created_at
    """
    CREATE TABLE IF NOT EXISTS fantasia_users (
        id INTEGER PRIMARY KEY,
        email TEXT NOT NULL UNIQUE,
        display_name TEXT,
        role TEXT CHECK (role IN ('owner','editor','viewer') ) DEFAULT 'owner',
        created_at TEXT NOT NULL DEFAULT (datetime('now')),
        updated_at TEXT
    );
    """,

    # --- core (maps old 'narratives') ---
    # Old fields carried over: id, title, description, owner_email, created_at
    """
    CREATE TABLE IF NOT EXISTS fantasia_core (
        id INTEGER PRIMARY KEY,
        title TEXT NOT NULL,
        description TEXT,
        owner_email TEXT NOT NULL,
        status TEXT DEFAULT 'active',
        provider TEXT,                 -- which LLM/provider generated it (optional)
        tags_json TEXT,                -- JSON array of string tags (optional)
        created_at TEXT NOT NULL DEFAULT (datetime('now')),
        updated_at TEXT,
        FOREIGN KEY (owner_email) REFERENCES fantasia_users(email) ON UPDATE CASCADE ON DELETE SET NULL
    );
    """,
    "CREATE INDEX IF NOT EXISTS idx_core_owner ON fantasia_core(owner_email);",
    "CREATE INDEX IF NOT EXISTS idx_core_title ON fantasia_core(title);",

    # --- domain (analogous to old 'narratives' in your note — a grouping under core) ---
    # Designed to let one core have many domains (you were generating multiple domains per core story)
    """
    CREATE TABLE IF NOT EXISTS fantasia_domain (
        id INTEGER PRIMARY KEY,
        core_id INTEGER NOT NULL,
        name TEXT NOT NULL,            -- domain name/title
        description TEXT,
        provider TEXT,
        targets_json TEXT,             -- JSON array/object of targets (kept from old dimension schema)
        created_at TEXT NOT NULL DEFAULT (datetime('now')),
        updated_at TEXT,
        FOREIGN KEY (core_id) REFERENCES fantasia_core(id) ON DELETE CASCADE
    );
    """,
    "CREATE INDEX IF NOT EXISTS idx_domain_core ON fantasia_domain(core_id);",
    "CREATE INDEX IF NOT EXISTS idx_domain_name ON fantasia_domain(name);",

    # --- dimension (maps old 'narrative_dimensions') ---
    # Old carried fields: id, nid->(now domain/core), title/name, thesis/description, targets
    """
    CREATE TABLE IF NOT EXISTS fantasia_dimension (
        id INTEGER PRIMARY KEY,
        domain_id INTEGER NOT NULL,
        name TEXT NOT NULL,            -- dimension title
        thesis TEXT,                   -- concise thesis (kept text)
        description TEXT,              -- longer description
        targets_json TEXT,             -- JSON of targets / metrics / aims
        provider TEXT,
        created_at TEXT NOT NULL DEFAULT (datetime('now')),
        updated_at TEXT,
        FOREIGN KEY (domain_id) REFERENCES fantasia_domain(id) ON DELETE CASCADE
    );
    """,
    "CREATE INDEX IF NOT EXISTS idx_dimension_domain ON fantasia_dimension(domain_id);",
    "CREATE INDEX IF NOT EXISTS idx_dimension_name ON fantasia_dimension(name);",

    # --- thesis (normalized, versionable statements per dimension) ---
    # New table to let a dimension own multiple thesis versions/variants.
    """
    CREATE TABLE IF NOT EXISTS fantasia_thesis (
        id INTEGER PRIMARY KEY,
        dimension_id INTEGER NOT NULL,
        version INTEGER NOT NULL DEFAULT 1,
        statement TEXT NOT NULL,       -- the actual thesis text
        rationale TEXT,                -- optional: why this thesis
        metrics_json TEXT,             -- optional: JSON of suggested metrics/targets
        author_email TEXT,             -- link to fantasia_users via email (optional)
        provider TEXT,
        created_at TEXT NOT NULL DEFAULT (datetime('now')),
        updated_at TEXT,
        FOREIGN KEY (dimension_id) REFERENCES fantasia_dimension(id) ON DELETE CASCADE,
        FOREIGN KEY (author_email) REFERENCES fantasia_users(email) ON UPDATE CASCADE ON DELETE SET NULL,
        UNIQUE (dimension_id, version)
    );
    """,
    "CREATE INDEX IF NOT EXISTS idx_thesis_dimension ON fantasia_thesis(dimension_id);",

    # --- compatibility views exposing hyphenated names (quoted identifiers) ---
    'CREATE VIEW IF NOT EXISTS "fantasia-core"      AS SELECT * FROM fantasia_core;',
    'CREATE VIEW IF NOT EXISTS "fantasia-domain"    AS SELECT * FROM fantasia_domain;',
    'CREATE VIEW IF NOT EXISTS "fantasia-dimension" AS SELECT * FROM fantasia_dimension;',
    'CREATE VIEW IF NOT EXISTS "fantasia-thesis"    AS SELECT * FROM fantasia_thesis;',
    'CREATE VIEW IF NOT EXISTS "fantasia-users"     AS SELECT * FROM fantasia_users;',
]

MIGRATION_NAME = "001_initial_fantasia_schema"

def already_applied(conn: sqlite3.Connection, name: str) -> bool:
    cur = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='schema_migrations';"
    )
    has_table = cur.fetchone() is not None
    if not has_table:
        return False
    cur = conn.execute("SELECT 1 FROM schema_migrations WHERE name = ?;", (name,))
    return cur.fetchone() is not None

def apply_schema(conn: sqlite3.Connection) -> None:
    with conn:
        for stmt in SCHEMA:
            conn.execute(stmt)
        # record migration
        conn.execute(
            "INSERT OR IGNORE INTO schema_migrations (name) VALUES (?);",
            (MIGRATION_NAME,)
        )

@app.get("/health")
def health():
    try:
        with connect() as conn:
            cur = conn.execute("SELECT 1;")
            cur.fetchone()
        return jsonify(ok=True, db=DB_PATH, ts=datetime.utcnow().isoformat() + "Z")
    except Exception as e:
        return jsonify(ok=False, error=str(e)), 500

@app.post("/admin/init-db")
def init_db_endpoint():
    # idempotent: safe to call multiple times
    with connect() as conn:
        apply_schema(conn)
    return jsonify(ok=True, db=DB_PATH, applied=MIGRATION_NAME)

def init_db_cli():
    with connect() as conn:
        if already_applied(conn, MIGRATION_NAME):
            print(f"[crayon] Schema already applied: {MIGRATION_NAME} @ {DB_PATH}")
        else:
            apply_schema(conn)
            print(f"[crayon] Applied schema: {MIGRATION_NAME} @ {DB_PATH}")


class PDTargetList(BaseModel):
    # Allow either list[str] directly or nested inside DimensionsResponse; included for future flexibility
    pass

class PDDimension(BaseModel):
    name: str = Field(..., description="Dimension name")
    thesis: str = Field(..., description="1–2 sentence distilled thesis")
    targets: List[str] = Field(..., min_items=3, max_items=6, description="Short target phrases")

class PDDimensionsResponse(BaseModel):
    dimensions: List[PDDimension] = Field(..., min_items=1)



# --- add this helper to ensure we can store the LLM "group" label on domains ---
def ensure_column(conn: sqlite3.Connection, table: str, column: str, coltype: str) -> None:
    cols = [r["name"] for r in conn.execute(f"PRAGMA table_info({table});")]
    if column not in cols:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {coltype};")

def run_migrations_domain_group_title(conn: sqlite3.Connection) -> None:
    # Add fantasia_domain.group_title if missing
    ensure_column(conn, "fantasia_domain", "group_title", "TEXT")

# Update init path to also run this after initial schema
def init_db_cli():
    with connect() as conn:
        if not already_applied(conn, MIGRATION_NAME):
            apply_schema(conn)
            print(f"[crayon] Applied schema: {MIGRATION_NAME} @ {DB_PATH}")
        # Run lightweight, idempotent column migration
        run_migrations_domain_group_title(conn)
        print("[crayon] Verified fantasia_domain.group_title")


# --- OpenAI utilities (OpenAI-only provider) ---
def _get_openai_client() -> Any:
    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is not set")
    if OpenAI is None:
        raise RuntimeError("openai SDK not installed. `pip install openai` (v1).")
    return OpenAI(api_key=api_key)

def _openai_json(system_msg: str, user_msg: str, model: str = "gpt-4o-mini") -> Dict[str, Any]:
    """
    Calls OpenAI with system+user messages and expects STRICT JSON back.
    Your system/user prompt already instructs 'Return ONLY JSON'.
    """
    client = _get_openai_client()
    resp = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": system_msg},
            {"role": "user", "content": user_msg},
        ],
        response_format={"type": "json_object"},
    )
    content = resp.choices[0].message.content
    return json.loads(content)


# --- DB helpers ---
def upsert_user(conn: sqlite3.Connection, email: str, display_name: str | None = None) -> None:
    conn.execute(
        """
        INSERT INTO fantasia_users(email, display_name)
        VALUES (?, ?)
        ON CONFLICT(email) DO UPDATE SET
          display_name = COALESCE(excluded.display_name, fantasia_users.display_name),
          updated_at = datetime('now')
        """,
        (email, display_name),
    )

def create_core(conn: sqlite3.Connection, title: str, description: str | None,
                owner_email: str, provider: str | None) -> int:
    cur = conn.execute(
        """
        INSERT INTO fantasia_core (title, description, owner_email, provider, created_at)
        VALUES (?, ?, ?, ?, datetime('now'))
        """,
        (title, description, owner_email, provider),
    )
    return int(cur.lastrowid)

def create_domain(conn: sqlite3.Connection, core_id: int, name: str, description: str | None,
                  group_title: str | None, provider: str | None, targets_json: str | None = None) -> int:
    cur = conn.execute(
        """
        INSERT INTO fantasia_domain (core_id, name, description, group_title, provider, targets_json, created_at)
        VALUES (?, ?, ?, ?, ?, ?, datetime('now'))
        """,
        (core_id, name, description, group_title, provider, targets_json),
    )
    return int(cur.lastrowid)

def render_prompt(template: str, **vars) -> str:
    """
    Safely render a template that contains literal JSON braces.
    Escapes all braces, then restores the two known placeholders.
    """
    safe = template.replace("{", "{{").replace("}", "}}")
    # restore actual placeholders
    for k in ("fantasia_core", "fantasia_core_description"):
        safe = safe.replace("{{" + k + "}}", "{" + k + "}")
    return safe.format(**vars)

DIM_LINE = re.compile(r"^\s*\d+\.\s*(?P<name>.+?)\s+—\s*(?P<thesis>.+?)\s*$")
TARGETS_LINE = re.compile(r"^\s*Narrative\s+Targets:\s*(?P<list>.+?)\s*$", re.I)

def parse_dimensions(text: str) -> list[dict]:
    """
    Parse the DIM output into [{name, thesis, targets:[...]}, ...].
    Robust to extra blank lines and trailing punctuation.
    """
    dims: list[dict] = []
    lines = [l.rstrip() for l in text.splitlines()]
    i = 0
    while i < len(lines):
        m = DIM_LINE.match(lines[i] or "")
        if not m:
            i += 1
            continue
        name = m.group("name").strip()
        thesis = m.group("thesis").strip()
        targets: list[str] = []
        j = i + 1
        # find nearest "Narrative Targets:" after the header line
        while j < len(lines):
            t = TARGETS_LINE.match(lines[j] or "")
            if t:
                raw = t.group("list")
                # split on comma or semicolon and strip
                for tok in re.split(r"[;,]", raw):
                    tok = tok.strip()
                    if tok:
                        targets.append(tok)
                break
            # stop if we hit another dimension header
            if DIM_LINE.match(lines[j] or ""):
                j -= 1  # step back so outer loop will reprocess header
                break
            j += 1
        dims.append({"name": name, "thesis": thesis, "targets": targets})
        i = max(j + 1, i + 1)
    return dims

def create_dimension(conn: sqlite3.Connection, domain_id: int, name: str,
                     thesis: str | None, description: str | None,
                     targets: list[str] | None, provider: str | None) -> int:
    cur = conn.execute(
        """
        INSERT INTO fantasia_dimension
            (domain_id, name, thesis, description, targets_json, provider, created_at)
        VALUES (?, ?, ?, ?, ?, ?, datetime('now'))
        """,
        (domain_id, name, thesis, description,
         json.dumps(targets or []), provider)
    )
    return int(cur.lastrowid)

def _openai_parse_with_pydantic(system_msg: str, user_msg: str, model: str, schema: type[BaseModel]) -> BaseModel:
    """
    Uses OpenAI Responses API with Pydantic parsing (openai>=1.51).
    """
    client = _get_openai_client()
    try:
        resp = client.responses.parse(
            model=model,
            input=[
                {"role": "system", "content": system_msg},
                {"role": "user", "content": user_msg},
            ],
            text_format=schema,
        )
    except Exception as e:
        # Surface SDK / API errors cleanly
        raise RuntimeError(f"OpenAI parse failed: {e}")
    if not hasattr(resp, "output_parsed") or resp.output_parsed is None:
        raise RuntimeError("OpenAI did not return parsed output")
    return resp.output_parsed  # This is an instance of `schema`





# --- API: Domain Architect (OpenAI only) ---
@app.post("/api/domain-architect")
def api_domain_architect():
    """
    Body JSON:
    {
      "email": "user@example.com",
      "core_title": "string",
      "core_description": "string",
      "model": "gpt-4o-mini"   // optional
    }
    """
    payload = request.get_json(silent=True) or {}
    email = (payload.get("email") or "").strip()
    core_title = (payload.get("core_title") or "").strip()
    core_desc = (payload.get("core_description") or "").strip()
    model = (payload.get("model") or "gpt-4o-mini").strip()

    if not email or not core_title:
        return jsonify(ok=False, error="Missing required fields: email, core_title"), 400

    # Prepare the LLM user message from your template
    user_msg = render_prompt(
        DOMAIN_ARCHITECT_USER_TEMPLATE,
        fantasia_core=core_title,
        fantasia_core_description=core_desc or "",
    )


    # Run the model
    try:
        llm_json = _openai_json(DOMAIN_ARCHITECT_SYS_MSG, user_msg, model=model)
    except Exception as e:
        return jsonify(ok=False, error=f"LLM failure: {e}"), 502

    # Basic shape validation
    if "groups" not in llm_json or not isinstance(llm_json["groups"], list):
        return jsonify(ok=False, error="LLM returned unexpected shape: missing 'groups' list"), 502

    # Persist: user, core, domains
    with connect() as conn, conn:  # transactional
        # ensure migration for group_title
        run_migrations_domain_group_title(conn)

        upsert_user(conn, email, display_name=None)
        core_id = create_core(conn, core_title, core_desc, owner_email=email, provider="openai")

        created_domains: List[Dict[str, Any]] = []
        for group in llm_json["groups"]:
            group_title = (group.get("title") or "").strip() or None
            domains = group.get("domains") or []
            if not isinstance(domains, list):
                continue
            for d in domains:
                name = ((d.get("name") or "").strip())
                desc = ((d.get("description") or "").strip()) or None
                if not name:
                    continue
                dom_id = create_domain(
                    conn=conn,
                    core_id=core_id,
                    name=name,
                    description=desc,
                    group_title=group_title,
                    provider="openai",
                    targets_json=None
                )
                created_domains.append({
                    "id": dom_id,
                    "name": name,
                    "description": desc,
                    "group_title": group_title
                })

        # Response: echo LLM structure + ids + core row
        return jsonify(
            ok=True,
            provider="openai",
            model=model,
            core={
                "id": core_id,
                "title": core_title,
                "description": core_desc,
                "owner_email": email
            },
            groups=[
                {
                    "title": (g.get("title") or None),
                    "domains": [
                        {
                            "id": next(
                                (cd["id"] for cd in created_domains
                                 if cd["name"] == (d.get("name") or "").strip()
                                 and cd["group_title"] == (g.get("title") or "").strip() or None),
                                None
                            ),
                            "name": (d.get("name") or ""),
                            "description": (d.get("description") or ""),
                        }
                        for d in (g.get("domains") or [])
                    ],
                }
                for g in llm_json.get("groups", [])
            ]
        ), 200

# ---- LIST CORES (by owner email) ----
@app.get("/api/cores", endpoint="crayon_list_cores")
def crayon_list_cores():
    q_email = (request.args.get("email") or "").strip().lower()
    if not q_email:
        return jsonify(ok=False, error="Missing required query param: email"), 400

    with connect() as conn:
        rows = conn.execute(
            """
            SELECT id, title, COALESCE(description,'') AS description,
                   owner_email, COALESCE(provider,'') AS provider, created_at
            FROM fantasia_core
            WHERE lower(owner_email) = ?
            ORDER BY created_at DESC
            """,
            (q_email,)
        ).fetchall()

    return jsonify(
        ok=True,
        cores=[{
            "id": r["id"],
            "title": r["title"],
            "description": r["description"],
            "owner_email": r["owner_email"],
            "provider": r["provider"] or None,
            "created_at": r["created_at"],
        } for r in rows]
    )

# ---- READ ONE CORE + ITS DOMAINS (grouped) ----
@app.get("/api/cores/<int:core_id>/domains", endpoint="crayon_read_core_with_domains")
def crayon_read_core_with_domains(core_id: int):
    q_email = (request.args.get("email") or "").strip().lower()
    if not q_email:
        return jsonify(ok=False, error="Missing required query param: email"), 400

    with connect() as conn:
        core = conn.execute(
            """
            SELECT id, title, COALESCE(description,'') AS description,
                   owner_email, COALESCE(provider,'') AS provider, created_at
            FROM fantasia_core
            WHERE id = ?
            """,
            (core_id,)
        ).fetchone()
        if not core:
            return jsonify(ok=False, error="Core not found"), 404
        if (core["owner_email"] or "").lower() != q_email:
            return jsonify(ok=False, error="Forbidden for this email"), 403

        dom_rows = conn.execute(
            """
            SELECT id, name, COALESCE(description,'') AS description,
                   COALESCE(group_title,'') AS group_title
            FROM fantasia_domain
            WHERE core_id = ?
            ORDER BY group_title COLLATE NOCASE, name COLLATE NOCASE
            """,
            (core_id,)
        ).fetchall()

    groups_map = {}
    for r in dom_rows:
        gt = r["group_title"] or ""
        groups_map.setdefault(gt, []).append({
            "id": r["id"],
            "name": r["name"],
            "description": r["description"],
            "group_title": (r["group_title"] or None),
        })

    groups = [{"title": (gt if gt else "Ungrouped"), "domains": doms}
              for gt, doms in groups_map.items()]

    return jsonify(
        ok=True,
        core={
            "id": core["id"],
            "title": core["title"],
            "description": core["description"],
            "owner_email": core["owner_email"],
            "provider": core["provider"] or None,
            "created_at": core["created_at"],
        },
        groups=groups
    )


@app.post("/api/dimensions/generate")
def api_generate_dimensions():
    """
    Body:
    {
      "email": "owner@example.com",
      "domain_id": 123,                  // required
      "count": 3,                        // optional (defaults 3 or 4)
      "model": "gpt-4o-mini"             // optional
    }

    Behavior:
      - Verifies domain belongs to a core owned by email.
      - Calls OpenAI with DIM_SYS_MSG + DIM_USER_TEMPLATE(domain name/desc).
      - Parses dimensions and inserts into fantasia_dimension.
      - Returns created rows.
    """
    payload = request.get_json(silent=True) or {}
    email = (payload.get("email") or "").strip().lower()
    domain_id = payload.get("domain_id")
    count = int(payload.get("count") or 3)
    model = (payload.get("model") or "gpt-4o-mini").strip()

    if not email or not domain_id:
        return jsonify(ok=False, error="Missing required fields: email, domain_id"), 400

    with connect() as conn:
        # fetch domain and owning core
        row = conn.execute(
            """
            SELECT d.id AS domain_id, d.name AS domain_name,
                   COALESCE(d.description,'') AS domain_description,
                   c.id AS core_id, c.owner_email
            FROM fantasia_domain d
            JOIN fantasia_core c ON c.id = d.core_id
            WHERE d.id = ?
            """,
            (domain_id,)
        ).fetchone()
        if not row:
            return jsonify(ok=False, error="Domain not found"), 404
        if (row["owner_email"] or "").lower() != email:
            return jsonify(ok=False, error="Forbidden for this email"), 403

        # Build user message safely (brace-escaping)
        user_msg = render_prompt(
            DIM_USER_TEMPLATE,
            domain_name=row["domain_name"],
            domain_description=row["domain_description"],
            count=count
        )

        # Prefer a structured model (your default can be "gpt-4o-2024-08-06" for JSON reliability)
        model = payload.get("model") or "gpt-5-mini-2025-08-07"

        try:
            parsed: PDDimensionsResponse = _openai_parse_with_pydantic(
                DIM_SYS_MSG, user_msg, model=model, schema=PDDimensionsResponse
            )
        except Exception as e:
            return jsonify(ok=False, error=str(e)), 502

        dims_payload = parsed.dimensions or []
        if not dims_payload:
            return jsonify(ok=False, error="No dimensions returned"), 502

        created = []
        with conn:
            for d in dims_payload:
                name = (d.name or "").strip()
                thesis = (d.thesis or "").strip() or None
                targets = [t.strip() for t in (d.targets or []) if t and t.strip()]
                if not name:
                    continue
                dim_id = create_dimension(
                    conn=conn,
                    domain_id=row["domain_id"],
                    name=name,
                    thesis=thesis,
                    description=None,
                    targets=targets,
                    provider="openai",
                )
                created.append({
                    "id": dim_id,
                    "name": name,
                    "thesis": thesis,
                    "targets": targets
                })


@app.get("/api/domains/<int:domain_id>/dimensions")
def read_dimensions_for_domain(domain_id: int):
    q_email = (request.args.get("email") or "").strip().lower()
    if not q_email:
        return jsonify(ok=False, error="Missing required query param: email"), 400

    with connect() as conn:
        # auth check: domain must belong to a core owned by email
        owner = conn.execute(
            """
            SELECT c.owner_email
            FROM fantasia_domain d
            JOIN fantasia_core c ON c.id = d.core_id
            WHERE d.id = ?
            """,
            (domain_id,)
        ).fetchone()
        if not owner:
            return jsonify(ok=False, error="Domain not found"), 404
        if (owner["owner_email"] or "").lower() != q_email:
            return jsonify(ok=False, error="Forbidden for this email"), 403

        rows = conn.execute(
            """
            SELECT id, name, COALESCE(thesis,'') AS thesis,
                   COALESCE(description,'') AS description,
                   COALESCE(targets_json,'[]') AS targets_json,
                   COALESCE(provider,'') AS provider,
                   created_at
            FROM fantasia_dimension
            WHERE domain_id = ?
            ORDER BY created_at DESC, name COLLATE NOCASE
            """,
            (domain_id,)
        ).fetchall()

    return jsonify(
        ok=True,
        domain_id=domain_id,
        dimensions=[{
            "id": r["id"],
            "name": r["name"],
            "thesis": r["thesis"],
            "description": r["description"],
            "targets": json.loads(r["targets_json"] or "[]"),
            "provider": r["provider"] or None,
            "created_at": r["created_at"]
        } for r in rows]
    )
