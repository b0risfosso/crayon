#!/usr/bin/env python3
# CEA_hunting_ledger.py
# stdlib-only hunting ledger (SQLite + CLI)

import argparse, sqlite3, sys, json
from datetime import datetime, timedelta
from pathlib import Path

SCHEMA_SQL = """
PRAGMA journal_mode=WAL;

CREATE TABLE IF NOT EXISTS hunts (
  hunt_id TEXT PRIMARY KEY,
  date_fired TEXT,                 -- ISO8601
  status TEXT,                     -- planned | published | closed
  target_type TEXT,                -- Investor | Founder | Journalist | Regulator | Company
  target_name TEXT,
  hunting_mode TEXT,               -- Private | Public | Cultural | Market | Institutional
  artifact_type TEXT,              -- Benchmark | Fragility Warning | Certification Badge | Meme | Index | Case Study
  claim TEXT,
  evidence_url TEXT,               -- comma-separated or JSON
  channel TEXT,                    -- Substack | Blog | LinkedIn | X/Twitter | Email | Press
  response_logged TEXT,            -- Ignored | Referenced | Disputed | Adopted
  outcome TEXT,                    -- free-form summary
  kill_confirmed INTEGER DEFAULT 0,
  notes TEXT,
  created_at TEXT DEFAULT (datetime('now')),
  updated_at TEXT DEFAULT (datetime('now')),
  next_review_date TEXT
);

CREATE TRIGGER IF NOT EXISTS hunts_updated_at
AFTER UPDATE ON hunts
FOR EACH ROW
BEGIN
  UPDATE hunts SET updated_at = datetime('now') WHERE hunt_id = OLD.hunt_id;
END;

CREATE TABLE IF NOT EXISTS responses (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  hunt_id TEXT NOT NULL,
  date TEXT,
  actor TEXT,
  response_type TEXT,              -- Reference | Dispute | Adoption | Inquiry
  content TEXT,
  url TEXT,
  FOREIGN KEY(hunt_id) REFERENCES hunts(hunt_id)
);

CREATE TABLE IF NOT EXISTS outcomes (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  hunt_id TEXT NOT NULL,
  date TEXT,
  outcome_type TEXT,               -- Earnings Miss | Guidance Cut | Funding Failure | Bankruptcy | Acquisition | Outperformance | Neutral
  details TEXT,
  measured_delta REAL,             -- e.g., price change %
  source_url TEXT,
  FOREIGN KEY(hunt_id) REFERENCES hunts(hunt_id)
);
"""

def connect(db_path):
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys=ON;")
    return conn

def init_db(db_path, seed=False):
    conn = connect(db_path)
    with conn:
        conn.executescript(SCHEMA_SQL)
        if seed:
            today = datetime.utcnow().date()
            rows = [
                (
                    f"HL-{today.strftime('%Y%m%d')}-001",
                    (today + timedelta(days=7)).isoformat(),
                    "planned",
                    "Company",
                    "CandidateCo A (example)",
                    "Public",
                    "Fragility Warning",
                    "Fragility Rating: High (C-) — liquidity + governance risk; 30–60% probability of negative event within 6–12 months.",
                    "",
                    "Substack",
                    "Ignored",
                    "",
                    0,
                    "Prep visuals; align with earnings calendar; include peer comparison chart.",
                    (today + timedelta(days=30)).isoformat(),
                ),
                (
                    f"HL-{today.strftime('%Y%m%d')}-002",
                    today.isoformat(),
                    "published",
                    "Company",
                    "CandidateCo B (example)",
                    "Public",
                    "Benchmark",
                    "Top 10 Fragile Growth Cos — CandidateCo B ranked #4 (Risk 78/100).",
                    "https://example.com/hunt/HL-seed-002",
                    "LinkedIn",
                    "Referenced",
                    "Pending",
                    0,
                    "Monitor mentions; outreach to 3 analysts; schedule follow-up thread in 14 days.",
                    (today + timedelta(days=14)).isoformat(),
                ),
            ]
            conn.executemany(
                """INSERT OR REPLACE INTO hunts
                (hunt_id, date_fired, status, target_type, target_name, hunting_mode, artifact_type, claim,
                 evidence_url, channel, response_logged, outcome, kill_confirmed, notes, next_review_date)
                 VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                rows,
            )
    conn.close()

def add_hunt(db, **kw):
    conn = connect(db)
    with conn:
        conn.execute(
            """INSERT INTO hunts
            (hunt_id, date_fired, status, target_type, target_name, hunting_mode, artifact_type, claim,
             evidence_url, channel, response_logged, outcome, kill_confirmed, notes, next_review_date)
            VALUES (:hunt_id, :date_fired, :status, :target_type, :target_name, :hunting_mode, :artifact_type, :claim,
                    :evidence_url, :channel, :response_logged, :outcome, :kill_confirmed, :notes, :next_review_date)
            """,
            kw,
        )
    conn.close()

def list_hunts(db, where=None, params=()):
    conn = connect(db)
    q = "SELECT * FROM hunts"
    if where:
        q += " WHERE " + where
    q += " ORDER BY date_fired DESC"
    cur = conn.execute(q, params)
    rows = [dict(zip([c[0] for c in cur.description], r)) for r in cur.fetchall()]
    conn.close()
    return rows

def update_hunt(db, hunt_id, **fields):
    if not fields:
        return
    sets = ", ".join(f"{k}=:{k}" for k in fields.keys())
    fields["hunt_id"] = hunt_id
    conn = connect(db)
    with conn:
        conn.execute(f"UPDATE hunts SET {sets} WHERE hunt_id=:hunt_id", fields)
    conn.close()

def add_response(db, hunt_id, actor, response_type, content, url=""):
    conn = connect(db)
    with conn:
        conn.execute(
            "INSERT INTO responses (hunt_id, date, actor, response_type, content, url) VALUES (?, ?, ?, ?, ?, ?)",
            (hunt_id, datetime.utcnow().isoformat(timespec="seconds"), actor, response_type, content, url),
        )
    conn.close()

def add_outcome(db, hunt_id, outcome_type, details="", measured_delta=None, source_url=""):
    conn = connect(db)
    with conn:
        conn.execute(
            """INSERT INTO outcomes (hunt_id, date, outcome_type, details, measured_delta, source_url)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (hunt_id, datetime.utcnow().isoformat(timespec="seconds"), outcome_type, details, measured_delta, source_url),
        )
    conn.close()

