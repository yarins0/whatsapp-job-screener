"""Tests for the combined classify+extract function."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agent.chains.combined import CombinedResult, JobFields, classify_and_extract


def _mock_client(is_job_post: bool, confidence: float, jobs: list[dict]) -> MagicMock:
    parsed_jobs = [JobFields(**j) for j in jobs]
    mock_response = MagicMock()
    mock_response.parsed_output = CombinedResult(
        is_job_post=is_job_post, confidence=confidence, jobs=parsed_jobs
    )
    mock_client = MagicMock()
    mock_client.messages.parse = AsyncMock(return_value=mock_response)
    return mock_client


_JOB_BACKEND = {
    "title": "Backend Engineer", "company": "Acme", "location": "Tel Aviv",
    "remote": True, "skills": ["Python", "FastAPI"], "salary": "30-40k",
    "contact": "jobs@acme.io", "summary": "Backend role at Acme in Tel Aviv.",
}

_JOB_FRONTEND = {
    "title": "Frontend Engineer", "company": "Acme", "location": "Tel Aviv",
    "remote": True, "skills": ["React"], "salary": None,
    "contact": "b@acme.io", "summary": "Frontend role at Acme.",
}


@pytest.mark.asyncio
async def test_combined_returns_job_post_with_jobs():
    with patch("agent.chains.combined._client", _mock_client(True, 0.95, [_JOB_BACKEND])):
        result = await classify_and_extract("Hiring backend at Acme...")

    assert result["is_job_post"] is True
    assert result["confidence"] == 0.95
    assert len(result["jobs"]) == 1
    assert result["jobs"][0]["title"] == "Backend Engineer"


@pytest.mark.asyncio
async def test_combined_returns_empty_jobs_for_non_post():
    with patch("agent.chains.combined._client", _mock_client(False, 0.05, [])):
        result = await classify_and_extract("Good morning everyone!")

    assert result["is_job_post"] is False
    assert result["jobs"] == []


@pytest.mark.asyncio
async def test_combined_handles_multiple_jobs_in_one_message():
    with patch("agent.chains.combined._client", _mock_client(True, 0.98, [_JOB_BACKEND, _JOB_FRONTEND])):
        result = await classify_and_extract("Acme is hiring backend and frontend engineers...")

    assert result["is_job_post"] is True
    assert len(result["jobs"]) == 2
    titles = {j["title"] for j in result["jobs"]}
    assert titles == {"Backend Engineer", "Frontend Engineer"}
