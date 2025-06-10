"""pdf_ingest.py ──────────────────────────────────────────────────────────
Batch-aware PDF → Source loader for the Engineering Knowledge-Graph.

Key changes (v0.2)
------------------
• **Positional argument** `path` can be a single PDF **or** a directory.  
• When given a directory the script ingests **every `*.pdf` under it (non-recursive)**.
• Common options (`--topic`, `--url`, `--cred`) apply to all files; per-file URL can be omitted.

Usage
-----
    # single file (old behaviour)
    python pdf_ingest.py paper.pdf "heart morphogenesis" --url https://...

    # whole folder
    python pdf_ingest.py ./pdfs/ "heart morphogenesis" --cred 0.6

Env vars via `.env`
-------------------
    OPENAI_API_KEY, OPENAI_MODEL, NEO4J_URI, NEO4J_USER, NEO4J_PWD

Install deps
------------
    pip install PyPDF2 python-dotenv neo4j openai pydantic

Environment:
    OPENAI_API_KEY   for summary generation
    (same Neo4j creds as other scripts via .env)

The script will:
1. Extract raw text & metadata from the PDF (using PyPDF2).
2. Derive `title` and `year` (heuristics + metadata fallback).
3. Ask GPT‑4o for a 1‑paragraph summary.
4. Build a `Source` Pydantic object.
5. Write/merge it into Neo4j with `(:Source)-[:ABOUT]->(:Topic)`.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import List
import json

import PyPDF2
from dotenv import load_dotenv
from neo4j import GraphDatabase
from openai import OpenAI
from pydantic import BaseModel, Field, ValidationError

from models import Source
from loader import load_sources

# ── 0. Config & helpers ────────────────────────────────────────────────────
load_dotenv()
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o")
client = OpenAI()

NEO4J_URI = os.getenv("NEO4J_URI", "bolt://localhost:7687")
NEO4J_USER = os.getenv("NEO4J_USER", "neo4j")
NEO4J_PWD = os.getenv("NEO4J_PWD", "testtesttes")

driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PWD))

# ── 2. PDF parsing utilities ──────────────────────────────────────────────

def extract_pdf_text(pdf_path: Path) -> str:
    reader = PyPDF2.PdfReader(str(pdf_path))
    text = "\n".join(page.extract_text() or "" for page in reader.pages)
    return text.strip()

META_FUNC = {
    "type": "function",
    "function": {
        "name": "emit_meta",
        "description": "Extract title (string) and year (int) from a raw paper text fragment",
        "parameters": {
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "year": {"type": "integer"},
            },
            "required": ["title", "year"],
        },
    }
}

SYS_PROMPT_META = (
    "You are a strict parser. From the first few pages of a scientific paper, "
    "return the *exact* paper title (no line‑breaks) and the four‑digit year "
    "of publication. Only use the provided function schema. If year is unknown, "
    "guess from the text (e.g., copyright, submission dates)."
)

def llm_extract_title_year(text: str) -> tuple[str, int | None]:
    snip = text[:4000]
    resp = client.chat.completions.create(
        model=OPENAI_MODEL,
        messages=[{"role": "system", "content": SYS_PROMPT_META}, {"role": "user", "content": snip}],
        tools=[META_FUNC],
        tool_choice="auto",
        temperature=0,
        max_tokens=100,
    )
    args_raw = resp.choices[0].message.tool_calls[0].function.arguments
    args = json.loads(args_raw)    # ← this gives you a dict
    title = args.get("title", "Untitled paper").strip()
    year = args.get("year")
    return title, year



# ── 3. LLM summary ────────────────────────────────────────────────────────

SYS_PROMPT_SUM = (
    "Summarise the following scientific paper in 5–6 sentences focusing on "
    "goal, methods, main results, and significance."
)

def llm_summary(text: str, max_tokens=200) -> str:
    resp = client.chat.completions.create(
        model=OPENAI_MODEL,
        messages=[{"role": "system", "content": SYS_PROMPT_SUM}, {"role": "user", "content": text[:4000]}],
        temperature=0,
        max_tokens=max_tokens,
    )
    return resp.choices[0].message.content.strip()


def ingest_pdf(pdf_path: Path, topic: str, default_url: str | None, cred: float):
    raw = extract_pdf_text(pdf_path)
    if not raw:
        print(f"⚠️  Skipping {pdf_path.name} — no extractable text")
        return None

    title, year = llm_extract_title_year(raw)
    summary = llm_summary(raw)

    fid = hashlib.md5(pdf_path.read_bytes()).hexdigest()[:12]
    src_id = f"pdf_{fid}"

    try:
        src = Source(
            id=src_id,
            topic=topic,
            url=default_url or "",
            type="paper",
            year=year or datetime.now().year,
            credibility=cred,
            summary=summary,
            title=title,
        )
    except ValidationError as e:
        print(f"❌ Validation failed for {pdf_path.name}: {e}")
        return None

    load_sources([src])
    print(f"✅ {pdf_path.name} → '{title[:60]}…' ({src.year})")
    return src_id


# ── 5. Main CLI logic ─────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser(description="Ingest one file or all PDFs in a folder into the KG")
    ap.add_argument("path", type=Path, help="PDF file or a directory containing PDFs")
    ap.add_argument("topic", help="Topic name (must exist or will be created)")
    ap.add_argument("--url", default=None, help="URL to store in each Source")
    ap.add_argument("--cred", type=float, default=0.5, help="Credibility 0‑1")
    args = ap.parse_args()

    paths: List[Path]
    if args.path.is_dir():
        paths = [p for p in args.path.iterdir() if p.suffix.lower() == ".pdf"]
        if not paths:
            sys.exit("No PDF files in directory.")
    elif args.path.suffix.lower() == ".pdf":
        paths = [args.path]
    else:
        sys.exit("Path must be a PDF file or directory of PDFs.")

    print(f"📂 Found {len(paths)} PDF(s) — processing…")
    success = 0
    for pdf in paths:
        if ingest_pdf(pdf, args.topic, args.url, max(0.0, min(args.cred, 1.0))):
            success += 1

    print(f"🎉 Done. {success}/{len(paths)} PDFs ingested.")

if __name__ == "__main__":
    main()