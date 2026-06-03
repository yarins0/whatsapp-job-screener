"""Tests for the retention cleanup tool."""

from __future__ import annotations

import sqlite3

from agent.tools.cleanup_tool import (
    RETENTION_GRACE_DAYS,
    _retention_days,
    cleanup_old_records,
)

# Default DUPLICATE_WINDOW_DAYS is 7, so the default retention cutoff is 14 days.
# "old" rows are inserted at 20 days; "recent" rows at <14 days.


def _insert_job(db, *, title: str, seen: int, days_ago: int) -> None:
    conn = sqlite3.connect(db)
    try:
        conn.execute(
            "INSERT INTO jobs (title, company, seen, created_at) "
            "VALUES (?, 'Acme', ?, datetime('now', ?))",
            (title, seen, f"-{days_ago} days"),
        )
        conn.commit()
    finally:
        conn.close()


def _insert_hash(db, *, hash_val: str, days_ago: int) -> None:
    conn = sqlite3.connect(db)
    try:
        conn.execute(
            "INSERT INTO seen_hashes (hash, created_at) VALUES (?, datetime('now', ?))",
            (hash_val, f"-{days_ago} days"),
        )
        conn.commit()
    finally:
        conn.close()


def _job_titles(db) -> set[str]:
    conn = sqlite3.connect(db)
    try:
        return {r[0] for r in conn.execute("SELECT title FROM jobs").fetchall()}
    finally:
        conn.close()


def _hashes(db) -> set[str]:
    conn = sqlite3.connect(db)
    try:
        return {r[0] for r in conn.execute("SELECT hash FROM seen_hashes").fetchall()}
    finally:
        conn.close()


def test_deletes_old_seen_jobs_only(temp_db, monkeypatch):
    monkeypatch.delenv("DUPLICATE_WINDOW_DAYS", raising=False)  # default 7 -> retention 14
    _insert_job(temp_db, title="old-seen", seen=1, days_ago=20)
    _insert_job(temp_db, title="recent-seen", seen=1, days_ago=5)
    _insert_job(temp_db, title="old-unseen", seen=0, days_ago=20)

    result = cleanup_old_records()

    # Old undelivered jobs survive — only delivered (seen=1) past-cutoff rows go.
    assert _job_titles(temp_db) == {"recent-seen", "old-unseen"}
    assert result["jobs"] == 1


def test_deletes_old_hashes(temp_db, monkeypatch):
    monkeypatch.delenv("DUPLICATE_WINDOW_DAYS", raising=False)
    _insert_hash(temp_db, hash_val="old", days_ago=20)
    _insert_hash(temp_db, hash_val="recent", days_ago=3)

    result = cleanup_old_records()

    assert _hashes(temp_db) == {"recent"}
    assert result["hashes"] == 1


def test_cutoff_respects_duplicate_window(temp_db, monkeypatch):
    monkeypatch.setenv("DUPLICATE_WINDOW_DAYS", "30")  # retention = 37 days
    _insert_job(temp_db, title="job-25d", seen=1, days_ago=25)
    _insert_hash(temp_db, hash_val="hash-25d", days_ago=25)

    cleanup_old_records()

    # 25 days is still inside the widened 37-day retention window — both kept.
    assert "job-25d" in _job_titles(temp_db)
    assert "hash-25d" in _hashes(temp_db)


def test_removes_expired_jobs_from_vector_index(temp_db, monkeypatch):
    monkeypatch.delenv("DUPLICATE_WINDOW_DAYS", raising=False)
    _insert_job(temp_db, title="old-seen", seen=1, days_ago=20)

    captured: dict = {}
    import agent.vector_store as vector_store

    monkeypatch.setattr(vector_store, "delete_jobs", lambda ids: captured.setdefault("ids", ids))

    cleanup_old_records()

    # The single expired job (row id 1) is forwarded to the vector index.
    assert captured["ids"] == [1]


def test_uninitialised_db_is_noop(tmp_path, monkeypatch):
    """An existing-but-empty DB file (no schema) must not crash cleanup.

    Reproduces the 0-byte ``jobs.db`` that took the whole stack down: the file
    exists (so the ``db.exists()`` guard passes) but has no tables.
    """
    empty_db = tmp_path / "empty.db"
    empty_db.touch()  # 0-byte file — exists() is True, but no tables
    monkeypatch.setenv("JOBS_DB_PATH", str(empty_db))

    assert cleanup_old_records() == {"hashes": 0, "jobs": 0}


def test_retention_is_window_plus_grace(monkeypatch):
    monkeypatch.setenv("DUPLICATE_WINDOW_DAYS", "10")
    assert _retention_days() == 10 + RETENTION_GRACE_DAYS
