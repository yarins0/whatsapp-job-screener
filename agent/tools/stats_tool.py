"""Per-group message stats — drives the adaptive pipeline mode selection.

Records how many messages per group are job posts. Once a group has at least
MIN_SAMPLE_SIZE messages and its job-post rate exceeds COMBINED_THRESHOLD, the
pipeline switches to a single combined classify+extract LLM call instead of two
separate calls, saving roughly one LLM round-trip per message.

All DB errors are caught and logged as warnings — stats are advisory and must
never interrupt the main pipeline.
"""

from __future__ import annotations

import logging
import os
import sqlite3
from pathlib import Path

logger = logging.getLogger(__name__)

DEFAULT_DB_PATH = Path(__file__).resolve().parents[2] / "db" / "jobs.db"

# Minimum messages observed before we trust the rate estimate.
MIN_SAMPLE_SIZE = 50
# Job-post rate above which we switch to the combined chain.
COMBINED_THRESHOLD = 0.70


def _db_path() -> Path:
    return Path(os.environ.get("JOBS_DB_PATH", DEFAULT_DB_PATH))


def record_message(group_name: str, is_job_post: bool) -> None:
    """Increment the message counters for *group_name*.

    Silently logs and returns on any DB error so a stats write failure never
    interrupts the pipeline.
    """
    try:
        conn = sqlite3.connect(_db_path())
        try:
            conn.execute(
                """
                INSERT INTO group_stats (group_name, total_messages, job_post_messages, updated_at)
                VALUES (?, 1, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(group_name) DO UPDATE SET
                    total_messages    = total_messages + 1,
                    job_post_messages = job_post_messages + excluded.job_post_messages,
                    updated_at        = CURRENT_TIMESTAMP
                """,
                (group_name, 1 if is_job_post else 0),
            )
            conn.commit()
        finally:
            conn.close()
    except Exception as exc:  # noqa: BLE001
        logger.warning("stats_tool: failed to record message for %r: %s", group_name, exc)


def get_pipeline_mode(group_name: str) -> str:
    """Return ``"combined"`` if the group's job-post rate warrants it, else ``"separate"``.

    Falls back to ``"separate"`` on any DB error (e.g. table doesn't exist on
    an old install) so existing deployments are never broken by a missing table.
    """
    try:
        conn = sqlite3.connect(_db_path())
        try:
            row = conn.execute(
                "SELECT total_messages, job_post_messages FROM group_stats WHERE group_name = ?",
                (group_name,),
            ).fetchone()
        finally:
            conn.close()

        if row is None:
            return "separate"  # no data yet for this group

        total, job_posts = row
        if total < MIN_SAMPLE_SIZE:
            return "separate"  # too early to judge

        rate = job_posts / total
        return "combined" if rate >= COMBINED_THRESHOLD else "separate"

    except Exception as exc:  # noqa: BLE001
        logger.warning("stats_tool: could not read pipeline mode for %r: %s", group_name, exc)
        return "separate"
