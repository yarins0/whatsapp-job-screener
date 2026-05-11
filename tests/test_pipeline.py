"""End-to-end pipeline tests — mock the chain functions, test everything else live."""

from __future__ import annotations

import sqlite3
from unittest.mock import AsyncMock, patch

import pytest

from agent.pipeline import run_pipeline


def _seed_combined_mode(db_path, group: str) -> None:
    """Seed group_stats so get_pipeline_mode() returns 'combined' for this group."""
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            "INSERT INTO group_stats (group_name, total_messages, job_post_messages) VALUES (?,?,?)",
            (group, 100, 95),
        )
        conn.commit()
    finally:
        conn.close()


def _mock_classify(is_job_post: bool, confidence: float = 0.95):
    return AsyncMock(return_value={"is_job_post": is_job_post, "confidence": confidence})


def _mock_extract(jobs):
    jobs_list = jobs if isinstance(jobs, list) else [jobs]
    return AsyncMock(return_value=jobs_list)


def _mock_combined(is_job_post: bool, confidence: float, jobs):
    jobs_list = jobs if isinstance(jobs, list) else [jobs]
    return AsyncMock(return_value={
        "is_job_post": is_job_post,
        "confidence": confidence,
        "jobs": jobs_list,
    })


_JOB_BACKEND = {
    "title": "Backend Engineer",
    "company": "Acme",
    "location": "Tel Aviv",
    "remote": True,
    "skills": ["Python", "FastAPI"],
    "salary": "30-40k",
    "contact": "jobs@acme.io",
    "summary": "Backend role at Acme in Tel Aviv (hybrid).",
}

_JOB_FRONTEND = {
    "title": "Frontend Engineer",
    "company": "Acme",
    "location": "Tel Aviv",
    "remote": True,
    "skills": ["React"],
    "salary": None,
    "contact": "fe@acme.io",
    "summary": "Frontend role at Acme.",
}

_MSG = {"group": "Tech Jobs TLV", "sender": "demo", "text": "Hiring...", "timestamp": 1700000000}


@pytest.mark.asyncio
async def test_stores_a_qualified_job(temp_db):
    with patch("agent.graph.classify_message", _mock_classify(True, 0.95)), \
         patch("agent.graph.extract_job", _mock_extract(_JOB_BACKEND)):
        result = await run_pipeline(_MSG, notify=False)

    assert result["action"] == "stored"
    assert result["job"]["title"] == "Backend Engineer"

    conn = sqlite3.connect(temp_db)
    try:
        n = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
    finally:
        conn.close()
    assert n == 1


@pytest.mark.asyncio
async def test_skips_non_job_post(temp_db):
    msg = {"group": "g", "sender": "s", "text": "anyone tried the new MacBook?", "timestamp": 0}
    with patch("agent.graph.classify_message", _mock_classify(False, 0.02)):
        result = await run_pipeline(msg, notify=False)

    assert result["action"] == "skipped"
    assert "not a job post" in result["reason"]


@pytest.mark.asyncio
async def test_skips_unwanted_role(temp_db):
    job = {
        "title": "Accountant", "company": "Acme", "location": "Tel Aviv",
        "remote": False, "skills": [], "salary": None,
        "contact": "jobs@acme.io", "summary": "Accounting role.",
    }
    msg = {"group": "g", "sender": "s", "text": "Accountant wanted", "timestamp": 0}

    with patch("agent.graph.classify_message", _mock_classify(True, 0.9)), \
         patch("agent.graph.extract_job", _mock_extract(job)):
        result = await run_pipeline(msg, notify=False)

    assert result["action"] == "skipped"
    assert result["reason"] == "no role keyword matched"


