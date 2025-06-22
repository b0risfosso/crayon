# /Users/b/fantasiagenesis/crayon/library/978-981-16-5214-1.pdf
# /Users/b/fantasiagenesis/crayon/library/Electricity_and_Magnetism_-_Purcell-3rd Edition-2.pdf
# /Users/b/fantasiagenesis/crayon/library/The Birds of East Africa- Kenya, Tanzania, Uganda, Rwanda, -- Terry Stevenson, John Fanshawe -- Princeton Field Guides, Princeton, New Jersey, Soho -- 9780691126654 -- 65438af214a8415b96bb9f23f49bbcbc -- Anna’s Archive.pdf
# /Users/b/fantasiagenesis/crayon/library/hogan_chapter_7_handbook_on_the_economics_of_electricity_112619r.pdf
# /Users/b/fantasiagenesis/crayon/library/watson-levin-2023-the-collective-intelligence-of-evolution-and-development.pdf

import sqlite3
from pathlib import Path

DB_FILE = "library.sqlite"
TARGET_PATH = "/Users/b/fantasiagenesis/crayon/library/978-981-16-5214-1.pdf"
N_CHARS = 20_000                    # how many characters you want

with sqlite3.connect(DB_FILE) as cx:
    cx.row_factory = lambda cur, row: row[0]      # return just the TEXT column
    text = cx.execute(
        "SELECT text FROM docs WHERE path = ?;",
        (TARGET_PATH,)
    ).fetchone()

if text is None:
    print("No document found for that path.")
else:
    snippet = text[:N_CHARS]
    print(snippet)                  # or: Path("snippet.txt").write_text(snippet)
