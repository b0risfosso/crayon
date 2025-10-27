#!/usr/bin/env python3
# jid.py — Flask app to chunk PDFs/TXT, run LLM with Pydantic schema, and persist results.

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


# --- Pydantic (v2 preferred; v1 shim) ---
try:
    from pydantic import BaseModel, Field, ValidationError
except Exception:  # pragma: no cover
    from pydantic.v1 import BaseModel, Field, ValidationError  # type: ignore

# --- OpenAI client (expects env OPENAI_API_KEY) ---
try:
    from openai import OpenAI
    _OPENAI_STYLE = "new"
    _client = OpenAI()
except Exception as _e:  # pragma: no cover
    # If you need legacy fallback, wire it yourself. This app assumes new SDK.
    raise RuntimeError("OpenAI SDK with `responses.parse` is required.") from _e

# --- Config defaults ---
DEFAULT_SOURCE = "/var/www/site/data/source"
DEFAULT_OUT = "/var/www/site/data/jid_out"
DEFAULT_CHUNK_CHARS = 2000
DEFAULT_MODEL = "gpt-5-mini-2025-08-07"
SUPPORTED_TXT_EXT = {".txt", ".md", ".rst", ".log"}
SUPPORTED_PDF_EXT = {".pdf"}
SUPPORTED_EPUB_EXT = {".epub"}
DB_PATH_DEFAULT = "/var/www/site/data/jid.db"

 
# ---- Token budget controls (defaults; can be overridden per /run) ----
SWITCH_FROM_MODEL_DEFAULT = DEFAULT_MODEL
FALLBACK_MODEL_DEFAULT = "gpt-5-mini-2025-08-07"
SWITCH_MODEL_LIMIT_DEFAULT = 10_000_000   # if today's tokens for default model > this, switch model
STOP_RUN_LIMIT_DEFAULT    = 1_000_000     # if today's TOTAL tokens > this, abort run

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("jid")

SYSTEM_MSG = (
    "You are an expert creative-technical ideation model that produces structured, "
    "actionable 'fantasia cores' (ideas, visions, aspirations, imaginations, emotions, experiences, achievements) that open new doors for builders."
)



USER_PROMPT_TEMPLATE = """Task: Read the following excerpt carefully. Then, generate 4–5 core fantasia inspired by it. Each fantasia should orbit around something that someone [creating] {VISION} would be driven to create, build, experience, achieve, or understand — not what is already well-understood or complete. 

Fantasia should spring from curiosity, possibility, or emotional resonance rather than simple description. Fantasia may emerge from any domain that stirs someone [creating] {VISION}'s imagination — science, engineering, philosophy, emotion, science fiction, fantasy, art, or other frontiers of thought. 

For each fantasia, include:

Title: A clear, descriptive title (not cryptic or abstract).  
Description (2–3 sentences): Explain the fantasia — what it explores, builds, or reveals.  
Rationale: A short rationale for why this fantasia would captivate someone [creating] {VISION}'s curiosity, creativity, or purpose.  

Goal: Produce ideas that open new doors — that make someone [creating] {VISION} feel that something meaningful, beautiful, or powerful could be created from the seed of this excerpt.  

Push the limits of what is possible, known, and understood.

<------------- EXCERPT START ---------------------->
{EXCERPT}
<------------- EXCERPT END ---------------------->
"""

# ---------- Pydantic schemas for LLM output ----------

class FantasiaCore(BaseModel):
    title: str = Field(..., description="Concise, descriptive title.")
    description: str = Field(..., description="2–3 sentence explanation of the fantasia.")
    rationale: str = Field(..., description="Why people would care or be drawn to this.")

class FantasiaBatch(BaseModel):
    items: List[FantasiaCore] = Field(
        ..., min_items=6, max_items=8,
        description="6–8 fantasia cores.")
    excerpt_hash: str = Field(..., description="Short hash of input chunk for traceability.")

class Topic(BaseModel):
    topic: str = Field(..., description="topic")
    description: str = Field(..., description="description of topic")

class ListOfTopics(BaseModel):
    items: List[Topic] = Field(
        ..., min_items=8, max_items=12,
        description="8-12 topics")

class ExistingPick(BaseModel):
    writing_id: int
    reason: Optional[str] = None
    score: Optional[float] = Field(default=None, ge=0.0, le=1.0)

class ProposedTopic(BaseModel):
    topic: str
    description: str
    reason: Optional[str] = None
    score: Optional[float] = Field(default=None, ge=0.0, le=1.0)

class CuratedPlan(BaseModel):
    vision: str
    selected_existing: List[ExistingPick] = Field(default_factory=list)
    proposed_new: List[ProposedTopic] = Field(default_factory=list)
    rationale: Optional[str] = None

# ---- Parsed output models matching your crayon endpoints ----
class PD_DomainItem(BaseModel):
    name: str
    description: str

class PD_DomainGroup(BaseModel):
    title: str
    domains: list[PD_DomainItem]

class PD_DomainArchitectOut(BaseModel):
    groups: list[PD_DomainGroup]

class PD_DimensionItem(BaseModel):
    name: str
    description: str

class PD_DimensionOut(BaseModel):
    dimensions: list[PD_DimensionItem]

class PD_ThesisOut(BaseModel):
    thesis: str

# ---- Token budget guard ----
class _TokenBudget:
    def __init__(self, db_path: Path, model: str, live_cap: int, daily_cap: int):
        self.db_path = db_path
        self.model = model
        self.live_cap = live_cap
        self.daily_cap = daily_cap
        self.live_used = 0  # for this one endpoint call

    def check_before(self) -> None:
        today = _today_for_model(self.db_path, self.model)
        # Check daily projected (we only know live_used so far; this prevents new calls when we're already near the limit)
        projected_total = int(today.get("total") or 0) + self.live_used
        if projected_total >= self.daily_cap:
            raise RuntimeError(f"Daily token cap reached for {self.model}: {projected_total} >= {self.daily_cap}")
        if self.live_used >= self.live_cap:
            raise RuntimeError(f"Per-request live token cap reached: {self.live_used} >= {self.live_cap}")

    def add_usage(self, conn: sqlite3.Connection, usage: dict) -> None:
        # usage = {"input": int, "output": int, "total": int}
        tot = int(usage.get("total") or (int(usage.get("input") or 0) + int(usage.get("output") or 0)))
        self.live_used += tot
        _record_llm_usage(conn, usage)
        _record_llm_usage_by_model(conn, self.model, usage)
        # Post-check to ensure we didn't overshoot live cap
        if self.live_used > self.live_cap:
            raise RuntimeError(f"Per-request live token cap exceeded post-call: {self.live_used} > {self.live_cap}")
        # Post-check daily
        today = _today_for_model(self.db_path, self.model)
        if int(today.get("total") or 0) > self.daily_cap:
            raise RuntimeError(f"Daily token cap exceeded post-call for {self.model}")


# ---------- Text extraction & chunking ----------

MIN_CHARS_PDF = 100     # total extracted chars across sampled/full text must exceed
MIN_CHARS_EPUB = 100

def read_text_file(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="ignore")

def read_pdf_pypdf(path: Path) -> Optional[str]:
    try:
        import pypdf
        reader = pypdf.PdfReader(str(path), strict=False)  # be permissive
        pages = []
        for p in reader.pages:
            try:
                pages.append(p.extract_text() or "")
            except Exception:
                # if one page fails, keep going; we'll still try pdftotext if result is too empty
                pages.append("")
        txt = "\n".join(pages)
        # if pypdf produced very little, trigger fallback
        if len(txt.strip()) < 50:
            return None
        return txt
    except Exception:
        return None


