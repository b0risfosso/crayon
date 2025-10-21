#!/usr/bin/env python3
"""
ingest_tester.py — scan a directory and test whether PDFs, EPUBs, and text files
can be opened and minimally read by your ingest pipeline.

- PDFs: attempts to open with pypdf and extract text from the first few pages.
- EPUBs: opens with ebooklib and extracts concatenated text of the first items.
- Text: opens using utf-8 (errors="replace") and reads first N bytes.

Outputs:
- A JSONL file with one record per file: status, error (if any), and basic stats.
- A CSV summary with per-type pass/fail counts.
- Exit code is nonzero if any file fails.

Usage:
    python ingest_tester.py --root /var/www/site/data/source --out ./ingest_test_out
    python ingest_tester.py --root /some/path --workers 4 --sample-pages 3

Requirements:
    pypdf>=4.2.0
    ebooklib>=0.18
    beautifulsoup4>=4.12.0
"""
import argparse
import csv
import hashlib
import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path
from typing import Optional, Tuple

# ---- Config ----
TEXT_EXTS = {".txt", ".text", ".md", ".rst", ".tex", ".csv", ".tsv", ".log"}
MIN_CHARS_PDF = 100     # stricter success criterion for PDFs
MIN_CHARS_EPUB = 100    # stricter success criterion for EPUBs


# ---- Helpers ----
def sha256_file(path: Path, chunk_size: int = 1024 * 1024) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while True:
            chunk = f.read(chunk_size)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def now_iso() -> str:
    return datetime.utcnow().isoformat() + "Z"


@dataclass
class TestResult:
    path: str
    relpath: str
    type: str  # pdf | epub | text | other
    status: str  # ok | skipped | fail
    bytes: int
    mtime: str
    sha256: Optional[str]
    sample_len: int
    pages_scanned: Optional[int]  # for epub this will be "items scanned"
    error: Optional[str]


# lazy imports inside handlers
def test_pdf(path: Path, sample_pages: int) -> Tuple[str, int]:
    from pypdf import PdfReader  # type: ignore
    reader = PdfReader(str(path))
    n_pages = min(sample_pages, len(reader.pages))
    sample_text_parts = []
    for i in range(n_pages):
        try:
            sample_text_parts.append(reader.pages[i].extract_text() or "")
        except Exception:
            # continue; leave empty string for this page
            sample_text_parts.append("")
    sample_text = "\n".join(sample_text_parts)
    return sample_text, n_pages


def test_epub(path: Path, max_items: int) -> Tuple[str, int]:
    from ebooklib import epub  # type: ignore
    from bs4 import BeautifulSoup  # type: ignore
    book = epub.read_epub(str(path))
    items = [i for i in book.get_items() if i.get_type() == 9]  # 9 == DOCUMENT
    n = min(len(items), max_items)
    sample_text_parts = []
    for i in range(n):
        content = items[i].get_content()
        soup = BeautifulSoup(content, "html.parser")
        sample_text_parts.append(soup.get_text(" ", strip=True))
    sample_text = "\n".join(sample_text_parts)
    return sample_text, n


def test_text(path: Path, sample_bytes: int) -> Tuple[str, int]:
    with path.open("r", encoding="utf-8", errors="replace") as f:
        sample = f.read(sample_bytes)
    return sample, len(sample)


def detect_type(path: Path) -> str:
    ext = path.suffix.lower()
    if ext == ".pdf":
        return "pdf"
    if ext == ".epub":
        return "epub"
    if ext in TEXT_EXTS:
        return "text"
    return "other"


def process_one(
    path: Path,
    root: Path,
    sample_pages: int,
    epub_items: int,
    text_bytes: int,
    compute_hash: bool,
) -> TestResult:
    stat = path.stat()
    ftype = detect_type(path)

    # default result
    res = TestResult(
        path=str(path),
        relpath=str(path.relative_to(root)),
        type=ftype,
        status="skipped",
        bytes=stat.st_size,
        mtime=datetime.utcfromtimestamp(stat.st_mtime).isoformat() + "Z",
        sha256=None,
        sample_len=0,
        pages_scanned=None,
        error=None,
    )

    try:
        if ftype == "pdf":
            sample, n_pages = test_pdf(path, sample_pages)
            res.sample_len = len(sample)
            res.pages_scanned = n_pages
            # PRINT: number of characters read
            print(f"[{now_iso()}] READ pdf {res.relpath} chars={res.sample_len}")
            # stricter success criterion
            res.status = "ok" if res.sample_len > MIN_CHARS_PDF else "fail"

        elif ftype == "epub":
            sample, n_items = test_epub(path, epub_items)
            res.sample_len = len(sample)
            res.pages_scanned = n_items
            # PRINT: number of characters read
            print(f"[{now_iso()}] READ epub {res.relpath} chars={res.sample_len}")
            # stricter success criterion
            res.status = "ok" if res.sample_len > MIN_CHARS_EPUB else "fail"

        elif ftype == "text":
            sample, n_read = test_text(path, text_bytes)
            res.sample_len = n_read
            # PRINT: number of characters read (n_read == chars from utf-8 decode)
            print(f"[{now_iso()}] READ text {res.relpath} chars={res.sample_len}")
            # keep original criterion for text: any readable content counts
            res.status = "ok" if n_read > 0 else "fail"

        else:
            res.status = "skipped"
            res.error = "Unsupported extension"

    except Exception as e:
        res.status = "fail"
        res.error = f"{type(e).__name__}: {e}"

    if compute_hash:
        try:
            res.sha256 = sha256_file(path)
        except Exception as e:
            if res.status == "ok":
                res.status = "fail"
            res.error = f"HashError {type(e).__name__}: {e}"

    return res


