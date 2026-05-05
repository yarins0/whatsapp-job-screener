"""Dedup tool — uses a SQLite ``seen_hashes`` table to skip duplicate posts.

Hash key = lowercase(title + company + contact). It's intentionally fuzzy:
the same role re-posted by different people still collapses to the same hash.
"""

from __future__ import annotations

import hashlib
import os
import sqlite3
from pathlib import Path

DEFAULT_DB_PATH = Path(__file__).resolve().parents[2] / "db" / "jobs.db"
DEFAULT_DUPLICATE_WINDOW_DAYS = 7


def _db_path() -> Path:
    return Path(os.environ.get("JOBS_DB_PATH", DEFAULT_DB_PATH))


def _duplicate_window_days() -> int:
    try:
        return int(os.environ.get("DUPLICATE_WINDOW_DAYS", DEFAULT_DUPLICATE_WINDOW_DAYS))
    except ValueError:
        return DEFAULT_DUPLICATE_WINDOW_DAYS


def _hash(job: dict) -> str:
    key = f"{job.get('title', '')}-{job.get('company', '')}-{job.get('contact', '')}"
    return hashlib.md5(key.lower().encode("utf-8")).hexdigest()


def _raw_hash(text: str) -> str:
    # Prefix avoids any collision with the structured _hash namespace.
    return "raw:" + hashlib.md5(text.strip().encode("utf-8")).hexdigest()


def _check_and_record(hash_val: str) -> bool:
    """Insert hash into seen_hashes. Returns True if it was already there (duplicate)."""
    db = _db_path()
    db.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db)
    try:
        cur = conn.cursor()
        cur.execute("INSERT OR IGNORE INTO seen_hashes (hash) VALUES (?)", (hash_val,))
        conn.commit()
        return cur.rowcount == 0
    finally:
        conn.close()


def is_raw_duplicate(text: str) -> bool:
    """Return True if this raw text has been seen before. Records it on first sight.

    Used by web scrapers to deduplicate before any LLM call — much cheaper than
    letting the pipeline discover the duplicate post-extraction.
    """
    return _check_and_record(_raw_hash(text))


def is_duplicate(job: dict) -> bool:
    """Return True if we've seen this job before. Records it on first sight."""
    hash_val = _hash(job)
    db = _db_path()
    db.parent.mkdir(parents=True, exist_ok=True)

    return _check_and_record(hash_val)


def is_time_duplicate(title: str | None, company: str | None, within_days: int | None = None) -> bool:
    """Return True if the same role at the same company was stored within the duplicate window.

    Rules:
    - If ``company`` is falsy, always returns False — an unknown company cannot
      reliably identify a duplicate.
    - Comparison is case-insensitive on both title and company.
    - A match means the job was already stored recently and the new posting is
      likely the same opening re-circulated across groups.

    Args:
        title: job title from the extracted posting.
        company: company name from the extracted posting.
        within_days: look-back window in days. Defaults to the
            ``DUPLICATE_WINDOW_DAYS`` env var (default: 7).
    """
    if not company:
        return False

    days = within_days if within_days is not None else _duplicate_window_days()

    db = _db_path()
    if not db.exists():
        return False

    conn = sqlite3.connect(db)
    try:
        row = conn.execute(
            """
            SELECT 1 FROM jobs
            WHERE lower(title)   = lower(?)
              AND lower(company)  = lower(?)
              AND created_at >= datetime('now', ? || ' days')
            LIMIT 1
            """,
            (title or "", company, f"-{days}"),
        ).fetchone()
    finally:
        conn.close()

    return row is not None
