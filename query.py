#!/usr/bin/env python3
"""Lightweight CLI to explore your `library.sqlite` database.

Features
--------
* Full‑text search over document text (FTS5)
* Optional filters on author, title, or path
* Pretty snippets with ⟦highlight⟧ around matches
* Prints up to `--limit` rows (default 10)

Examples
--------
$ python query.py -q "oxidative phosphorylation"
$ python query.py -q photosynthesis --author Weinberg --limit 5
$ python query.py --db /path/to/db.sqlite -q "quantum" --raw    # dump full text
"""

import argparse
import sqlite3
import sys
from pathlib import Path
from textwrap import shorten

SNIPPET_HIGHLIGHT = (
    "⟦",  # start
    "⟧",  # end
    "…"   # ellipsis
)


def open_db(db_path: Path) -> sqlite3.Connection:
    if not db_path.is_file():
        sys.exit(f"❌ Database not found: {db_path}")
    cx = sqlite3.connect(db_path)
    cx.row_factory = sqlite3.Row
    return cx


def build_query(filters: dict[str, str], limit: int, want_raw: bool) -> tuple[str, list]:
    where_clauses: list[str] = []
    params: list[str | int] = []

    # FTS5 full‑text search
    if text := filters.pop("text", None):
        where_clauses.append("docs MATCH ?")
        params.append(text)

    # Column filters (author, title, path)
    for col, value in list(filters.items()):
        if value:
            where_clauses.append(f"{col} LIKE ?")
            params.append(f"%{value}%")

    where_sql = "WHERE " + " AND ".join(where_clauses) if where_clauses else ""

    select_cols = (
        "docs.path, info.title, info.author, "
        "snippet(docs, 1, ?, ?, ?, 64) AS snippet" if not want_raw else "docs.text"
    )

    sql = (
        f"SELECT {select_cols} \n"
        "FROM docs LEFT JOIN info USING(path) \n"
        f"{where_sql} \n"
        "LIMIT ?"
    )

    params.extend(SNIPPET_HIGHLIGHT if not want_raw else [])
    params.append(limit)
    return sql, params


def main():
    ap = argparse.ArgumentParser(description="Query the library SQLite database.")
    ap.add_argument("-q", "--query", dest="text", help="full‑text search string (FTS5)")
    ap.add_argument("--author", help="filter author LIKE %%author%%")
    ap.add_argument("--title", help="filter title  LIKE %%title%%")
    ap.add_argument("--path", help="filter path   LIKE %%path%%")
    ap.add_argument("--db", default="library.sqlite", help="path to SQLite db (default: %(default)s)")
    ap.add_argument("--limit", type=int, default=10, help="max rows to return (default: %(default)s)")
    ap.add_argument("--raw", action="store_true", help="print full text column instead of snippet")
    args = ap.parse_args()

    db_path = Path(args.db).expanduser().resolve()
    cx = open_db(db_path)

    filters = {
        "text": args.text or "*",  # FTS5 requires non‑empty; '*' matches all
        "author": args.author,
        "title": args.title,
        "path": args.path,
    }

    sql, params = build_query(filters, args.limit, args.raw)


    try:
        rows = cx.execute(sql, params)
    except sqlite3.OperationalError as e:
        sys.exit(f"SQL error: {e}\nQuery was:\n{sql}\nParams: {params}")

    for i, row in enumerate(rows, 1):
        if args.raw:
            header = f"[{i}] {row['path']}\nTITLE : {row['title'] or '?'}\nAUTHOR: {row['author'] or '?'}\n"
            print(header + "-" * len(header))
            print(row['text'])
            print("\n" + "=" * 80 + "\n")
        else:
            title = row["title"] or "?"
            author = row["author"] or "?"
            snippet = shorten(row["snippet"], width=160, placeholder="…")
            print(f"[{i}] {title} — {author}\n{snippet}\n{row['path']}\n")

    cx.close()


if __name__ == "__main__":
    main()
