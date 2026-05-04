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


def _db_path() -> Path:
    return Path(os.environ.get("JOBS_DB_PATH", DEFAULT_DB_PATH))


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
