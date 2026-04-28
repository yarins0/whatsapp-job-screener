"""End-to-end pipeline test using a fake LLM with scripted JSON responses."""

from __future__ import annotations

import json
import sqlite3

import pytest
from langchain_core.language_models.fake_chat_models import FakeListChatModel

from agent.pipeline import run_pipeline


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
        "title": "Marketing Manager",
        "company": "Acme",
        "location": "Tel Aviv",
        "remote": False,
        "skills": [],
        "salary": None,
        "contact": "jobs@acme.io",
        "summary": "Marketing manager role.",
    }
    msg = {"group": "g", "sender": "s", "text": "Marketing manager wanted", "timestamp": 0}

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
