
# read the entire library
from pathlib import Path
from typing import Dict, List, Tuple, Union
from pathlib import Path
import fitz              # PyMuPDF
import pytesseract
from PIL import Image
import io
from ebooklib import epub, ITEM_DOCUMENT
from bs4 import BeautifulSoup
from pathlib import Path
import pypandoc
import hashlib, pathlib
import sqlite3
import json
from dotenv import load_dotenv
from openai import OpenAI
from pydantic import BaseModel, Field, ValidationError
import os
import sqlite3, json, zlib
from pathlib import Path
import time
from openai import OpenAIError



from PIL import Image

Image.MAX_IMAGE_PIXELS = None  # disables the warning


def print_obj(obj, i=1, params=["title", "author"]):
    print(f"{i} - ")
    for param in params:
        print(f"{param}: {obj[param]}")

def parse_func_resp(resp, obj_name="book_meta", params=["title", "author"]):
    args_raw = resp.choices[0].message.tool_calls[0].function.arguments
    raws = json.loads(args_raw)[obj_name]

    obj_lst = []
    for i, obj in enumerate(raws, 1):
        print_obj(obj, i, params)
        obj_lst.append(obj)
    
    return obj_lst


def book_meta_as_string(obj):
    return f"BOOK:\n{book["title"]}\n\nAUTHOR:\n{book["description"]}\n\nSIZE:\n{book["size"]}\n\nBOOK:\n{book["hask"]}"


def list_files_in_directory(
    dir_path: str | Path,
    *,
    recursive: bool = False
) -> Dict[Path, None]:
    """
    Return a dictionary {file_path: None} for all files in `dir_path`.
    Initially all values are None.
    """
    base = Path(dir_path).expanduser().resolve()
    if not base.is_dir():
        raise NotADirectoryError(f"{base} is not a directory.")

    pattern = "**/*" if recursive else "*"
    files = (p for p in base.glob(pattern) if p.is_file())
    return {p: None for p in files}

def extract_text_from_pdf(pdf_path: Path) -> str:
    """Try to extract text using PyMuPDF. Return empty string if no text found."""
    text = ""
    try:
        doc = fitz.open(pdf_path)
        text = "\n".join(page.get_text() for page in doc)
    except Exception as e:
        print(f"⚠️ Error reading PDF {pdf_path.name}: {e}")
    return text.strip()

def extract_text_from_pdf_with_ocr(pdf_path: Path) -> str:
    """Fallback: use OCR if no text was extractable from PDF."""
    try:
        doc = fitz.open(pdf_path)
        ocr_text = []
        for page in doc:
            pix = page.get_pixmap(dpi=300)
            img = Image.open(io.BytesIO(pix.tobytes("png")))
            text = pytesseract.image_to_string(img)
            ocr_text.append(text)
        return "\n".join(ocr_text).strip()
    except Exception as e:
        print(f"⚠️ OCR failed on {pdf_path.name}: {e}")
        return ""

def extract_text_from_epub(epub_path: Path) -> str:
    try:
        book = epub.read_epub(str(epub_path))
    except Exception as e:
        print(f"⚠️ Failed to load EPUB {epub_path.name}: {e}")
        return ""

    texts = []

    for item in book.get_items():
        if item.get_type() == ITEM_DOCUMENT:  # ← use the imported constant
            soup = BeautifulSoup(item.get_content(), "html.parser")
            text = soup.get_text(separator=" ", strip=True)
            if text:
                texts.append(text)

    return "\n\n".join(texts).strip()

def read_rtf_file(path: Path) -> str:
    return pypandoc.convert_file(str(path), 'plain')
    
