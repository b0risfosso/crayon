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
DB_PATH_DEFAULT = "/var/www/site/data/jid.db"

SYSTEM_MSG = (
    "You are an expert creative-technical ideation model that produces structured, "
    "actionable 'fantasia cores' (ideas, visions, aspirations, imaginations, emotions, experiences, achievements) that open new doors for builders."
)

USER_PROMPT_TEMPLATE = """Task: Read the following excerpt carefully. Then, generate 6–8 fantasia cores inspired by it. Each fantasia cores should orbit around something that humans would be driven to create, build, experience, achieve, or understand — what is already well-understood or complete is out of scope. Fantasia should spring from curiosity, possibility, or emotional resonance rather than simple description. Fantasia may emerge from any domain that stirs human imagination — science, engineering, philosophy, emotion, science fiction, fantasy, art, or other frontiers of thought. For each fantasia, include:
- Title: A clear, descriptive title (not cryptic or abstract).
- Description (2–3 sentences): Explain the fantasia — what it explores, builds, or reveals.
- Rationale: A short rationale for why this fantasia would captivate human curiosity, creativity, or purpose.

push the limits of what is possible, known, and understood.

<------------- EXCERPT START ---------------------->
{excerpt}
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

def run_llm_on_chunk(chunk: str, model: str = DEFAULT_MODEL) -> FantasiaBatch:
    user_msg = USER_PROMPT_TEMPLATE.format(excerpt=chunk)
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
                _record_llm_usage(conn, _usage_from_resp(resp))
                conn.commit()
    except Exception as e:
        # non-fatal; keep processing
        print(f"[warn] failed to record llm usage: {e}")

        
    # SDK returns parsed object mapped to Pydantic. If your SDK returns dict, validate explicitly:
    if isinstance(resp, FantasiaBatch):
        return resp
    # Fallback validation if SDK returns plain dict-like
    return FantasiaBatch.model_validate(resp)  # type: ignore

# ---------- I/O helpers ----------

def discover_files(root: Path) -> List[Path]:
    files: List[Path] = []
    for p in root.rglob("*"):
        if p.is_file() and p.suffix.lower() in (SUPPORTED_TXT_EXT | SUPPORTED_PDF_EXT):
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
                created_at TEXT NOT NULL
            )
            """
        )
        cur.execute("CREATE INDEX IF NOT EXISTS idx_fantasia_cores_created_at ON fantasia_cores(created_at);")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_fantasia_cores_file_name ON fantasia_cores(file_name);")

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


def insert_fantasia_rows(db_path: Path, rows: list[tuple[str, str, str, str, str]]) -> None:
    """
    rows: List of tuples (file_name, title, description, rationale, created_at)
    """
    if not rows:
        return
    with sqlite3.connect(db_path) as conn:
        cur = conn.cursor()
        cur.executemany(
            """
            INSERT INTO fantasia_cores (file_name, title, description, rationale, created_at)
            VALUES (?, ?, ?, ?, ?)
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

    if not source_dir.exists():
        return jsonify({"error": f"source does not exist: {source_dir}"}), 400

    # Ensure DB exists (safe even if already created)
    ensure_db(db_path)

    files = discover_files(source_dir)
    if max_files > 0:
        files = files[:max_files]

    job_started = datetime.utcnow().isoformat() + "Z"
    run_id = short_hash(job_started + str(source_dir))

    results_jsonl = out_dir / f"fantasia_core_results.{run_id}.jsonl"
    errors_jsonl = out_dir / f"errors.{run_id}.jsonl"

    total_chunks = 0
    processed_files = 0
    skipped_files = 0              # <-- NEW
    skipped_details = []  
    per_file_summaries = []

    for fp in files:
        try:
            if not force and file_already_processed(db_path, fp):
                skipped_files += 1
                skipped_details.append({"source_name": fp.name, "reason": "already_processed"})
                continue

            ext = fp.suffix.lower()
            if ext in SUPPORTED_TXT_EXT:
                text = read_text_file(fp)
            elif ext in SUPPORTED_PDF_EXT:
                text = read_pdf(fp)
            else:
                continue

            text = text.strip()
            if not text:
                # Mark empty reads as processed too, so we don't keep re-scanning empties
                mark_file_processed(db_path, fp, run_id)   # <-- optional but handy
                continue

            chunks = chunk_text(text, chunk_chars)
            file_out_dir = out_dir / fp.stem
            ensure_dir(file_out_dir)

            file_chunk_summaries = []

            for idx, (start, end, seg) in enumerate(chunks):
                total_chunks += 1
                record_base = {
                    "run_id": run_id,
                    "source_path": str(fp),
                    "source_name": fp.name,
                    "source_ext": ext,
                    "chunk_index": idx,
                    "start_char": start,
                    "end_char": end,
                    "chunk_chars": len(seg),
                    "model": model,
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
                    })
                    continue

                try:
                    parsed: FantasiaBatch = run_llm_on_chunk(seg, model=model, db_path=db_path)
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
                        (fp.name, it.title, it.description, it.rationale, created_at)
                        for it in parsed.items
                    ]
                    insert_fantasia_rows(db_path, rows_for_db)
                    # -----------------------------------------------

                    file_chunk_summaries.append({
                        "chunk_index": idx,
                        "items": len(parsed.items),
                        "excerpt_hash": parsed.excerpt_hash,
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

            # ... existing per-file manifest write and counters ...
            write_json(
                file_out_dir / "_manifest.json",
                {
                    "run_id": run_id,
                    "source": str(fp),
                    "chunks": len(chunks),
                    "chunk_chars": chunk_chars,
                    "model": model,
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
            "model": model,
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
        "db_path": str(db_path),      # <-- echoed
        "usage": usage_snapshot
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
    limit = int(request.args.get("limit") or 500)
    limit = max(1, min(limit, 5000))

    sql = "SELECT file_name, title, description, rationale, created_at FROM fantasia_cores"
    where, params = [], []
    if file_name:
        where.append("file_name = ?"); params.append(file_name)
    if q:
        like = f"%{q}%"
        where.append("(title LIKE ? OR description LIKE ? OR rationale LIKE ?)")
        params += [like, like, like]
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY datetime(created_at) DESC LIMIT ?"; params.append(limit)

    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute(sql, params)
        data = [dict(r) for r in cur.fetchall()]
    return jsonify(data)


# No `if __name__ == "__main__":` per your hosting mode. Run with gunicorn:
# gunicorn -w 4 -k gthread --threads 8 --bind 127.0.0.1:9013 jid:app
