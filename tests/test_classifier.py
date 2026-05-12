"""Classifier tests — mock get_llm so they run without an API key."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agent.chains.classifier import ClassificationResult, classify_message


def _mock_llm(is_job_post: bool, confidence: float) -> MagicMock:
    mock_chain = MagicMock()
    mock_chain.ainvoke = AsyncMock(return_value=ClassificationResult(is_job_post=is_job_post, confidence=confidence))
    mock_llm = MagicMock()
    mock_llm.with_structured_output.return_value = mock_chain
    return mock_llm


@pytest.mark.asyncio
async def test_classifier_returns_structured_dict():
    with patch("agent.chains.classifier.get_llm", return_value=_mock_llm(True, 0.92)):
        result = await classify_message("Hiring a backend engineer at Acme.")

    assert result == {"is_job_post": True, "confidence": 0.92}


@pytest.mark.asyncio
async def test_classifier_handles_negative():
    with patch("agent.chains.classifier.get_llm", return_value=_mock_llm(False, 0.05)):
        result = await classify_message("Anyone tried the new MacBook?")

    assert result["is_job_post"] is False
    assert 0.0 <= result["confidence"] <= 1.0
