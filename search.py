import sqlite3

def search_library(
    db_path: str,
    keywords: list[str],
    max_results: int = 100,
    match_all: bool = False
) -> list[dict]:
    """
    Search the library using full-text search on the 'docs' table.

    Returns a list of dicts containing:
    - path: file path
    - title: from metadata, or None
    - text: full document text
    - snippet: excerpt showing keyword match
    """
    if not keywords:
        return []

    quoted_keywords = [f'"{kw}"' for kw in keywords]
    fts_query = (' AND ' if match_all else ' OR ').join(quoted_keywords)

    with sqlite3.connect(db_path) as cx:
        cx.row_factory = sqlite3.Row
        rows = cx.execute(
            """
            SELECT docs.path,
                   info.title,
                   docs.text,
                   snippet(docs, 1, '<<<', '>>>', '...', 40) AS snippet
            FROM docs
            LEFT JOIN info USING(path)
            WHERE docs MATCH ?
            LIMIT ?
            """,
            (fts_query, max_results)
        ).fetchall()

        return [dict(row) for row in rows]


search_res = (search_library("library.sqlite", ["meet joe black"]))
for res in search_res:
    print(res['path'])