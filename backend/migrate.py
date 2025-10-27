import sqlite3
from pathlib import Path

base = Path("/var/www/site/data/")  # adjust if needed
jid_db = base / "jid.db"
fantasia_db = base / "fantasia_cores.db"


src = sqlite3.connect(jid_db)
src.row_factory = sqlite3.Row
dst = sqlite3.connect(fantasia_db)
dst.row_factory = sqlite3.Row

try:
    # copy fantasia_cores
    rows = src.execute("SELECT id, title, description, rationale, vision, created_at FROM fantasia_cores").fetchall()
    for r in rows:
        dst.execute("""
            INSERT OR IGNORE INTO fantasia_cores (id, title, description, rationale, vision, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (r["id"], r["title"], r["description"], r["rationale"], r["vision"], r["created_at"]))

    # copy fantasia_domain
    rows = src.execute("""
        SELECT id, core_id, name, description, group_title, provider, created_at
        FROM fantasia_domain
    """).fetchall()
    for r in rows:
        dst.execute("""
            INSERT OR IGNORE INTO fantasia_domain (id, core_id, name, description, group_title, provider, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (r["id"], r["core_id"], r["name"], r["description"], r["group_title"], r["provider"], r["created_at"]))
    # Note: we explicitly include `id` here to preserve identities. If you prefer auto-increment semantics going forward, you can skip inserting the id and let SQLite allocate, but then your foreign keys from dimensions → domains will break unless you remap. Easiest path is to preserve ids now.

    # copy fantasia_dimension
    rows = src.execute("""
        SELECT id, domain_id, name, description, provider, created_at
        FROM fantasia_dimension
    """).fetchall()
    for r in rows:
        dst.execute("""
            INSERT OR IGNORE INTO fantasia_dimension (id, domain_id, name, description, provider, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (r["id"], r["domain_id"], r["name"], r["description"], r["provider"], r["created_at"]))

    # copy fantasia_thesis
    rows = src.execute("""
        SELECT id, dimension_id, text, author_email, provider, created_at
        FROM fantasia_thesis
    """).fetchall()
    for r in rows:
        dst.execute("""
            INSERT OR IGNORE INTO fantasia_thesis (id, dimension_id, text, author_email, provider, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (r["id"], r["dimension_id"], r["text"], r["author_email"], r["provider"], r["created_at"]))
    dst.commit()
finally:
    src.close()
    dst.close()
