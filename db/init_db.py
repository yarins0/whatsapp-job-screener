"""Tiny helper to (re)create the SQLite DB from schema.sql.

Usage:
    python -m db.init_db                # uses ./db/jobs.db
    JOBS_DB_PATH=/tmp/x.db python -m db.init_db
"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

SCHEMA_PATH = Path(__file__).with_name("schema.sql")
DEFAULT_DB_PATH = Path(__file__).with_name("jobs.db")


def init_db(db_path: str | os.PathLike | None = None) -> Path:
    """Create the DB file and apply schema.sql. Idempotent."""
    db_file = Path(db_path or os.environ.get("JOBS_DB_PATH", DEFAULT_DB_PATH))
    db_file.parent.mkdir(parents=True, exist_ok=True)
    schema_sql = SCHEMA_PATH.read_text(encoding="utf-8")

    conn = sqlite3.connect(db_file)
    try:
        conn.executescript(schema_sql)
        conn.commit()
    finally:
        conn.close()
    return db_file


if __name__ == "__main__":
    path = init_db()
    print(f"Initialized DB at {path}")