def print_json(obj):
    print(json.dumps(obj, indent=2, ensure_ascii=False))

def main():
    ap = argparse.ArgumentParser(description="Company Evaluation AI — Hunting Ledger")
    ap.add_argument("--db", default="hunting_ledger.db", help="SQLite DB path")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_init = sub.add_parser("init", help="Initialize DB")
    p_init.add_argument("--seed", action="store_true", help="Insert sample rows")

    p_add = sub.add_parser("add-hunt", help="Add a hunt")
    for arg in [
        ("--hunt-id", str),
        ("--date-fired", str),
        ("--status", str),
        ("--target-type", str),
        ("--target-name", str),
        ("--hunting-mode", str),
        ("--artifact-type", str),
        ("--claim", str),
        ("--evidence-url", str),
        ("--channel", str),
        ("--response-logged", str),
        ("--outcome", str),
        ("--kill-confirmed", int),
        ("--notes", str),
        ("--next-review-date", str),
    ]:
        p_add.add_argument(arg[0], type=arg[1], required=(arg[0] in ("--hunt-id","--date-fired","--status","--target-type","--target-name","--hunting-mode","--artifact-type","--claim")))

    p_list = sub.add_parser("list", help="List hunts")
    p_list.add_argument("--where", help='SQL WHERE clause, e.g. "status=\'published\'"', default=None)

    p_update = sub.add_parser("update", help="Update a hunt")
    p_update.add_argument("--hunt-id", required=True)
    p_update.add_argument("--set", nargs="+", metavar="FIELD=VALUE", help="Fields to set")

    p_resp = sub.add_parser("add-response", help="Add a response to a hunt")
    p_resp.add_argument("--hunt-id", required=True)
    p_resp.add_argument("--actor", required=True)
    p_resp.add_argument("--type", required=True, dest="response_type")
    p_resp.add_argument("--content", required=True)
    p_resp.add_argument("--url", default="")

    p_out = sub.add_parser("add-outcome", help="Add an outcome to a hunt")
    p_out.add_argument("--hunt-id", required=True)
    p_out.add_argument("--type", required=True, dest="outcome_type")
    p_out.add_argument("--details", default="")
    p_out.add_argument("--delta", type=float, default=None)
    p_out.add_argument("--url", default="")

    args = ap.parse_args()
    db = args.db

    if args.cmd == "init":
        init_db(db, seed=args.seed)
        print(f"Initialized {db} (seed={args.seed})")
    elif args.cmd == "add-hunt":
        add_hunt(
            db,
            hunt_id=args.hunt_id,
            date_fired=args.date_fired,
            status=args.status,
            target_type=args.target_type,
            target_name=args.target_name,
            hunting_mode=args.hunting_mode,
            artifact_type=args.artifact_type,
            claim=args.claim,
            evidence_url=args.evidence_url or "",
            channel=args.channel or "",
            response_logged=args.response_logged or "Ignored",
            outcome=args.outcome or "",
            kill_confirmed=int(args.kill_confirmed or 0),
            notes=args.notes or "",
            next_review_date=args.next_review_date or "",
        )
        print("Hunt added.")
    elif args.cmd == "list":
        rows = list_hunts(db, where=args.where)
        print_json(rows)
    elif args.cmd == "update":
        fields = {}
        for kv in args.set or []:
            if "=" not in kv:
                print(f"Invalid --set entry: {kv}", file=sys.stderr); sys.exit(1)
            k, v = kv.split("=", 1)
            # coerce integer for kill_confirmed
            if k == "kill_confirmed":
                try: v = int(v)
                except: v = 0
            fields[k] = v
        update_hunt(db, args.hunt_id, **fields)
        print("Hunt updated.")
    elif args.cmd == "add-response":
        add_response(db, args.hunt_id, args.actor, args.response_type, args.content, args.url)
        print("Response added.")
    elif args.cmd == "add-outcome":
        add_outcome(db, args.hunt_id, args.outcome_type, args.details, args.delta, args.url)
        print("Outcome added.")

if __name__ == "__main__":
    main()
