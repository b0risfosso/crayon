PRAGMA journal_mode=WAL;
PRAGMA foreign_keys = ON;

-- Base immutable event log per request/operation
CREATE TABLE IF NOT EXISTS usage_events (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    ts              TEXT NOT NULL,            -- ISO timestamp
    app             TEXT NOT NULL,            -- 'jid' | 'crayon'
    model           TEXT NOT NULL,
    endpoint        TEXT,                     -- e.g. '/run', '/write'
    email           TEXT,
    request_id      TEXT,
    tokens_in       INTEGER NOT NULL DEFAULT 0,
    tokens_out      INTEGER NOT NULL DEFAULT 0,
    total_tokens    INTEGER NOT NULL,
    duration_ms     INTEGER DEFAULT 0,
    cost_usd        REAL DEFAULT 0.0,
    meta            TEXT                      -- JSON (prompt hash, retries, etc.)
);
CREATE INDEX IF NOT EXISTS idx_usage_ts ON usage_events(ts);
CREATE INDEX IF NOT EXISTS idx_usage_model ON usage_events(model);
CREATE INDEX IF NOT EXISTS idx_usage_app ON usage_events(app);

-- Aggregates
CREATE TABLE IF NOT EXISTS totals_all_time (
    id              INTEGER PRIMARY KEY CHECK (id=1),
    tokens_in       INTEGER NOT NULL DEFAULT 0,
    tokens_out      INTEGER NOT NULL DEFAULT 0,
    total_tokens    INTEGER NOT NULL DEFAULT 0,
    calls           INTEGER NOT NULL DEFAULT 0,
    last_ts         TEXT
);
INSERT OR IGNORE INTO totals_all_time (id) VALUES (1);

CREATE TABLE IF NOT EXISTS totals_by_model (
    model           TEXT PRIMARY KEY,
    tokens_in       INTEGER NOT NULL DEFAULT 0,
    tokens_out      INTEGER NOT NULL DEFAULT 0,
    total_tokens    INTEGER NOT NULL DEFAULT 0,
    calls           INTEGER NOT NULL DEFAULT 0,
    first_ts        TEXT,
    last_ts         TEXT
);

CREATE TABLE IF NOT EXISTS totals_daily (
    day             TEXT NOT NULL,            -- 'YYYY-MM-DD'
    model           TEXT NOT NULL,
    tokens_in       INTEGER NOT NULL DEFAULT 0,
    tokens_out      INTEGER NOT NULL DEFAULT 0,
    total_tokens    INTEGER NOT NULL DEFAULT 0,
    calls           INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (day, model)
);

-- Views
CREATE VIEW IF NOT EXISTS v_daily_totals AS
SELECT day,
       SUM(tokens_in) AS tokens_in,
       SUM(tokens_out) AS tokens_out,
       SUM(total_tokens) AS total_tokens,
       SUM(calls) AS calls
FROM totals_daily
GROUP BY day
ORDER BY day DESC;

CREATE VIEW IF NOT EXISTS v_model_totals AS
SELECT model, tokens_in, tokens_out, total_tokens, calls, first_ts, last_ts
FROM totals_by_model
ORDER BY total_tokens DESC;
