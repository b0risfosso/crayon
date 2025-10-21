#!/usr/bin/env python3
# jid.py — Flask app to chunk PDFs/TXT, run LLM with Pydantic schema, and persist results.

import os
import re
import json
import time
from datetime import datetime
from pathlib import Path
from typing import List, Tuple, Optional, Iterable
import sqlite3
import subprocess
from pathlib import Path
import tempfile
import shutil
import logging

from flask import Flask, request, jsonify

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
FALLBACK_MODEL_DEFAULT = "gpt-5"
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



USER_PROMPT_TEMPLATE = """Task: Read the following excerpt carefully. Then, generate 6–8 core fantasia inspired by it. Each fantasia should orbit around something that someone {VISION} would be driven to create, build, experience, achieve, or understand — not what is already well-understood or complete. 

Fantasia should spring from curiosity, possibility, or emotional resonance rather than simple description. Fantasia may emerge from any domain that stirs someone {VISION}'s imagination — science, engineering, philosophy, emotion, science fiction, fantasy, art, or other frontiers of thought. 

For each fantasia, include:

Title: A clear, descriptive title (not cryptic or abstract).  
Description (2–3 sentences): Explain the fantasia — what it explores, builds, or reveals.  
Rationale: A short rationale for why this fantasia would captivate someone {VISION}'s curiosity, creativity, or purpose.  

Goal: Produce ideas that open new doors — that make someone {VISION} feel that something meaningful, beautiful, or powerful could be created from the seed of this excerpt.  

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


def normalize_ws(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t\f\v]+", " ", text)
    text = re.sub(r"\n\s+\n", "\n\n", text)
    return text.strip()

def chunk_text(text: str, chunk_size: int) -> List[Tuple[int, int, str]]:
    """
    Return list of (start_idx, end_idx, chunk_text) near chunk_size chars,
    preferring sentence/space boundaries close to the edge.
    """
    if not text:
        return []
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

def run_llm_on_chunk(
    chunk: str,
    vision: str = "exploring the betterment of humanity",
    model: str = DEFAULT_MODEL,
    db_path: Optional[Path] = None,   # <--- NEW: pass db_path in explicitly
) -> FantasiaBatch:

    user_msg = USER_PROMPT_TEMPLATE.format(EXCERPT=chunk, VISION=vision)
    schema = FantasiaBatch  # Pydantic model as text_format schema

    resp = _client.responses.parse(  # type: ignore[attr-defined]
        model=model,
        input=[
            {"role": "system", "content": SYSTEM_MSG},
            {"role": "user", "content": user_msg},
        ],
        text_format=schema,
    )

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
    with sqlite3.connect(db_path) as conn:
        cur = conn.cursor()
        today = datetime.utcnow().strftime("%Y-%m-%d")
        def row(d):
            cur.execute("SELECT input_tokens, output_tokens, total_tokens FROM llm_usage_counters WHERE date = ?", (d,))
            r = cur.fetchone()
            return {"input": r[0], "output": r[1], "total": r[2]} if r else {"input": 0, "output": 0, "total": 0}
        return {"today": row(today), "all_time": row("ALL_TIME")}


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

        _init_llm_usage_table(conn)


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


# ---------- Flask app ----------

app = Flask(__name__)

@app.get("/health")
def health():
    return jsonify({"ok": True, "time": datetime.utcnow().isoformat() + "Z"})


@app.post("/run")
def run_pipeline():
    # ... existing code ...
    data = request.get_json(silent=True) or {}
    source_dir = Path(data.get("source", DEFAULT_SOURCE))
    out_dir = Path(data.get("out", DEFAULT_OUT))
    chunk_chars = int(data.get("chunk_chars", DEFAULT_CHUNK_CHARS))
    model = str(data.get("model", DEFAULT_MODEL))
    dry_run = bool(data.get("dry_run", False))
    max_files = int(data.get("max_files", 0))
    db_path = Path(data.get("db_path", DB_PATH_DEFAULT))
    force = bool(data.get("force", False))   # <-- NEW
    vision = data.get("vision", "exploring the betterment of humanity")

    # --- NEW: budget control overrides (optional) ---
    switch_from_model = str(data.get("switch_from_model", SWITCH_FROM_MODEL_DEFAULT))
    fallback_model    = str(data.get("fallback_model", FALLBACK_MODEL_DEFAULT))
    switch_model_limit = int(data.get("switch_model_limit", SWITCH_MODEL_LIMIT_DEFAULT))
    stop_run_limit     = int(data.get("stop_run_limit", STOP_RUN_LIMIT_DEFAULT))

    log.info(
        "🟢 /run called — source=%s, model=%s, dry_run=%s, force=%s, vision=%s, "
        "switch_from=%s, fallback=%s, switch_limit=%s, stop_limit=%s",
        source_dir, model, dry_run, force, vision, switch_from_model, fallback_model, switch_model_limit, stop_run_limit
    )

    if not source_dir.exists():
        log.error(f"❌ Source directory does not exist: {source_dir}")
        return jsonify({"error": f"source does not exist: {source_dir}"}), 400

    # Ensure DB exists (safe even if already created)
    ensure_db(db_path)

    files = discover_files(source_dir)
    if max_files > 0:
        files = files[:max_files]

    log.info(f"Found {len(files)} files to process in {source_dir}")

    job_started = datetime.utcnow().isoformat() + "Z"
    run_id = short_hash(job_started + str(source_dir))
    log.info(f"Run ID: {run_id}")

    ensure_dir(out_dir)

    results_jsonl = out_dir / f"fantasia_core_results.{run_id}.jsonl"
    errors_jsonl = out_dir / f"errors.{run_id}.jsonl"

    total_chunks = 0
    processed_files = 0
    skipped_files = 0              # <-- NEW
    skipped_details = []  
    per_file_summaries = []

    stopped_early = False
    stop_reason = None

    # --- NEW: hard-stop guard before any processing ---
    totals = _today_totals(db_path)
    if totals.get("total", 0) > stop_run_limit:
        stop_reason = f"daily_total_tokens_exceeded ({totals.get('total')} > {stop_run_limit})"
        log.warning("⛔ Aborting run before start: %s", stop_reason)
        usage_snapshot = _read_usage_snapshot(db_path)
        # Write minimal run manifest and return early
        write_json(
            out_dir / f"_run_{run_id}.json",
            {
                "run_id": run_id,
                "job_started": job_started,
                "source_root": str(source_dir),
                "out_root": str(out_dir),
                "model": model,
                "vision": vision,
                "chunk_chars": chunk_chars,
                "files": processed_files,
                "chunks": total_chunks,
                "results_jsonl": str(results_jsonl),
                "errors_jsonl": str(errors_jsonl),
                "per_file": per_file_summaries,
                "db_path": str(db_path),
                "skipped_files": skipped_files,
                "skipped_details": skipped_details,
                "usage": usage_snapshot,
                "stopped_early": True,
                "stop_reason": stop_reason,
            },
        )
        return jsonify({
            "run_id": run_id,
            "files_processed": processed_files,
            "files_skipped": skipped_files,
            "chunks_total": total_chunks,
            "results_jsonl": str(results_jsonl),
            "errors_jsonl": str(errors_jsonl),
            "out_root": str(out_dir),
            "per_file": per_file_summaries,
            "skipped_details": skipped_details,
            "db_path": str(db_path),
            "usage": usage_snapshot,
            "stopped_early": True,
            "stop_reason": stop_reason,
        })

    for fp in files:
        if stopped_early:
            break
        try:
            log.info(f"📄 Processing file: {fp.name}")
            if not force and file_already_processed(db_path, fp):
                log.info(f"⏩ Skipping already processed file: {fp.name}")
                skipped_files += 1
                skipped_details.append({"source_name": fp.name, "reason": "already_processed"})
                continue

            ext = fp.suffix.lower()
            if ext in SUPPORTED_TXT_EXT:
                text = read_text_file(fp)
            elif ext in SUPPORTED_PDF_EXT:
                text = read_pdf(fp)
                # File-level OCR fallback trigger (mirror tester): only if total chars too low
                if len(text) <= MIN_CHARS_PDF:
                    ocr_text = ocr_pdf_if_needed(fp)
                    if ocr_text:
                        text = (text + "\n" + ocr_text).strip()
                        log.info(f"🧾 OCR fallback applied to {fp.name}; added_chars={len(ocr_text)}, total={len(text)}")
            elif ext in SUPPORTED_EPUB_EXT:
                text = read_epub(fp, max_items=10)
            else:
                continue

            text = text.strip()
            log.info(f"READ {ext.lstrip('.')} {fp.name} chars={len(text)}")
            if not text:
                # Mark empty reads as processed too, so we don't keep re-scanning empties
                mark_file_processed(db_path, fp, run_id)   # <-- optional but handy
                continue

            if ext in SUPPORTED_PDF_EXT and len(text) <= MIN_CHARS_PDF:
                log.warning(f"Skipping {fp.name}: insufficient PDF text after fallback (len={len(text)} <= {MIN_CHARS_PDF})")
                mark_file_processed(db_path, fp, run_id)
                skipped_files += 1
                skipped_details.append({"source_name": fp.name, "reason": "pdf_text_too_short"})
                continue
            if ext in SUPPORTED_EPUB_EXT and len(text) <= MIN_CHARS_EPUB:
                log.warning(f"Skipping {fp.name}: insufficient EPUB text (len={len(text)} <= {MIN_CHARS_EPUB})")
                mark_file_processed(db_path, fp, run_id)
                skipped_files += 1
                skipped_details.append({"source_name": fp.name, "reason": "epub_text_too_short"})
                continue

            chunks = chunk_text(text, chunk_chars)
            log.info(f"Split into {len(chunks)} chunks of ~{chunk_chars} chars")
            file_out_dir = out_dir / fp.stem
            ensure_dir(file_out_dir)

            file_chunk_summaries = []

            for idx, (start, end, seg) in enumerate(chunks):
                if stopped_early:
                    break
                log.info(f"⚙️  Chunk {idx+1}/{len(chunks)} for {fp.name}")
                total_chunks += 1

                # --- NEW: budget checks before every LLM call ---
                current_model = model

                # 1) Check gpt-5-mini usage → if exceeds threshold, switch to gpt-5
                mini_usage = _today_for_model(db_path, switch_from_model)
                if mini_usage.get("total", 0) > switch_model_limit:
                    if current_model != fallback_model:
                        log.warning(
                            "🔁 Switching to fallback model due to gpt-5-mini token usage: %s > %s",
                            mini_usage.get("total", 0), switch_model_limit
                        )
                    current_model = fallback_model
                
                # 2) If using gpt-5, stop the run when it exceeds stop_run_limit
                if current_model == fallback_model:
                    gpt5_usage = _today_for_model(db_path, fallback_model)
                    if gpt5_usage.get("total", 0) > stop_run_limit:
                        stopped_early = True
                        stop_reason = (
                            f"gpt-5 token limit exceeded "
                            f"({gpt5_usage.get('total', 0)} > {stop_run_limit})"
                        )
                        log.warning("⛔ Stopping run mid-file: %s", stop_reason)
                        break

                record_base = {
                    "run_id": run_id,
                    "source_path": str(fp),
                    "source_name": fp.name,
                    "source_ext": ext,
                    "chunk_index": idx,
                    "start_char": start,
                    "end_char": end,
                    "chunk_chars": len(seg),
                    "model": current_model,
                    "vision": vision,
                    "created_at": datetime.utcnow().isoformat() + "Z",
                }

                if dry_run:
                    out_obj = {
                        **record_base,
                        "fantasia_core": None,
                        "excerpt_hash": short_hash(seg),
                        "dry_run": True,
                    }
                    append_jsonl(results_jsonl, out_obj)
                    write_json(file_out_dir / f"chunk_{idx:04d}.json", out_obj)
                    file_chunk_summaries.append({
                        "chunk_index": idx,
                        "items": 0,
                        "excerpt_hash": out_obj["excerpt_hash"],
                        "vision": vision,
                    })
                    continue

                try:
                    parsed: FantasiaBatch = run_llm_on_chunk(seg, vision, model=current_model, db_path=db_path)
                    log.info(f"✅ Completed LLM for chunk {idx+1}/{len(chunks)}")
                    out_obj = {
                        **record_base,
                        "fantasia_core": parsed.model_dump(),
                        "excerpt_hash": parsed.excerpt_hash,
                        "dry_run": False,
                    }
                    append_jsonl(results_jsonl, out_obj)
                    write_json(file_out_dir / f"chunk_{idx:04d}.json", out_obj)

                    # --- NEW: write each fantasia item into SQLite ---
                    created_at = record_base["created_at"]
                    rows_for_db = [
                        (fp.name, it.title, it.description, it.rationale, created_at, vision)
                        for it in parsed.items
                    ]
                    insert_fantasia_rows(db_path, rows_for_db)
                    # -----------------------------------------------

                    file_chunk_summaries.append({
                        "chunk_index": idx,
                        "items": len(parsed.items),
                        "excerpt_hash": parsed.excerpt_hash,
                        "vision": vision,
                    })
                except ValidationError as ve:
                    err = {
                        "run_id": run_id,
                        "source_path": str(fp),
                        "chunk_index": idx,
                        "error": "validation_error",
                        "details": json.loads(ve.json()),
                        "created_at": datetime.utcnow().isoformat() + "Z",
                    }
                    append_jsonl(errors_jsonl, err)
                except Exception as e:
                    err = {
                        "run_id": run_id,
                        "source_path": str(fp),
                        "chunk_index": idx,
                        "error": type(e).__name__,
                        "details": str(e),
                        "created_at": datetime.utcnow().isoformat() + "Z",
                    }
                    append_jsonl(errors_jsonl, err)
            log.info(f"✔️  Finished file: {fp.name} (chunks={len(chunks)})")

            # ... existing per-file manifest write and counters ...
            write_json(
                file_out_dir / "_manifest.json",
                {
                    "run_id": run_id,
                    "source": str(fp),
                    "chunks": len(chunks),
                    "chunk_chars": chunk_chars,
                    "model": model,
                    "vision": vision,
                    "created_at": datetime.utcnow().isoformat() + "Z",
                    "summary": file_chunk_summaries,
                },
            )
            processed_files += 1
            per_file_summaries.append({
                "source_name": fp.name,
                "chunks": len(chunks),
                "out_dir": str(file_out_dir),
            })

            mark_file_processed(db_path, fp, run_id)

        except Exception as e:
            log.error(f"💥 Error processing {fp.name}: {e}", exc_info=True)
            err = {
                "run_id": run_id,
                "source_path": str(fp),
                "error": type(e).__name__,
                "details": str(e),
                "created_at": datetime.utcnow().isoformat() + "Z",
            }
            append_jsonl(errors_jsonl, err)

    # ... existing run-level manifest and response ...
    usage_snapshot = _read_usage_snapshot(db_path)
    write_json(
        out_dir / f"_run_{run_id}.json",
        {
            "run_id": run_id,
            "job_started": job_started,
            "source_root": str(source_dir),
            "out_root": str(out_dir),
            "model": current_model,
            "vision": vision,
            "chunk_chars": chunk_chars,
            "files": processed_files,
            "chunks": total_chunks,
            "results_jsonl": str(results_jsonl),
            "errors_jsonl": str(errors_jsonl),
            "per_file": per_file_summaries,
            "db_path": str(db_path),   # <-- small convenience echo
            "skipped_files": skipped_files,          # <-- NEW
            "skipped_details": skipped_details,
            "usage": usage_snapshot,
            "stopped_early": bool(stopped_early),
            "stop_reason": stop_reason,
        },
    )
    log.info(f"🏁 Run {run_id} complete — {processed_files} files processed, {skipped_files} skipped, {total_chunks} chunks total.")

    if stopped_early:
        log.info(
            "🏁 Run %s ended early — %s | %s files processed, %s skipped, %s chunks total.",
            run_id, stop_reason, processed_files, skipped_files, total_chunks
        )
    else:
        log.info(
            "🏁 Run %s complete — %s files processed, %s skipped, %s chunks total.",
            run_id, processed_files, skipped_files, total_chunks
        )



    return jsonify({
        "run_id": run_id,
        "files_processed": processed_files,
        "files_skipped": skipped_files,
        "chunks_total": total_chunks,
        "results_jsonl": str(results_jsonl),
        "errors_jsonl": str(errors_jsonl),
        "out_root": str(out_dir),
        "per_file": per_file_summaries,
        "skipped_details": skipped_details,
        "db_path": str(db_path),      # <-- echoed
        "usage": usage_snapshot,
        "stopped_early": bool(stopped_early),
        "stop_reason": stop_reason,
    })


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

# No `if __name__ == "__main__":` per your hosting mode. Run with gunicorn:
# gunicorn -w 4 -k gthread --threads 8 --bind 127.0.0.1:9013 jid:app
