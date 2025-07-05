"""
library_ingest_clean.py
--------------------------------
Read documents from a directory, extract their text and basic metadata, then store everything in an SQLite
FTS5 database for fast search.

Supported file types
--------------------
* .txt – plain‑text files
* .pdf – PDFs (with automatic OCR fallback)
* .epub – EPUB ebooks
* .rtf – Rich‑Text files (converted to plain text via pandoc)
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import sqlite3
import time
from pathlib import Path
from typing import Dict, List, Tuple

import fitz  # PyMuPDF
import pytesseract
from PIL import Image
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from ebooklib import epub, ITEM_DOCUMENT
import pypandoc
from openai import OpenAI, OpenAIError
from keybert import KeyBERT


# ---------------------------------------------------------------------------
# Global configuration
# ---------------------------------------------------------------------------

Image.MAX_IMAGE_PIXELS = None  # disables DecompressionBombWarning

load_dotenv()
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o")
client = OpenAI()

model = KeyBERT()

MAX_RETRIES = 5
RETRY_DELAY = 5  # seconds

# ---------------------------------------------------------------------------
# Misc utilities
# ---------------------------------------------------------------------------

def sha256(path: Path, chunk: int = 1 << 20) -> str:
    """Compute the SHA‑256 digest of *path* in a memory‑efficient way."""
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(chunk), b""):
            h.update(block)
    return h.hexdigest()

# ---------------------------------------------------------------------------
# Text extraction helpers
# ---------------------------------------------------------------------------

def extract_text_from_pdf(pdf_path: Path) -> str:
    """Extract embedded text from a PDF via PyMuPDF."""
    try:
        doc = fitz.open(pdf_path)
        return "\n".join(page.get_text() for page in doc).strip()
    except Exception as exc:  # noqa: BLE001
        print(f"⚠️  Error reading PDF {pdf_path.name}: {exc}")
        return ""

def extract_text_from_pdf_with_ocr(pdf_path: Path) -> str:
    """OCR fallback for image‑based PDFs."""
    try:
        doc = fitz.open(pdf_path)
        ocr_text: List[str] = []
        for page in doc:
            pix = page.get_pixmap(dpi=300)
            img = Image.open(io.BytesIO(pix.tobytes("png")))
            ocr_text.append(pytesseract.image_to_string(img))
        return "\n".join(ocr_text).strip()
    except Exception as exc:  # noqa: BLE001
        print(f"⚠️  OCR failed on {pdf_path.name}: {exc}")
        return ""

def extract_text_from_epub(epub_path: Path) -> str:
    """Extract raw text from an EPUB ebook."""
    try:
        book = epub.read_epub(str(epub_path))
    except Exception as exc:  # noqa: BLE001
        print(f"⚠️  Failed to load EPUB {epub_path.name}: {exc}")
        return ""

    parts: List[str] = []
    for item in book.get_items():
        if item.get_type() == ITEM_DOCUMENT:
            soup = BeautifulSoup(item.get_content(), "html.parser")
            txt = soup.get_text(separator=" ", strip=True)
            if txt:
                parts.append(txt)
    return "\n\n".join(parts).strip()

def read_rtf_file(path: Path) -> str:
    """Convert an RTF file to plain text using pandoc."""
    return pypandoc.convert_file(str(path), "plain")

# ---------------------------------------------------------------------------
# Library ingestion
# ---------------------------------------------------------------------------

def read_library_files(files: List[Path], *, encoding: str = "utf-8") -> Dict[Path, str]:
    """Return {Path: extracted text} for supported files."""
    library: Dict[Path, str] = {}

    for path in files:
        ext = path.suffix.lower()

        if ext == ".txt":
            try:
                library[path] = path.read_text(encoding=encoding)
            except Exception as exc:  # noqa: BLE001
                print(f"⚠️  Skipping unreadable .txt file: {path.name} ({exc})")

        elif ext == ".pdf":
            print(f"📄 Reading PDF: {path.name}")
            text = extract_text_from_pdf(path)
            if not text:
                print(f"🧠 Trying OCR for {path.name} …")
                text = extract_text_from_pdf_with_ocr(path)
            if text:
                library[path] = text
            else:
                print(f"⚠️  No text could be extracted from {path.name}")

        elif ext == ".epub":
            print(f"📚 Reading EPUB: {path.name}")
            text = extract_text_from_epub(path)
            if text:
                library[path] = text
            else:
                print(f"⚠️  No text extracted from {path.name}")

        elif ext == ".rtf":
            print(f"📄 Reading RTF: {path.name}")
            text = read_rtf_file(path)
            if text:
                library[path] = text
            else:
                print(f"⚠️  No text extracted from {path.name}")

    return library

# ---------------------------------------------------------------------------
# Metadata extraction via OpenAI function calling
# ---------------------------------------------------------------------------

BOOK_FUNC = {
    "type": "function",
    "function": {
        "name": "book_meta",
        "description": "Returns the title and author of a document.",
        "parameters": {
            "type": "object",
            "properties": {
                "book_meta": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "required": ["title", "author"],
                        "properties": {
                            "title":  {"type": "string"},
                            "author": {"type": "string"},
                        },
                    },
                }
            },
            "required": ["book_meta"],
        },
    },
}

INFO_FUNC = {
    "type": "function",
    "function": {
        "name": "book_info",
        "description": "Returns the tags and category of a document.",
        "parameters": {
            "type": "object",
            "properties": {
                "book_info": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "required": ["tags", "category"],
                        "properties": {
                            "tags":  {"type": "string"},
                            "category": {"type": "string"},
                        },
                    },
                }
            },
            "required": ["book_info"],
        },
    },
}

SYS_PROMPT_META = (
    "You are a knowledgeable librarian.\n"
    "Given a file path and the document's text, return its title and author.\n"
    "If unsure of the title, create a plausible one from the content.\n"
    "If the author is unknown, use '??'."
)

SYS_PROMPT_INFO = (
    "You are a knowledgeable librarian.\n"
    "Given a file path and the document's text, return informative tags and the category of the text.\n"
)

def _print_obj(obj: dict, i: int, params: List[str]) -> None:
    print(f"{i} —")
    for p in params:
        print(f"{p}: {obj.get(p)}")

def _parse_func_resp(resp, obj_name: str = "book_meta", params: List[str] | None = None):
    if params is None:
        params = ["title", "author"]
    args_raw = resp.choices[0].message.tool_calls[0].function.arguments
    raws = json.loads(args_raw)[obj_name]

    parsed = []
    for i, obj in enumerate(raws, 1):
        _print_obj(obj, i, params)
        parsed.append(obj)
    return parsed[0]

def get_metadata(path: Path, text: str) -> dict | None:
    """Call the LLM once to get (title, author) metadata."""
    user_msg = f"BOOK PATH:\n{path}\n\nBOOK TEXT:\n{text[:5000]}"
    resp = client.chat.completions.create(
        model=OPENAI_MODEL,
        messages=[
            {"role": "system", "content": SYS_PROMPT_META},
            {"role": "user", "content": user_msg},
        ],
        tools=[BOOK_FUNC],
        tool_choice={"type": "function", "function": {"name": "book_meta"}},
    )
    return _parse_func_resp(resp)

def get_more_info(path: Path, text: str) -> tuple | None:
    """Call the LLM once to get (title, author) metadata."""
    user_msg = f"BOOK PATH:\n{path}\n\nBOOK TEXT:\n{text[:5000]}"
    resp = client.chat.completions.create(
        model=OPENAI_MODEL,
        messages=[
            {"role": "system", "content": SYS_PROMPT_INFO},
            {"role": "user", "content": user_msg},
        ],
        tools=[INFO_FUNC],
        tool_choice={"type": "function", "function": {"name": "book_info"}},
    )
    return _parse_func_resp(resp, ["tags", "category"])

def safe_get_metadata(path: Path, text: str, retries: int = MAX_RETRIES):
    """Retry wrapper around *get_metadata* to reduce transient failures."""
    for attempt in range(1, retries + 1):
        try:
            return get_metadata(path, text)
        except (OpenAIError, Exception) as exc:  # noqa: BLE001
            print(f"[{attempt}/{retries}] {path.name}: {exc}")
            if attempt == retries:
                return None
            time.sleep(RETRY_DELAY)

# ---------------------------------------------------------------------------
# SQLite helpers
# ---------------------------------------------------------------------------

def ensure_tables(cx: sqlite3.Connection) -> None:
    """Create *docs* (FTS5) and *info* tables if they don't yet exist."""
    cx.execute(
        """
        CREATE VIRTUAL TABLE IF NOT EXISTS docs
        USING fts5(path UNINDEXED, text);
        """
    )
    cx.execute(
        """
        CREATE TABLE IF NOT EXISTS info (
            path   TEXT PRIMARY KEY,
            title  TEXT,
            author TEXT,
            size   INTEGER,
            hash   TEXT,
            tags TEXT,
            category TEXT
        );
        """
    )
    cx.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_info_hash ON info(hash);")

