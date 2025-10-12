from __future__ import annotations
import os
from openai import OpenAI
import sqlite3
from flask import Flask, jsonify, abort, request, Response
from typing import List, Optional, Literal, Any, Dict, Tuple
from pydantic import BaseModel, Field, conlist, constr
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
    THESIS_SYS_MSG, 
    THESIS_USER_TEMPLATE,
    THESIS_EVAL_SYS_MSG, 
    THESIS_EVAL_USER_TEMPLATE,
    FANTASIA_SYS_MSG, 
    FANTASIA_USER_TEMPLATE,
)



DB_PATH = "/var/www/site/data/crayon_data.db"
DATA_DIR = str(Path(DB_PATH).parent)

app = Flask(__name__)

def connect(db_path: str = DB_PATH) -> sqlite3.Connection:
    Path(DATA_DIR).mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    # Sensible pragmas
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    return conn

# --- Base schema (idempotent) ---
SCHEMA = [
    # migrations meta
    """
    CREATE TABLE IF NOT EXISTS schema_migrations (
      id INTEGER PRIMARY KEY,
      name TEXT NOT NULL UNIQUE,
      applied_at TEXT NOT NULL DEFAULT (datetime('now'))
    );
    """,

    # users
    """
    CREATE TABLE IF NOT EXISTS fantasia_users (
      id INTEGER PRIMARY KEY,
      email TEXT NOT NULL UNIQUE,
      display_name TEXT,
      role TEXT CHECK (role IN ('owner','editor','viewer')) DEFAULT 'owner',
      created_at TEXT NOT NULL DEFAULT (datetime('now')),
      updated_at TEXT
    );
    """,

    # core
    """
    CREATE TABLE IF NOT EXISTS fantasia_core (
      id INTEGER PRIMARY KEY,
      title TEXT NOT NULL,
      description TEXT,
      owner_email TEXT NOT NULL,
      status TEXT DEFAULT 'active',
      provider TEXT,
      tags_json TEXT,
      created_at TEXT NOT NULL DEFAULT (datetime('now')),
      updated_at TEXT,
      FOREIGN KEY (owner_email) REFERENCES fantasia_users(email) ON UPDATE CASCADE ON DELETE SET NULL
    );
    """,
    "CREATE INDEX IF NOT EXISTS idx_core_owner ON fantasia_core(owner_email);",
    "CREATE INDEX IF NOT EXISTS idx_core_title ON fantasia_core(title);",

    # domain (note: includes group_title for grouping)
    """
    CREATE TABLE IF NOT EXISTS fantasia_domain (
      id INTEGER PRIMARY KEY,
      core_id INTEGER NOT NULL,
      name TEXT NOT NULL,
      description TEXT,
      group_title TEXT,               -- for Domain Architect groups
      provider TEXT,
      targets_json TEXT,
      created_at TEXT NOT NULL DEFAULT (datetime('now')),
      updated_at TEXT,
      FOREIGN KEY (core_id) REFERENCES fantasia_core(id) ON DELETE CASCADE
    );
    """,
    "CREATE INDEX IF NOT EXISTS idx_domain_core ON fantasia_domain(core_id);",
    "CREATE INDEX IF NOT EXISTS idx_domain_name ON fantasia_domain(name);",

    # dimension
    """
    CREATE TABLE IF NOT EXISTS fantasia_dimension (
      id INTEGER PRIMARY KEY,
      domain_id INTEGER NOT NULL,
      name TEXT NOT NULL,
      thesis TEXT,
      description TEXT,
      targets_json TEXT,
      provider TEXT,
      created_at TEXT NOT NULL DEFAULT (datetime('now')),
      updated_at TEXT,
      FOREIGN KEY (domain_id) REFERENCES fantasia_domain(id) ON DELETE CASCADE
    );
    """,
    "CREATE INDEX IF NOT EXISTS idx_dimension_domain ON fantasia_dimension(domain_id);",
    "CREATE INDEX IF NOT EXISTS idx_dimension_name ON fantasia_dimension(name);",

    # thesis (matches your current APIs)
    """
    CREATE TABLE IF NOT EXISTS fantasia_thesis (
        id           INTEGER PRIMARY KEY,
        core_id      INTEGER NOT NULL,
        domain_id    INTEGER NOT NULL,
        dimension_id INTEGER NOT NULL,
        thesis_text  TEXT    NOT NULL,
        provider     TEXT,
        analysis_json TEXT,
        author_email TEXT,  -- NEW
        created_at   TEXT    NOT NULL DEFAULT (datetime('now')),
        updated_at   TEXT,
        FOREIGN KEY (core_id)      REFERENCES fantasia_core(id)      ON DELETE CASCADE,
        FOREIGN KEY (domain_id)    REFERENCES fantasia_domain(id)    ON DELETE CASCADE,
        FOREIGN KEY (dimension_id) REFERENCES fantasia_dimension(id) ON DELETE CASCADE
        -- If you rebuild table later, you can also add:
        -- ,FOREIGN KEY (author_email) REFERENCES fantasia_users(email) ON UPDATE CASCADE ON DELETE SET NULL
        );
    """,
    "CREATE INDEX IF NOT EXISTS idx_thesis_core          ON fantasia_thesis(core_id);",
    "CREATE INDEX IF NOT EXISTS idx_thesis_domain        ON fantasia_thesis(domain_id);",
    "CREATE INDEX IF NOT EXISTS idx_thesis_dimension     ON fantasia_thesis(dimension_id);",
    "CREATE INDEX IF NOT EXISTS idx_thesis_created_at    ON fantasia_thesis(created_at);",
    "CREATE INDEX IF NOT EXISTS idx_thesis_author_email  ON fantasia_thesis(author_email);",  # NEW

    # quoted-name compatibility views
    'CREATE VIEW IF NOT EXISTS "fantasia-core"      AS SELECT * FROM fantasia_core;',
    'CREATE VIEW IF NOT EXISTS "fantasia-domain"    AS SELECT * FROM fantasia_domain;',
    'CREATE VIEW IF NOT EXISTS "fantasia-dimension" AS SELECT * FROM fantasia_dimension;',
    'CREATE VIEW IF NOT EXISTS "fantasia-thesis"    AS SELECT * FROM fantasia_thesis;',
    'CREATE VIEW IF NOT EXISTS "fantasia-users"     AS SELECT * FROM fantasia_users;',
]