def walk_files(root: Path):
    for dirpath, _dirnames, filenames in os.walk(root):
        for name in filenames:
            yield Path(dirpath) / name


def main():
    ap = argparse.ArgumentParser(description="Test ingestability of PDFs, EPUBs, and text files.")
    ap.add_argument("--root", required=True, type=Path, help="Root directory to scan")
    ap.add_argument("--out", type=Path, default=Path("./ingest_test_out"), help="Output directory for reports")
    ap.add_argument("--workers", type=int, default=4, help="Concurrent worker threads")
    ap.add_argument("--sample-pages", type=int, default=2, help="PDF pages to sample per file")
    ap.add_argument("--epub-items", type=int, default=3, help="EPUB document items to sample")
    ap.add_argument("--text-bytes", type=int, default=65536, help="Text bytes to read from text files")
    ap.add_argument("--hash", action="store_true", help="Compute SHA256 for each file (slower)")
    ap.add_argument("--fail-on-skip", action="store_true", help="Treat skipped (unsupported) as failures for exit code")
    ap.add_argument("--extensions", nargs="*", help="Override extensions (e.g. .pdf .epub .txt .md)")
    args = ap.parse_args()

    root = args.root
    out = args.out
    out.mkdir(parents=True, exist_ok=True)

    if args.extensions:
        global TEXT_EXTS
        TEXT_EXTS = set([e.lower() for e in args.extensions if e.lower() not in {".pdf", ".epub"}]) or TEXT_EXTS

    ts = datetime.utcnow().strftime("%Y%m%d-%H%M%S")
    jsonl_path = out / f"ingest_results_{ts}.jsonl"
    csv_path = out / f"ingest_summary_{ts}.csv"

    paths = list(walk_files(root))
    total = len(paths)
    print(f"[{now_iso()}] Scanning {total} files under {root} ...")

    results = []
    fails = 0
    skips = 0

    from concurrent.futures import ThreadPoolExecutor, as_completed
    with ThreadPoolExecutor(max_workers=args.workers) as ex, jsonl_path.open("w", encoding="utf-8") as jf:
        futs = {
            ex.submit(process_one, p, root, args.sample_pages, args.epub_items, args.text_bytes, args.hash): p
            for p in paths
        }
        for fut in as_completed(futs):
            res = fut.result()
            results.append(res)
            jf.write(json.dumps(asdict(res), ensure_ascii=False) + "\n")
            if res.status == "fail":
                fails += 1
            elif res.status == "skipped":
                skips += 1

    # Write summary CSV
    by_type = {}
    for r in results:
        by_type.setdefault(r.type, {"ok": 0, "fail": 0, "skipped": 0, "count": 0})
        by_type[r.type][r.status] += 1
        by_type[r.type]["count"] += 1

    with csv_path.open("w", newline="", encoding="utf-8") as cf:
        w = csv.writer(cf)
        w.writerow(["type", "count", "ok", "fail", "skipped"])
        for t, d in sorted(by_type.items()):
            w.writerow([t, d["count"], d["ok"], d["fail"], d["skipped"]])
        total_ok = sum(d["ok"] for d in by_type.values())
        total_fail = sum(d["fail"] for d in by_type.values())
        total_skipped = sum(d["skipped"] for d in by_type.values())
        w.writerow([])
        w.writerow(["TOTAL", total_ok + total_fail + total_skipped, total_ok, total_fail, total_skipped])

    print(f"[{now_iso()}] Done. Results -> {jsonl_path}  Summary -> {csv_path}")
    exit_fail = fails + (skips if args.fail_on_skip else 0)
    sys.exit(1 if exit_fail > 0 else 0)


if __name__ == "__main__":
    main()
