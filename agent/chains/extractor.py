"""Extractor — pulls structured job fields from a message."""

from __future__ import annotations

import os
from typing import List, Optional

import anthropic
from pydantic import BaseModel, Field

from agent.chains.cache_config import get_cache_control


class JobPost(BaseModel):
    title: str = Field(description="The role / job title. Required.")
    company: Optional[str] = Field(default=None, description="Hiring company name.")
    location: Optional[str] = Field(default=None, description="City, region, or country.")
    remote: Optional[bool] = Field(
        default=None,
        description="True if explicitly remote, false if explicitly on-site, null if unclear.",
    )
    skills: List[str] = Field(
        default_factory=list, description="Concrete technologies, languages, or skills."
    )
    salary: Optional[str] = Field(default=None, description="Salary or range as written.")
    contact: Optional[str] = Field(default=None, description="Email, phone, link, or instructions to apply.")
    summary: str = Field(description="A one-sentence summary the digest can quote.")


class ExtractionResult(BaseModel):
    jobs: List[JobPost] = Field(description="All job postings found in the message.")


_SYSTEM_PROMPT = """You extract structured job-posting data from WhatsApp messages.

Rules:
  * Return a JSON object with a "jobs" array. Each element matches the job schema.
    If the message contains a single job, the array has one element.
  * If a field is not mentioned, use null (or [] for skills).
  * "remote" is true only if the post explicitly says remote/hybrid-remote/work-from-home.
    Use false if it explicitly says on-site/in-office. Otherwise null.
  * "skills" is a flat list of strings; pull concrete tech (e.g. "Python", "React"),
    not soft skills.
  * "summary" must be a single neutral sentence (<= 25 words) describing the role.
  * Preserve the original language of the post when sensible (e.g. Hebrew titles).
  * "contact" must include any URL (http:// or https://) present in the message — even if it
    is not explicitly labelled as a contact link. A URL is always the contact field unless
    a different field is a more obvious fit. If there are multiple URLs, prefer the one that
    looks like an application or job link."""

_MODEL = os.getenv("LLM_MODEL") or "claude-haiku-4-5-20251001"

# Module-level client — replaced in tests via patch("agent.chains.extractor._client").
_client: anthropic.AsyncAnthropic | None = None


def _get_client() -> anthropic.AsyncAnthropic:
    global _client
    if _client is None:
        _client = anthropic.AsyncAnthropic()
    return _client


async def extract_job(message: str) -> list[dict]:
    """Extract structured job fields from a message.

    Returns a list of job dicts, one per job posting found in the message.
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
        output_format=ExtractionResult,
    )
    return [job.model_dump() for job in response.parsed_output.jobs]
