"""Store tool — persists a qualified job to SQLite and the vector index."""

from __future__ import annotations

import json
import logging
import os
import sqlite3
from pathlib import Path

logger = logging.getLogger(__name__)

DEFAULT_DB_PATH = Path(__file__).resolve().parents[2] / "db" / "jobs.db"


def _db_path() -> Path:
    return Path(os.environ.get("JOBS_DB_PATH", DEFAULT_DB_PATH))


def store_job(job: dict) -> int | None:
    """Insert ``job`` into the ``jobs`` table and the vector index.

    Returns the new SQLite row id, or None if the job is suppressed as a
    time-duplicate (same title + company stored within the last 7 days).
    """
    if _try_is_time_duplicate(job):
        logger.debug(
            "Skipping time-duplicate job title=%r company=%r",
            job.get("title"),
            job.get("company"),
        )
        return None

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
        row_id = cur.lastrowid
    finally:
        conn.close()

    _try_index_job(row_id, job)
    return row_id


def _try_is_time_duplicate(job: dict) -> bool:
    """Return True if the same role at the same company was stored within 7 days.

    Errors are non-fatal — if the DB is unavailable, the job is not suppressed.
    """
    try:
        from agent.tools.dedup_tool import is_time_duplicate
        return is_time_duplicate(job.get("title"), job.get("company"))
    except Exception as exc:
        logger.warning("dedup_tool.is_time_duplicate failed: %s", exc)
        return False


def _try_index_job(job_id: int, job: dict) -> None:
    """Index the job in the vector store; errors are non-fatal (job is already in SQLite)."""
    try:
        from agent.vector_store import index_job
        index_job(job_id, job)
    except Exception as exc:
        logger.warning("vector_store.index_job failed (job %s): %s", job_id, exc)