def read_pdf_pdftotext(path: Path) -> str:
    """
    Extract text from a PDF using the Poppler 'pdftotext' command-line tool.
    Returns the extracted text as a string, or an empty string on failure.
    Requires `pdftotext` (from poppler-utils) to be installed on the system.
    """
    try:
        # ensure Poppler is available
        if not shutil.which("pdftotext"):
            print("⚠️  pdftotext not found — skipping Poppler extraction.")
            return ""

        # Use a temp file to store the plain text
        with tempfile.NamedTemporaryFile(suffix=".txt", delete=False) as tmp_out:
            tmp_out_path = Path(tmp_out.name)

        # Run pdftotext quietly (-q), preserving layout (-layout)
        subprocess.run(
            ["pdftotext", "-layout", "-q", str(path), str(tmp_out_path)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )

        # Read the extracted text
        text = tmp_out_path.read_text(encoding="utf-8", errors="ignore")

        # Cleanup temporary file
        tmp_out_path.unlink(missing_ok=True)

        # Return stripped text
        return text.strip()
    except Exception as e:
        print(f"⚠️  pdftotext failed on {path}: {e}")
        return ""

def read_epub(path: Path, max_items: int = 10) -> str:
    """
    Extract text from an EPUB using ebooklib + BeautifulSoup (front-matter aware).
    Mirrors tester behavior; requires: ebooklib, beautifulsoup4.
    """
    try:
        from ebooklib import epub  # type: ignore
        from bs4 import BeautifulSoup  # type: ignore
    except Exception as e:
        log.error(f"EPUB deps missing for {path.name}: {e}")
        return ""
    try:
        book = epub.read_epub(str(path))
        # 9 == DOCUMENT
        items = [i for i in book.get_items() if getattr(i, 'get_type', lambda: None)() == 9]
        n = min(len(items), max_items)
        parts: List[str] = []
        for i in range(n):
            try:
                content = items[i].get_content()
                soup = BeautifulSoup(content, "html.parser")
                parts.append(soup.get_text(" ", strip=True))
            except Exception:
                parts.append("")
        return "\n".join(parts).strip()
    except Exception as e:
        log.error(f"EPUB parse failed for {path.name}: {e}")
        return ""

def ocr_pdf_if_needed(path: Path) -> str:
    """
    If a PDF has no extractable text, run OCR to produce a searchable version
    and re-extract text from it using Poppler's pdftotext.
    Requires `ocrmypdf` and `pdftotext` to be installed.
    Returns the OCR-extracted text as a string (empty string on failure).
    """
    try:
        # Verify required tools exist
        if not shutil.which("ocrmypdf"):
            print("⚠️  ocrmypdf not found — skipping OCR fallback.")
            return ""
        if not shutil.which("pdftotext"):
            print("⚠️  pdftotext not found — OCR fallback requires it.")
            return ""

        # Create temporary files
        with tempfile.TemporaryDirectory() as td:
            out_pdf = Path(td) / "ocr.pdf"
            out_txt = Path(td) / "ocr.txt"

            # Run OCR (quiet mode, skip if already searchable)
            subprocess.run(
                ["ocrmypdf", "--skip-text", "--quiet", str(path), str(out_pdf)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )

            # Extract text from OCR’d PDF
            subprocess.run(
                ["pdftotext", "-layout", "-q", str(out_pdf), str(out_txt)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )

            # Read text result
            if out_txt.exists():
                text = out_txt.read_text(encoding="utf-8", errors="ignore").strip()
                return text
            return ""

    except Exception as e:
        print(f"⚠️  OCR extraction failed on {path}: {e}")
        return ""

def read_pdf(path: Path) -> str:
    # Prefer pdftotext first (better accuracy and fewer errors)
    txt = read_pdf_pdftotext(path)
    if txt.strip():
        return txt

    # Fallback to pypdf if Poppler result is empty
    txt = read_pdf_pypdf(path)
    if txt.strip():
        return txt

    # Final fallback (optional OCR)
    return ocr_pdf_if_needed(path) or ""


def _apply_fantasia_structure_schema(db_path: Path) -> None:
    """
    Adds fantasia_domain, fantasia_dimension, fantasia_thesis (idempotent).
    Mirrors the structure you use in crayon so we can store outputs locally in jid.db.
    """
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL;")
        cur.execute("PRAGMA synchronous=NORMAL;")

        SCHEMA = [
            # domain
            """
            CREATE TABLE IF NOT EXISTS fantasia_domain (
              id INTEGER PRIMARY KEY,
              core_id INTEGER NOT NULL,
              name TEXT NOT NULL,
              description TEXT,
              provider TEXT,
              group_title TEXT,
              targets_json TEXT,
              created_at TEXT NOT NULL DEFAULT (datetime('now')),
              updated_at TEXT,
              FOREIGN KEY (core_id) REFERENCES fantasia_cores(id) ON DELETE CASCADE
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

            # thesis
            """
            CREATE TABLE IF NOT EXISTS fantasia_thesis (
              id INTEGER PRIMARY KEY,
              core_id INTEGER NOT NULL,
              domain_id INTEGER NOT NULL,
              dimension_id INTEGER NOT NULL,
              text TEXT NOT NULL,
              author_email TEXT,
              provider TEXT,
              created_at TEXT NOT NULL DEFAULT (datetime('now')),
              updated_at TEXT,
              FOREIGN KEY (core_id)     REFERENCES fantasia_cores(id)      ON DELETE CASCADE,
              FOREIGN KEY (domain_id)   REFERENCES fantasia_domain(id)     ON DELETE CASCADE,
              FOREIGN KEY (dimension_id)REFERENCES fantasia_dimension(id)  ON DELETE CASCADE
            );
            """,
            "CREATE INDEX IF NOT EXISTS idx_thesis_core ON fantasia_thesis(core_id);",
            "CREATE INDEX IF NOT EXISTS idx_thesis_domain ON fantasia_thesis(domain_id);",
            "CREATE INDEX IF NOT EXISTS idx_thesis_dimension ON fantasia_thesis(dimension_id);",
            "CREATE INDEX IF NOT EXISTS idx_thesis_created_at ON fantasia_thesis(created_at);",
        ]

        for stmt in SCHEMA:
            cur.execute(stmt)
        conn.commit()



def normalize_ws(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t\f\v]+", " ", text)
    text = re.sub(r"\n\s+\n", "\n\n", text)
    return text.strip()

def chunk_text(text: str, chunk_size: int, *, fast: bool = False) -> List[Tuple[int, int, str]]:
    """
    Return list of (start_idx, end_idx, chunk_text) near chunk_size chars,
    preferring sentence/space boundaries close to the edge.
    """
    if not text:
        return []
        if fast:
            # Minimal processing to avoid pathological regex cost
            text = text.replace("\r\n", "\n").replace("\r", "\n").strip()
            n = len(text)
            chunks = []
            i = 0
            while i < n:
                j = min(i + chunk_size, n)
                seg = text[i:j]
                if seg:
                    chunks.append((i, j, seg))
                i = j
            return chunks
    # Default path with nicer boundaries
    text = normalize_ws(text)
    chunks = []
    i, n = 0, len(text)
    while i < n:
        j = min(i + chunk_size, n)
        window = text[i:j]
        boundary = max(window.rfind(". "), window.rfind("? "), window.rfind("! "), window.rfind("\n"))
        if boundary != -1 and j - (i + boundary + 1) < 300:
            j = i + boundary + 1
        else:
            space = window.rfind(" ")
            if space != -1 and j - (i + space) < 200:
                j = i + space
        seg = text[i:j].strip()
        if seg:
            chunks.append((i, j, seg))
        i = j
    return chunks

def short_hash(s: str) -> str:
    import hashlib
    return hashlib.sha1(s.encode("utf-8")).hexdigest()[:12]

# ---------- fingerprinting ------------------
# --- NEW: quick stat-only fingerprint (no file read) ---
def stat_fingerprint(p: Path) -> tuple[int, int]:
    st = p.stat()
    return int(st.st_size), int(st.st_mtime_ns)

# ---------- LLM call (structured) ----------

def _extract_json_text_from_responses(resp: Any) -> str:
    # Preferred: built-in convenience
    text = getattr(resp, "output_text", None)
    if isinstance(text, str) and text.strip():
        return text

    # Fallback: walk outputs to find a message text
    try:
        outs = getattr(resp, "output", None) or resp.get("output", [])
        for part in outs:
            if part.get("type") == "message":
                content = part.get("content") or []
                for c in content:
                    t = c.get("text")
                    if t and t.strip():
                        return t
    except Exception:
        pass

    # Last resort: stringify entire object
    return str(resp)


def run_llm_on_chunk(doc: str, vision: str, *, model: str, db_path: Path) -> Tuple[FantasiaBatch, str]:
    """
    Calls the Responses API with text_format=FantasiaBatch so the SDK parses directly into our schema.
    Returns (parsed_model, raw_text_for_debug).
    """

    user_msg = USER_PROMPT_TEMPLATE.format(EXCERPT=doc, VISION=vision)
    schema = FantasiaBatch  # Pydantic model as text_format schema

    resp = _client.responses.parse(  # type: ignore[attr-defined]
        model=model,
        input=[
            {"role": "system", "content": SYSTEM_MSG},
            {"role": "user", "content": user_msg},
        ],
        text_format=FantasiaBatch,
    )

    parsed = getattr(resp, "output_parsed", None) or getattr(resp, "parsed", None)
    raw_text = getattr(resp, "output_text", None) or getattr(resp, "text", None) or ""

    if parsed is None:
        # Fallback: parse the raw text as JSON (strip code fences if present)
        m = re.search(r"```(?:json)?\s*(\{.*\})\s*```", raw_text, re.S)
        raw_json = m.group(1) if m else raw_text
        parsed = FantasiaBatch.model_validate(json.loads(raw_json))

    # Ensure we really have the right type (SDKs sometimes return dicts)
    if not isinstance(parsed, FantasiaBatch):
        parsed = FantasiaBatch.model_validate(parsed)

    # Optional: attach a short content hash if your caller wants it
    excerpt_hash = hashlib.sha1(doc.encode("utf-8")).hexdigest()[:12]
    # if your downstream expects parsed.excerpt_hash, you can do:
    setattr(parsed, "excerpt_hash", excerpt_hash)

    # --- crayon-style token accounting ---
    try:
        if db_path is not None:
            with sqlite3.connect(db_path) as conn:
                usage = _usage_from_resp(resp)
                _record_llm_usage_by_model(conn, model, usage)
                _record_llm_usage(conn, usage)
    except Exception as e:
        # non-fatal; keep processing
        log.warning(f"failed to record llm usage: {e}")

    return parsed, raw_text



        
    # SDK returns parsed object mapped to Pydantic. If your SDK returns dict, validate explicitly:
    if isinstance(resp, FantasiaBatch):
        return resp
    # Fallback validation if SDK returns plain dict-like
    return FantasiaBatch.model_validate(resp)  # type: ignore

# ---------- I/O helpers ----------

def discover_files(root: Path) -> List[Path]:
    files: List[Path] = []
    for p in root.rglob("*"):
        if p.is_file() and p.suffix.lower() in (SUPPORTED_TXT_EXT | SUPPORTED_PDF_EXT | SUPPORTED_EPUB_EXT):
            files.append(p)
    return sorted(files)

def ensure_dir(p: Path) -> None:
    p.mkdir(parents=True, exist_ok=True)

def append_jsonl(path: Path, obj) -> None:
    ensure_dir(path.parent)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(obj, ensure_ascii=False) + "\n")

def write_json(path: Path, obj) -> None:
    ensure_dir(path.parent)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")

# --- token accounting (crayon-compatible) ---
def _init_llm_usage_table(conn: sqlite3.Connection) -> None:
    conn.execute("""
    CREATE TABLE IF NOT EXISTS llm_usage_counters (
        date TEXT PRIMARY KEY,                      -- 'YYYY-MM-DD' or 'ALL_TIME'
        input_tokens  INTEGER NOT NULL DEFAULT 0,
        output_tokens INTEGER NOT NULL DEFAULT 0,
        total_tokens  INTEGER NOT NULL DEFAULT 0
    );
    """)
    conn.execute("""
    INSERT OR IGNORE INTO llm_usage_counters(date, input_tokens, output_tokens, total_tokens)
    VALUES ('ALL_TIME', 0, 0, 0)
    """)
    conn.execute("""
    CREATE TABLE IF NOT EXISTS llm_usage_counters_by_model (
        date TEXT NOT NULL,                         -- 'YYYY-MM-DD' or 'ALL_TIME'
        model TEXT NOT NULL,                        -- e.g., 'gpt-5-mini-2025-08-07'
        input_tokens  INTEGER NOT NULL DEFAULT 0,
        output_tokens INTEGER NOT NULL DEFAULT 0,
        total_tokens  INTEGER NOT NULL DEFAULT 0,
        PRIMARY KEY (date, model)
    );
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_llm_usage_by_model_model ON llm_usage_counters_by_model(model)")
    conn.commit()

def _usage_from_resp(resp) -> dict:
    """
    Mirrors crayon.py: accept both {prompt,input}_tokens and {completion,output}_tokens.
    Returns {'input': int, 'output': int, 'total': int}
    """
    u = getattr(resp, "usage", None)
    get = (lambda k: (u.get(k) if isinstance(u, dict) else getattr(u, k, None)) if u else None)
    inp  = get("prompt_tokens")    or get("input_tokens")    or 0
    outp = get("completion_tokens") or get("output_tokens")  or 0
    tot  = get("total_tokens") or (int(inp) + int(outp))
    return {"input": int(inp), "output": int(outp), "total": int(tot)}


def _record_llm_usage_by_model(conn: sqlite3.Connection, model: str, usage: dict) -> None:
    """
    Per-model counters: increment today's row and ALL_TIME for this model.
    """
    inp, outp, tot = usage.get("input", 0), usage.get("output", 0), usage.get("total", 0)
    if not (inp or outp or tot):
        return
    today = datetime.utcnow().strftime("%Y-%m-%d")
    # Today/model
    conn.execute("""
        INSERT OR IGNORE INTO llm_usage_counters_by_model(date, model, input_tokens, output_tokens, total_tokens)
        VALUES (?, ?, 0, 0, 0)
    """, (today, model))
    conn.execute("""
        UPDATE llm_usage_counters_by_model
        SET input_tokens  = input_tokens  + ?,
            output_tokens = output_tokens + ?,
            total_tokens  = total_tokens  + ?
        WHERE date = ? AND model = ?
    """, (inp, outp, tot, today, model))
    # ALL_TIME/model
    conn.execute("""
        INSERT OR IGNORE INTO llm_usage_counters_by_model(date, model, input_tokens, output_tokens, total_tokens)
        VALUES ('ALL_TIME', ?, 0, 0, 0)
    """, (model,))
    conn.execute("""
        UPDATE llm_usage_counters_by_model
        SET input_tokens  = input_tokens  + ?,
            output_tokens = output_tokens + ?,
            total_tokens  = total_tokens  + ?
        WHERE date = 'ALL_TIME' AND model = ?
    """, (inp, outp, tot, model))
    conn.commit()


def _record_llm_usage(conn: sqlite3.Connection, usage: dict) -> None:
    """
    Identical behavior: bump today's row AND ALL_TIME row.
    """
    inp, outp, tot = usage.get("input", 0), usage.get("output", 0), usage.get("total", 0)
    if not (inp or outp or tot):
        return
    today = datetime.utcnow().strftime("%Y-%m-%d")
    conn.execute("INSERT OR IGNORE INTO llm_usage_counters(date) VALUES (?)", (today,))
    conn.execute("""
        UPDATE llm_usage_counters
        SET input_tokens  = input_tokens  + ?,
            output_tokens = output_tokens + ?,
            total_tokens  = total_tokens  + ?
        WHERE date = ?
    """, (inp, outp, tot, today))
    conn.execute("""
        UPDATE llm_usage_counters
        SET input_tokens  = input_tokens  + ?,
            output_tokens = output_tokens + ?,
            total_tokens  = total_tokens  + ?
        WHERE date = 'ALL_TIME'
    """, (inp, outp, tot))


def _read_usage_snapshot(db_path: Path) -> dict:
    """
    Snapshot of token usage.

    Returns:
    {
      "totals": { "today": {...}, "all_time": {...} },     # legacy aggregate (if table exists)
      "by_model": {
        "<modelA>": { "today": {...}, "all_time": {...} },
        "<modelB>": { "today": {...}, "all_time": {...} }
      }
    }
    """
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        today = datetime.utcnow().strftime("%Y-%m-%d")

        # --- Legacy totals (llm_usage_counters) ---
        def legacy_row(date_val: str) -> dict:
            try:
                cur.execute(
                    "SELECT input_tokens, output_tokens, total_tokens "
                    "FROM llm_usage_counters WHERE date = ?",
                    (date_val,),
                )
                r = cur.fetchone()
                return {"input": r["input_tokens"], "output": r["output_tokens"], "total": r["total_tokens"]} if r else {"input": 0, "output": 0, "total": 0}
            except sqlite3.OperationalError:
                # table might not exist yet; return zeros gracefully
                return {"input": 0, "output": 0, "total": 0}

        totals = {
            "today": legacy_row(today),
            "all_time": legacy_row("ALL_TIME"),
        }

        # --- Per-model (llm_usage_counters_by_model) ---
        def fetch_by_model(date_val: str) -> dict:
            try:
                cur.execute(
                    "SELECT model, input_tokens, output_tokens, total_tokens "
                    "FROM llm_usage_counters_by_model WHERE date = ?",
                    (date_val,),
                )
                rows = cur.fetchall()
            except sqlite3.OperationalError:
                # table might not exist yet
                rows = []

            out = {}
            for r in rows:
                out[r["model"]] = {
                    "input": r["input_tokens"],
                    "output": r["output_tokens"],
                    "total": r["total_tokens"],
                }
            return out

        today_by = fetch_by_model(today)
        all_by   = fetch_by_model("ALL_TIME")

        models = set(today_by.keys()) | set(all_by.keys())
        by_model = {
            m: {
                "today":   today_by.get(m, {"input": 0, "output": 0, "total": 0}),
                "all_time": all_by.get(m,   {"input": 0, "output": 0, "total": 0}),
            }
            for m in sorted(models)
        }

        return {"totals": totals, "by_model": by_model}



def _today_totals(db_path: Path) -> dict:
    """Return today's {'input','output','total'} (aggregate over all models)."""
    snap = _read_usage_snapshot(db_path)
    return snap.get("totals", {}).get("today", {"input": 0, "output": 0, "total": 0})

def _today_for_model(db_path: Path, model: str) -> dict:
    """Return today's per-model usage dict or zeros."""
    snap = _read_usage_snapshot(db_path)
    return snap.get("by_model", {}).get(model, {}).get("today", {"input": 0, "output": 0, "total": 0})


# ---------- JID + friends -----------
# --- SQLite helpers (add somewhere above Flask endpoints) ---

# --- UPDATED: ensure_db now also creates processed_files table ---
def ensure_db(db_path: Path) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path) as conn:
        cur = conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL;")
        cur.execute("PRAGMA synchronous=NORMAL;")
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS fantasia_cores (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                file_name TEXT NOT NULL,
                title TEXT NOT NULL,
                description TEXT NOT NULL,
                rationale TEXT NOT NULL,
                created_at TEXT NOT NULL,
                vision TEXT
            )
            """
        )
        try:
            cur.execute("ALTER TABLE fantasia_cores ADD COLUMN vision TEXT")
            _apply_fantasia_structure_schema(db_path)
        except Exception:
            pass  # column already exists
        cur.execute("CREATE INDEX IF NOT EXISTS idx_fantasia_cores_created_at ON fantasia_cores(created_at);")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_fantasia_cores_file_name ON fantasia_cores(file_name);")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_fantasia_cores_vision ON fantasia_cores(vision);")

        # Track which exact file state (by path + stat) was processed
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS processed_files (
                path TEXT NOT NULL,
                size_bytes INTEGER NOT NULL,
                mtime_ns INTEGER NOT NULL,
                run_id TEXT NOT NULL,
                processed_at TEXT NOT NULL,
                PRIMARY KEY (path, size_bytes, mtime_ns)
            )
            """
        )
        cur.execute("CREATE INDEX IF NOT EXISTS idx_processed_files_run ON processed_files(run_id);")

        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS writings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                topic TEXT NOT NULL,
                description TEXT,
                document TEXT NOT NULL,
                created_at TEXT NOT NULL,
                model TEXT,
                run_id TEXT
            )
            """
        )
        cur.execute("CREATE INDEX IF NOT EXISTS idx_writings_created_at ON writings(created_at);")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_writings_topic ON writings(topic);")

        # ✅ Track (vision, writing_id) completion to avoid repeats
        cur.execute("""
        CREATE TABLE IF NOT EXISTS writing_vision_done (
            writing_id INTEGER NOT NULL,
            vision     TEXT    NOT NULL,
            done_at    TEXT    NOT NULL,
            PRIMARY KEY (writing_id, vision)
        );
        """)
        cur.execute("CREATE INDEX IF NOT EXISTS idx_wvd_vision ON writing_vision_done(vision);")


        _init_llm_usage_table(conn)
        conn.commit()

def insert_writings_rows(
    db_path: Path,
    rows: list[tuple[str, Optional[str], str, str, Optional[str], Optional[str]]]
) -> None:
    """
    rows: List of tuples (topic, description, document, created_at, model, run_id)
    """
    if not rows:
        return
    with sqlite3.connect(db_path) as conn:
        cur = conn.cursor()
        cur.executemany(
            """
            INSERT INTO writings (topic, description, document, created_at, model, run_id)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
        conn.commit()

# --- NEW: check if the file with current stat is already processed ---
def file_already_processed(db_path: Path, path: Path) -> bool:
    size_bytes, mtime_ns = stat_fingerprint(path)
    with sqlite3.connect(db_path) as conn:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT 1 FROM processed_files
            WHERE path = ? AND size_bytes = ? AND mtime_ns = ?
            LIMIT 1
            """,
            (str(path), size_bytes, mtime_ns),
        )
        row = cur.fetchone()
        return row is not None


# --- NEW: record that we processed this file state ---
def mark_file_processed(db_path: Path, path: Path, run_id: str) -> None:
    size_bytes, mtime_ns = stat_fingerprint(path)
    with sqlite3.connect(db_path) as conn:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT OR IGNORE INTO processed_files
                (path, size_bytes, mtime_ns, run_id, processed_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (str(path), size_bytes, mtime_ns, run_id, datetime.utcnow().isoformat() + "Z"),
        )
        conn.commit()


def insert_fantasia_rows(db_path: Path, rows: list[tuple[str, str, str, str, str, Optional[str]]]) -> None:
    """
    rows: List of tuples (file_name, title, description, rationale, created_at, vision)
    """
    if not rows:
        return
    with sqlite3.connect(db_path) as conn:
        cur = conn.cursor()
        cur.executemany(
            """
            INSERT INTO fantasia_cores (file_name, title, description, rationale, created_at, vision)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
        conn.commit()


def mark_writing_vision_done(db_path: Path, writing_id: int, vision: str) -> None:
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "INSERT OR IGNORE INTO writing_vision_done (writing_id, vision, done_at) VALUES (?, ?, ?)",
            (writing_id, vision, datetime.utcnow().isoformat() + "Z"),
        )
        conn.commit()

