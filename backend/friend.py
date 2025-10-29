import sqlite3
import threading
import queue
import time
import re
import json
from pathlib import Path
from datetime import datetime
from flask import Flask, request, jsonify
import logging

import os
import json, re, hashlib
import time
from datetime import datetime
from pathlib import Path
from typing import List, Tuple, Optional, Iterable, Any, Dict
import sqlite3
import subprocess
from pathlib import Path
import tempfile
import shutil
import logging
import random

from flask import Flask, request, jsonify
import uuid

import time
import traceback

from zoneinfo import ZoneInfo

from crayon_prompts import (
    DOMAIN_ARCHITECT_SYS_MSG,
    DOMAIN_ARCHITECT_USER_TEMPLATE,
    DIM_SYS_MSG, DIM_USER_TEMPLATE,
    THESIS_SYS_MSG, THESIS_USER_TEMPLATE,
    SIM_SYS_MSG,
)

import threading
import queue
import time

# import shared helpers from jid.py
from jid import (
    FANTASIA_DB_PATH,
    DB_PATH_DEFAULT as JID_DB_PATH,
    #ensure_fantasia_db,
    _usage_from_resp,
    _record_llm_usage,
    _record_llm_usage_by_model,
    _read_usage_snapshot,
    _today_for_model,
    short_hash,
    RunLogger,
    _TokenBudget,
    # Helpers you'll need (we'll define below if they don't exist yet in jid)
    # e.g. _load_core_state, _insert_domain, _insert_dimension, _insert_thesis,
    #      build_domain_prompt, build_dimension_prompt, build_thesis_prompt,
    #      etc.
)

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

# --- Pydantic (v2 preferred; v1 shim) ---
try:
    from pydantic import BaseModel, Field, ValidationError
except Exception:  # pragma: no cover
    from pydantic.v1 import BaseModel, Field, ValidationError  # type: ignore

# import OpenAI client that jid already uses
try:
    from openai import OpenAI
    _OPENAI_STYLE = "new"
    _client = OpenAI()
except Exception as _e:  # pragma: no cover
    pass

from time import sleep

TOKEN_CHECK_INTERVAL = 60  # seconds between token-limit rechecks


log = logging.getLogger("jid_friend")
logging.basicConfig(level=logging.INFO)

app = Flask("jid-friend")

_WORKER_COUNT = 4 

_fantasia_job_queue = queue.Queue()
_fantasia_job_seen  = set()          # core_ids currently queued or in progress
_fantasia_workers_started = 0
_fantasia_worker_lock = threading.Lock()



class PD_DomainItem(BaseModel):
    name: str
    description: str

class PD_DomainGroup(BaseModel):
    title: str
    domains: list[PD_DomainItem]

class PD_DomainArchitectOut(BaseModel):
    groups: list[PD_DomainGroup]

class PDDimension(BaseModel):
    name: str = Field(..., description="Dimension name")
    thesis: str = Field(..., description="1–2 sentence distilled thesis")
    targets: List[str] = Field(..., min_length=3, max_length=6, description="Short target phrases")

class PDDimensionsResponse(BaseModel):
    dimensions: List[PDDimension] = Field(..., min_length=1)

class PDThesis(BaseModel):
    thesis: str = Field(..., description="Precise thesis. 2-3 sentences")


def _worker_thread_loop():
    while True:
        job = _fantasia_job_queue.get()  # blocking wait for next job
        core_id = job.get("core_id")
        model = job.get("model", "gpt-5-mini-2025-08-07")
        daily_cap = int(job.get("daily_cap", 10_000_000))

        try:
            # --- Token Guard ---
            # Check current token usage for this model in jid.db
            usage_today = _today_for_model(JID_DB_PATH, model)
            used_total = usage_today.get("total", 0)

            if used_total >= daily_cap:
                log.warning(
                    f"⏸️ Token guard: {model} used {used_total}/{daily_cap} tokens today. "
                    "Pausing queue."
                )
                # Put job back into queue head
                _fantasia_job_queue.put(job)
                _fantasia_job_queue.task_done()
                # Sleep before retrying
                sleep(TOKEN_CHECK_INTERVAL)
                continue

            # --- Safe to process ---
            _process_job(job)

        except Exception as e:
            log.error("💥 worker error core_id=%s: %s", core_id, e, exc_info=True)
        finally:
            # free this core for future enqueueing regardless of result
            _fantasia_job_seen.discard(core_id)
            _fantasia_job_queue.task_done()


def ensure_fantasia_db(db_path: str = FANTASIA_DB_PATH):
    conn = sqlite3.connect(db_path, timeout=30.0)
    try:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA synchronous=NORMAL;")

        # cores table (unchanged)
        conn.execute("""
        CREATE TABLE IF NOT EXISTS fantasia_cores (
            id          INTEGER PRIMARY KEY,
            title       TEXT,
            description TEXT,
            rationale   TEXT,
            vision      TEXT,
            created_at  TEXT
        )
        """)

        # domain table (unchanged)
        conn.execute("""
        CREATE TABLE IF NOT EXISTS fantasia_domain (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            core_id     INTEGER NOT NULL,
            name        TEXT,
            description TEXT,
            group_title TEXT,
            provider    TEXT,
            created_at  TEXT,
            UNIQUE(core_id, name)
        )
        """)

        # dimension table (original create, without targets)
        conn.execute("""
        CREATE TABLE IF NOT EXISTS fantasia_dimension (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            domain_id   INTEGER NOT NULL,
            name        TEXT,
            description TEXT,
            targets     TEXT,
            provider    TEXT,
            created_at  TEXT,
            UNIQUE(domain_id, name)
        )
        """)

        # thesis table (unchanged)
        conn.execute("""
        CREATE TABLE IF NOT EXISTS fantasia_thesis (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            dimension_id  INTEGER NOT NULL,
            text          TEXT,
            author_email  TEXT,
            provider      TEXT,
            created_at    TEXT
        )
        """)

        conn.execute("""
        CREATE TABLE IF NOT EXISTS fantasia_world (
            id INTEGER PRIMARY KEY,
            core_id INTEGER,
            domain_id INTEGER,
            dimension_id INTEGER,
            thesis_id INTEGER,
            model TEXT,
            created_at TEXT,
            world_spec TEXT
        )
        """)

        conn.execute("CREATE INDEX IF NOT EXISTS idx_world_core ON fantasia_world(core_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_world_dim ON fantasia_world(dimension_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_world_thesis ON fantasia_world(thesis_id)")


        # indexes
        conn.execute("CREATE INDEX IF NOT EXISTS idx_domain_core        ON fantasia_domain(core_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_dimension_domain   ON fantasia_dimension(domain_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_thesis_dimension   ON fantasia_thesis(dimension_id)")

        # ✅ NEW: ensure `targets` column exists even if table was created before we added it
        cols = conn.execute("PRAGMA table_info(fantasia_dimension)").fetchall()
        colnames = {c["name"] for c in cols}
        if "targets" not in colnames:
            conn.execute("ALTER TABLE fantasia_dimension ADD COLUMN targets TEXT")

        conn.commit()
    finally:
        conn.close()



