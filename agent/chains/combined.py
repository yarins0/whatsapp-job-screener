"""Combined classify+extract chain — one API call for high-density job groups."""

from __future__ import annotations

import os
from typing import List, Optional

import anthropic
from pydantic import BaseModel, Field

from agent.chains.cache_config import get_cache_control


class JobFields(BaseModel):
    title: str = Field(description="The role / job title. Required.")
    company: Optional[str] = Field(default=None)
    location: Optional[str] = Field(default=None)
    remote: Optional[bool] = Field(default=None)
    skills: List[str] = Field(default_factory=list)
    salary: Optional[str] = Field(default=None)
    contact: Optional[str] = Field(default=None)
    summary: str = Field(description="One-sentence summary (<= 25 words).")


class CombinedResult(BaseModel):
    is_job_post: bool = Field(description="True iff the message contains at least one job posting.")
    confidence: float = Field(ge=0.0, le=1.0)
    jobs: List[JobFields] = Field(
        default_factory=list,
        description="All job postings found. Empty list when is_job_post is false.",
    )


_SYSTEM_PROMPT = """You are a classifier and extractor for WhatsApp job-posting messages.

Step 1 — classify:
Determine whether the message contains one or more job postings.
A job post typically has at least two of: role/title, company, contact info, location/salary/skills.
It is NOT a job post if it is someone looking for work, a question, news, or general chat.

Step 2 — extract (only when is_job_post is true):
For each job posting found in the message, extract the structured fields.
Rules:
  * A single message may contain multiple job postings — extract all of them.
  * If a field is not mentioned, use null (or [] for skills).
  * "remote" is true only if explicitly stated. False if explicitly on-site. Otherwise null.
  * "skills" is a flat list of concrete technologies (e.g. "Python", "React"), not soft skills.
  * "summary" must be a single neutral sentence (<= 25 words).
  * Preserve the original language for titles and company names.
  * "contact" must include any URL (http:// or https://) present in the message — even if it
    is not explicitly labelled as a contact link. A URL is always the contact field unless
    a different field is a more obvious fit. If there are multiple URLs, prefer the one that
    looks like an application or job link.

When is_job_post is false, set jobs to []."""

_MODEL = os.getenv("LLM_MODEL") or "claude-haiku-4-5-20251001"

# Module-level client — replaced in tests via patch("agent.chains.combined._client").
_client: anthropic.AsyncAnthropic | None = None


def _get_client() -> anthropic.AsyncAnthropic:
    global _client
    if _client is None:
        _client = anthropic.AsyncAnthropic()
    return _client


async def classify_and_extract(message: str) -> dict:
    """Classify and extract jobs in a single API call.

    Returns a dict with keys ``is_job_post`` (bool), ``confidence`` (float),
    and ``jobs`` (list of job dicts).
    """
    system_block: dict = {"type": "text", "text": _SYSTEM_PROMPT}
    cache_control = get_cache_control()
    if cache_control is not None:
        system_block["cache_control"] = cache_control

    response = await _get_client().messages.parse(
        model=_MODEL,
        max_tokens=1024,
        system=[system_block],
        messages=[{"role": "user", "content": f"Message:\n{message}"}],
        output_format=CombinedResult,
    )
    result = response.parsed_output
    return {
        "is_job_post": result.is_job_post,
        "confidence": result.confidence,
        "jobs": [j.model_dump() for j in result.jobs],
    }