def writing_vision_already_done(db_path: Path, writing_id: int, vision: str) -> bool:
    with sqlite3.connect(db_path) as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT 1 FROM writing_vision_done WHERE writing_id=? AND vision=? LIMIT 1",
            (writing_id, vision),
        )
        return cur.fetchone() is not None

def fetch_writings(db_path: Path, limit: int = 10000) -> list[tuple[int, str, str | None, str]]:
    """
    Returns [(id, topic, description, document)] newest-first.
    No filtering by model, date, or size.
    """
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute("""
            SELECT id, topic, description, document
            FROM writings
            ORDER BY datetime(created_at) DESC
            LIMIT ?
        """, (limit,))
        return [(r["id"], r["topic"], r["description"], r["document"]) for r in cur.fetchall()]


def _fetch_all_topics(conn: sqlite3.Connection, limit: int = 100000):
    """
    Returns list[(id, topic, description, document)] from writings table.
    """
    cur = conn.cursor()
    cur.execute(
        "SELECT id, topic, description, document FROM writings ORDER BY datetime(created_at) DESC LIMIT ?",
        (limit,),
    )
    return cur.fetchall()

def _select_topics_for_vision(
    vision: str,
    all_topics: List[tuple],
    model: str,
    total_limit: int,
    db_path: Path,
) -> CuratedPlan:
    """
    Calls mini model to curate existing topics and propose new topics.
    `all_topics` is list of tuples: (id, topic, description, document)
    """
    # compress payload for the model
    topics_compact = [
        {"id": wid, "topic": t, "description": (d or "")[:500]}
        for (wid, t, d, _doc) in all_topics
    ]

    system_msg = (
        "You are a curator selecting topics that best explore a given vision.\n"
        "Return JSON that matches the CuratedPlan schema. Prefer diversity and coverage.\n"
        "You may choose ANY number of existing topics (including zero). Propose new topics to fill blind spots.\n"
        f"The TOTAL count (len(selected_existing) + len(proposed_new)) MUST be <= {total_limit}.\n"
        "Include optional 'score' in [0,1] to indicate priority."
    )
    user_msg = (
        "VISION:\n"
        f"{vision}\n\n"
        "ALL_TOPICS (existing):\n"
        f"{json.dumps(topics_compact)[:120000]}\n\n"
        "INSTRUCTIONS:\n"
        "- Choose only as many existing topics as are truly relevant.\n"
        "- If gaps remain, propose new topics to reach the total limit.\n"
        "- Provide brief 'reason' fields to justify high-priority picks.\n"
        "- Output must be valid JSON for CuratedPlan."
    )

    # Use your parse path (same pattern as /write)
    resp = _client.responses.parse(  # type: ignore[attr-defined]
        model=model,  # e.g., gpt-5-mini-*
        input=[
            {"role": "system", "content": system_msg},
            {"role": "user", "content": user_msg},
        ],
        text_format=CuratedPlan,
    )

    try:
        usage = _usage_from_resp(resp)
        with sqlite3.connect(db_path) as conn:
            _record_llm_usage_by_model(conn, model, usage)  # per-model totals
            _record_llm_usage(conn, usage)                  # global totals
        log.debug("Curator usage recorded: %s", usage)
    except Exception as ue:
        log.warning("curator usage accounting failed: %s", ue)

    parsed = getattr(resp, "output_parsed", None) or getattr(resp, "parsed", None)
    if parsed is None:
        raw_text = getattr(resp, "output_text", None) or getattr(resp, "text", None) or ""
        m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw_text, re.S)
        raw_json = m.group(1) if m else raw_text
        parsed = CuratedPlan.model_validate(json.loads(raw_json))

    if not isinstance(parsed, CuratedPlan):
        parsed = CuratedPlan.model_validate(parsed)

    return parsed

