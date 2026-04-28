"""Store tool — persists a qualified job to SQLite."""

from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path

DEFAULT_DB_PATH = Path(__file__).resolve().parents[2] / "db" / "jobs.db"


def _db_path() -> Path:
    return Path(os.environ.get("JOBS_DB_PATH", DEFAULT_DB_PATH))


def store_job(job: dict) -> int:
    """Insert ``job`` into the ``jobs`` table; return the new row id."""
    db = _db_path()
    db.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(db)
    try:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO jobs (
                title, company, location, remote, skills,
                salary, contact, summary, group_name, timestamp, seen
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0)
            """,
            (
                job.get("title"),
                job.get("company"),
                job.get("location"),
                job.get("remote"),
                json.dumps(job.get("skills") or []),
                job.get("salary"),
                job.get("contact"),
                job.get("summary"),
                job.get("group") or job.get("group_name"),
                job.get("timestamp"),
            ),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()