@pytest.mark.asyncio
async def test_skips_duplicate(temp_db):
    msg = {"group": "g", "sender": "s", "text": "Hiring backend engineer", "timestamp": 0}

    with patch("agent.graph.classify_message", _mock_classify(True, 0.9)), \
         patch("agent.graph.extract_job", _mock_extract(_JOB_BACKEND)):
        first = await run_pipeline(msg, notify=False)
    assert first["action"] == "stored"

    with patch("agent.graph.classify_message", _mock_classify(True, 0.9)), \
         patch("agent.graph.extract_job", _mock_extract(_JOB_BACKEND)):
        second = await run_pipeline(msg, notify=False)
    assert second["action"] == "skipped"
    assert second["reason"] == "duplicate"


@pytest.mark.asyncio
async def test_stores_multiple_jobs_from_one_message(temp_db):
    msg = {"group": "g", "sender": "s", "text": "Two openings at Acme...", "timestamp": 0}

    with patch("agent.graph.classify_message", _mock_classify(True, 0.97)), \
         patch("agent.graph.extract_job", _mock_extract([_JOB_BACKEND, _JOB_FRONTEND])):
        result = await run_pipeline(msg, notify=False)

    assert result["action"] == "stored"
    assert len(result["stored"]) == 2

    conn = sqlite3.connect(temp_db)
    try:
        n = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
    finally:
        conn.close()
    assert n == 2


@pytest.mark.asyncio
async def test_partial_store_when_one_of_two_jobs_is_duplicate(temp_db):
    msg = {"group": "g", "sender": "s", "text": "Two openings...", "timestamp": 0}

    with patch("agent.graph.classify_message", _mock_classify(True, 0.95)), \
         patch("agent.graph.extract_job", _mock_extract(_JOB_BACKEND)):
        await run_pipeline(msg, notify=False)

    with patch("agent.graph.classify_message", _mock_classify(True, 0.95)), \
         patch("agent.graph.extract_job", _mock_extract([_JOB_BACKEND, _JOB_FRONTEND])):
        result = await run_pipeline(msg, notify=False)

    assert result["action"] == "partial"
    assert len(result["stored"]) == 1
    assert result["stored"][0]["title"] == "Frontend Engineer"
    assert len(result["skipped"]) == 1
    assert result["skipped"][0]["reason"] == "duplicate"


@pytest.mark.asyncio
async def test_pipeline_uses_combined_mode_for_high_density_group(temp_db):
    _seed_combined_mode(temp_db, "jobs@g.us")
    msg = {"group": "jobs@g.us", "sender": "s", "text": "Hiring backend at Acme...", "timestamp": 0}

    with patch("agent.graph.classify_and_extract", _mock_combined(True, 0.96, _JOB_BACKEND)):
        result = await run_pipeline(msg, notify=False)

    assert result["action"] == "stored"
    assert result["job"]["title"] == "Backend Engineer"


@pytest.mark.asyncio
async def test_pipeline_records_stats_for_job_post(temp_db):
    msg = {"group": "stats-group@g.us", "sender": "s", "text": "Hiring...", "timestamp": 0}

    with patch("agent.graph.classify_message", _mock_classify(True, 0.95)), \
         patch("agent.graph.extract_job", _mock_extract(_JOB_BACKEND)):
        await run_pipeline(msg, notify=False)

    conn = sqlite3.connect(temp_db)
    try:
        row = conn.execute(
            "SELECT total_messages, job_post_messages FROM group_stats WHERE group_name=?",
            ("stats-group@g.us",),
        ).fetchone()
    finally:
        conn.close()
    assert row == (1, 1)


@pytest.mark.asyncio
async def test_pipeline_records_stats_for_non_job_post(temp_db):
    msg = {"group": "stats-group@g.us", "sender": "s", "text": "Morning!", "timestamp": 0}

    with patch("agent.graph.classify_message", _mock_classify(False, 0.02)):
        await run_pipeline(msg, notify=False)

    conn = sqlite3.connect(temp_db)
    try:
        row = conn.execute(
            "SELECT total_messages, job_post_messages FROM group_stats WHERE group_name=?",
            ("stats-group@g.us",),
        ).fetchone()
    finally:
        conn.close()
    assert row == (1, 0)
