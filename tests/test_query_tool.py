"""Tests for agent/tools/query_tool.py and agent/list_jobs.py.

All tests run offline — no API key required.
The temp_db fixture from conftest.py creates an isolated SQLite file per test.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _insert_job(db_path, *, title="Software Engineer", company="Acme",
                location="Tel Aviv", remote=0, summary="A great role",
                skills=None, contact="hr@acme.com", group_name="Tech Jobs",
                days_ago=1, seen=0):
    """Insert a single job row with created_at set relative to now."""
    created_at = (datetime.utcnow() - timedelta(days=days_ago)).strftime("%Y-%m-%d %H:%M:%S")
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            """
            INSERT INTO jobs
              (title, company, location, remote, skills, contact,
               summary, group_name, timestamp, seen, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                title, company, location, remote,
                json.dumps(skills or []),
                contact, summary, group_name,
                int(datetime.utcnow().timestamp()),
                seen, created_at,
            ),
        )
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# query_jobs tests
# ---------------------------------------------------------------------------

def test_returns_empty_list_when_no_jobs(temp_db):
    from agent.tools.query_tool import query_jobs

    assert query_jobs() == []


def test_returns_job_within_days_window(temp_db):
    from agent.tools.query_tool import query_jobs

    _insert_job(temp_db, title="Python Dev", days_ago=2)
    jobs = query_jobs(days=7)

    assert len(jobs) == 1
    assert jobs[0]["title"] == "Python Dev"


def test_excludes_jobs_outside_days_cutoff(temp_db):
    from agent.tools.query_tool import query_jobs

    _insert_job(temp_db, title="Old Job", days_ago=10)
    jobs = query_jobs(days=7)

    assert jobs == []


def test_days_zero_returns_all_jobs(temp_db):
    from agent.tools.query_tool import query_jobs

    _insert_job(temp_db, title="Recent Job", days_ago=1)
    _insert_job(temp_db, title="Ancient Job", days_ago=365)
    jobs = query_jobs(days=0)

    assert len(jobs) == 2


def test_role_filter_matches_title(temp_db):
    from agent.tools.query_tool import query_jobs

    _insert_job(temp_db, title="Python Backend Developer", days_ago=1)
    _insert_job(temp_db, title="Sales Manager", days_ago=1)
    jobs = query_jobs(days=7, role="python")

    assert len(jobs) == 1
    assert jobs[0]["title"] == "Python Backend Developer"


def test_role_filter_matches_summary(temp_db):
    from agent.tools.query_tool import query_jobs

    _insert_job(temp_db, title="Developer", summary="Looking for a node.js expert", days_ago=1)
    _insert_job(temp_db, title="Designer", summary="Creative role", days_ago=1)
    jobs = query_jobs(days=7, role="node.js")

    assert len(jobs) == 1
    assert jobs[0]["title"] == "Developer"


def test_unseen_only_excludes_seen_jobs(temp_db):
    from agent.tools.query_tool import query_jobs

    _insert_job(temp_db, title="Unseen Job", days_ago=1, seen=0)
    _insert_job(temp_db, title="Already Seen", days_ago=1, seen=1)
    jobs = query_jobs(days=7, unseen_only=True)

    assert len(jobs) == 1
    assert jobs[0]["title"] == "Unseen Job"


def test_limit_caps_results(temp_db):
    from agent.tools.query_tool import query_jobs

    for i in range(5):
        _insert_job(temp_db, title=f"Job {i}", days_ago=1)
    jobs = query_jobs(days=7, limit=3)

    assert len(jobs) == 3


def test_results_ordered_newest_first(temp_db):
    from agent.tools.query_tool import query_jobs

    _insert_job(temp_db, title="Older Job", days_ago=3)
    _insert_job(temp_db, title="Newer Job", days_ago=1)
    jobs = query_jobs(days=7)

    assert jobs[0]["title"] == "Newer Job"
    assert jobs[1]["title"] == "Older Job"


# ---------------------------------------------------------------------------
# format_jobs_telegram tests
# ---------------------------------------------------------------------------

def test_format_jobs_telegram_empty(temp_db):
    from agent.tools.query_tool import format_jobs_telegram

    result = format_jobs_telegram([], days=7)
    assert "No jobs found" in result


def test_format_jobs_telegram_matches_digest_style(temp_db):
    from agent.tools.query_tool import format_jobs_telegram

    jobs = [
        {
            "title": "Python Dev",
            "company": "Acme",
            "location": "Tel Aviv",
            "remote": 0,
            "summary": "Great role",
            "skills": json.dumps(["Python", "FastAPI"]),
            "contact": "hr@acme.com",
        }
    ]
    result = format_jobs_telegram(jobs, days=7)

    assert "*Python Dev* @ Acme (Tel Aviv)" in result
    assert "  Great role" in result
    assert "  Skills: Python, FastAPI" in result
    assert "  Contact: hr@acme.com" in result


# ---------------------------------------------------------------------------
# format_table (CLI) tests
# ---------------------------------------------------------------------------

def test_format_table_empty():
    from agent.list_jobs import format_table

    assert format_table([]) == "No jobs found."


def test_format_table_includes_title_and_company():
    from agent.list_jobs import format_table

    jobs = [{"id": 1, "title": "Backend Dev", "company": "Startup",
             "location": "Remote", "remote": 1, "group_name": "Jobs",
             "created_at": "2026-05-01 10:00:00"}]
    result = format_table(jobs)

    assert "Backend Dev" in result
    assert "Startup" in result
    assert "Remote" in result
