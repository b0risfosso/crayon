PRAGMA journal_mode=WAL;
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS visions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    title           TEXT,
    text            TEXT NOT NULL,
    email           TEXT,
    status          TEXT DEFAULT 'draft',            -- draft|active|archived|error
    priority        INTEGER DEFAULT 0,               -- sort/usefulness
    tags            TEXT,                            -- comma-separated or JSON in metadata
    source          TEXT,                            -- 'jid' | 'crayon' | other
    slug            TEXT UNIQUE,
    metadata        TEXT,                            -- JSON string (key/values)
    created_at      TEXT NOT NULL,                   -- ISO 8601
    updated_at      TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_visions_email ON visions(email);
CREATE INDEX IF NOT EXISTS idx_visions_status ON visions(status);
CREATE INDEX IF NOT EXISTS idx_visions_created ON visions(created_at);

CREATE TABLE IF NOT EXISTS pictures (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    vision_id       INTEGER NOT NULL,
    subtext         TEXT,
    title           TEXT,
    description     TEXT,
    function        TEXT,
    email           TEXT,
    order_index     INTEGER DEFAULT 0,
    status          TEXT DEFAULT 'draft',            -- draft|ready|published|archived|error
    source          TEXT,                            -- 'jid' | 'crayon' | other
    slug            TEXT,
    metadata        TEXT,                            -- JSON string (params, knobs, etc.)
    assets          TEXT,                            -- JSON string (image paths, urls)
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL,
    FOREIGN KEY (vision_id) REFERENCES visions(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_pictures_vision ON pictures(vision_id);
CREATE INDEX IF NOT EXISTS idx_pictures_email ON pictures(email);
CREATE INDEX IF NOT EXISTS idx_pictures_status ON pictures(status);
CREATE UNIQUE INDEX IF NOT EXISTS idx_pictures_slug ON pictures(slug);
