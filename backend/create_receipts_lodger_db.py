#!/usr/bin/env python3
"""
Receipts Ledger DB Creator (SQLite / Postgres)

Usage (SQLite default):
  python create_receipts_ledger_db.py --sqlite receipts_ledger.db
  # or just:
  python create_receipts_ledger_db.py

Usage (Postgres):
  python create_receipts_ledger_db.py --postgres "postgresql://user:pass@host:5432/dbname"
  # requires: pip install psycopg2-binary

Schema:
  - analysis_run       : one row per UI run
  - analysis_output    : model outputs per run & analysis
  - claim              : normalized statements from outputs
  - source             : canonicalized web sources (dedup by canonical_url)
  - receipt            : links claims to sources with quotes/locators
  - audit_ledger       : (optional) append-only audit events

Idempotent: safe to re-run.
"""
import argparse
import sys
from typing import List

# ---- SQLite implementation ----
def run_sqlite(path: str) -> None:
    import sqlite3
    print(f"[sqlite] Creating/connecting DB at: {path}")
    conn = sqlite3.connect(path)
    try:
        cur = conn.cursor()
        # Enforce foreign keys
        cur.execute("PRAGMA foreign_keys = ON")

        cur.executescript("""
        CREATE TABLE IF NOT EXISTS analysis_run (
          run_id          TEXT PRIMARY KEY,
          entity_id       TEXT,
          entity_name     TEXT NOT NULL,
          input_query     TEXT NOT NULL,
          backbone_json   TEXT,
          started_at      TEXT NOT NULL,
          completed_at    TEXT
        );

        CREATE TABLE IF NOT EXISTS claim (
          claim_id        TEXT PRIMARY KEY,
          run_id          TEXT NOT NULL,
          analysis_type   TEXT NOT NULL CHECK (analysis_type IN
                            ('moat','perf_valuation','perf_revenue','fragility')),
          json_path       TEXT NOT NULL,
          metric_key      TEXT,
          claim_text      TEXT NOT NULL,
          value_num       REAL,
          unit            TEXT,
          created_at      TEXT NOT NULL,
          UNIQUE (run_id, analysis_type, json_path),
          FOREIGN KEY (run_id) REFERENCES analysis_run(run_id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS source (
          source_id       TEXT PRIMARY KEY,
          canonical_url   TEXT NOT NULL UNIQUE,
          raw_url         TEXT,
          domain          TEXT,
          title           TEXT,
          publisher       TEXT,
          published_at    TEXT,
          first_seen_at   TEXT NOT NULL,
          content_hash    TEXT,
          archive_uri     TEXT
        );

        CREATE TABLE IF NOT EXISTS receipt (
          receipt_id      TEXT PRIMARY KEY,
          claim_id        TEXT NOT NULL,
          source_id       TEXT NOT NULL,
          access_time     TEXT NOT NULL,
          locator         TEXT,
          quote_text      TEXT,
          quote_hash      TEXT,
          support_type    TEXT NOT NULL CHECK (support_type IN
                            ('direct_quote','data_point','inference','figure')),
          confidence      INTEGER NOT NULL CHECK (confidence BETWEEN 0 AND 100),
          verify_status   TEXT NOT NULL CHECK (verify_status IN
                            ('collected','auto_verified','human_verified','rejected')),
          verifier        TEXT,
          notes           TEXT,
          FOREIGN KEY (claim_id) REFERENCES claim(claim_id) ON DELETE CASCADE,
          FOREIGN KEY (source_id) REFERENCES source(source_id) ON DELETE RESTRICT
        );

        CREATE TABLE IF NOT EXISTS analysis_output (
          run_id          TEXT NOT NULL,
          analysis_type   TEXT NOT NULL CHECK (analysis_type IN
                            ('moat','perf_valuation','perf_revenue','fragility')),
          output_json     TEXT NOT NULL,
          PRIMARY KEY (run_id, analysis_type),
          FOREIGN KEY (run_id) REFERENCES analysis_run(run_id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS audit_ledger (
          seq        INTEGER PRIMARY KEY AUTOINCREMENT,
          ts         TEXT NOT NULL,
          event_type TEXT NOT NULL,
          run_id     TEXT,
          payload    TEXT NOT NULL,
          prev_hash  TEXT,
          row_hash   TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_claim_run_type   ON claim(run_id, analysis_type);
        CREATE INDEX IF NOT EXISTS idx_claim_metric_key ON claim(metric_key);
        CREATE INDEX IF NOT EXISTS idx_claim_value_num  ON claim(value_num);
        CREATE INDEX IF NOT EXISTS idx_receipt_claim    ON receipt(claim_id);
        CREATE INDEX IF NOT EXISTS idx_source_domain    ON source(domain);
        CREATE INDEX IF NOT EXISTS idx_output_run_type  ON analysis_output(run_id, analysis_type);
        """)
        conn.commit()
        print("[sqlite] Schema created successfully.")
    finally:
        conn.close()

