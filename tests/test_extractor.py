"""Extractor tests — use a FakeListChatModel for offline determinism."""

from __future__ import annotations

import json

import pytest
from langchain_core.language_models.fake_chat_models import FakeListChatModel

from agent.chains.extractor import build_extractor_chain


def _fake_llm(payload: dict) -> FakeListChatModel:
    return FakeListChatModel(responses=[json.dumps(payload)])


@pytest.mark.asyncio
async def test_extractor_returns_jobpost_dict():
    fake_response = {
        "title": "Senior Backend Engineer",
        "company": "Acme Inc.",
        "location": "Tel Aviv",
        "remote": True,
        "skills": ["Python", "FastAPI", "PostgreSQL"],
        "salary": "30-40k ILS",
        "contact": "jobs@acme.io",
        "summary": "Senior Backend Engineer at Acme in Tel Aviv (hybrid).",
    }
    chain = build_extractor_chain(_fake_llm(fake_response))

    result = await chain.ainvoke({"message": "Hiring senior backend at Acme..."})

    assert result["title"] == "Senior Backend Engineer"
    assert result["remote"] is True
    assert "Python" in result["skills"]
    assert result["contact"] == "jobs@acme.io"


@pytest.mark.asyncio
async def test_extractor_allows_null_fields():
    fake_response = {
        "title": "Node.js developer",
        "company": None,
        "location": None,
        "remote": None,
        "skills": [],
        "salary": None,
        "contact": None,
        "summary": "A Node.js role.",
    }
    chain = build_extractor_chain(_fake_llm(fake_response))

    result = await chain.ainvoke({"message": "Some thin job post"})

    assert result["title"] == "Node.js developer"
    assert result["company"] is None
    assert result["skills"] == []