def ingest(library: Dict[Path, str], metadata: Dict[Path, dict], db_file: Path) -> None:
    """Insert document texts and metadata into *db_file*."""
    cx = sqlite3.connect(db_file)
    cx.execute("PRAGMA journal_mode=WAL")
    ensure_tables(cx)

    with cx:
        for path, text in library.items():
            cx.execute(
                "INSERT OR REPLACE INTO docs(path, text) VALUES (?,?)",
                (str(path), text),
            )
        for path, md in metadata.items():
            cx.execute(
                """
                INSERT OR REPLACE INTO info(path, title, author, size, hash)
                VALUES (?,?,?,?,?)
                """,
                (
                    str(path),
                    md.get("title"),
                    md.get("author"),
                    md["size"],
                    md["hash"],
                    md["tags"],
                    md["category"]
                ),
            )

    total_docs = cx.execute("SELECT COUNT(*) FROM docs").fetchone()[0]
    cx.close()
    print(f"✓ Ingested {len(library)} documents ({total_docs} total) → {db_file}")

# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    DB_FILE = Path("library_temp.sqlite")
    LIBRARY_DIR = Path("/Users/b/fantasiagenesis/crayon/library/dressmaking")

    all_files = [p for p in LIBRARY_DIR.rglob("*") if p.is_file()]

    # Gather already‑ingested hashes
    cx = sqlite3.connect(DB_FILE)
    try:
        existing_hashes = {h for (h,) in cx.execute("SELECT hash FROM info WHERE hash IS NOT NULL")}
    except sqlite3.OperationalError:
        existing_hashes = set()
    cx.close()

    # Filter out existing files
    todo: List[Tuple[Path, str]] = []
    for p in all_files:
        h = sha256(p)
        if h not in existing_hashes:
            todo.append((p, h))
        else:
            print(f"⏭️  Skipping {p.name} — already in database")

    # Extract text
    library = read_library_files([p for p, _ in todo])

    # Extract metadata via LLM
    metadata: Dict[Path, dict] = {}
    failures: List[Path] = []

    for path, text in library.items():
        md = safe_get_metadata(path, text)
        if md is None:
            failures.append(path)
            md = {"title": None, "author": None}
        md["size"] = path.stat().st_size
        md["hash"] = sha256(path)

        # get tags, category
        try:
            info = get_more_info(path, text)
            md["tags"], md["category"] = info["tags"], info["category"]
        except:
            md["tags"] = model.extract_keywords(text, keyphrase_ngram_range=(1, 2), stop_words='english', top_n=5)

        metadata[path] = md

    ingest(library, metadata, DB_FILE)

    if failures:
        print("⚠️  Metadata extraction failed for:")
        for p in failures:
            print(f"  - {p}")