MIGRATION_NAME = "001_crayon_initial"

def _init_llm_usage_table(conn):
    conn.execute("""
    CREATE TABLE IF NOT EXISTS llm_usage_counters (
        date TEXT PRIMARY KEY,                      -- 'YYYY-MM-DD' or 'ALL_TIME'
        input_tokens  INTEGER NOT NULL DEFAULT 0,
        output_tokens INTEGER NOT NULL DEFAULT 0,
        total_tokens  INTEGER NOT NULL DEFAULT 0
    );
    """)
    # Ensure ALL_TIME row exists
    conn.execute("""
    INSERT OR IGNORE INTO llm_usage_counters(date, input_tokens, output_tokens, total_tokens)
    VALUES ('ALL_TIME', 0, 0, 0)
    """)
    conn.commit()

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    return conn


def already_applied(conn: sqlite3.Connection, name: str) -> bool:
    has_table = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='schema_migrations';"
    ).fetchone() is not None
    if not has_table:
        return False
    return conn.execute(
        "SELECT 1 FROM schema_migrations WHERE name = ?;", (name,)
    ).fetchone() is not None

def apply_schema(conn: sqlite3.Connection) -> None:
    with conn:
        for stmt in SCHEMA:
            conn.execute(stmt)
        conn.execute(
            "INSERT OR IGNORE INTO schema_migrations (name) VALUES (?);",
            (MIGRATION_NAME,)
        )