def read_library_files(
    files: list[Path],
    *,
    encoding: str = "utf-8"
) -> Dict[Path, str]:
    """
    Read content from .txt and .pdf files.
    PDF fallback uses OCR if no extractable text.
    """
    library: Dict[Path, str] = {}

    for path in files:
        if path.suffix.lower() == ".txt":
            try:
                library[path] = path.read_text(encoding=encoding)
            except Exception as e:
                print(f"⚠️ Skipping unreadable .txt file: {path.name} ({e})")

        elif path.suffix.lower() == ".pdf":
            print(f"📄 Reading PDF: {path.name}")
            text = extract_text_from_pdf(path)
            if not text:
                print(f"🧠 Trying OCR for {path.name} …")
                text = extract_text_from_pdf_with_ocr(path)
            if text:
                library[path] = text
            else:
                print(f"⚠️ No text could be extracted from {path.name}")

        elif path.suffix.lower() == ".epub":
            print(f"📚 Reading EPUB: {path.name}")
            text = extract_text_from_epub(path)
            if text:
                library[path] = text
            else:
                print(f"⚠️ No text extracted from {path.name}")

        elif path.suffix.lower() == ".rtf":
            print(f"📚 Reading EPUB: {path.name}")
            text = read_rtf_file(path)
            if text:
                library[path] = text
            else:
                print(f"⚠️ No text extracted from {path.name}")

    return library

def file_hash(path: pathlib.Path, algo="sha256", chunk=1<<20) -> str:
    h = hashlib.new(algo)
    with path.open('rb') as f:
        for block in iter(lambda: f.read(chunk), b''):
            h.update(block)
    return h.hexdigest()

def sha256(path: Path, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(chunk), b""):
            h.update(block)
    return h.hexdigest()


def build_db(library: Dict[Path, str], db_file: str | Path = "library.sqlite"):
    db_file = Path(db_file).expanduser().resolve()

    # connect (FTS5 is built-in in SQLite ≥ 3.9; Python 3.11 ships 3.41+)
    cx = sqlite3.connect(db_file)
    cx.execute("PRAGMA journal_mode=WAL")        # safer concurrent reads
    cx.execute("PRAGMA temp_store=2")            # temp tables in RAM

    # create FTS5 virtual table
    cx.execute(
        """
        CREATE VIRTUAL TABLE IF NOT EXISTS docs
        USING fts5(
            path UNINDEXED,        -- full path on disk
            text,                  -- document text (indexed)
            size  UNINDEXED,       -- bytes on disk
            hash  UNINDEXED        -- sha256 of file
        );
        """
    )

    # bulk ingest
    with cx:                             # autocommit block
        for p, txt in library.items():
            meta_size = p.stat().st_size
            meta_hash = sha256(p)
            cx.execute(
                "INSERT OR REPLACE INTO docs VALUES (?,?,?,?)",
                (str(p), txt, meta_size, meta_hash),
            )

    cx.close()
    print(f"✓ Wrote {len(library)} documents → {db_file}")


def search(db_file: str | Path, query: str, limit: int = 10):
    cx = sqlite3.connect(db_file)
    cx.row_factory = sqlite3.Row
    sql = "SELECT path, snippet(docs, 1, '⟦', '⟧', '…', 64) AS snippet FROM docs WHERE docs MATCH ? LIMIT ?"
    for row in cx.execute(sql, (query, limit)):
        print(f"{row['path']}\n{row['snippet']}\n")
    cx.close()

def get_metadata(path, text):
    resp = client.chat.completions.create( # could fail. need error catching.
            model=OPENAI_MODEL,
            messages=[
                {"role": "system", "content": sys_msg_meta},
                {"role": "user",   "content": usr_msg_meta},
            ],
            
            tools=[BOOK_FUNC],
            tool_choice={
                "type": "function",
                "function": {       # ← required nesting
                    "name": "book_meta"
                }
            }
        )
    metadata = parse_func_resp(resp)[0]                   # ➜ list of artifacts
    #meta[path] = metadata

    return metadata


MAX_RETRIES = 5
RETRY_DELAY = 5   # seconds

def safe_get_metadata(path, text, retries=MAX_RETRIES):
    for attempt in range(1, retries + 1):
        try:
            return get_metadata(path, text)     # ← your wrapper around the LLM call
        except (OpenAIError, Exception) as e:
            print(f"[{attempt}/{retries}] {path.name}: {e}")
            if attempt == retries:
                return None
            time.sleep(RETRY_DELAY)


