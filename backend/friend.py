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
)

import threading
import queue
import time

# import shared helpers from jid.py
from jid import (
    FANTASIA_DB_PATH,
    DB_PATH_DEFAULT as JID_DB_PATH,
    ensure_fantasia_db,
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

log = logging.getLogger("jid_friend")
logging.basicConfig(level=logging.INFO)

app = Flask("jid-friend")

_fantasia_job_queue = queue.Queue()
_fantasia_job_seen  = set()          # core_ids currently queued or in progress
_fantasia_worker_started = False
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


def _maybe_start_worker():
    global _fantasia_worker_started
    with _fantasia_worker_lock:
        if _fantasia_worker_started:
            return
        t = threading.Thread(
            target=_fantasia_worker_loop,
            name="fantasia-worker-loop",
            daemon=True,
        )
        t.start()
        _fantasia_worker_started = True
        log.info("🌙 fantasia-worker loop started")


def _fantasia_worker_loop():
    while True:
        job = _fantasia_job_queue.get()  # blocking call
        core_id = job["core_id"]
        try:
            _process_job(job)
        except Exception as e:
            log.error("💥 worker error core_id=%s: %s", core_id, e, exc_info=True)
        finally:
            # allow this core to be scheduled again in future
            _fantasia_job_seen.discard(core_id)
            _fantasia_job_queue.task_done()


def _open_fc_conn():
    """
    Open fantasia_cores.db for WRITING.
    This is the only process that writes to this DB, so we don't expect writer collisions.
    We still set WAL + timeout to be safe for any read concurrency.
    """
    ensure_fantasia_db(FANTASIA_DB_PATH)
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


def _openai_parse_guarded(*, model, sys_msg, user_msg, OutSchema, budget, jid_conn):
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

        # after increment, enforce caps again
        budget.check_before()

    except Exception as e:
        # don't kill the job if usage logging had a hiccup
        log.warning("⚠️ usage logging failed: %s", e)

    return parsed


def _load_core_state(fc_conn, core_id: int):
    # core
    core_row = fc_conn.execute("""
        SELECT id, title, description, rationale, vision, created_at
        FROM fantasia_cores
        WHERE id=?
    """, (core_id,)).fetchone()

    if not core_row:
        # You could choose to create a stub core row here if missing,
        # but for now let's treat "no such core" as an error.
        raise RuntimeError(f"core {core_id} not found in fantasia_cores.db")

    # domains
    dom_rows = fc_conn.execute("""
        SELECT id, name, description, group_title, provider, created_at
        FROM fantasia_domain
        WHERE core_id=?
        ORDER BY id ASC
    """, (core_id,)).fetchall()

    # dimensions (for all domain ids)
    dom_ids = [int(r["id"]) for r in dom_rows]
    dim_rows = []
    if dom_ids:
        qmarks = ",".join("?" for _ in dom_ids)
        dim_rows = fc_conn.execute(f"""
            SELECT id, domain_id, name, description, provider, created_at
            FROM fantasia_dimension
            WHERE domain_id IN ({qmarks})
            ORDER BY id ASC
        """, dom_ids).fetchall()

    # theses (for all dimension ids)
    dim_ids = [int(r["id"]) for r in dim_rows]
    th_rows = []
    if dim_ids:
        qmarks = ",".join("?" for _ in dim_ids)
        th_rows = fc_conn.execute(f"""
            SELECT id, dimension_id, text, author_email, provider, created_at
            FROM fantasia_thesis
            WHERE dimension_id IN ({qmarks})
            ORDER BY id ASC
        """, dim_ids).fetchall()

    # Organize for convenience:
    dims_by_domain = {}
    for d in dim_rows:
        dims_by_domain.setdefault(int(d["domain_id"]), []).append(d)

    thesis_by_dim = {}
    for t in th_rows:
        thesis_by_dim.setdefault(int(t["dimension_id"]), []).append(t)

    # Boolean summary
    has_domains = len(dom_rows) > 0

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

    return {
        "core": core_row,
        "domains": domain_states,
        "has_domains": has_domains,
    }


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
    user_msg = user_msg = render_prompt(
        THESIS_USER_TEMPLATE,
        core_name=core_row["title"],
        core_description=core_row["description"],
        domain_name=domain_name,
        domain_description=domain_desc,
        dimension_name=dimension_name,
        dimension_description=(dimension_desc or ""),
        dimension_targets=(row["dimension_targets"] or ""),
    )
    return sys_msg, user_msg

def _generate_domains_for_core(fc_conn, jid_conn, core_row, budget, model, force):
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
        OutSchema=PD_DomainGroup,
        budget=budget,
        jid_conn=jid_conn,
    )
    # parsed should be something like {groups:[{group_title, domains:[{name,desc,provider}, ...]}]}

    created_ids = []
    now = datetime.utcnow().isoformat() + "Z"
    for group in parsed.groups:
        gtitle = group.group_title
        for d in group.domains:
            dom_id = _insert_domain(
                fc_conn,
                core_id=core_id,
                name=d.name,
                description=d.description,
                group_title=gtitle,
                provider=d.provider or model,
                created_at=now,
            )
            created_ids.append(dom_id)
    return created_ids



