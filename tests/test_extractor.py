"""Extractor tests — mock the Anthropic client for offline determinism."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agent.chains.extractor import ExtractionResult, JobPost, extract_job


def _mock_client(jobs: list[dict]) -> MagicMock:
    parsed_jobs = [JobPost(**j) for j in jobs]
    mock_response = MagicMock()
    mock_response.parsed_output = ExtractionResult(jobs=parsed_jobs)
    mock_client = MagicMock()
    mock_client.messages.parse = AsyncMock(return_value=mock_response)
    return mock_client


@pytest.mark.asyncio
async def test_extractor_returns_jobpost_list():
    job = {
        "title": "Senior Backend Engineer",
        "company": "Acme Inc.",
        "location": "Tel Aviv",
        "remote": True,
        "skills": ["Python", "FastAPI", "PostgreSQL"],
        "salary": "30-40k ILS",
        "contact": "jobs@acme.io",
        "summary": "Senior Backend Engineer at Acme in Tel Aviv (hybrid).",
    }
    with patch("agent.chains.extractor._client", _mock_client([job])):
        result = await extract_job("Hiring senior backend at Acme...")

    assert isinstance(result, list)
    assert len(result) == 1
    assert result[0]["title"] == "Senior Backend Engineer"
    assert result[0]["remote"] is True
    assert "Python" in result[0]["skills"]
    assert result[0]["contact"] == "jobs@acme.io"


@pytest.mark.asyncio
async def test_extractor_allows_null_fields():
    job = {
        "title": "Node.js developer",
        "company": None,
        "location": None,
        "remote": None,
        "skills": [],
        "salary": None,
        "contact": None,
        "summary": "A Node.js role.",
    }
    with patch("agent.chains.extractor._client", _mock_client([job])):
        result = await extract_job("Some thin job post")

    assert result[0]["title"] == "Node.js developer"
    assert result[0]["company"] is None
    assert result[0]["skills"] == []


@pytest.mark.asyncio
async def test_extractor_returns_multiple_jobs():
    jobs = [
        {"title": "Backend Engineer", "company": "Acme", "location": "TLV",
         "remote": False, "skills": ["Python"], "salary": None,
         "contact": "a@acme.io", "summary": "Backend role."},
        {"title": "Frontend Engineer", "company": "Acme", "location": "TLV",
         "remote": True, "skills": ["React"], "salary": None,
         "contact": "b@acme.io", "summary": "Frontend role."},
    ]
    with patch("agent.chains.extractor._client", _mock_client(jobs)):
        result = await extract_job("Two openings at Acme...")

    assert len(result) == 2
    titles = {j["title"] for j in result}
    assert titles == {"Backend Engineer", "Frontend Engineer"}