# --- small, idempotent column helpers (for future tweaks) ---
def ensure_column(conn: sqlite3.Connection, table: str, col: str, decl: str) -> None:
    cols = {r["name"].lower() for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    if col.lower() not in cols:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {decl};")

def run_migration_thesis_author(conn: sqlite3.Connection) -> None:
    # Add column if missing
    ensure_column(conn, "fantasia_thesis", "author_email", "TEXT")
    # Index
    conn.execute("CREATE INDEX IF NOT EXISTS idx_thesis_author_email ON fantasia_thesis(author_email);")


def run_post_migrations(conn: sqlite3.Connection) -> None:
    # Keep these around so later changes stay safe if the base SCHEMA shipped earlier without them.
    ensure_column(conn, "fantasia_domain", "group_title", "TEXT")
    ensure_column(conn, "fantasia_thesis", "analysis_json", "TEXT")
    ensure_column(conn, "fantasia_thesis", "core_id", "INTEGER")
    ensure_column(conn, "fantasia_thesis", "domain_id", "INTEGER")
    ensure_column(conn, "fantasia_thesis", "thesis_text", "TEXT")
    ensure_column(conn, "fantasia_thesis", "analysis_json", "TEXT")
    run_migration_thesis_author(conn)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_thesis_core ON fantasia_thesis(core_id);")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_thesis_domain ON fantasia_thesis(domain_id);")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_thesis_created_at ON fantasia_thesis(created_at);")


# --- health & init endpoints ---
@app.get("/health")
def health():
    try:
        with connect() as conn:
            conn.execute("SELECT 1;").fetchone()
        return jsonify(ok=True, db=DB_PATH, ts=datetime.utcnow().isoformat() + "Z")
    except Exception as e:
        return jsonify(ok=False, error=str(e)), 500

@app.post("/admin/init-db")
def init_db_endpoint():
    with connect() as conn:
        apply_schema(conn)
        run_post_migrations(conn)
    return jsonify(ok=True, db=DB_PATH, applied=MIGRATION_NAME)

# ensure your /admin/init-db (or app startup) calls run_all_migrations(conn)


def create_thesis(conn: sqlite3.Connection, core_id: int, domain_id: int, dimension_id: int,
                  text: str, provider: Optional[str], author_email: Optional[str]) -> int:
    cur = conn.execute(
        """
        INSERT INTO fantasia_thesis
          (core_id, domain_id, dimension_id, thesis_text, provider, analysis_json, author_email, created_at)
        VALUES (?,      ?,         ?,           ?,          ?,        NULL,         ?,            datetime('now'))
        """,
        (core_id, domain_id, dimension_id, text, provider, (author_email or None))
    )
    return int(cur.lastrowid)


def update_thesis_analysis(conn: sqlite3.Connection, thesis_id: int, analysis_json: str) -> None:
    conn.execute(
        "UPDATE fantasia_thesis SET analysis_json = ?, updated_at = datetime('now') WHERE id = ?",
        (analysis_json, thesis_id)
    )

def ensure_thesis_analysis_column(conn: sqlite3.Connection) -> None:
    # add fantasia_thesis.analysis_json if missing
    ensure_column(conn, "fantasia_thesis", "analysis_json", "TEXT")

def run_migrations_extra(conn: sqlite3.Connection) -> None:
    run_migrations_domain_group_title(conn)
    ensure_thesis_analysis_column(conn)

# and ensure init runs it:
def init_db_cli():
    with connect() as conn:
        if not already_applied(conn, MIGRATION_NAME):
            apply_schema(conn)
            print(f"[crayon] Applied schema: {MIGRATION_NAME} @ {DB_PATH}")
        run_migrations_extra(conn)
        print("[crayon] Verified fantasia_domain.group_title & fantasia_thesis.analysis_json")



class PDTargetList(BaseModel):
    # Allow either list[str] directly or nested inside DimensionsResponse; included for future flexibility
    pass

class PDDimension(BaseModel):
    name: str = Field(..., description="Dimension name")
    thesis: str = Field(..., description="1–2 sentence distilled thesis")
    targets: List[str] = Field(..., min_length=3, max_length=6, description="Short target phrases")

class PDDimensionsResponse(BaseModel):
    dimensions: List[PDDimension] = Field(..., min_length=1)

class PDThesis(BaseModel):
    thesis: str = Field(..., description="Precise thesis. 2-3 sentences")

class PDThesisEval(BaseModel):
    verification: List[str] = Field(..., min_length=1, description="Steps to verify/falsify")
    if_true: str = Field(..., description="Next steps if thesis holds")
    if_false_alternative_thesis: str = Field(..., description="Alternative thesis if falsified")

