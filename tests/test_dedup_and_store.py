"""Tests for dedup_tool and store_tool against an isolated SQLite DB."""

from __future__ import annotations

import json
import sqlite3

from agent.tools.dedup_tool import is_duplicate
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
