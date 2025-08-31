# Create a SQLite Hunting Ledger with schema + starter rows, and display it

import sqlite3
import pandas as pd
from datetime import datetime, timedelta
from caas_jupyter_tools import display_dataframe_to_user

db_path = "/mnt/data/CEA_hunting_ledger.db"
conn = sqlite3.connect(db_path)
cur = conn.cursor()

# Create tables
cur.executescript(
"""
PRAGMA journal_mode=WAL;

CREATE TABLE IF NOT EXISTS hunts (
  hunt_id TEXT PRIMARY KEY,
  date_fired TEXT,                 -- ISO 8601 timestamp when artifact published (or planned publish date)
  status TEXT,                     -- planned | published | closed
  target_type TEXT,                -- Investor | Founder | Journalist | Regulator | Company
  target_name TEXT,
  hunting_mode TEXT,               -- Private | Public | Cultural | Market | Institutional
  artifact_type TEXT,              -- Benchmark | Fragility Warning | Certification Badge | Meme | Index | Case Study
  claim TEXT,                      -- The precise prediction/call
  evidence_url TEXT,               -- Link to artifact (blog, PDF, post). Can be multiple, comma-separated
  channel TEXT,                    -- Substack | Blog | LinkedIn | X/Twitter | Email | Press
  response_logged TEXT,            -- Ignored | Referenced | Disputed | Adopted
  outcome TEXT,                    -- Real-world outcome: earnings miss, collapse, acquisition, etc.
  kill_confirmed INTEGER,          -- 0/1
  notes TEXT,
  created_at TEXT DEFAULT (datetime('now')),
  updated_at TEXT DEFAULT (datetime('now')),
  next_review_date TEXT            -- when to revisit/score the call (e.g., earnings date)
);

CREATE TRIGGER IF NOT EXISTS hunts_updated_at
AFTER UPDATE ON hunts
FOR EACH ROW
BEGIN
  UPDATE hunts SET updated_at = datetime('now') WHERE hunt_id = OLD.hunt_id;
END;

CREATE TABLE IF NOT EXISTS responses (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  hunt_id TEXT,
  date TEXT,                       -- ISO timestamp of response
  actor TEXT,                      -- who responded (analyst, journalist, founder, investor)
  response_type TEXT,              -- Reference | Dispute | Adoption | Inquiry
  content TEXT,                    -- short note/quote
  url TEXT,                        -- link/screenshot location
  FOREIGN KEY(hunt_id) REFERENCES hunts(hunt_id)
);

CREATE TABLE IF NOT EXISTS outcomes (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  hunt_id TEXT,
  date TEXT,                       -- ISO timestamp of outcome
  outcome_type TEXT,               -- Earnings Miss | Guidance Cut | Funding Failure | Bankruptcy | Acquisition | Outperformance | Neutral
  details TEXT,                    -- brief description
  measured_delta REAL,             -- optional numeric impact (e.g., price change %, revenue surprise)
  source_url TEXT,
  FOREIGN KEY(hunt_id) REFERENCES hunts(hunt_id)
);
"""
)

# Seed with starter rows if empty
cur.execute("SELECT COUNT(*) FROM hunts")
count = cur.fetchone()[0]

if count == 0:
    today = datetime.utcnow().date()
    # Planned hunt (upcoming)
    cur.execute(
        """
        INSERT INTO hunts (hunt_id, date_fired, status, target_type, target_name, hunting_mode, artifact_type, claim, channel, response_logged, outcome, kill_confirmed, notes, next_review_date)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "HL-{}-001".format(today.strftime("%Y%m%d")),
            (today + timedelta(days=7)).isoformat(),
            "planned",
            "Company",
            "CandidateCo A (example)",
            "Public",
            "Fragility Warning",
            "Fragility Rating: High (C-) — liquidity + governance risk; 30–60% probability of negative event within 6–12 months.",
            "Substack",
            "Ignored",
            "",
            0,
            "Prep visuals; align with earnings calendar; include peer comparison chart.",
            (today + timedelta(days=30)).isoformat(),
        ),
    )
    # Published hunt (example)
    cur.execute(
        """
        INSERT INTO hunts (hunt_id, date_fired, status, target_type, target_name, hunting_mode, artifact_type, claim, evidence_url, channel, response_logged, outcome, kill_confirmed, notes, next_review_date)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "HL-{}-002".format(today.strftime("%Y%m%d")),
            today.isoformat(),
            "published",
            "Company",
            "CandidateCo B (example)",
            "Public",
            "Benchmark",
            "Top 10 Fragile Growth Cos — CandidateCo B ranked #4 (Risk 78/100).",
            "https://example.com/hunt/HL-{}-002".format(today.strftime("%Y%m%d")),
            "LinkedIn",
            "Referenced",
            "Pending",
            0,
            "Monitor mentions; outreach to 3 analysts; schedule follow-up thread in 14 days.",
            (today + timedelta(days=14)).isoformat(),
        ),
    )
    # Add sample response to published hunt
    cur.execute(
        """
        INSERT INTO responses (hunt_id, date, actor, response_type, content, url)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            "HL-{}-002".format(today.strftime("%Y%m%d")),
            datetime.utcnow().isoformat(timespec="seconds"),
            "Analyst @ FinNews (example)",
            "Reference",
            "Interesting framework — curious about inputs behind 'Risk 78/100'.",
            "https://example.com/post/ref-1",
        ),
    )

conn.commit()

# Show tables to the user
hunts_df = pd.read_sql_query("SELECT * FROM hunts ORDER BY date_fired DESC", conn)
responses_df = pd.read_sql_query("SELECT * FROM responses ORDER BY date DESC", conn)
outcomes_df = pd.read_sql_query("SELECT * FROM outcomes ORDER BY date DESC", conn)

display_dataframe_to_user("Hunting Ledger — Hunts", hunts_df)
display_dataframe_to_user("Hunting Ledger — Responses", responses_df)
display_dataframe_to_user("Hunting Ledger — Outcomes", outcomes_df)

conn.close()

db_path