class PDFantasiaItem(BaseModel):
    title: constr(strip_whitespace=True, min_length=1)
    description: constr(strip_whitespace=True, min_length=10)
    human_interest: constr(strip_whitespace=True, min_length=5)

class PDFantasiaResponse(BaseModel):
    fantasia: conlist(PDFantasiaItem, min_length=6, max_length=8)


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
def _usage_from_resp(resp) -> dict:
    u = getattr(resp, "usage", None)
    get = (lambda k: (u.get(k) if isinstance(u, dict) else getattr(u, k, None)) if u else None)
    inp  = get("prompt_tokens") or get("input_tokens")  or 0
    outp = get("completion_tokens") or get("output_tokens") or 0
    tot  = get("total_tokens") or (inp + outp)
    return {"input": int(inp), "output": int(outp), "total": int(tot)}


def _get_openai_client() -> Any:
    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is not set")
    if OpenAI is None:
        raise RuntimeError("openai SDK not installed. `pip install openai` (v1).")
    return OpenAI(api_key=api_key)

def _openai_json(system_msg: str, user_msg: str, model: str = "gpt-5-mini-2025-08-07") -> dict:
    """
    Calls OpenAI with system+user messages and expects STRICT JSON back.
    Records token usage to the local SQLite counters table.
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

    # --- new: extract usage and record ---
    usage = _usage_from_resp(resp)
    try:
        conn = get_db()
        _record_llm_usage(conn, usage)
    except Exception as e:
        print(f"[warn] failed to record llm usage: {e}")

    # --- parse JSON payload ---
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
    Safely render a template that contains literal JSON braces by:
      1) Escaping all braces,
      2) Re-enabling the placeholders present in `vars`,
      3) Formatting with those vars.
    """
    safe = template.replace("{", "{{").replace("}", "}}")
    for k in vars.keys():
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
    Records token usage to the local SQLite counters table.
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
        raise RuntimeError(f"OpenAI parse failed: {e}")

    if not hasattr(resp, "output_parsed") or resp.output_parsed is None:
        raise RuntimeError("OpenAI did not return parsed output")

    # --- new: extract and record token usage ---
    try:
        usage = _usage_from_resp(resp)
        conn = get_db()
        _record_llm_usage(conn, usage)
    except Exception as e:
        print(f"[warn] failed to record llm usage: {e}")

    return resp.output_parsed  # instance of `schema`



def _record_llm_usage(conn, usage: dict):
    if not usage: 
        return
    inp  = int(usage.get("input")  or 0)
    outp = int(usage.get("output") or 0)
    tot  = int(usage.get("total")  or (inp + outp))
    if (inp + outp + tot) == 0:
        return
    today = datetime.now(ZoneInfo("America/Chicago")).strftime("%Y-%m-%d")

    with conn:  # atomic transaction
        # Ensure today's row exists
        conn.execute("""
            INSERT OR IGNORE INTO llm_usage_counters(date, input_tokens, output_tokens, total_tokens)
            VALUES (?, 0, 0, 0)
        """, (today,))
        # Bump today's counters
        conn.execute("""
            UPDATE llm_usage_counters
            SET input_tokens  = input_tokens  + ?,
                output_tokens = output_tokens + ?,
                total_tokens  = total_tokens  + ?
            WHERE date = ?
        """, (inp, outp, tot, today))
        # Bump ALL_TIME counters
        conn.execute("""
            UPDATE llm_usage_counters
            SET input_tokens  = input_tokens  + ?,
                output_tokens = output_tokens + ?,
                total_tokens  = total_tokens  + ?
            WHERE date = 'ALL_TIME'
        """, (inp, outp, tot))






