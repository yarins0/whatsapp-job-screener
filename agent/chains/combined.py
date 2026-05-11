"""Combined classify+extract chain.

Used by the adaptive pipeline for high-density job groups (>=70% job posts).
Merges the classifier and extractor into a single LLM call that returns both
the classification decision AND the extracted job list in one JSON response.

When is_job_post is false the jobs list will be empty and the pipeline skips
the dedup/filter/store steps without a second LLM call.
"""

from __future__ import annotations

from typing import List, Optional

from langchain_core.language_models import BaseLanguageModel
from langchain_core.output_parsers import JsonOutputParser
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.runnables import Runnable
from pydantic import BaseModel, Field


class JobFields(BaseModel):
    """Extracted fields for a single job within a combined response."""

    title: str = Field(description="The role / job title. Required.")
    company: Optional[str] = Field(default=None)
    location: Optional[str] = Field(default=None)
    remote: Optional[bool] = Field(default=None)
    skills: List[str] = Field(default_factory=list)
    salary: Optional[str] = Field(default=None)
    contact: Optional[str] = Field(default=None)
    summary: str = Field(description="One-sentence summary (<= 25 words).")


class CombinedResult(BaseModel):
    """Output schema for the combined classify+extract chain."""

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

Respond with JSON only:
{{"is_job_post": <true|false>, "confidence": <float 0..1>, "jobs": [<job>, ...]}}
When is_job_post is false, set jobs to [].
Do not include any other text."""


def build_combined_chain(llm: BaseLanguageModel) -> Runnable:
    """Build the combined classify+extract LCEL chain.

    Args:
        llm: any LangChain chat model.

    Returns:
        A Runnable taking ``{"message": str}`` and returning a dict with keys
        ``is_job_post`` (bool), ``confidence`` (float), and ``jobs`` (list of dicts).
    """
    prompt = ChatPromptTemplate.from_messages(
        [("system", _SYSTEM_PROMPT), ("human", "Message:\n{message}")]
    )
    parser = JsonOutputParser(pydantic_object=CombinedResult)
    return prompt | llm | parser