# ---- Postgres implementation ----
def run_postgres(dsn: str) -> None:
    try:
        import psycopg2
    except Exception:
        print("[postgres] psycopg2 not installed. Run: pip install psycopg2-binary", file=sys.stderr)
        raise

    print(f"[postgres] Connecting with DSN: {dsn}")
    conn = psycopg2.connect(dsn)
    conn.autocommit = True
    try:
        with conn.cursor() as cur:
            cur.execute("""
            CREATE TABLE IF NOT EXISTS analysis_run (
              run_id          UUID PRIMARY KEY,
              entity_id       TEXT,
              entity_name     TEXT NOT NULL,
              input_query     TEXT NOT NULL,
              backbone_json   JSONB,
              started_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
              completed_at    TIMESTAMPTZ
            );

            CREATE TABLE IF NOT EXISTS claim (
              claim_id        UUID PRIMARY KEY,
              run_id          UUID NOT NULL REFERENCES analysis_run(run_id) ON DELETE CASCADE,
              analysis_type   TEXT NOT NULL CHECK (analysis_type IN
                                ('moat','perf_valuation','perf_revenue','fragility')),
              json_path       TEXT NOT NULL,
              metric_key      TEXT,
              claim_text      TEXT NOT NULL,
              value_num       DOUBLE PRECISION,
              unit            TEXT,
              created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
              UNIQUE (run_id, analysis_type, json_path)
            );

            CREATE TABLE IF NOT EXISTS source (
              source_id       UUID PRIMARY KEY,
              canonical_url   TEXT NOT NULL UNIQUE,
              raw_url         TEXT,
              domain          TEXT,
              title           TEXT,
              publisher       TEXT,
              published_at    TIMESTAMPTZ,
              first_seen_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
              content_hash    TEXT,
              archive_uri     TEXT
            );

            CREATE TABLE IF NOT EXISTS receipt (
              receipt_id      UUID PRIMARY KEY,
              claim_id        UUID NOT NULL REFERENCES claim(claim_id) ON DELETE CASCADE,
              source_id       UUID NOT NULL REFERENCES source(source_id) ON DELETE RESTRICT,
              access_time     TIMESTAMPTZ NOT NULL DEFAULT now(),
              locator         TEXT,
              quote_text      TEXT,
              quote_hash      TEXT,
              support_type    TEXT NOT NULL CHECK (support_type IN
                                ('direct_quote','data_point','inference','figure')),
              confidence      INTEGER NOT NULL CHECK (confidence BETWEEN 0 AND 100),
              verify_status   TEXT NOT NULL CHECK (verify_status IN
                                ('collected','auto_verified','human_verified','rejected')),
              verifier        TEXT,
              notes           TEXT
            );

            CREATE TABLE IF NOT EXISTS analysis_output (
              run_id          UUID NOT NULL REFERENCES analysis_run(run_id) ON DELETE CASCADE,
              analysis_type   TEXT NOT NULL CHECK (analysis_type IN
                                ('moat','perf_valuation','perf_revenue','fragility')),
              output_json     JSONB NOT NULL,
              PRIMARY KEY (run_id, analysis_type)
            );

            CREATE TABLE IF NOT EXISTS audit_ledger (
              seq        BIGSERIAL PRIMARY KEY,
              ts         TIMESTAMPTZ NOT NULL DEFAULT now(),
              event_type TEXT NOT NULL,
              run_id     UUID,
              payload    JSONB NOT NULL,
              prev_hash  TEXT,
              row_hash   TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_claim_run_type       ON claim(run_id, analysis_type);
            CREATE INDEX IF NOT EXISTS idx_claim_metric_key     ON claim(metric_key);
            CREATE INDEX IF NOT EXISTS idx_claim_value_num      ON claim(value_num);
            CREATE INDEX IF NOT EXISTS idx_receipt_claim        ON receipt(claim_id);
            CREATE INDEX IF NOT EXISTS idx_source_domain        ON source(domain);
            CREATE INDEX IF NOT EXISTS idx_output_run_type      ON analysis_output(run_id, analysis_type);
            """)
        print("[postgres] Schema created successfully.")
    finally:
        conn.close()

def main(argv: List[str] = None) -> int:
    import argparse
    p = argparse.ArgumentParser(description="Create Receipts Ledger database (SQLite or Postgres).")
    g = p.add_mutually_exclusive_group()
    g.add_argument("--sqlite", metavar="PATH", nargs="?", const="receipts_ledger.db",
                   help="SQLite database file (default: receipts_ledger.db)")
    g.add_argument("--postgres", metavar="DSN",
                   help='Postgres DSN (e.g., "postgresql://user:pass@host:5432/dbname")')
    args = p.parse_args(argv)

    if args.postgres:
        run_postgres(args.postgres)
    else:
        db_path = args.sqlite if args.sqlite else "receipts_ledger.db"
        run_sqlite(db_path)
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