# --- API: Domain Architect (OpenAI only) ---
@app.post("/api/domain-architect")
def api_domain_architect():
    """
    Body JSON:
    {
      "email": "user@example.com",
      "core_title": "string",
      "core_description": "string",
      "model": "gpt-5-mini-2025-08-07"   // optional
    }
    """
    payload = request.get_json(silent=True) or {}
    email = (payload.get("email") or "").strip()
    core_title = (payload.get("core_title") or "").strip()
    core_desc = (payload.get("core_description") or "").strip()
    model = (payload.get("model") or "gpt-5-mini-2025-08-07").strip()

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
      "domain_id": 123,
      "count": 3,
      "model": "gpt-5-mini-2025-08-07"
    }
    """
    payload = request.get_json(silent=True) or {}
    email = (payload.get("email") or "").strip().lower()
    domain_id = payload.get("domain_id")
    count = int(payload.get("count") or 3)
    model = (payload.get("model") or "gpt-5-mini-2025-08-07").strip()

    if not email or not domain_id:
        return jsonify(ok=False, error="Missing required fields: email, domain_id"), 400

    # Fetch + auth
    with connect() as conn:
        row = conn.execute(
            """
            SELECT
            d.id   AS domain_id,
            d.name AS domain_name,
            COALESCE(d.description,'') AS domain_description,
            c.id   AS core_id,
            c.title AS core_name,
            COALESCE(c.description,'') AS core_description,
            c.owner_email
            FROM fantasia_domain d
            JOIN fantasia_core   c ON c.id = d.core_id
            WHERE d.id = ?
            """,
            (domain_id,)
        ).fetchone()

        if not row:
            return jsonify(ok=False, error="Domain not found"), 404
        if (row["owner_email"] or "").lower() != email:
            return jsonify(ok=False, error="Forbidden for this email"), 403

    # Build prompt (brace-safe)
    user_msg = render_prompt(
        DIM_USER_TEMPLATE,
        core_name=row["core_name"],
        core_description=row["core_description"],
        domain_name=row["domain_name"],
        domain_description=row["domain_description"],
        count=count,
    )

    # Call OpenAI with Pydantic parsing; fall back to JSON mode if unavailable
    try:
        client = _get_openai_client()
        parsed = None

        # Prefer responses.parse with Pydantic (openai>=1.51)
        try:
            resp = client.responses.parse(
                model=model,
                input=[
                    {"role": "system", "content": DIM_SYS_MSG},
                    {"role": "user", "content": user_msg},
                ],
                text_format=PDDimensionsResponse,
            )
            parsed = resp.output_parsed  # PDDimensionsResponse
            usage = _usage_from_resp(resp)
            _record_llm_usage(conn, usage)
        except Exception as e_parse:
            return jsonify(ok=False, error=f"OpenAI parse failed: {e_parse}"), 502

        if not parsed or not parsed.dimensions:
            return jsonify(ok=False, error="No dimensions returned"), 502

        dims_payload = parsed.dimensions

    except Exception as e:
        # Any unexpected client errors
        return jsonify(ok=False, error=f"LLM failure: {e}"), 502

    # Persist
    created = []
    with connect() as conn, conn:
        for d in dims_payload:
            name = (d.name or "").strip()
            if not name:
                continue
            thesis = (d.thesis or "").strip() or None
            targets = [t.strip() for t in (d.targets or []) if t and t.strip()]
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

    return jsonify(
        ok=True,
        provider="openai",
        model=model,
        domain={
            "id": row["domain_id"],
            "name": row["domain_name"],
            "description": row["domain_description"],
            "core_id": row["core_id"]
        },
        dimensions=created
    ), 200



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

@app.post("/api/thesis/generate")
def api_thesis_generate():
    """
    Body:
    {
      "email": "owner@example.com",
      "dimension_id": 123,
      "model": "gpt-5-mini-2025-08-07"
    }
    """
    payload = request.get_json(silent=True) or {}
    email = (payload.get("email") or "").strip().lower()
    dim_id = payload.get("dimension_id")
    model = (payload.get("model") or "gpt-5-mini-2025-08-07").strip()

    if not email or not dim_id:
        return jsonify(ok=False, error="Missing required fields: email, dimension_id"), 400

    # Fetch dimension + domain + core; auth by owner
    with connect() as conn:
        row = conn.execute(
            """
            SELECT
              dim.id   AS dimension_id,
              COALESCE(dim.name,'') AS dimension_name,
              COALESCE(dim.thesis,'') AS dimension_thesis,
              COALESCE(dim.description,'') AS dimension_description,
              COALESCE(dim.targets_json,'') AS dimension_targets,
              d.id   AS domain_id,
              COALESCE(d.name,'') AS domain_name,
              COALESCE(d.description,'') AS domain_description,
              c.id   AS core_id,
              c.title AS core_name,
              COALESCE(c.description,'') AS core_description,
              c.owner_email
            FROM fantasia_dimension dim
            JOIN fantasia_domain    d ON d.id = dim.domain_id
            JOIN fantasia_core      c ON c.id = d.core_id
            WHERE dim.id = ?
            """,
            (dim_id,)
        ).fetchone()

        if not row:
            return jsonify(ok=False, error="Dimension not found"), 404
        if (row["owner_email"] or "").lower() != email:
            return jsonify(ok=False, error="Forbidden for this email"), 403

    # Build user message (brace-safe)
    user_msg = render_prompt(
        THESIS_USER_TEMPLATE,
        core_name=row["core_name"],
        core_description=row["core_description"],
        domain_name=row["domain_description"],
        domain_description=row["domain_description"],
        dimension_name=(row["dimension_name"] or ""),
        dimension_description=(row["dimension_description"] or row["dimension_thesis"] or row["dimension_name"] or ""),
        dimension_targets=(row["dimension_targets"] or ""),
    )

    # Generate thesis
    try:
        parsed: PDThesis = _openai_parse_with_pydantic(
            THESIS_SYS_MSG, user_msg, model=model, schema=PDThesis
        )
    except Exception as e:
        return jsonify(ok=False, error=str(e)), 502

    thesis_text = (parsed.thesis or "").strip().strip('"')
    if not thesis_text:
        return jsonify(ok=False, error="Empty thesis returned"), 502

    # Persist
    with connect() as conn, conn:
        thesis_id = create_thesis(
            conn=conn,
            core_id=row["core_id"],
            domain_id=row["domain_id"],
            dimension_id=row["dimension_id"],
            text=thesis_text,
            provider="openai",
            author_email=email,   # NEW
        )


    return jsonify(
        ok=True,
        provider="openai",
        model=model,
        thesis={
            "id": thesis_id,
            "text": thesis_text,
            "core_id": row["core_id"],
            "domain_id": row["domain_id"],
            "dimension_id": row["dimension_id"]
        }
    ), 200


@app.post("/api/thesis/evaluate")
def api_thesis_evaluate():
    """
    Body:
    {
      "email": "owner@example.com",
      "thesis_id": 456,
      "model": "gpt-5-mini-2025-08-07",
      "save": true   # optional; default true: store analysis_json onto the thesis
    }
    """
    payload = request.get_json(silent=True) or {}
    email = (payload.get("email") or "").strip().lower()
    thesis_id = payload.get("thesis_id")
    model = (payload.get("model") or "gpt-5-mini-2025-08-07").strip()
    save = payload.get("save", True)

    if not email or not thesis_id:
        return jsonify(ok=False, error="Missing required fields: email, thesis_id"), 400

    with connect() as conn:
        row = conn.execute(
            """
            SELECT
              t.id   AS thesis_id,
              t.thesis_text,
              dim.id AS dimension_id,
              COALESCE(dim.name,'') AS dimension_name,
              COALESCE(dim.thesis,'') AS dimension_thesis,
              COALESCE(dim.description,'') AS dimension_description,
              COALESCE(dim.targets_json,'') AS dimension_targets,
              d.id   AS domain_id,
              COALESCE(d.name,'') AS domain_name,
              COALESCE(d.description,'') AS domain_description,
              c.id   AS core_id,
              c.title AS core_name,
              COALESCE(c.description,'') AS core_description,
              c.owner_email
            FROM fantasia_thesis t
            JOIN fantasia_dimension dim ON dim.id = t.dimension_id
            JOIN fantasia_domain    d   ON d.id = t.domain_id
            JOIN fantasia_core      c   ON c.id = t.core_id
            WHERE t.id = ?
            """,
            (thesis_id,)
        ).fetchone()

        if not row:
            return jsonify(ok=False, error="Thesis not found"), 404
        if (row["owner_email"] or "").lower() != email:
            return jsonify(ok=False, error="Forbidden for this email"), 403

    user_msg = render_prompt(
        THESIS_EVAL_USER_TEMPLATE,
        core_name=row["core_name"],
        core_description=row["core_description"],
        domain_name=row["domain_name"],
        domain_description=row["domain_description"],
        dimension_name=(row["dimension_name"] or ""),
        dimension_description=(row["dimension_description"] or row["dimension_thesis"] or ""),
        dimension_targets=(row["dimension_targets"] or ""),
        thesis=row["thesis_text"],
    )

    try:
        parsed: PDThesisEval = _openai_parse_with_pydantic(
            THESIS_EVAL_SYS_MSG, user_msg, model=model, schema=PDThesisEval
        )
    except Exception as e:
        return jsonify(ok=False, error=str(e)), 502

    analysis = {
        "verification": parsed.verification,
        "if_true": parsed.if_true,
        "if_false_alternative_thesis": parsed.if_false_alternative_thesis,
    }

    if save:
        with connect() as conn, conn:
            ensure_thesis_analysis_column(conn)
            update_thesis_analysis(conn, thesis_id=int(row["thesis_id"]), analysis_json=json.dumps(analysis))

    return jsonify(
        ok=True,
        provider="openai",
        model=model,
        thesis_id=row["thesis_id"],
        analysis=analysis,
        saved=bool(save),
    ), 200


@app.get("/api/dimensions/<int:dimension_id>/theses")
def list_theses_for_dimension(dimension_id: int):
    q_email = (request.args.get("email") or "").strip().lower()
    if not q_email:
        return jsonify(ok=False, error="Missing required query param: email"), 400
    with connect() as conn:
        owner = conn.execute(
            "SELECT c.owner_email FROM fantasia_dimension dim JOIN fantasia_domain d ON d.id=dim.domain_id JOIN fantasia_core c ON c.id=d.core_id WHERE dim.id=?",
            (dimension_id,)
        ).fetchone()
        if not owner: return jsonify(ok=False, error="Dimension not found"), 404
        if (owner["owner_email"] or "").lower() != q_email:
            return jsonify(ok=False, error="Forbidden for this email"), 403
        rows = conn.execute(
            "SELECT id, thesis_text, COALESCE(analysis_json,'') AS analysis_json, created_at FROM fantasia_thesis WHERE dimension_id=? ORDER BY created_at DESC",
            (dimension_id,)
        ).fetchall()
    return jsonify(ok=True, theses=[{"id":r["id"], "text":r["thesis_text"], "analysis": (json.loads(r["analysis_json"]) if r["analysis_json"] else None), "created_at": r["created_at"]} for r in rows])


@app.get("/api/admin/llm-usage")
def get_llm_usage():
    conn = get_db()
    rows = conn.execute("SELECT date, input_tokens, output_tokens, total_tokens FROM llm_usage_counters ORDER BY date DESC").fetchall()
    return jsonify(ok=True, usage=[dict(r) for r in rows])


@app.post("/api/fantasia/generate")
def api_fantasia_generate():
    """
    Generate 6–8 fantasia via Pydantic parsing.
    Token usage is recorded by _openai_parse_with_pydantic.
    """
    data = request.get_json(force=True) or {}
    focus = (data.get("focus") or "").strip()
    excerpt = (data.get("excerpt") or "").strip()
    if not excerpt:
        return jsonify(ok=False, error="excerpt is required"), 400

    model = data.get("model") or "gpt-5-mini-2025-08-07"
    user_msg = FANTASIA_USER_TEMPLATE.format(focus=focus, excerpt=excerpt)

    try:
        parsed: PDFantasiaResponse = _openai_parse_with_pydantic(
            FANTASIA_SYS_MSG, user_msg, model=model, schema=PDFantasiaResponse
        )
    except Exception as e:
        return jsonify(ok=False, error=str(e)), 500

    # Optionally persist here if desired later.
    return jsonify(ok=True, model=model, fantasia=[fi.model_dump() for fi in parsed.fantasia]), 200