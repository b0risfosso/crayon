import sqlite3
from textwrap import shorten   # handy for cheap snippets

def search_library(
    db_path: str,
    keywords: list[str] | None = None,
    max_results: int = 100,
    match_all: bool = False,
) -> list[dict]:
    """
    Return rows from the library.

    If *keywords* is falsy, the function returns *max_results* rows
    without filtering.  Otherwise it performs a full‑text search on the
    FTS5 table ``docs``.
    """
    with sqlite3.connect(db_path) as cx:
        cx.row_factory = sqlite3.Row

        # ──────────────────────────────────────────────────────────
        # Case 1 ― ordinary keyword search
        # ──────────────────────────────────────────────────────────
        if keywords:
            quoted = [f'"{kw}"' for kw in keywords]
            fts_query = (' AND ' if match_all else ' OR ').join(quoted)

            rows = cx.execute(
                """
                SELECT docs.path,
                       info.title,
                       docs.text,
                       snippet(docs, 1, '<<<', '>>>', ' … ', 40) AS snippet
                FROM docs
                LEFT JOIN info USING(path)
                WHERE docs MATCH ?
                LIMIT ?
                """,
                (fts_query, max_results),
            ).fetchall()

        # ──────────────────────────────────────────────────────────
        # Case 2 ― “return everything”
        # ──────────────────────────────────────────────────────────
        else:
            rows = cx.execute(
                """
                SELECT docs.path,
                       info.title,
                       docs.text,
                       substr(docs.text, 1, 400) AS snippet   -- cheap preview
                FROM docs
                LEFT JOIN info USING(path)
                LIMIT ?
                """,
                (max_results,),
            ).fetchall()

        return [dict(r) for r in rows]
