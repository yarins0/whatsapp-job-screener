"""Classifier — decides whether a WhatsApp message is a job posting."""

from __future__ import annotations

import os
from typing import Optional

import anthropic
from pydantic import BaseModel, Field

from agent.chains.cache_config import get_cache_control


class ClassificationResult(BaseModel):
    is_job_post: bool = Field(description="True iff the message is a job posting.")
    confidence: float = Field(ge=0.0, le=1.0, description="Model confidence between 0.0 and 1.0.")


_SYSTEM_PROMPT = """You are a classifier that detects job postings in WhatsApp group messages.

A job post typically contains at least two of:
  - a role or job title (e.g. "Backend engineer", "מפתח/ת Node.js")
  - a company, employer, or hiring team
  - contact information or application instructions (email, phone, link, "DM me")
  - location, salary, or required skills

It is NOT a job post if it is:
  - someone *looking for* a job ("I'm available", "open to roles")
  - a question, news article, link share, or general chat
  - a recruiter announcing they are open to chat without a specific role

Respond with JSON only, matching exactly:
{"is_job_post": <true|false>, "confidence": <float 0..1>}
Do not include any other text."""

_MODEL = os.getenv("LLM_MODEL") or "claude-haiku-4-5-20251001"

# Module-level client — replaced in tests via patch("agent.chains.classifier._client").
_client: anthropic.AsyncAnthropic | None = None


def _get_client() -> anthropic.AsyncAnthropic:
    global _client
    if _client is None:
        _client = anthropic.AsyncAnthropic()
    return _client


async def classify_message(message: str) -> dict:
    """Classify whether a message is a job posting.

    Returns a dict with keys ``is_job_post`` (bool) and ``confidence`` (float).
    """
    system_block: dict = {"type": "text", "text": _SYSTEM_PROMPT}
    cache_control = get_cache_control()
    if cache_control is not None:
        system_block["cache_control"] = cache_control

    response = await _get_client().messages.parse(
        model=_MODEL,
        max_tokens=256,
        system=[system_block],
        messages=[{"role": "user", "content": f"Message:\n{message}"}],
        output_format=ClassificationResult,
    )
    return response.parsed_output.model_dump()
