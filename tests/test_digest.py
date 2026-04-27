"""Tests for the digest formatter and DB selection (no Telegram call)."""

from __future__ import annotations

import json
import sqlite3

from agent.tools.store_tool import store_job
from digest.digest import _fetch_unseen, _mark_seen, format_digest


def _job(**overrides):
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


def test_format_digest_renders_markdown():
    rows = [
        {
            "id": 1,
            "title": "Senior Backend Engineer",
            "company": "Acme",
            "location": "Tel Aviv",
            "remote": 1,
            "skills": json.dumps(["Python", "FastAPI"]),
            "salary": "30k",
            "contact": "jobs@acme.io",
            "summary": "Senior backend at Acme.",
            "group_name": "Tech Jobs TLV",
            "timestamp": 1700000000,
        }
    ]
    text = format_digest(rows)

    assert "Daily Job Digest" in text
    assert "*Senior Backend Engineer*" in text
    assert "Remote" in text
    assert "Python" in text
    assert "jobs@acme.io" in text


def test_fetch_and_mark_seen_roundtrip(temp_db):
    job_id = store_job(_job())

    unseen = _fetch_unseen()
    assert len(unseen) == 1
    assert unseen[0]["title"] == "Senior Backend Engineer"

    _mark_seen([job_id])

    assert _fetch_unseen() == []

    # Sanity check: row is still there but flagged seen
    conn = sqlite3.connect(temp_db)
    try:
        seen = conn.execute("SELECT seen FROM jobs WHERE id = ?", (job_id,)).fetchone()[0]
    finally:
        conn.close()
    assert seen == 1


def test_format_digest_empty_returns_empty_string():
    assert format_digest([]) == ""
