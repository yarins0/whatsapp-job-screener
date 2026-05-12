"""Extractor — pulls structured job fields from a message."""

from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel, Field

from langchain_core.messages import HumanMessage

from agent.chains.llm_factory import build_system_message, get_llm


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
  * If a "[Link preview]" block is present, prefer its title for the "title" field
    over any title mentioned in the message body — the preview is more structured.
  * "contact" must include any URL (http:// or https://) present in the message — even if it
    is not explicitly labelled as a contact link. A URL is always the contact field unless
    a different field is a more obvious fit. If there are multiple URLs, prefer the one that
    looks like an application or job link."""


async def extract_job(message: str) -> list[dict]:
    """Extract structured job fields from a message.

    Returns a list of job dicts, one per job posting found in the message.
    """
    chain = get_llm().with_structured_output(ExtractionResult)
    result: ExtractionResult = await chain.ainvoke([
        build_system_message(_SYSTEM_PROMPT),
        HumanMessage(content=f"Message:\n{message}"),
    ])
    return [job.model_dump() for job in result.jobs]
