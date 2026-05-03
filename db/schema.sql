-- WhatsApp Job Screener — SQLite schema
-- Apply with:  sqlite3 db/jobs.db < db/schema.sql

CREATE TABLE IF NOT EXISTS jobs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    title       TEXT,
    company     TEXT,
    location    TEXT,
    remote      BOOLEAN,
    skills      TEXT,                       -- JSON array of strings
    salary      TEXT,
    contact     TEXT,
    summary     TEXT,
    group_name  TEXT,
    timestamp   INTEGER,
    seen        BOOLEAN DEFAULT 0,
    created_at  DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_jobs_seen      ON jobs(seen);
CREATE INDEX IF NOT EXISTS idx_jobs_timestamp ON jobs(timestamp);

CREATE TABLE IF NOT EXISTS seen_hashes (
    hash       TEXT PRIMARY KEY,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- Per-group message stats used by the adaptive pipeline to decide whether
-- to run a combined classify+extract call (one LLM round-trip) or the
-- default separate classify-then-extract flow (two round-trips).
CREATE TABLE IF NOT EXISTS group_stats (
    group_name        TEXT PRIMARY KEY,
    total_messages    INTEGER NOT NULL DEFAULT 0,
    job_post_messages INTEGER NOT NULL DEFAULT 0,
    updated_at        DATETIME DEFAULT CURRENT_TIMESTAMP
);