ensure_fantasia_db(Path(FANTASIA_DB_PATH))

def _maybe_start_worker_pool():
    global _fantasia_workers_started
    with _fantasia_worker_lock:
        if _fantasia_workers_started >= _WORKER_COUNT:
            return
        # spin up remaining workers
        while _fantasia_workers_started < _WORKER_COUNT:
            t = threading.Thread(
                target=_worker_thread_loop,
                name=f"fantasia-worker-{_fantasia_workers_started}",
                daemon=True,
            )
            t.start()
            _fantasia_workers_started += 1
        log.info("🌙 started %d fantasia workers", _fantasia_workers_started)

def _open_fc_conn():
    """
    Open fantasia_cores.db for WRITING.
    This is the only process that writes to this DB, so we don't expect writer collisions.
    We still set WAL + timeout to be safe for any read concurrency.
    """
    #ensure_fantasia_db(FANTASIA_DB_PATH)
    conn = sqlite3.connect(FANTASIA_DB_PATH, timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    return conn

def _open_jid_conn():
    """
    Open jid.db for usage logging / budgets.
    jid.db is still shared with the main app,
    so we give a generous timeout and WAL.
    """
    conn = sqlite3.connect(JID_DB_PATH, timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    return conn


def _openai_parse_guarded(*, model, sys_msg, user_msg, OutSchema, budget):
    """
    model: "gpt-5-mini-2025-08-07"
    sys_msg: system prompt string
    user_msg: user/content prompt string
    OutSchema: Pydantic model class describing expected output
    budget: _TokenBudget instance
    jid_conn: sqlite connection to jid.db (for usage logging)
    """

    # check caps before call
    budget.check_before()


    resp = _client.responses.parse(
        model=model,
        input=[
            {"role": "system", "content": sys_msg},
            {"role": "user",  "content": user_msg},
        ],
        text_format=OutSchema,
    )

    parsed = getattr(resp, "output_parsed", None) or getattr(resp, "parsed", None)
    raw_text = getattr(resp, "output_text", None) or getattr(resp, "text", None) or ""

    # fallback if SDK didn't auto-parse
    if parsed is None:
        m = re.search(r"```(?:json)?\s*(\{.*\})\s*```", raw_text, re.S)
        raw_json = m.group(1) if m else raw_text
        parsed = OutSchema.model_validate(json.loads(raw_json))

    # normalize to OutSchema
    if not isinstance(parsed, OutSchema):
        parsed = OutSchema.model_validate(parsed)

    # token accounting
    jid_conn = _open_jid_conn()
    try:
        usage = _usage_from_resp(resp)
        _record_llm_usage_by_model(jid_conn, model, usage)
        _record_llm_usage(jid_conn, usage)
        jid_conn.commit()  # commit any inserts we just did into fantasia_cores

        total_used = (
            usage.get("total_tokens")
            or (usage.get("input_tokens", 0) + usage.get("output_tokens", 0))
            or 0
        )
        budget.live_used += total_used

        # after increment, enforce caps again
        budget.check_before()

    except Exception as e:
        # don't kill the job if usage logging had a hiccup
        log.warning("⚠️ usage logging failed: %s", e)
    finally:
        jid_conn.close()

    return parsed


def _openai_text(*, model, sys_msg, user_msg, budget, jid_conn):
    """
    Light wrapper for text-only generations (no Pydantic parse).
    Still logs usage and enforces budget.
    Returns raw text string.
    """
    # budget check before call
    budget.check_before()

    resp = _client.responses.create(
        model=model,
        input=[
            {"role": "system", "content": sys_msg},
            {"role": "user",  "content": user_msg},
        ],
    )

    # extract text
    raw_text = getattr(resp, "output_text", None) or getattr(resp, "text", None) or ""

    # token accounting + commit
    try:
        usage = _usage_from_resp(resp)
        _record_llm_usage_by_model(jid_conn, model, usage)
        _record_llm_usage(jid_conn, usage)

        total_used = (
            usage.get("total_tokens")
            or (usage.get("input_tokens", 0) + usage.get("output_tokens", 0))
            or 0
        )
        budget.live_used += total_used

        budget.check_before()
    except Exception as e:
        log.warning("⚠️ usage logging failed (sim world): %s", e)

    return raw_text


def upsert_core_into_fantasia_db(core_id, title, description, rationale, vision, created_at=None):
    from pathlib import Path
    import sqlite3
    from datetime import datetime

    if created_at is None:
        created_at = datetime.utcnow().isoformat() + "Z"

    # open fantasia_cores.db
    conn = sqlite3.connect(FANTASIA_DB_PATH, timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")

    try:

        # insert OR replace the core row
        conn.execute("""
            INSERT INTO fantasia_cores (id, title, description, rationale, vision, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                title=excluded.title,
                description=excluded.description,
                rationale=excluded.rationale,
                vision=excluded.vision
        """, (core_id, title, description, rationale, vision, created_at))

        conn.commit()
    finally:
        conn.close()


def _ensure_core_present(fc_conn, jid_conn, core_id: int):
    # first try fantasia_cores.db
    row = fc_conn.execute("""
        SELECT id, title, description, rationale, vision, created_at
        FROM fantasia_cores
        WHERE id=?
    """, (core_id,)).fetchone()

    if row:
        return row  # done

    # not found in fantasia_cores.db → try to pull from jid.db
    jid_row = jid_conn.execute("""
        SELECT id, title, description, rationale, vision, created_at
        FROM fantasia_cores
        WHERE id=?
    """, (core_id,)).fetchone()


    if not jid_row:
        raise RuntimeError(f"core {core_id} not found in fantasia_cores.db or jid.db")

    now = jid_row["created_at"]
    if not now:
        from datetime import datetime
        now = datetime.utcnow().isoformat() + "Z"

    # insert into fantasia_cores.db
    fc_conn.execute("""
        INSERT INTO fantasia_cores (id, title, description, rationale, vision, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            title=excluded.title,
            description=excluded.description,
            rationale=excluded.rationale,
            vision=excluded.vision
    """, (
        jid_row["id"],
        jid_row["title"],
        jid_row["description"],
        jid_row["rationale"],
        jid_row["vision"],
        now,
    ))

    return fc_conn.execute("""
        SELECT id, title, description, rationale, vision, created_at
        FROM fantasia_cores
        WHERE id=?
    """, (core_id,)).fetchone()


def load_core_state(core_id: int):
    """
    Return (core_row, domain_states) just like _load_core_state used to,
    but open/close fc_conn and jid_conn inside.
    """
    fc_conn = _open_fc_conn()
    try:
        # short-lived jid_conn just to ensure hydration
        jid_conn = _open_jid_conn()
        try:
            core_row = _ensure_core_present(fc_conn, jid_conn, core_id)
            jid_conn.commit()
        finally:
            jid_conn.close()

        # pull domains, dims, theses (same logic you already have)
        dom_rows = fc_conn.execute("""
            SELECT id, name, description, group_title, provider, created_at
            FROM fantasia_domain
            WHERE core_id=?
            ORDER BY id ASC
        """, (core_id,)).fetchall()

        dom_ids = [int(r["id"]) for r in dom_rows]
        if dom_ids:
            qmarks = ",".join("?" for _ in dom_ids)
            dim_rows = fc_conn.execute(f"""
                SELECT id, domain_id, name, description, provider, created_at
                FROM fantasia_dimension
                WHERE domain_id IN ({qmarks})
                ORDER BY id ASC
            """, dom_ids).fetchall()
        else:
            dim_rows = []

        dim_ids = [int(r["id"]) for r in dim_rows]
        if dim_ids:
            qmarks = ",".join("?" for _ in dim_ids)
            th_rows = fc_conn.execute(f"""
                SELECT id, dimension_id, text, author_email, provider, created_at
                FROM fantasia_thesis
                WHERE dimension_id IN ({qmarks})
                ORDER BY id ASC
            """, dim_ids).fetchall()
        else:
            th_rows = []

        # organize
        dims_by_domain = {}
        for d in dim_rows:
            dims_by_domain.setdefault(int(d["domain_id"]), []).append(d)

        thesis_by_dim = {}
        for t in th_rows:
            thesis_by_dim.setdefault(int(t["dimension_id"]), []).append(t)

        domain_states = []
        for dom in dom_rows:
            d_id = int(dom["id"])
            dims = dims_by_domain.get(d_id, [])
            dom_state = {
                "domain_row": dom,
                "dimensions": [],
            }
            for dim in dims:
                dim_id = int(dim["id"])
                theses = thesis_by_dim.get(dim_id, [])
                dom_state["dimensions"].append({
                    "dimension_row": dim,
                    "theses": theses,
                })
            domain_states.append(dom_state)

        return core_row, domain_states
    finally:
        fc_conn.close()


def _insert_domain(fc_conn, core_id, name, description, group_title, provider, created_at):
    cur = fc_conn.execute("""
        INSERT INTO fantasia_domain (core_id, name, description, group_title, provider, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (core_id, name, description, group_title, provider, created_at))
    return cur.lastrowid

def _insert_dimension(fc_conn, domain_id, name, description, targets, provider, created_at):
    cur = fc_conn.execute("""
        INSERT INTO fantasia_dimension (domain_id, name, description, targets, provider, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (domain_id, name, description, targets, provider, created_at))
    return cur.lastrowid

def _insert_thesis(fc_conn, dimension_id, text, author_email, provider, created_at):
    cur = fc_conn.execute("""
        INSERT INTO fantasia_thesis (dimension_id, text, author_email, provider, created_at)
        VALUES (?, ?, ?, ?, ?)
    """, (dimension_id, text, author_email, provider, created_at))
    return cur.lastrowid

def insert_domains_for_core(core_id: int, domains_to_insert, provider_model: str, run_logger):
    """
    domains_to_insert = [
      {"group_title": gtitle, "name": dname, "description": ddesc},
      ...
    ]
    """
    now = datetime.utcnow().isoformat() + "Z"
    fc_conn = _open_fc_conn()
    try:
        created_ids = []
        for dom in domains_to_insert:
            cur_id = _insert_domain(
                fc_conn,
                core_id=core_id,
                name=dom["name"],
                description=dom["description"],
                group_title=dom["group_title"],
                provider=provider_model,
                created_at=now,
            )
            created_ids.append(cur_id)
            run_logger.ev("💾 domain.insert",
                          core_id=core_id,
                          domain_id=cur_id,
                          name_preview=dom["name"][:80])
        fc_conn.commit()
        return created_ids
    except Exception:
        fc_conn.rollback()
        raise
    finally:
        fc_conn.close()


def insert_dimensions_for_domain(domain_id: int, dims_to_insert, provider_model: str, run_logger, core_id: int):
    """
    dims_to_insert = [
      {"name": ..., "thesis": ..., "targets": [...]},
      ...
    ]
    """
    now = datetime.utcnow().isoformat() + "Z"
    fc_conn = _open_fc_conn()
    try:
        for dim in dims_to_insert:
            targets_str = json.dumps(dim["targets"])
            dim_id = _insert_dimension(
                fc_conn,
                domain_id=domain_id,
                name=dim["name"],
                description=dim["thesis"],
                targets=targets_str,
                provider=provider_model,
                created_at=now,
            )
            run_logger.ev("💾 dimension.insert",
                          core_id=core_id,
                          domain_id=domain_id,
                          dimension_id=dim_id,
                          name_preview=dim["name"][:80])
        fc_conn.commit()
    except Exception:
        fc_conn.rollback()
        raise
    finally:
        fc_conn.close()


def insert_thesis_for_dimension(dimension_id: int, thesis_text: str, provider_model: str, author_email: str|None, run_logger, core_id: int, domain_id: int):
    now = datetime.utcnow().isoformat() + "Z"
    fc_conn = _open_fc_conn()
    try:
        t_id = _insert_thesis(
            fc_conn,
            dimension_id=dimension_id,
            text=thesis_text,
            author_email=author_email,
            provider=provider_model,
            created_at=now,
        )
        run_logger.ev("💾 thesis.insert",
                      core_id=core_id,
                      domain_id=domain_id,
                      dimension_id=dimension_id,
                      thesis_id=t_id,
                      text_preview=thesis_text[:120])
        fc_conn.commit()
    except Exception:
        fc_conn.rollback()
        raise
    finally:
        fc_conn.close()


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

def build_domain_prompt(core_row):
    sys_msg = DOMAIN_ARCHITECT_SYS_MSG
    user_msg = render_prompt(
        DOMAIN_ARCHITECT_USER_TEMPLATE,
        fantasia_core=core_row["title"],
        fantasia_core_description=core_row["description"] or "",
    )
    return sys_msg, user_msg

def build_dimension_prompt(core_row, domain_name, domain_desc):
    sys_msg = DIM_SYS_MSG
    user_msg = render_prompt(
        DIM_USER_TEMPLATE,
        core_name=core_row["title"],
        core_description=core_row["description"],
        domain_name=domain_name,
        domain_description=domain_desc,
    )
    return sys_msg, user_msg

def build_thesis_prompt(core_row, domain_name, domain_desc, dimension_name, dimension_desc, dimension_targets):
    sys_msg = THESIS_SYS_MSG
    user_msg = render_prompt(
        THESIS_USER_TEMPLATE,
        core_name=core_row["title"],
        core_description=core_row["description"],
        domain_name=domain_name,
        domain_description=domain_desc,
        dimension_name=dimension_name,
        dimension_description=(dimension_desc or ""),
        dimension_targets=(dimension_targets or ""),
    )
    return sys_msg, user_msg

def build_sim_user_msg(core_row, domain_name, domain_desc, dimension_name, dimension_desc, dimension_targets, thesis):
    """
    Produce the user message according to your spec:
    Fantasia Core, Domain, Dimension, Thesis, Task...
    We'll pull data from the rows we already have in memory.
    """
    core_title = core_row["title"] or ""
    core_desc  = core_row["description"] or ""

    return f"""Fantasia Core:
{core_title}
{core_description}

Domain:
{domain_name}
{domain_desc}

Dimension:
{dimension_name}
{dimension_desc}
{dimension_targets}

Thesis:
{thesis}

Task:
Construct a structured simulation outline of a living world that embodies this thesis.
Populate all sections defined in the System Message above.
Design the world so its behavior can be observed, tuned, and verified by an external participant (the observer).
"""

def build_sim_prompt(core_row, domain_name, domain_desc, dimension_name, dimension_desc, dimension_targets, thesis):
    sys_msg = THESIS_SYS_MSG
    user_msg = build_sim_user_msg(core_row, domain_name, domain_desc, dimension_name, dimension_desc, dimension_targets, thesis)
    return sys_msg, user_msg

def _generate_domains_for_core(fc_conn, core_row, budget, model, force):
    """
    Returns list of newly inserted domain_ids.
    """
    core_id = int(core_row["id"])

    # If not forcing and we already have domains, skip
    existing = fc_conn.execute(
        "SELECT id FROM fantasia_domain WHERE core_id=? LIMIT 1", (core_id,)
    ).fetchone()
    if existing and not force:
        return []

    sys_msg, user_msg = build_domain_prompt(core_row)
    parsed = _openai_parse_guarded(
        model=model,
        sys_msg=sys_msg,
        user_msg=user_msg,
        OutSchema=PD_DomainArchitectOut,
        budget=budget,
    )
    # parsed should be something like {groups:[{group_title, domains:[{name,desc,provider}, ...]}]}

    created_ids = []
    now = datetime.utcnow().isoformat() + "Z"
    for group in parsed.groups:
        gtitle = group.title
        for d in group.domains:
            dom_id = _insert_domain(
                fc_conn,
                core_id=core_id,
                name=d.name,
                description=d.description,
                group_title=gtitle,
                provider=model,
                created_at=now,
            )
            created_ids.append(dom_id)
    return created_ids

def _save_world_spec(core_id, domain_id, dimension_id, thesis_id, model, world_spec, run_logger):
    now = datetime.utcnow().isoformat() + "Z"
    fc_conn = _open_fc_conn()
    try:
        fc_conn.execute("""
            INSERT INTO fantasia_world
            (core_id, domain_id, dimension_id, thesis_id, model, created_at, world_spec)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            core_id,
            domain_id,
            dimension_id,
            thesis_id,
            model,
            now,
            world_spec,
        ))
        fc_conn.commit()
    except Exception:
        fc_conn.rollback()
        raise
    finally:
        fc_conn.close()

    run_logger.ev("🌍 sim.world.stored",
                  core_id=core_id,
                  domain_id=domain_id,
                  dimension_id=dimension_id,
                  thesis_id=thesis_id,
                  bytes=len(world_spec))


def _generate_dimensions_and_theses(fc_conn, core_row, budget,
                                    model, force_dimensions, force_theses, email, R):
    core_id = int(core_row["id"])
    now = datetime.utcnow().isoformat() + "Z"

    # fetch all current domains for this core (after domains may have been added)
    dom_rows = fc_conn.execute("""
        SELECT id, name, description, group_title, provider, created_at
        FROM fantasia_domain
        WHERE core_id=?
        ORDER BY id ASC
    """, (core_id,)).fetchall()

    for dom in dom_rows:
        domain_id = int(dom["id"])
        domain_name = dom["name"] or ""
        domain_desc = dom["description"] or ""

        # 1. dimensions for this domain
        dims_existing = fc_conn.execute("""
            SELECT id, name, description, targets, provider, created_at
            FROM fantasia_dimension
            WHERE domain_id=?
        """, (domain_id,)).fetchall()

        gen_dimensions = False
        if not dims_existing:
            gen_dimensions = True
        elif force_dimensions:
            gen_dimensions = True

        if gen_dimensions:
            R.ev("🤖 llm.dimensions.begin", core_id=core_id, domain_id=domain_id, live_used=budget.live_used)
            sys_msg, user_msg = build_dimension_prompt(core_row, domain_name, domain_desc)

            dims_parsed = _openai_parse_guarded(
                model=model,
                sys_msg=sys_msg,
                user_msg=user_msg,
                OutSchema=PDDimensionsResponse,
                budget=budget,
            )
            

            # dims_parsed.dimensions: [{name, description, provider}, ...]
            for dim in dims_parsed.dimensions:
                targets_str = json.dumps(dim.targets)
                dim_id = _insert_dimension(
                    fc_conn,
                    domain_id=domain_id,
                    name=dim.name,
                    description=dim.thesis,
                    targets=targets_str,
                    provider=model,
                    created_at=now,
                )
                R.ev("💾 dimension.insert",
                     core_id=core_id,
                     domain_id=domain_id,
                     dimension_id=dim_id,
                     name_preview=dim.name[:80])

        # refresh dimensions after possible insert
        dim_rows = fc_conn.execute("""
            SELECT id, name, description, targets, provider, created_at
            FROM fantasia_dimension
            WHERE domain_id=?
        """, (domain_id,)).fetchall()

        # 2. thesis per dimension
        for dim in dim_rows:
            dimension_id = int(dim["id"])
            dim_name = dim["name"] or ""
            dim_desc = dim["description"] or ""
            dim_targets = dim["targets"] or ""

            thesis_existing = fc_conn.execute("""
                SELECT id FROM fantasia_thesis WHERE dimension_id=? LIMIT 1
            """, (dimension_id,)).fetchone()

            if thesis_existing and not force_theses:
                continue

            R.ev("🤖 llm.thesis.begin",
                 core_id=core_id,
                 domain_id=domain_id,
                 dimension_id=dimension_id,
                 model=model,
                 live_used=budget.live_used)

            sys_msg, user_msg = build_thesis_prompt(core_row, domain_name, domain_desc, dim_name, dim_desc, dim_targets)

            thesis_parsed = _openai_parse_guarded(
                model=model,
                sys_msg=sys_msg,
                user_msg=user_msg,
                OutSchema=PDThesis,
                budget=budget,
            )

            # thesis_parsed.text, thesis_parsed.provider, etc.
            t_id = _insert_thesis(
                fc_conn,
                dimension_id=dimension_id,
                text=thesis_parsed.thesis,
                author_email=email,
                provider=model,
                created_at=now,
            )
            R.ev("💾 thesis.insert",
                 core_id=core_id,
                 domain_id=domain_id,
                 dimension_id=dimension_id,
                 thesis_id=t_id,
                 text_preview=thesis_parsed.thesis[:120])

            # --- NEW: simulation world model generation ---
            sim_sys_msg, sim_user_msg = build_sim_prompt(core_row, domain_name, domain_desc, dim_name, dim_desc, dim_targets, thesis_parsed.thesis)
            
            # run second LLM call for the simulation spec
            sim_text = _openai_text(
                model=model,
                sys_msg=sim_sys_msg,
                user_msg=sim_user_msg,
                budget=budget,
            )

            # You now have sim_text (a long structured world spec).
            # Option A: just log it for now
            R.ev("🌍 sim.world.generated",
                core_id=core_id,
                domain_id=domain_id,
                dimension_id=dimension_id,
                thesis_id=t_id,
                bytes=len(sim_text))

            # Option B (recommended): persist it in DB
            # You'd first create a table once:
            #   CREATE TABLE IF NOT EXISTS fantasia_world (
            #       id INTEGER PRIMARY KEY,
            #       thesis_id INTEGER,
            #       core_id INTEGER,
            #       domain_id INTEGER,
            #       dimension_id INTEGER,
            #       model TEXT,
            #       created_at TEXT,
            #       world_spec TEXT
            #   );
            #
            # Then here:
            _save_world_spec(
                core_id=core_id,
                domain_id=domain_id,
                dimension_id=dimension_id,
                thesis_id=t_id,
                model=model,
                world_spec=sim_text,
                run_logger=R,
            )



def _get_random_core_ids_for_visions(
    visions: list[str],
    n: int,
) -> list[int]:
    """
    visions: list of vision TEXT values (strings, exactly as stored in fantasia_cores.vision in jid.db)
    n: number to randomly sample per vision

    Returns a flat list of unique core_ids (ints).
    """
    jid_conn = _open_jid_conn()
    try:
        jid_conn.row_factory = sqlite3.Row
        chosen_core_ids = set()

        for v_text in visions:
            if v_text is None:
                continue
            v_text_clean = v_text.strip()
            if v_text_clean == "":
                # we should still support "" because you DO allow empty vision in dashboard
                pass

            rows = jid_conn.execute(
                """
                SELECT id
                FROM fantasia_cores
                WHERE vision = ?
            """,
                (v_text_clean,),
            ).fetchall()

            all_ids = [int(r["id"]) for r in rows]
            if not all_ids:
                continue

            # sample up to n
            if len(all_ids) <= n:
                sample_ids = all_ids
            else:
                sample_ids = random.sample(all_ids, n)

            for cid in sample_ids:
                chosen_core_ids.add(cid)

        return list(chosen_core_ids)
    finally:
        jid_conn.close()


def _core_needs_structure(fc_conn, core_id: int) -> bool:
    """
    Return True if this core_id looks unstructured (no domains yet).
    Return False if at least one domain already exists.
    """
    row = fc_conn.execute(
        "SELECT id FROM fantasia_domain WHERE core_id=? LIMIT 1",
        (core_id,)
    ).fetchone()
    return (row is None)


def _process_job(job: dict):
    core_id         = int(job["core_id"])
    email           = job.get("email")
    model           = job.get("model", "gpt-5-mini-2025-08-07")
    force_domains   = bool(job.get("force_domains", False))
    force_dimensions= bool(job.get("force_dimensions", False))
    force_theses    = bool(job.get("force_theses", False))
    live_cap        = int(job.get("live_cap", 3_000_000))
    daily_cap       = int(job.get("daily_cap", 10_000_000))

    # jid_conn is only needed for token accounting, and we'll commit/close quickly after each LLM call
    # So: open it when calling LLM, not for the whole job.
    # Budget still needs to read jid.db snapshot first:
    budget = _TokenBudget(JID_DB_PATH, model, live_cap, daily_cap)

    run_id = short_hash(f"{core_id}|{time.time()}|{model}")
    R = RunLogger(run_id)
    R.ev("🟢 worker.begin", core_id=core_id, model=model, force_domains=force_domains,
         force_dimensions=force_dimensions, force_theses=force_theses,
         live_cap=live_cap, daily_cap=daily_cap)

    # 1. Snapshot current state
    core_row, domain_states = load_core_state(core_id)
    has_domains = len(domain_states) > 0

    R.ev("📜 domain.inventory",
         core_id=core_id,
         has_domains=has_domains,
         force_domains=force_domains,
         live_used=budget.live_used)

    # 2. Maybe generate domains (LLM, no DB hold)
    new_domain_defs = []  # list of {"group_title", "name", "description"}
    if force_domains or not has_domains:
        sys_msg, user_msg = build_domain_prompt(core_row)

        parsed = _openai_parse_guarded(
            model=model,
            sys_msg=sys_msg,
            user_msg=user_msg,
            OutSchema=PD_DomainArchitectOut,
            budget=budget,
        )

        # flatten parsed.groups into new_domain_defs
        for group in parsed.groups:
            gtitle = group.title
            for d in group.domains:
                new_domain_defs.append({
                    "group_title": gtitle,
                    "name": d.name,
                    "description": d.description,
                })

        # write them
        if new_domain_defs:
            created_ids = insert_domains_for_core(core_id, new_domain_defs, model, R)
            R.ev("✅ llm.domains.parsed",
                 core_id=core_id,
                 groups=len(created_ids),
                 live_used=budget.live_used)

        # refresh snapshot after insert so we know real domain IDs
        core_row, domain_states = load_core_state(core_id)

    # 3. For each domain, maybe generate dimensions and theses
    for dom_state in domain_states:
        dom_row = dom_state["domain_row"]
        domain_id = int(dom_row["id"])
        domain_name = dom_row["name"] or ""
        domain_desc = dom_row["description"] or ""

        dims_existing = dom_state["dimensions"]  # from snapshot

        # generate dimensions?
        need_dims = force_dimensions or (len(dims_existing) == 0)
        if need_dims:
            R.ev("🤖 llm.dimensions.begin",
                 core_id=core_id,
                 domain_id=domain_id,
                 live_used=budget.live_used)

            sys_msg, user_msg = build_dimension_prompt(core_row, domain_name, domain_desc)

            dims_parsed = _openai_parse_guarded(
                model=model,
                sys_msg=sys_msg,
                user_msg=user_msg,
                OutSchema=PDDimensionsResponse,
                budget=budget,
            )

            # insert dimensions
            dims_to_insert = []
            for dim in dims_parsed.dimensions:
                dims_to_insert.append({
                    "name": dim.name,
                    "thesis": dim.thesis,
                    "targets": dim.targets,
                })

            if dims_to_insert:
                insert_dimensions_for_domain(domain_id, dims_to_insert, model, R, core_id)

            # refresh snapshot for this domain (so we know dim ids for thesis gen)
            core_row2, domain_states2 = load_core_state(core_id)
            # find this same domain again
            dom_state = next(d for d in domain_states2
                             if int(d["domain_row"]["id"]) == domain_id)
            dims_existing = dom_state["dimensions"]

        # now handle theses per dimension
        for dim_state in dims_existing:
            dim_row = dim_state["dimension_row"]
            dimension_id = int(dim_row["id"])
            dim_name = dim_row["name"] or ""
            dim_desc = dim_row["description"] or ""
            dim_targets = dim_row["targets"] or ""

            has_thesis = len(dim_state["theses"]) > 0
            if has_thesis and not force_theses:
                continue

            R.ev("🤖 llm.thesis.begin",
                 core_id=core_id,
                 domain_id=domain_id,
                 dimension_id=dimension_id,
                 model=model,
                 live_used=budget.live_used)

            sys_msg, user_msg = build_thesis_prompt(
                core_row,
                domain_name,
                domain_desc,
                dim_name,
                dim_desc,
                dim_targets,
            )

            thesis_parsed = _openai_parse_guarded(
                model=model,
                sys_msg=sys_msg,
                user_msg=user_msg,
                OutSchema=PDThesis,
                budget=budget,
            )

            insert_thesis_for_dimension(
                dimension_id,
                thesis_parsed.thesis,
                model,
                email,
                R,
                core_id,
                domain_id,
            )

            sim_sys_msg, sim_user_msg = build_sim_prompt(core_row, domain_name, domain_desc, dim_name, dim_desc, dim_targets, thesis_parsed.thesis)
            
            # run second LLM call for the simulation spec
            sim_text = _openai_text(
                model=model,
                sys_msg=sim_sys_msg,
                user_msg=sim_user_msg,
                budget=budget,
            )

            # You now have sim_text (a long structured world spec).
            # Option A: just log it for now
            R.ev("🌍 sim.world.generated",
                core_id=core_id,
                domain_id=domain_id,
                dimension_id=dimension_id,
                thesis_id=t_id,
                bytes=len(sim_text))

            # Option B (recommended): persist it in DB
            # You'd first create a table once:
            #   CREATE TABLE IF NOT EXISTS fantasia_world (
            #       id INTEGER PRIMARY KEY,
            #       thesis_id INTEGER,
            #       core_id INTEGER,
            #       domain_id INTEGER,
            #       dimension_id INTEGER,
            #       model TEXT,
            #       created_at TEXT,
            #       world_spec TEXT
            #   );
            #
            # Then here:
            _save_world_spec(
                core_id=core_id,
                domain_id=domain_id,
                dimension_id=dimension_id,
                thesis_id=t_id,
                model=model,
                world_spec=sim_text,
                run_logger=R,
            )

    # usage snapshot (still needs jid.db, but read-only)
    usage_snapshot = _read_usage_snapshot(JID_DB_PATH)

    R.ev("📊 worker.usage.end",
         core_id=core_id,
         live_used=budget.live_used,
         by_model=usage_snapshot.get("by_model", {}))

    R.ev("🏁 worker.complete",
         core_id=core_id,
         live_used=budget.live_used)


@app.post("/enqueue-structure")
def enqueue_structure():
    """
    Accepts either:
    OLD STYLE:
    {
      "core_ids": [123,124,125],
      "email": "...",
      "model": "gpt-5-mini-2025-08-07",
      "force_domains": false,
      "force_dimensions": false,
      "force_theses": false,
      "force_core": false,
      "live_cap": 3000000,
      "daily_cap": 10000000
    }

    NEW STYLE:
    {
      "visions": ["acquiring a billion dollars", "learning to see love as resonant recognition"],
      "n": 5,
      "email": "...",
      "model": "gpt-5-mini-2025-08-07",
      "force_domains": false,
      "force_dimensions": false,
      "force_theses": false,
      "force_core": false,
      "live_cap": 3000000,
      "daily_cap": 10000000
    }

    Behavior:
    - For each vision string, randomly sample up to n fantasia_cores from jid.db (DB_PATH_DEFAULT).
    - If force_core==False, skip cores that already have at least one domain in fantasia_cores.db.
    - Enqueue the rest unless they're already queued.
    """

    data = request.get_json(silent=True) or {}

    force_core        = bool(data.get("force_core", False))
    force_domains     = bool(data.get("force_domains", False))
    force_dimensions  = bool(data.get("force_dimensions", False))
    force_theses      = bool(data.get("force_theses", False))
    email             = data.get("email")
    model             = data.get("model", "gpt-5-mini-2025-08-07")
    live_cap          = int(data.get("live_cap", 3_000_000))
    daily_cap         = int(data.get("daily_cap", 10_000_000))

    # ---- 1. Resolve which core_ids we want ----

    final_core_ids = set()

    # A) explicit core_ids (backward compatible)
    if isinstance(data.get("core_ids"), list):
        for cid in data["core_ids"]:
            try:
                final_core_ids.add(int(cid))
            except Exception:
                pass

    # B) visions + n
    if isinstance(data.get("visions"), list) and data["visions"]:
        # n must be >=1
        try:
            n = int(data.get("n", 0))
        except Exception:
            n = 0
        if n < 1:
            n = 1

        # Clean/normalize visions to strings
        vision_strings = []
        for v in data["visions"]:
            if v is None:
                continue
            vs = str(v).strip()
            # Important: your data model absolutely includes empty-string vision ("")
            # and may include None. We will include "" but skip None.
            vision_strings.append(vs)

        sampled_ids = _get_random_core_ids_for_visions(vision_strings, n)
        for cid in sampled_ids:
            final_core_ids.add(int(cid))

    final_core_ids = list(final_core_ids)

    if not final_core_ids:
        return jsonify({"ok": False, "error": "no_core_ids"}), 400

    # ---- 2. Filter by "already structured" unless force_core is True ----

    fc_conn = _open_fc_conn()
    try:
        core_ids_to_enqueue = []
        skipped_already_structured = []

        for cid in final_core_ids:
            if force_core:
                core_ids_to_enqueue.append(cid)
                continue

            if _core_needs_structure(fc_conn, cid):
                core_ids_to_enqueue.append(cid)
            else:
                skipped_already_structured.append(cid)
    finally:
        fc_conn.close()

    # ---- 3. Enqueue jobs the same way as before ----

    _maybe_start_worker_pool()

    enqueued = []
    skipped_already_queued = []

    job_opts = {
        "email": email,
        "model": model,
        "force_domains": force_domains,
        "force_dimensions": force_dimensions,
        "force_theses": force_theses,
        "live_cap": live_cap,
        "daily_cap": daily_cap,
    }

    for cid in core_ids_to_enqueue:
        if cid in _fantasia_job_seen:
            skipped_already_queued.append(cid)
            continue

        job = {
            "core_id": cid,
            **job_opts,
        }
        _fantasia_job_seen.add(cid)
        _fantasia_job_queue.put(job)
        enqueued.append(cid)

    return jsonify({
        "ok": True,
        "enqueued": enqueued,
        "skipped_already_structured": skipped_already_structured,
        "skipped_already_queued": skipped_already_queued,
        "queue_length": _fantasia_job_queue.qsize(),
    }), 202


def _core_structure_status(conn, core_id: int) -> str:
    """
    Returns "complete" if:
      - the core has at least 1 domain
      - every domain has at least 1 dimension
      - every dimension has at least 1 thesis
    Otherwise "incomplete".
    """

    # any domains?
    dom_rows = conn.execute("""
        SELECT id FROM fantasia_domain
        WHERE core_id = ?
    """, (core_id,)).fetchall()
    if not dom_rows:
        return "incomplete"

    domain_ids = [int(r["id"]) for r in dom_rows]

    # map domain_id -> has_dimensions
    q_dims = conn.execute(f"""
        SELECT domain_id, COUNT(*) AS c
        FROM fantasia_dimension
        WHERE domain_id IN ({",".join(["?"]*len(domain_ids))})
        GROUP BY domain_id
    """, domain_ids).fetchall()
    dims_by_domain = {int(r["domain_id"]): int(r["c"]) for r in q_dims}

    # if any domain has 0 dimensions -> incomplete
    for did in domain_ids:
        if dims_by_domain.get(did, 0) == 0:
            return "incomplete"

    # now check each dimension has ≥1 thesis
    # we'll gather all dimensions for those domains
    dim_rows = conn.execute(f"""
        SELECT id
        FROM fantasia_dimension
        WHERE domain_id IN ({",".join(["?"]*len(domain_ids))})
    """, domain_ids).fetchall()
    if not dim_rows:
        return "incomplete"

    dim_ids = [int(r["id"]) for r in dim_rows]

    q_th = conn.execute(f"""
        SELECT dimension_id, COUNT(*) AS c
        FROM fantasia_thesis
        WHERE dimension_id IN ({",".join(["?"]*len(dim_ids))})
        GROUP BY dimension_id
    """, dim_ids).fetchall()
    thesis_by_dim = {int(r["dimension_id"]): int(r["c"]) for r in q_th}

    # if any dimension has 0 theses -> incomplete
    for mid in dim_ids:
        if thesis_by_dim.get(mid, 0) == 0:
            return "incomplete"

    return "complete"


def delete_incomplete_cores(db_path):
    """
    Deletes all cores from fantasia_cores.db that do not have a complete structure
    (no domains, missing dimensions, or missing theses).
    Cleans up related entries in fantasia_domain, fantasia_dimension, fantasia_thesis,
    and fantasia_world if present.
    """
    import sqlite3

    conn = sqlite3.connect(db_path, timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = OFF;")  # ensure manual control
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")

    try:
        # Get all core IDs
        core_rows = conn.execute("SELECT id FROM fantasia_cores").fetchall()
        if not core_rows:
            print("No cores found.")
            return

        incomplete_ids = []

        for row in core_rows:
            cid = int(row["id"])
            status = _core_structure_status(conn, cid)
            if status != "complete":
                incomplete_ids.append(cid)

        if not incomplete_ids:
            print("All cores are complete. Nothing to delete.")
            return

        print(f"Deleting {len(incomplete_ids)} incomplete cores...")

        for cid in incomplete_ids:
            print(f"  - Deleting core {cid}")
            # Delete theses linked to this core
            conn.execute("""
                DELETE FROM fantasia_thesis
                WHERE dimension_id IN (
                    SELECT id FROM fantasia_dimension
                    WHERE domain_id IN (
                        SELECT id FROM fantasia_domain
                        WHERE core_id = ?
                    )
                )
            """, (cid,))
            # Delete dimensions
            conn.execute("""
                DELETE FROM fantasia_dimension
                WHERE domain_id IN (
                    SELECT id FROM fantasia_domain
                    WHERE core_id = ?
                )
            """, (cid,))
            # Delete domains
            conn.execute("DELETE FROM fantasia_domain WHERE core_id = ?", (cid,))
            # Delete world models (optional)
            try:
                conn.execute("DELETE FROM fantasia_world WHERE core_id = ?", (cid,))
            except sqlite3.OperationalError:
                pass  # Table may not exist
            # Finally delete the core
            conn.execute("DELETE FROM fantasia_cores WHERE id = ?", (cid,))

        conn.commit()
        print("✅ Incomplete cores deleted successfully.")

    except Exception as e:
        conn.rollback()
        print("❌ Error deleting incomplete cores:", e)
        raise
    finally:
        conn.close()

#delete_incomplete_cores("/var/www/site/data/fantasia_cores.db")


@app.get("/api/fantasia/worlds/by-vision")
def api_worlds_by_vision():
    """
    Returns cores for a given vision, plus structure_status, BUT
    restricted to cores that have at least one world model recorded.

    Query params:
      ?vision=<string>   (optional; if omitted treat as ALL)
    """
    vision = (request.args.get("vision") or None)
    # open fantasia_cores.db
    ensure_fantasia_db(FANTASIA_DB_PATH)
    conn = sqlite3.connect(FANTASIA_DB_PATH, timeout=30.0)
    conn.row_factory = sqlite3.Row
    try:
        # build base query
        params = []
        where_clauses = []

        # join fantasia_world so we only include cores that actually have worlds
        sql = """
        SELECT fc.id,
               fc.title,
               fc.description,
               fc.rationale,
               fc.vision,
               fc.created_at
        FROM fantasia_cores fc
        JOIN fantasia_world fw
          ON fw.core_id = fc.id
        """

        if vision is not None:
            where_clauses.append(" (fc.vision = ?) ")
            params.append(vision)

        if where_clauses:
            sql += " WHERE " + " AND ".join(where_clauses)

        sql += """
        GROUP BY fc.id
        ORDER BY datetime(fc.created_at) DESC
        LIMIT 500
        """

        cores = []
        for row in conn.execute(sql, params).fetchall():
            cid = int(row["id"])
            status = _core_structure_status(conn, cid)
            cores.append({
                "id": cid,
                "title": row["title"],
                "description": row["description"],
                "rationale": row["rationale"],
                "vision": row["vision"],
                "created_at": row["created_at"],
                "structure_status": status,
            })

        return jsonify({"ok": True, "cores": cores})
    finally:
        conn.close()


@app.get("/api/fantasia/core/<int:core_id>/worlds")
def api_core_worlds(core_id: int):
    """
    Return full nested structure for a single core_id, including the generated
    world specs associated with theses.
    {
      ok: true,
      core: {
        id, title, description, rationale, vision, created_at, structure_status,
        domains_with_worlds: [
          {
            id, name, description, group_title, provider, created_at,
            dimensions_with_worlds: [
              {
                id, name, description, provider, created_at,
                theses_with_worlds: [
                  {
                    id, text, author_email, provider, created_at,
                    worlds: [
                      { id, model, created_at, world_spec }
                    ]
                  },
                  ...
                ]
              },
              ...
            ]
          },
          ...
        ]
      }
    }
    """

    ensure_fantasia_db(FANTASIA_DB_PATH)
    conn = sqlite3.connect(FANTASIA_DB_PATH, timeout=30.0)
    conn.row_factory = sqlite3.Row
    try:
        # core row
        c = conn.execute("""
            SELECT id, title, description, rationale, vision, created_at
            FROM fantasia_cores
            WHERE id = ?
        """, (core_id,)).fetchone()
        if not c:
            return jsonify({"ok": False, "error": "not_found"}), 404

        core_meta = {
            "id": int(c["id"]),
            "title": c["title"],
            "description": c["description"],
            "rationale": c["rationale"],
            "vision": c["vision"],
            "created_at": c["created_at"],
            "structure_status": _core_structure_status(conn, int(c["id"])),
        }

        # domains for this core
        dom_rows = conn.execute("""
            SELECT id, name, description, group_title, provider, created_at
            FROM fantasia_domain
            WHERE core_id=?
            ORDER BY id ASC
        """, (core_id,)).fetchall()

        domains_payload = []
        for dom in dom_rows:
            domain_id = int(dom["id"])

            # dimensions for this domain
            dim_rows = conn.execute("""
                SELECT id, name, description, provider, created_at
                FROM fantasia_dimension
                WHERE domain_id=?
                ORDER BY id ASC
            """, (domain_id,)).fetchall()

            dim_payload = []
            for dim in dim_rows:
                dimension_id = int(dim["id"])

                # theses for this dimension
                th_rows = conn.execute("""
                    SELECT id, text, author_email, provider, created_at
                    FROM fantasia_thesis
                    WHERE dimension_id=?
                    ORDER BY id ASC
                """, (dimension_id,)).fetchall()

                th_payload = []
                for th in th_rows:
                    thesis_id = int(th["id"])

                    # world specs attached to this thesis
                    world_rows = []
                    try:
                        world_rows = conn.execute("""
                            SELECT id, model, created_at, world_spec
                            FROM fantasia_world
                            WHERE thesis_id=?
                            ORDER BY id ASC
                        """, (thesis_id,)).fetchall()
                    except sqlite3.OperationalError:
                        # fantasia_world may not exist yet on older DBs
                        world_rows = []

                    th_payload.append({
                        "id": thesis_id,
                        "text": th["text"],
                        "author_email": th["author_email"],
                        "provider": th["provider"],
                        "created_at": th["created_at"],
                        "worlds": [
                            {
                                "id": int(w["id"]),
                                "model": w["model"],
                                "created_at": w["created_at"],
                                "world_spec": w["world_spec"],
                            }
                            for w in world_rows
                        ]
                    })

                dim_payload.append({
                    "id": dimension_id,
                    "name": dim["name"],
                    "description": dim["description"],
                    "provider": dim["provider"],
                    "created_at": dim["created_at"],
                    "theses_with_worlds": th_payload,
                })

            domains_payload.append({
                "id": domain_id,
                "name": dom["name"],
                "description": dom["description"],
                "group_title": dom["group_title"],
                "provider": dom["provider"],
                "created_at": dom["created_at"],
                "dimensions_with_worlds": dim_payload,
            })

        core_meta["domains_with_worlds"] = domains_payload

        return jsonify({"ok": True, "core": core_meta})
    finally:
        conn.close()