def _commission_single_writing(topic: str, description: str, model_write: str, db_path: Path) -> tuple:
    """
    Creates one new writing doc via gpt-5 (or chosen writer model),
    persists to DB, returns (id, topic, description, document).
    """
    write_llm_input = """ROLE
        You are a subject-matter expert writing a rigorous, self-contained 2-page synthesis on {topic}.
        DELIVERABLE
        A cohesive document (~1000 words) with the following sections:
        A. Abstract (≤120 words)
        B. Background
        C. Core Analysis (2–4 subsections)
        D. Evidence Review (3–6 primary sources, discuss convergence/divergence)
        E. Counterarguments / Open Questions
        F. Implications
        G. References ([Author, Year] with working links or DOIs)
        REQUIREMENTS
        - Every factual claim must have a reputable source
        - Precise, formal language; logical flow
        - Include at least one figure description or quantitative comparison
        - Mark uncertainty explicitly


        Ensure high accuracy and critical thinking in your response.
        """
    write_instruct = write_llm_input.format(topic=f"{topic} — {description}")
    resp = _client.responses.create(
        model=model_write,
        #tools=[{"type": "web_search"}],
        input=[{"role": "user", "content": write_instruct}],
    )
    writing = getattr(resp, "output_text", None) or getattr(resp, "text", None) or "[No content returned]"
    created_at = datetime.utcnow().isoformat() + "Z"

    with sqlite3.connect(db_path) as conn:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO writings(topic, description, document, created_at, model, run_id)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (topic, description, writing, created_at, model_write, f"curate-{short_hash(topic+created_at)}"),
        )
        wid = cur.lastrowid

        # usage accounting (best-effort)
        try:
            usage = _usage_from_resp(resp)
            _record_llm_usage_by_model(conn, model_write, usage)
            _record_llm_usage(conn, usage)
        except Exception as ue:
            log.warning(f"usage accounting failed: {ue}")

        conn.commit()

        return (wid, topic, description, writing)

