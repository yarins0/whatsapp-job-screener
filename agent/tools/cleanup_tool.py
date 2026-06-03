"""Retention cleanup — purges expired dedup hashes and old delivered jobs.

Runs daily from the digest scheduler. Records are kept for
``DUPLICATE_WINDOW_DAYS + RETENTION_GRACE_DAYS`` days so cleanup never deletes a
row that is still inside the active duplicate-detection window.
"""

from __future__ import annotations

import logging
import os
import sqlite3
from pathlib import Path

from agent.tools.dedup_tool import _duplicate_window_days

logger = logging.getLogger(__name__)

DEFAULT_DB_PATH = Path(__file__).resolve().parents[2] / "db" / "jobs.db"

# Grace period added on top of the duplicate window before a row becomes
# eligible for deletion. Guarantees cleanup never removes a record the dedup
# logic could still match a new posting against.
RETENTION_GRACE_DAYS = 7


def _db_path() -> Path:
    return Path(os.environ.get("JOBS_DB_PATH", DEFAULT_DB_PATH))


def _retention_days() -> int:
    """Age in days past which a record is deleted: duplicate window + grace."""
    return _duplicate_window_days() + RETENTION_GRACE_DAYS


def cleanup_old_records() -> dict:
    """Delete expired dedup hashes and old delivered jobs.

    - ``seen_hashes``: every row older than the retention cutoff (pure dedup
      state with no query or history value).
    - ``jobs``: rows older than the cutoff that have already been delivered in a
      digest (``seen = 1``). Undelivered jobs (``seen = 0``) are always kept so a
      broken or delayed digest never silently loses a posting.

    Deleted job ids are also removed from the vector index to keep it in sync.

    Returns:
        ``{"hashes": int, "jobs": int}`` — how many rows were removed from each
        table.
    """
    cutoff = f"-{_retention_days()} days"
    db = _db_path()
    if not db.exists():
        return {"hashes": 0, "jobs": 0}

    conn = sqlite3.connect(db)
    try:
        expired_job_ids = [
            row[0]
            for row in conn.execute(
                "SELECT id FROM jobs "
                "WHERE seen = 1 AND created_at < datetime('now', ?)",
                (cutoff,),
            ).fetchall()
        ]

        hashes_deleted = conn.execute(
            "DELETE FROM seen_hashes WHERE created_at < datetime('now', ?)",
            (cutoff,),
        ).rowcount

        if expired_job_ids:
            placeholders = ",".join("?" * len(expired_job_ids))
            conn.execute(
                f"DELETE FROM jobs WHERE id IN ({placeholders})",
                expired_job_ids,
            )
        conn.commit()
    finally:
        conn.close()

    _try_delete_from_index(expired_job_ids)

    logger.info(
        "cleanup_old_records: removed %d hashes, %d jobs (retention=%d days)",
        hashes_deleted,
        len(expired_job_ids),
        _retention_days(),
    )
    return {"hashes": hashes_deleted, "jobs": len(expired_job_ids)}


def _try_delete_from_index(job_ids: list[int]) -> None:
    """Remove deleted jobs from the vector index; errors are non-fatal.

    The rows are already gone from SQLite at this point, so a failure here only
    leaves stale ids in Chroma — never a data-loss situation worth aborting for.
    """
    if not job_ids:
        return
    try:
        from agent.vector_store import delete_jobs

        delete_jobs(job_ids)
    except Exception as exc:
        logger.warning("vector_store.delete_jobs failed: %s", exc)