# artifact structure
# ── LLM function spec (updated) ─────────────────────────────────────────--
BOOK_FUNC = {
    "type": "function",
    "function": {
        "name": "book_meta",
        "description": "Returns the title and author of a book.",
        "parameters": {
            "type": "object",
            "properties": {
                "book_meta": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "required": [
                            "title", "author",
                        ],
                        "properties": {
                            "title":  {"type": "string"},
                            "author": {"type": "string"},
                        }
                    }
                }
            },
            "required": ["book_meta"]
        }
    }
}


SYS_PROMPT_META = (
    "You are a knowledgable librarian.\n"
    "Using the following path name and book/article text return the title of the text and its author.\n"
    "If you are unsure of the title, create one using the context of the informatoin in the text.\n"
    "If you are unsure of the author, utilize '??'."
    )


load_dotenv()
client = OpenAI()
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o")



# 3. Check results


if __name__ == "__main__":
    DB_FILE = "library.sqlite"
    cx = sqlite3.connect(DB_FILE)

    # assume you already have `library = {Path(...): "text", ...}`
    library_dir = "/Users/b/fantasiagenesis/crayon/library/"
    #files = list(Path(library_dir).rglob("*"))  # recursive
    all_files   = (p for p in Path(library_dir).rglob("*") if p.is_file())

    try:
        existing_hashes = {
            h for (h,) in cx.execute(
                "SELECT hash FROM info WHERE hash IS NOT NULL"
            )
        }
    except Exception as e:
        print(f"⚠️ Failed to load existing hashes: {e}")
        existing_hashes = set()

    todo: List[Tuple[Path, str]] = []
    for p in all_files:
        h = sha256(p)
        if h in existing_hashes:
            print(f"⏭️  Skipping {p.name} — already in database (hash: {h[:12]}...)")
            continue
        todo.append((p, h))

    library = read_library_files([p for p, _ in todo])

    meta: Dict[Path, dict] = {}

    sys_msg_meta = SYS_PROMPT_META

    errors = []

    for path, text in list(library.items()):
        usr_msg_meta = f"BOOK PATH:\n{path}\n\nBOOK TEXT:\n{text[:5000]}"
        md = safe_get_metadata(path, text)
        if md is None:
            errors.append(path)
            md = {"title": None, "author": None}     # placeholders
            continue
        
        md["size"] = path.stat().st_size
        md["hash"] = sha256(path)
        meta[path] = md

    

    #build_db(library, "library.sqlite")          # ➊ create / update DB
    #search("library.sqlite", "oxidative phosphorylation")  # ➋ quick query

    cx.execute("PRAGMA journal_mode=WAL")

    # ➊ FTS5 table for *text* (already created earlier)
    cx.execute("""
    CREATE VIRTUAL TABLE IF NOT EXISTS docs
    USING fts5(path UNINDEXED, text);
    """)

    # ➋ Ordinary table for *metadata*
    cx.execute("""
    CREATE TABLE IF NOT EXISTS info (
        path   TEXT PRIMARY KEY,
        title  TEXT,
        author TEXT,
        size   INTEGER,
        hash   TEXT
    );
    """)
    cx.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_info_hash ON info(hash);")

    with cx:
        # docs table (fts5) – unchanged
        for path, text in library.items():
            cx.execute(
                "INSERT OR REPLACE INTO docs(path, text) VALUES (?,?)",
                (str(path), text)
            )

        # info table
        for path, md in meta.items():
            cx.execute(
                """INSERT OR REPLACE INTO info
                (path, title, author, size, hash)
                VALUES (?,?,?,?,?)""",
                (
                    str(path),
                    md.get("title"),
                    md.get("author"),
                    md["size"],
                    md["hash"]
                )
            )

    c = cx.execute('SELECT COUNT(*) FROM docs').fetchone()[0]
    print('rows in docs:', c)

    cx.close()
    print(f"✓ Ingested {len(library)} documents, {len(meta)} metadata rows → {DB_FILE}")

    
