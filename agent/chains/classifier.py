"""Classifier chain — decides whether a WhatsApp message is a job posting.

LangChain concepts demonstrated:
  * ChatPromptTemplate (system + human turns)
  * Pydantic schema for typed output
  * JsonOutputParser
  * LCEL pipe operator: prompt | llm | parser
"""

from __future__ import annotations

from langchain_core.language_models import BaseLanguageModel
from langchain_core.output_parsers import JsonOutputParser
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.runnables import Runnable
from pydantic import BaseModel, Field


class ClassificationResult(BaseModel):
    """Output schema for the classifier."""

    is_job_post: bool = Field(description="True iff the message is a job posting.")
    confidence: float = Field(
        description="Model confidence between 0.0 and 1.0.", ge=0.0, le=1.0
    )


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
{{"is_job_post": <true|false>, "confidence": <float 0..1>}}
Do not include any other text."""


def build_classifier_chain(llm: BaseLanguageModel) -> Runnable:
    """Build the classifier LCEL chain.

    Args:
        llm: any LangChain chat model (ChatAnthropic in production,
             FakeListChatModel in tests).

    Returns:
        A Runnable taking ``{"message": str}`` and returning a dict
        with keys ``is_job_post`` (bool) and ``confidence`` (float).
    """
    prompt = ChatPromptTemplate.from_messages(
        [("system", _SYSTEM_PROMPT), ("human", "Message:\n{message}")]
    )
    parser = JsonOutputParser(pydantic_object=ClassificationResult)
    return prompt | llm | parser
