"""Classifier tests — use a FakeListChatModel so they run without an API key."""

from __future__ import annotations

import json

import pytest
from langchain_core.language_models.fake_chat_models import FakeListChatModel

from agent.chains.classifier import build_classifier_chain


def _fake_llm(responses: list[dict]) -> FakeListChatModel:
    """Wrap a list of dict responses as a fake chat model that returns JSON strings."""
    return FakeListChatModel(responses=[json.dumps(r) for r in responses])


@pytest.mark.asyncio
async def test_classifier_returns_structured_dict():
    llm = _fake_llm([{"is_job_post": True, "confidence": 0.92}])
    chain = build_classifier_chain(llm)

    result = await chain.ainvoke({"message": "Hiring a backend engineer at Acme."})

    assert result == {"is_job_post": True, "confidence": 0.92}


@pytest.mark.asyncio
async def test_classifier_handles_negative():
    llm = _fake_llm([{"is_job_post": False, "confidence": 0.05}])
    chain = build_classifier_chain(llm)

    result = await chain.ainvoke({"message": "Anyone tried the new MacBook?"})

    assert result["is_job_post"] is False
    assert 0.0 <= result["confidence"] <= 1.0
