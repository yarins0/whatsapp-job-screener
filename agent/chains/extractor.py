"""Extractor chain — pulls structured fields out of a job posting message.

LangChain concepts demonstrated:
  * Pydantic schema with optional/list types
  * JsonOutputParser pulling format instructions into the prompt
  * Few-shot guidance via the system message
"""

from __future__ import annotations

from typing import List, Optional

from langchain_core.language_models import BaseLanguageModel
from langchain_core.output_parsers import JsonOutputParser
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.runnables import Runnable
from pydantic import BaseModel, Field


class JobPost(BaseModel):
    """Structured representation of a single job posting."""

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
    contact: Optional[str] = Field(
        default=None, description="Email, phone, link, or instructions to apply."
    )
    summary: str = Field(description="A one-sentence summary the digest can quote.")


_SYSTEM_PROMPT = """You extract structured job-posting data from WhatsApp messages.

Rules:
  * Return a single JSON object that matches the schema. No prose, no Markdown.
  * If a field is not mentioned, use null (or [] for skills).
  * "remote" is true only if the post explicitly says remote/hybrid-remote/work-from-home.
    Use false if it explicitly says on-site/in-office. Otherwise null.
  * "skills" is a flat list of strings; pull concrete tech (e.g. "Python", "React"),
    not soft skills.
  * "summary" must be a single neutral sentence (<= 25 words) describing the role.
  * Preserve the original language of the post when sensible (e.g. Hebrew titles)."""


def build_extractor_chain(llm: BaseLanguageModel) -> Runnable:
    """Build the extractor LCEL chain.

    Returns a Runnable that takes ``{"message": str}`` and returns a dict
    matching :class:`JobPost`.
    """
    parser = JsonOutputParser(pydantic_object=JobPost)
    prompt = ChatPromptTemplate.from_messages(
        [
            ("system", _SYSTEM_PROMPT + "\n\n{format_instructions}"),
            ("human", "Message:\n{message}"),
        ]
    ).partial(format_instructions=parser.get_format_instructions())
    return prompt | llm | parser
