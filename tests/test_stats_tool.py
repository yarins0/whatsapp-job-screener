"""Tests for stats_tool — record_message and get_pipeline_mode."""

from __future__ import annotations

import sqlite3

import pytest

from agent.tools.stats_tool import (
    COMBINED_THRESHOLD,
    MIN_SAMPLE_SIZE,
    get_pipeline_mode,
    record_message,
)


def _seed(db_path, group: str, total: int, job_posts: int) -> None:
    """Insert a row directly into group_stats to simulate prior observations."""
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            "INSERT INTO group_stats (group_name, total_messages, job_post_messages) VALUES (?,?,?)",
            (group, total, job_posts),
        )
        conn.commit()
    finally:
        conn.close()


def test_get_mode_returns_separate_for_unknown_group(temp_db):
    assert get_pipeline_mode("never-seen@g.us") == "separate"


def test_get_mode_returns_separate_below_min_sample(temp_db):
    _seed(temp_db, "group@g.us", MIN_SAMPLE_SIZE - 1, MIN_SAMPLE_SIZE - 1)
    assert get_pipeline_mode("group@g.us") == "separate"


def test_get_mode_returns_separate_when_rate_below_threshold(temp_db):
    # 40% job posts — below the 70% threshold
    _seed(temp_db, "mixed@g.us", MIN_SAMPLE_SIZE, int(MIN_SAMPLE_SIZE * 0.4))
    assert get_pipeline_mode("mixed@g.us") == "separate"


def test_get_mode_returns_combined_when_rate_meets_threshold(temp_db):
    # Exactly at threshold
    _seed(temp_db, "jobs@g.us", MIN_SAMPLE_SIZE, int(MIN_SAMPLE_SIZE * COMBINED_THRESHOLD))
    assert get_pipeline_mode("jobs@g.us") == "combined"


def test_get_mode_returns_combined_for_high_density_group(temp_db):
    _seed(temp_db, "jobs@g.us", 200, 190)  # 95% job posts
    assert get_pipeline_mode("jobs@g.us") == "combined"


def test_record_message_increments_total(temp_db):
    record_message("group@g.us", is_job_post=False)
    record_message("group@g.us", is_job_post=False)

    conn = sqlite3.connect(temp_db)
    try:
        row = conn.execute(
            "SELECT total_messages, job_post_messages FROM group_stats WHERE group_name=?",
            ("group@g.us",),
        ).fetchone()
    finally:
        conn.close()

    assert row == (2, 0)


def test_record_message_increments_job_post_counter(temp_db):
    record_message("group@g.us", is_job_post=True)
    record_message("group@g.us", is_job_post=False)
    record_message("group@g.us", is_job_post=True)

    conn = sqlite3.connect(temp_db)
    try:
        row = conn.execute(
            "SELECT total_messages, job_post_messages FROM group_stats WHERE group_name=?",
            ("group@g.us",),
        ).fetchone()
    finally:
        conn.close()

    assert row == (3, 2)


def test_record_message_does_not_crash_on_missing_table(tmp_path, monkeypatch):
    """Stats errors must never raise — they log a warning and return silently."""
    empty_db = tmp_path / "empty.db"
    empty_db.touch()  # valid SQLite file with no tables
    monkeypatch.setenv("JOBS_DB_PATH", str(empty_db))

    # Should not raise
    record_message("group@g.us", is_job_post=True)


def test_get_mode_returns_separate_on_missing_table(tmp_path, monkeypatch):
    """Missing group_stats table (old install) must return 'separate', not crash."""
    empty_db = tmp_path / "empty.db"
    empty_db.touch()
    monkeypatch.setenv("JOBS_DB_PATH", str(empty_db))

    assert get_pipeline_mode("group@g.us") == "separate"