# --- Structured logging helper (keeps your emoji style) ---
def _log_event(event: str, **fields):
    """
    Emit a single-line structured log with compact JSON for fields.
    Use for searchable logs without losing your friendly emojis.
    """
    try:
        payload = {k: v for k, v in fields.items() if v is not None}
        log.info("%s %s", event, json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
    except Exception:
        log.info("%s %s", event, str(fields))

class RunLogger:
    """Logger adapter that tags every line with run_id (and optional vision/writing)."""
    def __init__(self, run_id: str):
        self.run_id = run_id
    def ev(self, event: str, **fields):
        _log_event(event, run_id=self.run_id, **fields)
    def ev_v(self, event: str, vision: str, **fields):
        _log_event(event, run_id=self.run_id, vision=vision, **fields)
    def ev_w(self, event: str, vision: str, writing_id: int, **fields):
        _log_event(event, run_id=self.run_id, vision=vision, writing_id=writing_id, **fields)


def _openai_parse_guarded(model: str, system_msg: str, user_msg: str, schema: type[BaseModel],
                          budget: _TokenBudget, conn: sqlite3.Connection):
    """
    Wrap _client.responses.parse with budget checks and usage accounting.
    """
    budget.check_before()
    try:
        resp = _client.responses.parse(  # type: ignore[attr-defined]
            model=model,
            input=[
                {"role": "system", "content": system_msg},
                {"role": "user", "content": user_msg},
            ],
            text_format=schema,
        )
    except Exception as e:
        raise RuntimeError(f"OpenAI parse failed: {e}")

    parsed = getattr(resp, "output_parsed", None) or getattr(resp, "parsed", None)
    if parsed is None:
        raise RuntimeError("OpenAI did not return parsed output")

    # record usage if present
    usage = {}
    try:
        u = getattr(resp, "usage", None) or {}
        # new SDK usage fields often: {"input_tokens":.., "output_tokens":.., "total_tokens":..}
        inp = int(u.get("input_tokens") or u.get("input") or 0)
        outp = int(u.get("output_tokens") or u.get("output") or 0)
        tot = int(u.get("total_tokens") or (inp + outp))
        usage = {"input": inp, "output": outp, "total": tot}
    except Exception:
        usage = {}

    budget.add_usage(conn, usage)
    return parsed


# ---- DB helpers for this endpoint ----
def _pick_random_core(conn: sqlite3.Connection) -> dict | None:
    row = conn.execute("""
        SELECT id, file_name, title, description, rationale, created_at, COALESCE(vision,'') AS vision
        FROM fantasia_cores
        ORDER BY RANDOM()
        LIMIT 1
    """).fetchone()
    return dict(row) if row else None

def _domain_count(conn, core_id: int) -> int:
    return conn.execute("SELECT COUNT(*) FROM fantasia_domain WHERE core_id=?", (core_id,)).fetchone()[0]

def _dimensions_for_domain(conn, domain_id: int) -> list[sqlite3.Row]:
    return conn.execute("SELECT * FROM fantasia_dimension WHERE domain_id=?", (domain_id,)).fetchall()

def _thesis_for_dimension(conn, dimension_id: int) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM fantasia_thesis WHERE dimension_id=? LIMIT 1", (dimension_id,)).fetchone()

def _insert_domain(conn, core_id: int, name: str, description: str, provider: str, group_title: str | None) -> int:
    cur = conn.execute("""
        INSERT INTO fantasia_domain(core_id, name, description, provider, group_title)
        VALUES (?, ?, ?, ?, ?)
    """, (core_id, name, description, provider, group_title))
    return int(cur.lastrowid)

def _insert_dimension(conn, domain_id: int, name: str, description: str, provider: str) -> int:
    cur = conn.execute("""
        INSERT INTO fantasia_dimension(domain_id, name, description, provider)
        VALUES (?, ?, ?, ?)
    """, (domain_id, name, description, provider))
    return int(cur.lastrowid)

def _insert_thesis(conn, core_id: int, domain_id: int, dimension_id: int, text: str, provider: str, author_email: str | None) -> int:
    cur = conn.execute("""
        INSERT INTO fantasia_thesis(core_id, domain_id, dimension_id, text, provider, author_email)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (core_id, domain_id, dimension_id, text, provider, author_email))
    return int(cur.lastrowid)


# ---------- Flask app ----------

app = Flask(__name__)

@app.get("/health")
def health():
    return jsonify({"ok": True, "time": datetime.utcnow().isoformat() + "Z"})


@app.post("/run")
def run_pipeline():
    """
    Curated writings-mode:
    - If selection_mode="curate": pass ALL topics+descriptions + the vision to mini LLM to choose n existing and propose (m-n) new.
    - Optionally commission new writings (gpt-5) for proposed topics, then generate fantasia cores for all selected (existing + new).
    - If selection_mode="random": preserve old behavior (random sample) for compatibility.
    """
    data = request.get_json(silent=True) or {}

    # --- Inputs / defaults ---
    db_path          = Path(data.get("db_path", DB_PATH_DEFAULT))
    out_dir          = Path(data.get("out", DEFAULT_OUT))
    model            = str(data.get("model", DEFAULT_MODEL))  # curator model (mini)
    dry_run          = bool(data.get("dry_run", False))
    force            = bool(data.get("force", False))
    mini_token_limit = int(data.get("mini_token_limit", SWITCH_MODEL_LIMIT_DEFAULT))
    selection_mode   = str(data.get("selection_mode", "curate")).lower()
    # total_limit is the only hard cap; LLM decides how many existing to keep
    total_limit      = int(data.get("total_limit", 12))  # cap on existing+new combined
    n_select_raw     = data.get("n_select", None)        # optional; if provided, behaves as before
    n_select         = int(n_select_raw) if n_select_raw is not None else None
    commission_new   = bool(data.get("commission_new", True))               # create writings for proposed_new?
    model_write      = data.get("model_write", "gpt-5-mini-2025-08-07")                     # writer model for new topics
    max_token_count  = int(data.get("max_token_count", 10_000_000))          # daily budget for writer
    max_writings     = int(data.get("max_writings", 0))                     # used only in random mode
    visions_input    = data.get("visions")

    # Normalize visions
    if isinstance(visions_input, str):
        visions = [v.strip() for v in visions_input.split(",") if v.strip()]
    elif isinstance(visions_input, list):
        visions = [str(v).strip() for v in visions_input if str(v).strip()]
    else:
        v_single = str(data.get("vision", "exploring the betterment of humanity")).strip()
        visions = [v_single] if v_single else []

    if not visions:
        return jsonify({"error": "No visions provided (use 'vision' or 'visions')."}), 400

    run_wall_start = time.perf_counter()

    _run_ctx = {
        "mode": selection_mode,
        "curator_model": model,
        "writer_model": model_write,
        "dry_run": dry_run,
        "force": force,
        "mini_limit": mini_token_limit,
        "writer_limit": max_token_count,
        "total_limit": total_limit,
        "n_select": n_select,
        "commission_new": commission_new,
        "max_writings_random": max_writings,
        "visions_count": len(visions),
    }

    # create run_id as you already do
    job_started = datetime.utcnow().isoformat() + "Z"
    run_id = short_hash(job_started + "|".join(visions) + model + selection_mode)
    R = RunLogger(run_id)

    R.ev("🟢 /run.start", **_run_ctx, visions=visions, db_path=str(db_path), out_dir=str(out_dir))


    ensure_db(db_path)
    ensure_dir(out_dir)

    job_started = datetime.utcnow().isoformat() + "Z"
    run_id = short_hash(job_started + "|".join(visions) + model + selection_mode)
    run_out_dir = out_dir / f"run_writings_{run_id}"
    ensure_dir(run_out_dir)

    results_jsonl = run_out_dir / f"fantasia_core_results.{run_id}.jsonl"
    errors_jsonl  = run_out_dir / f"errors.{run_id}.jsonl"

    R.ev("📁 /run.paths", run_out_dir=str(run_out_dir),
     results_jsonl=str(results_jsonl), errors_jsonl=str(errors_jsonl))


    # Hard-stop guard (mini curator)
    gptmini_today = _today_for_model(db_path, model)
    if gptmini_today.get("total", 0) >= mini_token_limit:
        stop_reason = f"mini_token_budget_exceeded ({gptmini_today.get('total')} >= {mini_token_limit})"
        usage_snapshot = _read_usage_snapshot(db_path)
        manifest = {
            "run_id": run_id,
            "job_started": job_started,
            "curator_model": model,
            "writer_model": model_write,
            "visions": visions,
            "writings_processed": 0,
            "writings_skipped": 0,
            "results_jsonl": str(results_jsonl),
            "errors_jsonl": str(errors_jsonl),
            "db_path": str(db_path),
            "usage": usage_snapshot,
            "stopped_early": True,
            "stop_reason": stop_reason,
            "per_vision": [],
            "per_item": [],
        }
        write_json(run_out_dir / f"_run_{run_id}.json", manifest)
        R.ev("⛔ /run.abort.mini_budget", today=_today_for_model(db_path, model), limit=mini_token_limit)
        return jsonify(manifest), 200

    # Load writings (ALL)
    with sqlite3.connect(db_path) as conn:
        #all_writings = _fetch_all_topics(conn, limit=10_000)  # [(id, topic, description, document)]
        all_writings = []
    R.ev("📚 writings.inventory", total=len(all_writings))

    processed_total = 0
    skipped_total   = 0
    skipped_details = []
    per_item_summary = []
    per_vision_stats = []
    stopped_early = False
    stop_reason   = None

    for vision in visions:
        vision_wall_start = time.perf_counter()
        R.ev_v("🎯 vision.begin", vision=vision)

        if stopped_early:
            break

        # Build the plan
        if selection_mode == "random":
            # legacy behavior
            if max_writings > 0 and len(all_writings) > max_writings:
                sampled = random.sample(all_writings, max_writings)
            else:
                sampled = all_writings
            selected_existing_ids = [wid for (wid, _t, _d, _doc) in sampled]
            proposed_new = []
            plan_rationale = "Random sample (legacy mode)."
            R.ev_v("🎲 vision.plan.random", vision=vision, selected_existing=len(sampled),
                max_writings=max_writings, rationale="Random sample (legacy mode)")


        else:
            # curated behavior
            # budget check before each curator call
            # curated behavior
            gptmini_today = _today_for_model(db_path, model)
            R.ev_v("🧮 budget.check.curator", vision=vision, today=gptmini_today, limit=mini_token_limit)
            if gptmini_today.get("total", 0) >= mini_token_limit:
                stopped_early = True
                stop_reason = f"mini_token_budget_reached ({gptmini_today.get('total')} >= {mini_token_limit})"
                log.warning("⛔ Stopping before curator for vision=%s: %s", vision, stop_reason)
                break

            try:
                plan = _select_topics_for_vision(vision, all_writings, model=model, total_limit=total_limit, db_path=db_path)
                R.ev_v("🧭 vision.plan.curated.raw", vision=vision,
                    selected_existing=len(plan.selected_existing),
                    proposed_new=len(plan.proposed_new),
                    rationale=plan.rationale)

            except Exception as e:
                append_jsonl(errors_jsonl, {"vision": vision, "error": "curation_failed", "details": str(e)})
                return jsonify({"error": f"curation failed for vision '{vision}': {e}"}), 500

            # If user explicitly provided n_select, honor it by trimming existing picks
            selected_existing_ids = [p.writing_id for p in plan.selected_existing]
            if n_select is not None:
                # sort by score desc if present, else keep order
                selected_existing_ids = sorted(
                    plan.selected_existing,
                    key=lambda p: (p.score if p.score is not None else 0.0),
                    reverse=True
                )
                selected_existing_ids = [p.writing_id for p in selected_existing_ids][:max(0, n_select)]

            # Enforce total_limit: fill with new proposals, trim excess if curator exceeded cap
            need_slots = max(0, total_limit - len(selected_existing_ids))

            proposed_new = plan.proposed_new
            # sort proposals by score desc if provided
            proposed_new_sorted = sorted(
                proposed_new,
                key=lambda x: (x.score if x.score is not None else 0.0),
                reverse=True
            )
            proposed_new = proposed_new_sorted[:need_slots]

            # If LLM selected more existing than total_limit (possible when n_select is None), trim
            if len(selected_existing_ids) > total_limit:
                selected_existing_ids = selected_existing_ids[:total_limit]
                proposed_new = []

            plan_rationale = plan.rationale

            R.ev_v("🧭 vision.plan.curated.enforced", vision=vision,
                selected_existing=len(selected_existing_ids),
                proposed_new=len(proposed_new),
                total_limit=total_limit,
                n_select=n_select)


        # Optionally commission proposed new writings (writer budget check)
        commissioned = []
        if proposed_new and commission_new and not dry_run:
            if proposed_new and commission_new and not dry_run:
                R.ev_v("✍️ commission.begin", vision=vision, proposed=len(proposed_new))
            gpt5_today = _today_for_model(db_path, model_write)
            if gpt5_today.get("total", 0) >= max_token_count:
                stop_reason = f"writer_daily_token_budget_exceeded ({gpt5_today.get('total')} >= {max_token_count})"
                log.warning("⛔ Skipping commissioning new writings: %s", stop_reason)
            else:
                for pt in proposed_new:
                    R.ev_v("🧮 budget.check.writer", vision=vision,
                        today=_today_for_model(db_path, model_write),
                        limit=max_token_count)
                    R.ev_v("✍️ commission.request", vision=vision, topic=pt.topic, description=pt.description[:160])
                    # recheck writer budget before each
                    gpt5_today = _today_for_model(db_path, model_write)
                    if gpt5_today.get("total", 0) >= max_token_count:
                        stop_reason = f"writer_token_budget_reached ({gpt5_today.get('total')} >= {max_token_count})"
                        log.warning("⛔ Stopping commissioning loop: %s", stop_reason)
                        break
                    try:
                        t0 = time.perf_counter()
                        row = _commission_single_writing(pt.topic, pt.description, model_write, db_path)
                        dt = time.perf_counter() - t0
                        commissioned.append(row)
                        R.ev_v("✅ commission.ok", vision=vision, writing_id=row[0], topic=row[1], ms=int(dt*1000))
                    except Exception as e:
                        R.ev_v("💥 commission.error", vision=vision, topic=pt.topic, error=str(e))
                        append_jsonl(errors_jsonl, {
                            "vision": vision, "topic": pt.topic, "error": "commission_failed", "details": str(e)
                        })
                R.ev_v("✍️ commission.end", vision=vision, commissioned=len(commissioned))


        # Collate the final set of writings for fantasia generation
        id_to_row = {wid: (wid, t, d, doc) for (wid, t, d, doc) in all_writings}
        selected_existing_rows = [id_to_row[i] for i in selected_existing_ids if i in id_to_row]
        final_rows = selected_existing_rows + commissioned  # [(id, topic, desc, doc)]

        # Generate fantasia cores for each selected writing
        v_processed = 0
        v_skipped   = 0

        for (wid, topic, desc, doc) in final_rows:
            if stopped_early:
                break

            if not force and writing_vision_already_done(db_path, wid, vision):
                v_skipped += 1
                skipped_total += 1
                skipped_details.append({"writing_id": wid, "topic": topic, "vision": vision, "reason": "already_done_for_vision"})
                R.ev_w("⤴️ fantasia.skip.already_done", vision=vision, writing_id=wid)
                continue

            # Budget check BEFORE each mini call
            gptmini_today = _today_for_model(db_path, model)
            if gptmini_today.get("total", 0) >= mini_token_limit:
                stopped_early = True
                stop_reason = f"mini_token_budget_reached ({gptmini_today.get('total')} >= {mini_token_limit})"
                log.warning("⛔ Stopping before writing_id=%s (vision=%s): %s", wid, vision, stop_reason)
                break

            record_base = {
                "run_id": run_id,
                "writing_id": wid,
                "topic": topic,
                "created_at": datetime.utcnow().isoformat() + "Z",
                "model": model,
                "vision": vision,
                "selection_mode": selection_mode,
                "plan_rationale": plan_rationale,
            }

            if dry_run:
                append_jsonl(results_jsonl, {**record_base, "dry_run": True})
                v_processed += 1
                processed_total += 1
                per_item_summary.append({"writing_id": wid, "topic": topic, "items": 0, "dry_run": True, "vision": vision})
                continue

            try:
                R.ev_w("🧮 budget.check.curator", vision=vision,
                    writing_id=wid, today=_today_for_model(db_path, model), limit=mini_token_limit)

                R.ev_w("🌱 fantasia.begin", vision=vision, writing_id=wid, topic=topic)
                t_write_start = time.perf_counter()

                parsed, raw_text = run_llm_on_chunk(doc, vision, model=model, db_path=db_path)

                created_at = record_base["created_at"]
                rows_for_db = [
                    (f"writing#{wid}", it.title, it.description, it.rationale, created_at, vision)
                    for it in parsed.items
                ]
                log.info("parsed %d fantasia items for writing_id=%s vision=%s", len(rows_for_db), wid, vision)

                items_count = len(rows_for_db)
                dt = time.perf_counter() - t_write_start
                R.ev_w("🌱 fantasia.parsed", vision=vision, writing_id=wid, items=items_count, ms=int(dt*1000))

                if rows_for_db:
                    insert_fantasia_rows(db_path, rows_for_db)
                    mark_writing_vision_done(db_path, wid, vision)
                    R.ev_w("💾 fantasia.persisted", vision=vision, writing_id=wid, items=items_count)
                    v_processed += 1
                    processed_total += 1
                else:
                    R.ev_w("⚠️ fantasia.zero_items", vision=vision, writing_id=wid)
            except ValidationError as ve:
                append_jsonl(errors_jsonl, {
                    **record_base,
                    "error": "validation_error",
                    "details": json.loads(ve.json()),
                    "raw_model_text": (raw_text or "")[:8000],
                })
                
            except sqlite3.IntegrityError as ie:
                append_jsonl(errors_jsonl, {**record_base, "error": "IntegrityError", "details": str(ie)})
            except Exception as e:
                append_jsonl(errors_jsonl, {**record_base, "error": type(e).__name__, "details": str(e)})
                R.ev_w("💥 fantasia.error", vision=vision, writing_id=wid,
                    error=type(e).__name__, details=str(e))
                log.error("Fantasia error (vision=%s, writing_id=%s): %s\n%s",
                        vision, wid, e, traceback.format_exc())


        per_vision_stats.append({
            "vision": vision,
            "processed": v_processed,
            "skipped": v_skipped,
            "selected_existing": selected_existing_ids,
            "proposed_new_count": len(proposed_new),
            "commissioned_new_count": len(commissioned),
            "plan_rationale": plan_rationale,
        })
        R.ev_v("🎯 vision.end", vision=vision,
            processed=v_processed, skipped=v_skipped,
            ms=int((time.perf_counter()-vision_wall_start)*1000))


    usage_snapshot = _read_usage_snapshot(db_path)
    R.ev("📊 usage.snapshot.end", by_model=usage_snapshot.get("by_model", {}))

    manifest_path = run_out_dir / f"_run_{run_id}.json"

    manifest = {
        "run_id": run_id,
        "job_started": job_started,
        "curator_model": model,
        "writer_model": model_write,
        "visions": visions,
        "writings_processed": processed_total,
        "writings_skipped": skipped_total,
        "skipped_details": skipped_details,
        "results_jsonl": str(results_jsonl),
        "errors_jsonl": str(errors_jsonl),
        "per_item": per_item_summary,
        "per_vision": per_vision_stats,
        "db_path": str(db_path),
        "usage": usage_snapshot,
        "stopped_early": bool(stopped_early),
        "stop_reason": stop_reason,
    }
    write_json(manifest_path, manifest)
    R.ev("🏁 /run.complete",
        processed=processed_total,
        skipped=skipped_total,
        visions=len(visions),
        stopped_early=bool(stopped_early),
        stop_reason=stop_reason,
        manifest=str(manifest_path),
        ms=int((time.perf_counter()-run_wall_start)*1000))

    return jsonify(manifest), 200




@app.get("/files")
def list_files():
    db_path = Path(request.args.get("db_path") or DB_PATH_DEFAULT)
    with sqlite3.connect(db_path) as conn:
        cur = conn.cursor()
        cur.execute("SELECT DISTINCT file_name FROM fantasia_cores ORDER BY file_name ASC")
        rows = [r[0] for r in cur.fetchall()]
    return jsonify(rows)

@app.get("/fantasias")
def list_fantasias():
    db_path = Path(request.args.get("db_path") or DB_PATH_DEFAULT)
    q = (request.args.get("q") or "").strip()
    file_name = (request.args.get("file_name") or "").strip()
    vision = (request.args.get("vision") or "").strip()
    limit = int(request.args.get("limit") or 500)
    limit = max(1, min(limit, 5000))

    sql = "SELECT file_name, title, description, rationale, vision, created_at FROM fantasia_cores"
    where, params = [], []
    if file_name:
        where.append("file_name = ?"); params.append(file_name)
    if vision:
        where.append("vision = ?"); params.append(vision)
    if q:
        like = f"%{q}%"
        where.append("(title LIKE ? OR description LIKE ? OR rationale LIKE ? OR IFNULL(vision,'') LIKE ?)")
        params += [like, like, like, like]
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY datetime(created_at) DESC LIMIT ?"; params.append(limit)

    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute(sql, params)
        data = [dict(r) for r in cur.fetchall()]
    return jsonify(data)


@app.get("/usage")
def get_usage():
    """
    Simple token counter endpoint.
    - GET /usage                             -> full snapshot: {"totals": {...}, "by_model": {...}}
    - GET /usage?model=gpt-5-mini-2025-08-07 -> just that model's {"today": {...}, "all_time": {...}}
    Optional: &db_path=/path/to/jid.db
    """
    db_path = Path(request.args.get("db_path") or DB_PATH_DEFAULT)
    model = (request.args.get("model") or "").strip()
    snap = _read_usage_snapshot(db_path)
    if model:
        m = snap.get("by_model", {}).get(model, None)
        # Always return the shape; default to zeros if not present
        m_today = (m or {}).get("today", {"input": 0, "output": 0, "total": 0})
        m_all   = (m or {}).get("all_time", {"input": 0, "output": 0, "total": 0})
        return jsonify({"model": model, "today": m_today, "all_time": m_all})
    return jsonify(snap)


@app.route("/write", methods=["POST", "GET"])
def write_by_gpt():
    """
    Generate ~10 topics, write a ~2-page synthesis for each, and persist to SQLite.
    Optional JSON body:
      {
        "model_write": "gpt-5",        # model for the writing step (default: "gpt-5")
        "model_topics": "gpt-5-mini",  # model for topic generation (default: DEFAULT_MODEL)
        "count": 10                    # target topics (8-12 allowed by schema)
      }
    """
    # --- config / inputs ---
    data = request.get_json(silent=True) or {}
    model_write  = data.get("model_write", "gpt-5-mini-2025-08-07")
    model_topics = data.get("model_topics", DEFAULT_MODEL)
    target_count = int(data.get("count", 10))
    out_dir      = Path(data.get("out", DEFAULT_OUT))
    target_count = max(8, min(12, target_count))  # must fit schema
    dry_run      = bool(data.get("dry_run", False))
    topic_batch_size = int(data.get("topic_batch_size", 10))
    max_token_count = int(data.get("max_token_count", 1000000))    # stop when gpt-5 exceeds this

    log.info(f"🟢 /write called — model_write={model_write}, model_topics={model_topics}, max_token_count={max_token_count}")

    db_path = Path(DB_PATH_DEFAULT)
    ensure_db(db_path)
    job_started = datetime.utcnow().isoformat() + "Z"
    run_id = f"write-{datetime.utcnow().strftime('%Y%m%dT%H%M%SZ')}-{uuid.uuid4().hex[:8]}"
    run_out_dir = out_dir / f"writings_{run_id}"
    ensure_dir(run_out_dir)

    results_jsonl = run_out_dir / f"writings.{run_id}.jsonl"
    errors_jsonl  = run_out_dir / f"errors.{run_id}.jsonl"

    total_written = 0
    stopped_early = False
    stop_reason   = None
    all_topics: List[Topic] = []

    usage_snapshot = _read_usage_snapshot(db_path)
    gpt5_today = _today_for_model(db_path, model_write)
    log.info(f"Current gpt-5 usage before run: {gpt5_today}")

    if gpt5_today.get("total", 0) >= max_token_count:
        stop_reason = f"daily_token_budget_exceeded ({gpt5_today.get('total')} >= {max_token_count})"
        log.warning("⛔ Aborting write before start: %s", stop_reason)
        return jsonify({
            "run_id": run_id,
            "writings": total_written,
            "stopped_early": True,
            "stop_reason": stop_reason,
            "usage": usage_snapshot,
        })

    # --- your fixed prompt (DO NOT MODIFY) ---
    write_llm_input = """ROLE
        You are a subject-matter expert writing a rigorous, self-contained 2-page synthesis on {topic}.

        DELIVERABLE
        A cohesive document (~1000 words) with the following sections:

        A. Abstract (≤120 words) – capture the scope, significance, and main conclusion.
        B. Background – summarize essential context and definitions.
        C. Core Analysis – explain key mechanisms, theories, or findings (2–4 subsections).
        D. Evidence Review – cite 3–6 primary or peer-reviewed sources; discuss their convergence/divergence.
        E. Counterarguments / Open Questions – identify uncertainties, gaps, or ongoing debates.
        F. Implications – why this topic matters scientifically or practically.
        G. References – formatted [Author, Year] with working links or DOIs.

        REQUIREMENTS
        - Every factual claim must be traceable to a reputable source.
        - Use precise, formal language and logical flow (cause → evidence → inference → implication).
        - Integrate at least one figure description or quantitative comparison where relevant.
        - Avoid filler; compress ideas without sacrificing rigor.
        - Explicitly mark uncertainty using phrases like “Current evidence suggests…” or “Unresolved questions include…”
        """

    # --- main loop ---
    batch_index = 0
    rows_for_db = []
    while not stopped_early:
        batch_index += 1
        log.info(f"🧠 Generating topic batch {batch_index}")
        # --- 1) Generate topics that validate against your ListOfTopics schema ---
        try:
            # SDK expects: input=[...], text_format=Schema
            topics_resp = _client.responses.parse(  # type: ignore[attr-defined]
                model=model_topics,
                input=[
                    {
                        "role": "system",
                        "content": (
                            "Return a JSON object that matches the Pydantic schema ListOfTopics. "
                            f"Generate {topic_batch_size} distinct topics across diverse disciplines. "
                            "Use concise, informative descriptions."
                        ),
                    },
                    {
                        "role": "user",
                        "content": (
                            "Produce fields exactly as: {\"items\": [{\"topic\": str, \"description\": str}, ...]}. "
                            f"Length must be between 8 and 12 items (aim for {topic_batch_size})."
                        ),
                    },
                ],
                text_format=ListOfTopics,  # <- your schema
            )

            # --- unwrap ParsedResponse[ListOfTopics] safely ---
            topics_model = getattr(topics_resp, "output_parsed", None)
            if topics_model is None:
                topics_model = getattr(topics_resp, "parsed", None)

            if topics_model is None:
                # fallback: parse raw text as JSON and validate
                raw_text = getattr(topics_resp, "output_text", None) or getattr(topics_resp, "text", None) or ""
                m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw_text, re.S)
                raw_json = m.group(1) if m else raw_text
                topics_model = ListOfTopics.model_validate(json.loads(raw_json))

            if not isinstance(topics_model, ListOfTopics):
                topics_model = ListOfTopics.model_validate(topics_model)

            topics_list: List[Topic] = topics_model.items
            topics_batch = topics_list  # <- define for downstream code

        except Exception as e:
            log.error(f"💥 Error generating topics: {e}", exc_info=True)
            append_jsonl(errors_jsonl, {"run_id": run_id, "error": "topic_generation", "details": str(e)})
            return jsonify({"run_id": run_id, "error": f"Failed to generate topics: {e}"}), 500

        # append to master list
        all_topics.extend(topics_batch)

        for t in topics_batch:
            if stopped_early:
                break
            topic_text = f"{t.topic} - {t.description}".strip()
            write_instruct = write_llm_input.format(topic=topic_text)

            # budget check before each write
            gpt5_today = _today_for_model(db_path, model_write)
            if gpt5_today.get("total", 0) >= max_token_count:
                stopped_early = True
                stop_reason = f"token_budget_reached ({gpt5_today.get('total')} >= {max_token_count})"
                log.warning("⛔ Stopping write loop: %s", stop_reason)
                break

            created_at = datetime.utcnow().isoformat() + "Z"
            record_base = {
                "run_id": run_id,
                "topic": t.topic,
                "description": t.description,
                "created_at": created_at,
                "model": model_write,
            }

            if dry_run:
                append_jsonl(results_jsonl, {**record_base, "dry_run": True})
                continue

            try:
                resp = _client.responses.create(
                    model=model_write,
                    #tools=[{"type": "web_search"}],
                    #reasoning={ "effort": "low" },
                    input=[{"role": "user", "content": write_instruct}],
                )
                writing = getattr(resp, "output_text", None) or getattr(resp, "text", None) or ""
                if not writing.strip():
                    writing = "[No content returned]"

                # persist to JSONL + DB
                out_obj = {**record_base, "document": writing}
                append_jsonl(results_jsonl, out_obj)
                rows_for_db.append((t.topic, t.description, writing, created_at, model_write, run_id))
                total_written += 1

                # update token counters
                try:
                    with sqlite3.connect(db_path) as conn:
                        usage = _usage_from_resp(resp)
                        _record_llm_usage_by_model(conn, model_write, usage)
                        _record_llm_usage(conn, usage)
                except Exception as ue:
                    log.warning(f"usage accounting failed: {ue}")

                # re-check after accounting
                gpt5_today = _today_for_model(db_path, model_write)
                if gpt5_today.get("total", 0) >= max_token_count:
                    stopped_early = True
                    stop_reason = f"token_budget_reached ({gpt5_today.get('total')} >= {max_token_count})"
                    log.warning("⛔ Stopping mid-batch: %s", stop_reason)
                    break

            except Exception as e:
                err = {**record_base, "error": type(e).__name__, "details": str(e)}
                append_jsonl(errors_jsonl, err)
                log.error(f"💥 Error writing topic '{t.topic}': {e}", exc_info=True)

        # small safety: write accumulated rows every batch
        if rows_for_db:
            insert_writings_rows(db_path, rows_for_db)
            rows_for_db.clear()

        if stopped_early:
            break

    # --- manifest + response ---
    usage_snapshot = _read_usage_snapshot(db_path)
    manifest = {
        "run_id": run_id,
        "job_started": job_started,
        "out_root": str(run_out_dir),
        "model_write": model_write,
        "model_topics": model_topics,
        "topics_generated": len(all_topics),
        "writings_saved": total_written,
        "db_path": str(db_path),
        "usage": usage_snapshot,
        "stopped_early": stopped_early,
        "stop_reason": stop_reason,
    }
    write_json(run_out_dir / f"_write_{run_id}.json", manifest)

    log.info(f"🏁 /write complete — {total_written} writings, stopped_early={stopped_early}, reason={stop_reason}")
    return jsonify(manifest)


# --- NEW: lightweight read APIs for the dashboard ---

@app.get("/writings")
def list_writings():
    """
    List writings with light metadata (no full document) and optional filtering.
    Query params:
      q:        substring match in topic or description
      limit:    max rows (default 100, max 2000)
      since:    ISO8601 timestamp (UTC) to filter created_at >= since
      run_id:   exact match filter
      model:    exact match filter
    """
    db_path = Path(request.args.get("db_path") or DB_PATH_DEFAULT)
    q       = (request.args.get("q") or "").strip()
    limit   = int(request.args.get("limit") or 100)
    limit   = max(1, min(2000, limit))
    since   = (request.args.get("since") or "").strip()
    run_id  = (request.args.get("run_id") or "").strip()
    model   = (request.args.get("model") or "").strip()

    sql = """
      SELECT id, topic, description, created_at, model, run_id
      FROM writings
    """
    where, params = [], []
    if q:
        like = f"%{q}%"
        where.append("(topic LIKE ? OR IFNULL(description,'') LIKE ?)")
        params += [like, like]
    if since:
        where.append("created_at >= ?")
        params.append(since)
    if run_id:
        where.append("run_id = ?")
        params.append(run_id)
    if model:
        where.append("model = ?")
        params.append(model)
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY datetime(created_at) DESC LIMIT ?"
    params.append(limit)

    ensure_db(db_path)
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute(sql, params)
        rows = [dict(r) for r in cur.fetchall()]
    return jsonify(rows)


@app.get("/writing/<int:writing_id>")
def get_writing(writing_id: int):
    """
    Fetch a single writing including the full document text.
    """
    db_path = Path(request.args.get("db_path") or DB_PATH_DEFAULT)
    ensure_db(db_path)
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute("""
            SELECT id, topic, description, document, created_at, model, run_id
            FROM writings
            WHERE id = ?
            LIMIT 1
        """, (writing_id,))
        row = cur.fetchone()
        if not row:
            return jsonify({"error": "writing not found"}), 404
        return jsonify(dict(row))


@app.get("/visions")
def list_visions():
    """
    Return visions with temporal metadata so the UI can sort by recency.
    ?order=last_seen|first_seen|alpha   (default: last_seen)
    Optional: &db_path=...
    """
    db_path = Path(request.args.get("db_path") or DB_PATH_DEFAULT)
    order = (request.args.get("order") or "last_seen").lower()
    order_sql = {
        "last_seen":  "ORDER BY datetime(last_seen) DESC",
        "first_seen": "ORDER BY datetime(first_seen) DESC",
        "alpha":      "ORDER BY vision COLLATE NOCASE ASC",
    }.get(order, "ORDER BY datetime(last_seen) DESC")

    sql = f"""
    SELECT
      vision,
      MIN(datetime(created_at)) AS first_seen,
      MAX(datetime(created_at)) AS last_seen,
      COUNT(*)                  AS core_count
    FROM fantasia_cores
    WHERE vision IS NOT NULL AND TRIM(vision) != ''
    GROUP BY vision
    {order_sql}
    """

    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute(sql)
        rows = [dict(r) for r in cur.fetchall()]
    return jsonify(rows)


# ---- The endpoint ----@app.post("/api/fantasia/ensure-structure")
def api_fantasia_ensure_structure():
    """
    Select (random or specified) fantasia core and ensure/grow:
      - domains
      - dimensions per domain
      - thesis per dimension

    Optional force flags let you append new structure even if it already exists.

    Request JSON (all optional):
    {
      "email": "boris@fantasiagenesis.com",
      "model": "gpt-5-mini-2025-08-07",
      "target_dimensions_per_domain": 3,

      "core_id": 482,

      "force_domains": false,
      "force_dimensions": false,
      "force_thesis": false
    }
    """
    data = request.get_json(silent=True) or {}
    email = (data.get("email") or "").strip().lower() or None
    model = (data.get("model") or "gpt-5-mini-2025-08-07").strip()
    target_dims = max(1, int(data.get("target_dimensions_per_domain") or 3))

    force_domains = bool(data.get("force_domains"))
    force_dimensions = bool(data.get("force_dimensions"))
    force_thesis = bool(data.get("force_thesis"))

    # budgets
    LIVE_CAP   = 3_000_000
    DAILY_CAP  = 10_000_000
    budget = _TokenBudget(DB_PATH, model, LIVE_CAP, DAILY_CAP)

    created = {
        "core_id": None,
        "domains_created": 0,
        "dimensions_created": 0,
        "theses_created": 0,
        "live_tokens_used": 0,
        "model": model,
    }

    with sqlite3.connect(DB_PATH) as conn, conn:
        conn.row_factory = sqlite3.Row

        # make sure schema exists
        _apply_fantasia_structure_schema(DB_PATH)

        # ---- choose core ----
        core = None
        if data.get("core_id") is not None:
            core_row = conn.execute("""
                SELECT id, file_name, title, description, rationale, created_at,
                       COALESCE(vision,'') AS vision
                FROM fantasia_cores
                WHERE id = ?
                LIMIT 1
            """, (int(data["core_id"]),)).fetchone()
            if core_row:
                core = dict(core_row)
        else:
            core = _pick_random_core(conn)

        if not core:
            return jsonify(ok=False, error="No matching fantasia_core found."), 404

        core_id = int(core["id"])
        created["core_id"] = core_id

        # ---- DOMAINS ----
        have_domains = (_domain_count(conn, core_id) > 0)

        if force_domains or (not have_domains):
            # Build user message for domains
            user_msg = (DOMAIN_ARCHITECT_USER_TEMPLATE
                        .replace("{core_title}", core["title"])
                        .replace("{core_description}", core["description"]))

            parsed = _openai_parse_guarded(
                model, DOMAIN_ARCHITECT_SYS_MSG, user_msg,
                PD_DomainArchitectOut, budget, conn
            )

            for group in parsed.groups:
                group_title = (group.title or "").strip() or None
                for d in group.domains:
                    name = (d.name or "").strip()
                    desc = (d.description or "").strip()
                    if not name:
                        continue
                    _insert_domain(
                        conn,
                        core_id,
                        name,
                        desc,
                        provider="openai",
                        group_title=group_title
                    )
                    created["domains_created"] += 1

        # fetch (now possibly larger) domain set
        domains = conn.execute(
            "SELECT * FROM fantasia_domain WHERE core_id=? ORDER BY id",
            (core_id,)
        ).fetchall()

        # ---- DIMENSIONS ----
        for dom in domains:
            dom_id = int(dom["id"])
            dims_existing = _dimensions_for_domain(conn, dom_id)

            if force_dimensions or (len(dims_existing) == 0):
                # Build prompt for new dimensions
                user_msg = (DIM_USER_TEMPLATE
                            .replace("{core_name}", core["title"])
                            .replace("{core_description}", core["description"])
                            .replace("{domain_name}", dom["name"])
                            .replace("{domain_description}", dom["description"] or "")
                            .replace("{count}", str(target_dims)))

                parsed_dims = _openai_parse_guarded(
                    model, DIM_SYS_MSG, user_msg,
                    PD_DimensionOut, budget, conn
                )

                for di in parsed_dims.dimensions[:target_dims]:
                    name = (di.name or "").strip()
                    desc = (di.description or "").strip()
                    if not name:
                        continue
                    _insert_dimension(
                        conn,
                        dom_id,
                        name,
                        desc,
                        provider="openai"
                    )
                    created["dimensions_created"] += 1

        # refresh dimensions after potential inserts
        all_dims = conn.execute("""
            SELECT d.id AS domain_id,
                   d.name AS domain_name,
                   m.*
            FROM fantasia_domain d
            JOIN fantasia_dimension m ON m.domain_id = d.id
            WHERE d.core_id = ?
            ORDER BY d.id, m.id
        """, (core_id,)).fetchall()

        # ---- THESES ----
        for row in all_dims:
            dim_id = int(row["id"])
            has_thesis = (_thesis_for_dimension(conn, dim_id) is not None)

            if force_thesis or (not has_thesis):
                user_msg = (THESIS_USER_TEMPLATE
                            .replace("{core_name}", core["title"])
                            .replace("{core_description}", core["description"])
                            .replace("{domain_name}", row["domain_name"])
                            .replace("{domain_description}", "")  # optional
                            .replace("{dimension_name}", row["name"])
                            .replace("{dimension_description}", row["description"] or ""))

                parsed_thesis = _openai_parse_guarded(
                    model, THESIS_SYS_MSG, user_msg,
                    PD_ThesisOut, budget, conn
                )

                _insert_thesis(
                    conn,
                    core_id=core_id,
                    domain_id=int(row["domain_id"]),
                    dimension_id=dim_id,
                    text=(parsed_thesis.thesis or "").strip(),
                    provider="openai",
                    author_email=email
                )
                created["theses_created"] += 1

        created["live_tokens_used"] = budget.live_used

    return jsonify(ok=True, **created), 200
