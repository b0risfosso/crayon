PRAGMA journal_mode=WAL;
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS visions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    title           TEXT,
    text            TEXT NOT NULL,
    focuses         TEXT,                         -- JSON string or CSV (list of focuses or focus objects)
    explanation     TEXT,                         -- optional, vision-level explanation
    email           TEXT,
    status          TEXT DEFAULT 'draft',         -- draft|active|archived|error
    priority        INTEGER DEFAULT 0,            -- sort/usefulness
    tags            TEXT,                         -- comma-separated or JSON in metadata
    source          TEXT,                         -- 'jid' | 'crayon' | other
    slug            TEXT UNIQUE,
    metadata        TEXT,                         -- JSON string (key/values)
    created_at      TEXT NOT NULL,                -- ISO 8601
    updated_at      TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_visions_email   ON visions(email);
CREATE INDEX IF NOT EXISTS idx_visions_status  ON visions(status);
CREATE INDEX IF NOT EXISTS idx_visions_created ON visions(created_at);
CREATE UNIQUE INDEX IF NOT EXISTS idx_visions_text_email ON visions(text, email);

CREATE TABLE IF NOT EXISTS pictures (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    vision_id       INTEGER NOT NULL,
    focus           TEXT,                         -- optional, picture-specific focus
    title           TEXT,
    description     TEXT,
    function        TEXT,
    explanation     TEXT,                         -- NEW: explanation tied to this picture (optional)
    email           TEXT,
    order_index     INTEGER DEFAULT 0,
    status          TEXT DEFAULT 'draft',         -- draft|ready|published|archived|error
    source          TEXT,                         -- 'jid' | 'crayon' | other
    slug            TEXT,
    metadata        TEXT,                         -- JSON string (params, knobs, etc.)
    assets          TEXT,                         -- JSON string (image paths, urls)
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL,
    FOREIGN KEY (vision_id) REFERENCES visions(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_pictures_vision ON pictures(vision_id);
CREATE INDEX IF NOT EXISTS idx_pictures_email  ON pictures(email);
CREATE INDEX IF NOT EXISTS idx_pictures_status ON pictures(status);
CREATE UNIQUE INDEX IF NOT EXISTS idx_pictures_slug ON pictures(slug);

CREATE TABLE IF NOT EXISTS waxes (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    vision_id     INTEGER NOT NULL,
    picture_id    INTEGER,                   -- NEW: tie wax to a specific picture
    title         TEXT,
    content       TEXT NOT NULL,              -- full Wax Stack text
    content_hash  TEXT,                       -- sha256(content) for idempotency
    email         TEXT,
    source        TEXT,                       -- 'crayon' | other
    metadata      TEXT,                       -- JSON
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL,
    FOREIGN KEY (vision_id) REFERENCES visions(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_waxes_vision ON waxes(vision_id);
CREATE INDEX IF NOT EXISTS idx_waxes_email ON waxes(email);
CREATE UNIQUE INDEX IF NOT EXISTS idx_waxes_content_hash ON waxes(content_hash);
CREATE INDEX IF NOT EXISTS idx_waxes_picture ON waxes(picture_id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_waxes_pic_email ON waxes(picture_id, email);


CREATE TABLE IF NOT EXISTS worlds (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    vision_id     INTEGER NOT NULL,
    picture_id    INTEGER,                    -- NEW: tie world to a specific picture
    wax_id        INTEGER,
    title         TEXT,
    html          TEXT NOT NULL,              -- full HTML document
    html_hash     TEXT,                       -- optional: last content hash
    email         TEXT,
    source        TEXT,
    metadata      TEXT,                       -- JSON
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL,
    FOREIGN KEY (vision_id) REFERENCES visions(id) ON DELETE CASCADE,
    FOREIGN KEY (picture_id) REFERENCES pictures(id) ON DELETE SET NULL,
    FOREIGN KEY (wax_id)    REFERENCES waxes(id)   ON DELETE SET NULL
);
CREATE INDEX IF NOT EXISTS idx_worlds_vision     ON worlds(vision_id);
CREATE INDEX IF NOT EXISTS idx_worlds_picture    ON worlds(picture_id);
CREATE INDEX IF NOT EXISTS idx_worlds_email      ON worlds(email);
CREATE UNIQUE INDEX IF NOT EXISTS idx_worlds_pic_email ON worlds(picture_id, email);

PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS prompt_outputs (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    vision_id    INTEGER,                  -- nullable
    picture_id   INTEGER,                  -- nullable
    collection   TEXT NOT NULL,            -- e.g., 'garden_architect'
    prompt_key   TEXT NOT NULL,            -- e.g., 'soil_profile'
    prompt_text  TEXT NOT NULL,            -- fully rendered user prompt
    system_text  TEXT,                     -- rendered system prompt (if any)
    output_text  TEXT NOT NULL,            -- model completion
    model        TEXT,
    email        TEXT,
    metadata     TEXT,                     -- JSON (token counts, finish_reason, latency, etc.)
    created_at   TEXT NOT NULL,
    FOREIGN KEY (vision_id)  REFERENCES visions(id)   ON DELETE SET NULL,
    FOREIGN KEY (picture_id) REFERENCES pictures(id)  ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_prompt_outputs_collection ON prompt_outputs(collection);
CREATE INDEX IF NOT EXISTS idx_prompt_outputs_pic ON prompt_outputs(picture_id);
CREATE INDEX IF NOT EXISTS idx_prompt_outputs_vision ON prompt_outputs(vision_id);
CREATE INDEX IF NOT EXISTS idx_prompt_outputs_email ON prompt_outputs(email);


PRAGMA foreign_keys = ON;

-- 1) New table
-- Core ideas table (title renamed to "source")
CREATE TABLE IF NOT EXISTS core_ideas (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  source      TEXT NOT NULL,       -- passage/doc/topic identifier (was "title")
  core_idea   TEXT NOT NULL,       -- the distilled idea sentence
  email       TEXT,
  origin      TEXT,                -- e.g., 'manual' | 'jid' | 'crayon'
  metadata    TEXT,                -- JSON
  created_at  TEXT NOT NULL,
  updated_at  TEXT NOT NULL,
  CHECK (length(source) > 0),
  CHECK (length(core_idea) > 0)
);

CREATE INDEX IF NOT EXISTS idx_core_ideas_source ON core_ideas(source);
CREATE INDEX IF NOT EXISTS idx_core_ideas_email  ON core_ideas(email);
CREATE UNIQUE INDEX IF NOT EXISTS idx_core_ideas_dedupe
  ON core_ideas(source, core_idea, IFNULL(email, ''));


-- 3) Triggers to maintain updated_at
CREATE TRIGGER IF NOT EXISTS trg_core_ideas_insert_ts
AFTER INSERT ON core_ideas
BEGIN
  UPDATE core_ideas
  SET created_at = COALESCE(NEW.created_at, strftime('%Y-%m-%dT%H:%M:%fZ','now')),
      updated_at = COALESCE(NEW.updated_at, strftime('%Y-%m-%dT%H:%M:%fZ','now'))
  WHERE id = NEW.id;
END;

CREATE TRIGGER IF NOT EXISTS trg_core_ideas_update_ts
AFTER UPDATE ON core_ideas
BEGIN
  UPDATE core_ideas
  SET updated_at = strftime('%Y-%m-%dT%H:%M:%fZ','now')
  WHERE id = NEW.id;
END;

