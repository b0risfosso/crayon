import os
from openai import OpenAI
import sqlite3
from flask import Flask, jsonify, abort, request, Response
from typing import List, Optional, Literal
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
    DOMAIN_ARCHITECT_SYS_MSG,
    DOMAIN_ARCHITECT_USER_TEMPLATE,
)
from __future__ import annotations



DB_PATH = "/var/www/site/data/crayon_data.db"
DATA_DIR = str(Path(DB_PATH).parent)

app = Flask(__name__)


#!/usr/bin/env python3
# crayon.py
# Minimal Flask service + SQLite initializer for the new Fantasia schema.

from __future__ import annotations
import os
import json
import sqlite3
from pathlib import Path
from datetime import datetime
from flask import Flask, jsonify, request

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

if __name__ == "__main__":
    # Initialize on boot, then serve
    init_db_cli()
    # Bind to a new port/service (keeps old app.py live)
    # Example: run behind systemd+gunicorn as crayon.service on 8011
    app.run(host="127.0.0.1", port=8011, debug=False)
