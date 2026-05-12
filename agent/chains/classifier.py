"""Classifier — decides whether a WhatsApp message is a job posting."""

from __future__ import annotations

from pydantic import BaseModel, Field

from langchain_core.messages import HumanMessage

from agent.chains.llm_factory import build_system_message, get_llm


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


async def classify_message(message: str) -> dict:
    """Classify whether a message is a job posting.

    Returns a dict with keys ``is_job_post`` (bool) and ``confidence`` (float).
    """
    chain = get_llm().with_structured_output(ClassificationResult)
    result: ClassificationResult = await chain.ainvoke([
        build_system_message(_SYSTEM_PROMPT),
        HumanMessage(content=f"Message:\n{message}"),
    ])
    return result.model_dump()
