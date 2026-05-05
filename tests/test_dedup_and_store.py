"""Tests for dedup_tool and store_tool against an isolated SQLite DB."""

from __future__ import annotations

import json
import sqlite3

import sqlite3 as _sqlite3

from agent.tools.dedup_tool import is_duplicate, is_time_duplicate
from agent.tools.store_tool import store_job


def _job(**overrides) -> dict:
    base = {
        "title": "Senior Backend Engineer",
        "company": "Acme",
        "location": "Tel Aviv",
        "remote": True,
        "skills": ["Python", "FastAPI"],
        "salary": "30k",
        "contact": "jobs@acme.io",
        "summary": "Senior backend at Acme.",
        "group": "Tech Jobs TLV",
        "timestamp": 1700000000,
    }
    base.update(overrides)
    return base


def test_first_sighting_is_not_duplicate(temp_db):
    assert is_duplicate(_job()) is False
    # second time should now be duplicate
    assert is_duplicate(_job()) is True


def test_different_jobs_dedup_independently(temp_db):
    assert is_duplicate(_job(title="Backend A")) is False
    assert is_duplicate(_job(title="Backend B")) is False
    assert is_duplicate(_job(title="Backend A")) is True


# ---------------------------------------------------------------------------
# is_time_duplicate
# ---------------------------------------------------------------------------

def test_time_duplicate_no_company_returns_false(temp_db):
    """Company=None means we cannot identify the posting — never a duplicate."""
    assert is_time_duplicate("Full Stack Engineer", None) is False


def test_time_duplicate_empty_db_returns_false(temp_db):
    """No stored jobs → nothing to match against."""
    assert is_time_duplicate("Backend Engineer", "Acme") is False


def test_time_duplicate_same_title_and_company_returns_true(temp_db):
    """Same title + company stored within the window → duplicate."""
    conn = _sqlite3.connect(temp_db)
    conn.execute(
        "INSERT INTO jobs (title, company, seen) VALUES (?, ?, 0)",
        ("Backend Engineer", "Acme"),
    )
    conn.commit()
    conn.close()

    assert is_time_duplicate("Backend Engineer", "Acme") is True


def test_time_duplicate_is_case_insensitive(temp_db):
    """Title and company comparison must be case-insensitive."""
    conn = _sqlite3.connect(temp_db)
    conn.execute(
        "INSERT INTO jobs (title, company, seen) VALUES (?, ?, 0)",
        ("backend engineer", "acme"),
    )
    conn.commit()
    conn.close()

    assert is_time_duplicate("Backend Engineer", "ACME") is True


def test_time_duplicate_different_company_returns_false(temp_db):
    """Same title but different company → not a duplicate."""
    conn = _sqlite3.connect(temp_db)
    conn.execute(
        "INSERT INTO jobs (title, company, seen) VALUES (?, ?, 0)",
        ("Backend Engineer", "OtherCorp"),
    )
    conn.commit()
    conn.close()

    assert is_time_duplicate("Backend Engineer", "Acme") is False


def test_time_duplicate_outside_window_returns_false(temp_db):
    """A matching job older than the window is not considered a duplicate."""
    conn = _sqlite3.connect(temp_db)
    # Insert a row with created_at forced to 8 days ago.
    conn.execute(
        "INSERT INTO jobs (title, company, seen, created_at) VALUES (?, ?, 0, datetime('now', '-8 days'))",
        ("Backend Engineer", "Acme"),
    )
    conn.commit()
    conn.close()

    assert is_time_duplicate("Backend Engineer", "Acme", within_days=7) is False


def test_store_job_inserts_row(temp_db):
    job_id = store_job(_job())
    assert job_id >= 1

    conn = sqlite3.connect(temp_db)
    try:
        row = conn.execute(
            "SELECT title, company, remote, skills, group_name, seen FROM jobs WHERE id = ?",
            (job_id,),
        ).fetchone()
    finally:
        conn.close()

    title, company, remote, skills_json, group_name, seen = row
    assert title == "Senior Backend Engineer"
    assert company == "Acme"
    assert bool(remote) is True
    assert json.loads(skills_json) == ["Python", "FastAPI"]
    assert group_name == "Tech Jobs TLV"
    assert seen == 0