def _generate_dimensions_and_theses(fc_conn, jid_conn, core_row, budget,
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
                jid_conn=jid_conn,
            )

            # dims_parsed.dimensions: [{name, description, provider}, ...]
            for dim in dims_parsed.dimensions:
                dim_id = _insert_dimension(
                    fc_conn,
                    domain_id=domain_id,
                    name=dim.name,
                    description=dim.description,
                    targets=dim.targets,
                    provider=dim.provider or model,
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
                jid_conn=jid_conn,
            )

            # thesis_parsed.text, thesis_parsed.provider, etc.
            t_id = _insert_thesis(
                fc_conn,
                dimension_id=dimension_id,
                text=thesis_parsed.text,
                author_email=email,
                provider=thesis_parsed.provider or model,
                created_at=now,
            )
            R.ev("💾 thesis.insert",
                 core_id=core_id,
                 domain_id=domain_id,
                 dimension_id=dimension_id,
                 thesis_id=t_id,
                 text_preview=thesis_parsed.text[:120])


def _process_job(job: dict):
    """
    job fields:
      core_id: int
      email: str|None
      model: str
      force_domains: bool
      force_dimensions: bool
      force_theses: bool
      live_cap: int
      daily_cap: int
    """

    core_id         = int(job["core_id"])
    email           = job.get("email")
    model           = job.get("model", "gpt-5-mini-2025-08-07")
    force_domains   = bool(job.get("force_domains", False))
    force_dimensions= bool(job.get("force_dimensions", False))
    force_theses    = bool(job.get("force_theses", False))
    live_cap        = int(job.get("live_cap", 3_000_000))
    daily_cap       = int(job.get("daily_cap", 10_000_000))

    # open DBs
    fc_conn  = _open_fc_conn()
    jid_conn = _open_jid_conn()

    # build budget from jid.db (global usage tables)
    budget = _TokenBudget(JID_DB_PATH, model, live_cap, daily_cap)

    run_id = short_hash(f"{core_id}|{time.time()}|{model}")
    R = RunLogger(run_id)
    R.ev("🟢 worker.begin",
         core_id=core_id,
         model=model,
         force_domains=force_domains,
         force_dimensions=force_dimensions,
         force_theses=force_theses,
         live_cap=live_cap,
         daily_cap=daily_cap)

    try:
        # load snapshot of this core
        core_state = _load_core_state(fc_conn, core_id)
        core_row = core_state["core"]  # sqlite Row for fantasia_cores

        # generate domains (if missing / forced)
        R.ev("📜 domain.inventory",
             core_id=core_id,
             has_domains=core_state["has_domains"],
             force_domains=force_domains,
             live_used=budget.live_used)

        new_domains = _generate_domains_for_core(
            fc_conn,
            jid_conn,
            core_row,
            budget,
            model,
            force_domains
        )

        R.ev("✅ llm.domains.parsed",
             core_id=core_id,
             groups=len(new_domains or []),
             live_used=budget.live_used)

        # generate dimensions + theses
        _generate_dimensions_and_theses(
            fc_conn,
            jid_conn,
            core_row,
            budget,
            model,
            force_dimensions,
            force_theses,
            email,
            R
        )

        # snapshot usage
        usage_snapshot = _read_usage_snapshot(JID_DB_PATH)

        R.ev("📊 worker.usage.end",
             core_id=core_id,
             live_used=budget.live_used,
             by_model=usage_snapshot.get("by_model", {}))

        fc_conn.commit()
        jid_conn.commit()

        R.ev("🏁 worker.complete",
             core_id=core_id,
             live_used=budget.live_used)

    except Exception as e:
        fc_conn.rollback()
        jid_conn.rollback()
        R.ev("💥 worker.error",
             core_id=core_id,
             error_type=type(e).__name__,
             error=str(e))
        log.error("worker error core_id=%s: %s", core_id, e, exc_info=True)
    finally:
        fc_conn.close()
        jid_conn.close()
