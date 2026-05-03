"""End-to-end pipeline tests using a fake LLM with scripted JSON responses."""

from __future__ import annotations

import json
import sqlite3

import pytest
from langchain_core.language_models.fake_chat_models import FakeListChatModel

from agent.pipeline import run_pipeline


def _seed_combined_mode(db_path, group: str) -> None:
    """Seed group_stats so get_pipeline_mode() returns 'combined' for this group."""
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            "INSERT INTO group_stats (group_name, total_messages, job_post_messages) VALUES (?,?,?)",
            (group, 100, 95),  # 95% job posts — well above the 70% threshold
        )
        conn.commit()
    finally:
        conn.close()


def _llm(*payloads: dict) -> FakeListChatModel:
    return FakeListChatModel(responses=[json.dumps(p) for p in payloads])


@pytest.mark.asyncio
async def test_stores_a_qualified_job(temp_db):
    classification = {"is_job_post": True, "confidence": 0.95}
    extraction = {
        "title": "Backend Engineer",
        "company": "Acme",
        "location": "Tel Aviv",
        "remote": True,
        "skills": ["Python", "FastAPI"],
        "salary": "30-40k",
        "contact": "jobs@acme.io",
        "summary": "Backend role at Acme in Tel Aviv (hybrid).",
    }

    msg = {
        "group": "Tech Jobs TLV",
        "sender": "demo",
        "text": "Hiring backend engineer at Acme...",
        "timestamp": 1700000000,
    }
    result = await run_pipeline(msg, llm=_llm(classification, extraction))

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
    classification = {"is_job_post": False, "confidence": 0.02}

    result = await run_pipeline(msg, llm=_llm(classification))

    assert result["action"] == "skipped"
    assert "not a job post" in result["reason"]


@pytest.mark.asyncio
async def test_skips_unwanted_role(temp_db):
    classification = {"is_job_post": True, "confidence": 0.9}
    extraction = {
        "title": "Accountant",
        "company": "Acme",
        "location": "Tel Aviv",
        "remote": False,
        "skills": [],
        "salary": None,
        "contact": "jobs@acme.io",
        "summary": "Accounting role.",
    }
    msg = {"group": "g", "sender": "s", "text": "Accountant wanted", "timestamp": 0}

    result = await run_pipeline(msg, llm=_llm(classification, extraction))

    assert result["action"] == "skipped"
    assert result["reason"] == "no role keyword matched"


@pytest.mark.asyncio
async def test_skips_duplicate(temp_db):
    classification = {"is_job_post": True, "confidence": 0.9}
    extraction = {
        "title": "Backend Engineer",
        "company": "Acme",
        "location": "Tel Aviv",
        "remote": True,
        "skills": ["Python"],
        "salary": None,
        "contact": "jobs@acme.io",
        "summary": "Backend role at Acme.",
    }
    msg = {"group": "g", "sender": "s", "text": "Hiring backend engineer", "timestamp": 0}

    # First run stores
    first = await run_pipeline(msg, llm=_llm(classification, extraction))
    assert first["action"] == "stored"

    # Second run should dedup *before* storing
    second = await run_pipeline(msg, llm=_llm(classification, extraction))
    assert second["action"] == "skipped"
    assert second["reason"] == "duplicate"


@pytest.mark.asyncio
async def test_stores_multiple_jobs_from_one_message(temp_db):
    """A message containing two job posts should store both independently."""
    classification = {"is_job_post": True, "confidence": 0.97}
    # Extractor returns a list — two job posts in one message
    extraction = [
        {
            "title": "Backend Engineer",
            "company": "Acme",
            "location": "Tel Aviv",
            "remote": False,
            "skills": ["Python"],
            "salary": None,
            "contact": "be@acme.io",
            "summary": "Backend role at Acme.",
        },
        {
            "title": "Frontend Engineer",
            "company": "Acme",
            "location": "Tel Aviv",
            "remote": True,
            "skills": ["React"],
            "salary": None,
            "contact": "fe@acme.io",
            "summary": "Frontend role at Acme.",
        },
    ]
    msg = {"group": "g", "sender": "s", "text": "Two openings at Acme...", "timestamp": 0}

    result = await run_pipeline(msg, llm=_llm(classification, extraction))

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
    """With two jobs, if one is a duplicate only the new one is stored."""
    classification = {"is_job_post": True, "confidence": 0.95}
    job_a = {
        "title": "Backend Engineer", "company": "Acme", "location": "TLV",
        "remote": True, "skills": ["Python"], "salary": None,
        "contact": "be@acme.io", "summary": "Backend role.",
    }
    job_b = {
        "title": "Frontend Engineer", "company": "Acme", "location": "TLV",
        "remote": True, "skills": ["React"], "salary": None,
        "contact": "fe@acme.io", "summary": "Frontend role.",
    }
    msg = {"group": "g", "sender": "s", "text": "Two openings...", "timestamp": 0}

    # First run: store job_a alone
    await run_pipeline(msg, llm=_llm(classification, job_a))

    # Second run: message contains both job_a (dup) and job_b (new)
    result = await run_pipeline(msg, llm=_llm(classification, [job_a, job_b]))

    assert result["action"] == "partial"
    assert len(result["stored"]) == 1
    assert result["stored"][0]["title"] == "Frontend Engineer"
    assert len(result["skipped"]) == 1
    assert result["skipped"][0]["reason"] == "duplicate"


@pytest.mark.asyncio
async def test_pipeline_uses_combined_mode_for_high_density_group(temp_db):
    """Pipeline issues a single combined LLM call for a known high-density group."""
    _seed_combined_mode(temp_db, "jobs@g.us")

    # Combined chain receives ONE scripted response containing both
    # classification and extraction.
    combined_response = {
        "is_job_post": True,
        "confidence": 0.96,
        "jobs": [
            {
                "title": "Backend Engineer",
                "company": "Acme",
                "location": "Tel Aviv",
                "remote": True,
                "skills": ["Python"],
                "salary": None,
                "contact": "jobs@acme.io",
                "summary": "Backend role at Acme.",
            }
        ],
    }
    msg = {
        "group": "jobs@g.us",
        "sender": "s",
        "text": "Hiring backend at Acme...",
        "timestamp": 0,
    }

    result = await run_pipeline(msg, llm=_llm(combined_response))

    assert result["action"] == "stored"
    assert result["job"]["title"] == "Backend Engineer"


@pytest.mark.asyncio
async def test_pipeline_records_stats_for_job_post(temp_db):
    """A stored job increments the group's job_post_messages counter."""
    classification = {"is_job_post": True, "confidence": 0.95}
    extraction = {
        "title": "Backend Engineer", "company": "Acme", "location": "TLV",
        "remote": True, "skills": ["Python"], "salary": None,
        "contact": "jobs@acme.io", "summary": "Backend role.",
    }
    msg = {"group": "stats-group@g.us", "sender": "s", "text": "Hiring...", "timestamp": 0}

    await run_pipeline(msg, llm=_llm(classification, extraction))

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
    """A skipped non-job message increments total_messages but not job_post_messages."""
    classification = {"is_job_post": False, "confidence": 0.02}
    msg = {"group": "stats-group@g.us", "sender": "s", "text": "Morning!", "timestamp": 0}

    await run_pipeline(msg, llm=_llm(classification))

    conn = sqlite3.connect(temp_db)
    try:
        row = conn.execute(
            "SELECT total_messages, job_post_messages FROM group_stats WHERE group_name=?",
            ("stats-group@g.us",),
        ).fetchone()
    finally:
        conn.close()

    assert row == (1, 0)
