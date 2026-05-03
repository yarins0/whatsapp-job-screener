"""Tests for the combined classify+extract chain."""

from __future__ import annotations

import json

import pytest
from langchain_core.language_models.fake_chat_models import FakeListChatModel

from agent.chains.combined import build_combined_chain


def _fake_llm(payload: dict) -> FakeListChatModel:
    return FakeListChatModel(responses=[json.dumps(payload)])


@pytest.mark.asyncio
async def test_combined_returns_job_post_with_jobs():
    response = {
        "is_job_post": True,
        "confidence": 0.95,
        "jobs": [
            {
                "title": "Backend Engineer",
                "company": "Acme",
                "location": "Tel Aviv",
                "remote": True,
                "skills": ["Python", "FastAPI"],
                "salary": "30-40k",
                "contact": "jobs@acme.io",
                "summary": "Backend role at Acme in Tel Aviv.",
            }
        ],
    }
    chain = build_combined_chain(_fake_llm(response))
    result = await chain.ainvoke({"message": "Hiring backend at Acme..."})

    assert result["is_job_post"] is True
    assert result["confidence"] == 0.95
    assert len(result["jobs"]) == 1
    assert result["jobs"][0]["title"] == "Backend Engineer"


@pytest.mark.asyncio
async def test_combined_returns_empty_jobs_for_non_post():
    response = {"is_job_post": False, "confidence": 0.05, "jobs": []}
    chain = build_combined_chain(_fake_llm(response))
    result = await chain.ainvoke({"message": "Good morning everyone!"})

    assert result["is_job_post"] is False
    assert result["jobs"] == []


@pytest.mark.asyncio
async def test_combined_handles_multiple_jobs_in_one_message():
    response = {
        "is_job_post": True,
        "confidence": 0.98,
        "jobs": [
            {
                "title": "Backend Engineer",
                "company": "Acme",
                "location": "Tel Aviv",
                "remote": False,
                "skills": ["Python"],
                "salary": None,
                "contact": "a@acme.io",
                "summary": "Backend role at Acme.",
            },
            {
                "title": "Frontend Engineer",
                "company": "Acme",
                "location": "Tel Aviv",
                "remote": True,
                "skills": ["React"],
                "salary": None,
                "contact": "b@acme.io",
                "summary": "Frontend role at Acme.",
            },
        ],
    }
    chain = build_combined_chain(_fake_llm(response))
    result = await chain.ainvoke({"message": "Acme is hiring backend and frontend engineers..."})

    assert result["is_job_post"] is True
    assert len(result["jobs"]) == 2
    titles = {j["title"] for j in result["jobs"]}
    assert titles == {"Backend Engineer", "Frontend Engineer"}
