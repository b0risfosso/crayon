#!/usr/bin/env python3
"""
ingest_tester.py — scan a directory and test whether PDFs, EPUBs, and text files
can be opened and minimally read by your ingest pipeline.

- PDFs: choose extractor (--pdf-extractor {auto,fitz,pypdf,pdfminer}) and sample first N pages.
- EPUBs: ebooklib + BeautifulSoup to extract text from first items.
- Text: utf-8 (errors="replace"), read first N bytes.
- Optional OCR fallback for PDFs: --ocr to OCR first K pages *only if* total extracted
  characters across sampled pages ≤ MIN_CHARS_PDF.

Outputs:
- JSONL: one record per file (status, error, basic stats).
- CSV summary.
- Exit is non-zero if any file fails (and optionally if any are skipped).

Requirements (pick what you need):
    pypdf>=4.2.0
    ebooklib>=0.18
    beautifulsoup4>=4.12.0
    pymupdf          # for --pdf-extractor fitz or auto
    pdfminer.six     # for --pdf-extractor pdfminer or auto
    pdf2image        # for --ocr
    pytesseract      # for --ocr
System deps for OCR:
    poppler (for pdf2image), tesseract-ocr
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
from typing import Optional, Tuple, List

# ---- Config ----
TEXT_EXTS = {".txt", ".text", ".md", ".rst", ".tex", ".csv", ".tsv", ".log"}
MIN_CHARS_PDF = 100      # success criterion for PDFs (total sample length > 100)
MIN_CHARS_EPUB = 100     # success criterion for EPUBs (total sample length > 100)

# OCR defaults
DEFAULT_OCR_PAGES = 2    # max first pages to OCR if total extracted chars are too low


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
    pages_scanned: Optional[int]  # for epub, this is "items scanned"
    error: Optional[str]


# ---- PDF extractors ----
def extract_pdf_text_fitz(path: Path, sample_pages: int) -> Tuple[str, int, List[int]]:
    import fitz  # PyMuPDF
    doc = fitz.open(str(path))
    n_pages = min(sample_pages, doc.page_count)
    parts, per_page_counts = [], []
    for i in range(n_pages):
        page = doc.load_page(i)
        txt = page.get_text("text") or ""
        parts.append(txt)
        per_page_counts.append(len(txt))
        print(f"[{now_iso()}] PAGE(pdf:fitz) {path.name} p={i+1} chars={len(txt)}")
    return "\n".join(parts), n_pages, per_page_counts


def extract_pdf_text_pypdf(path: Path, sample_pages: int) -> Tuple[str, int, List[int]]:
    from pypdf import PdfReader
    reader = PdfReader(str(path))
    n_pages = min(sample_pages, len(reader.pages))
    parts, per_page_counts = [], []
    for i in range(n_pages):
        try:
            t = reader.pages[i].extract_text() or ""
        except Exception:
            t = ""
        parts.append(t)
        per_page_counts.append(len(t))
        print(f"[{now_iso()}] PAGE(pdf:pypdf) {path.name} p={i+1} chars={len(t)}")
    return "\n".join(parts), n_pages, per_page_counts


def extract_pdf_text_pdfminer(path: Path, sample_pages: int) -> Tuple[str, int, List[int]]:
    from pdfminer.high_level import extract_pages
    from pdfminer.layout import LTTextContainer
    parts, per_page_counts, count = [], [], 0
    for page_layout in extract_pages(str(path)):
        txt_chunks = []
        for el in page_layout:
            if isinstance(el, LTTextContainer):
                txt_chunks.append(el.get_text())
        text = "".join(txt_chunks)
        count += 1
        parts.append(text)
        per_page_counts.append(len(text))
        print(f"[{now_iso()}] PAGE(pdf:pdfminer) {path.name} p={count} chars={len(text)}")
        if count >= sample_pages:
            break
    return "\n".join(parts), count, per_page_counts


def extract_pdf_text(path: Path, sample_pages: int, which: str) -> Tuple[str, int, List[int]]:
    """
    which: 'fitz' | 'pypdf' | 'pdfminer' | 'auto'
    auto tries: fitz -> pypdf -> pdfminer
    """
    if which == "fitz":
        return extract_pdf_text_fitz(path, sample_pages)
    if which == "pypdf":
        return extract_pdf_text_pypdf(path, sample_pages)
    if which == "pdfminer":
        return extract_pdf_text_pdfminer(path, sample_pages)

    # auto
    try:
        text, n, counts = extract_pdf_text_fitz(path, sample_pages)
        if len(text.strip()) >= 20:
            return text, n, counts
    except Exception:
        pass
    try:
        text, n, counts = extract_pdf_text_pypdf(path, sample_pages)
        if len(text.strip()) >= 20:
            return text, n, counts
    except Exception:
        pass
    # last resort
    return extract_pdf_text_pdfminer(path, sample_pages)


# ---- OCR fallback (file-level trigger) ----
def ocr_pdf_first_pages(path: Path, pages_to_ocr: int) -> Tuple[str, List[int]]:
    """
    OCR first 'pages_to_ocr' pages into text, returns (joined_text, per_page_char_counts).
    Requires: pdf2image (with poppler), pytesseract, and tesseract system binary.
    """
    from pdf2image import convert_from_path
    import pytesseract

    images = convert_from_path(str(path), first_page=1, last_page=pages_to_ocr)
    parts, counts = [], []
    for idx, img in enumerate(images, start=1):
        txt = pytesseract.image_to_string(img) or ""
        parts.append(txt)
        counts.append(len(txt))
        print(f"[{now_iso()}] PAGE(pdf:ocr) {path.name} p={idx} chars={len(txt)}")
    return "\n".join(parts), counts


# ---- EPUB/Text ----
def test_epub(path: Path, max_items: int) -> Tuple[str, int]:
    from ebooklib import epub
    from bs4 import BeautifulSoup
    book = epub.read_epub(str(path))
    items = [i for i in book.get_items() if i.get_type() == 9]  # DOCUMENT
    n = min(len(items), max_items)
    parts = []
    for i in range(n):
        content = items[i].get_content()
        soup = BeautifulSoup(content, "html.parser")
        parts.append(soup.get_text(" ", strip=True))
    txt = "\n".join(parts)
    return txt, n


def test_text(path: Path, sample_bytes: int) -> Tuple[str, int]:
    with path.open("r", encoding="utf-8", errors="replace") as f:
        sample = f.read(sample_bytes)
    return sample, len(sample)


# ---- Orchestration ----
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
    pdf_extractor: str,
    use_ocr: bool,
    ocr_pages: int,
) -> TestResult:
    stat = path.stat()
    ftype = detect_type(path)

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
            text, n_pages, _counts = extract_pdf_text(path, sample_pages, pdf_extractor)
            total_chars = len(text)
            # File-level OCR fallback: only if total chars across sampled pages too low
            if use_ocr and total_chars <= MIN_CHARS_PDF:
                pages_to_ocr = min(ocr_pages, max(1, n_pages))
                ocr_text, ocr_counts = ocr_pdf_first_pages(path, pages_to_ocr)
                if ocr_text:
                    text = (text + "\n" + ocr_text).strip()
                    added = len(ocr_text)
                    total_chars = len(text)
                    print(f"[{now_iso()}] OCR fallback applied to {path.name} "
                          f"(pages<= {pages_to_ocr}); added_chars={added}, total_after_ocr={total_chars}")

            res.sample_len = total_chars
            res.pages_scanned = n_pages
            print(f"[{now_iso()}] READ pdf {res.relpath} chars={total_chars}")
            res.status = "ok" if total_chars > MIN_CHARS_PDF else "fail"

        elif ftype == "epub":
            sample, n_items = test_epub(path, epub_items)
            res.sample_len = len(sample)
            res.pages_scanned = n_items
            print(f"[{now_iso()}] READ epub {res.relpath} chars={res.sample_len}")
            res.status = "ok" if res.sample_len > MIN_CHARS_EPUB else "fail"

        elif ftype == "text":
            sample, n_read = test_text(path, text_bytes)
            res.sample_len = n_read
            print(f"[{now_iso()}] READ text {res.relpath} chars={res.sample_len}")
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
    ap.add_argument("--pdf-extractor", choices=["auto", "fitz", "pypdf", "pdfminer"], default="auto",
                    help="PDF text extractor to use")

    ap.add_argument("--epub-items", type=int, default=3, help="EPUB document items to sample")
    ap.add_argument("--text-bytes", type=int, default=65536, help="Text bytes to read from text files")

    ap.add_argument("--hash", action="store_true", help="Compute SHA256 for each file (slower)")
    ap.add_argument("--fail-on-skip", action="store_true", help="Treat skipped as failures for exit code")
    ap.add_argument("--extensions", nargs="*", help="Override text extensions (e.g. .pdf .epub .txt .md)")

    # OCR flags (file-level trigger)
    ap.add_argument("--ocr", action="store_true", help="Enable OCR fallback if total PDF chars ≤ MIN_CHARS_PDF")
    ap.add_argument("--ocr-pages", type=int, default=DEFAULT_OCR_PAGES,
                    help="Max number of first pages to OCR when fallback triggers")

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

    results: List[TestResult] = []
    fails = 0
    skips = 0

    with ThreadPoolExecutor(max_workers=args.workers) as ex, jsonl_path.open("w", encoding="utf-8") as jf:
        futs = {
            ex.submit(
                process_one,
                p, root, args.sample_pages, args.epub_items, args.text_bytes,
                args.hash, args.pdf_extractor, args.ocr, args.ocr_pages
            ): p
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

    # Summary CSV
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
