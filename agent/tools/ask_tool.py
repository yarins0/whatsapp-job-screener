"""Ask tool — convert a natural-language job query into structured query_jobs() parameters.

The LLM extracts filter parameters (days, role, unseen_only, limit) from the user's
question, then delegates to query_jobs() + format_jobs_telegram() for the actual DB
lookup and formatting.

Designed to be injected with a FakeListChatModel in tests so no API key is needed.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from langchain_core.language_models import BaseLanguageModel
from langchain_core.output_parsers import JsonOutputParser
from langchain_core.prompts import ChatPromptTemplate

from agent.tools.query_tool import format_jobs_telegram, query_jobs

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = """You are a query parser for a job database. \
The user will ask a natural-language question about stored jobs. \
Extract the filter parameters and return them as a JSON object — nothing else.

Available fields:
  days         (integer, default 7)  — how many past days to search.
               0 means all time.
               Interpret: "today" → 1, "this week" / "recent" → 7,
               "this month" → 30, "all time" / "ever" → 0.
  role         (string or null)      — keyword matched against job title and summary.
               Use null when no specific role is mentioned.
  unseen_only  (boolean, default false) — true when the user says "unseen", "new",
               "haven't seen", or similar.
  limit        (integer, default 10, max 20) — number of results to return.
               Increase only when the user explicitly asks for "more" or a number.

Return exactly this shape:
{{"days": <int>, "role": <str|null>, "unseen_only": <bool>, "limit": <int>}}

No explanation, no markdown — only the JSON object."""


def _default_llm() -> BaseLanguageModel:
    import os
    from langchain_anthropic import ChatAnthropic
    return ChatAnthropic(
        model=os.getenv("LLM_MODEL", "claude-haiku-4-5-20251001"),
        temperature=0,
    )


def ask_jobs(question: str, *, llm: Optional[BaseLanguageModel] = None) -> str:
    """Convert a natural-language question into query_jobs() params and return results.

    Args:
        question: the user's natural-language query (e.g. "remote Python jobs this week").
        llm: optional override for the language model; defaults to the production LLM
             from agent/pipeline.py. Pass a FakeListChatModel in tests.

    Returns:
        A Telegram-ready string produced by format_jobs_telegram().
        On LLM or parse failure, falls back to a keyword search with the raw question.
    """
    resolved_llm = llm or _default_llm()

    prompt = ChatPromptTemplate.from_messages([
        ("system", _SYSTEM_PROMPT),
        ("human", "{question}"),
    ])
    parser = JsonOutputParser()
    chain = prompt | resolved_llm | parser

    params: dict[str, Any] = _extract_params(chain, question)

    jobs = query_jobs(
        days=params["days"],
        role=params["role"],
        unseen_only=params["unseen_only"],
        limit=params["limit"],
    )
    return format_jobs_telegram(
        jobs,
        days=params["days"],
        role=params["role"],
        unseen_only=params["unseen_only"],
    )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _extract_params(chain, question: str) -> dict[str, Any]:
    """Invoke the extraction chain and return validated query parameters.

    Falls back to a safe default (keyword search with the raw question) if the
    LLM returns something that cannot be parsed or is missing required fields.
    """
    try:
        raw = chain.invoke({"question": question})
        return _validate_params(raw)
    except Exception as exc:
        logger.warning("ask_tool: LLM extraction failed (%s) — falling back to keyword search", exc)
        return _fallback_params(question)


def _validate_params(raw: Any) -> dict[str, Any]:
    """Coerce and clamp the raw dict returned by the LLM into safe query params."""
    if not isinstance(raw, dict):
        raise ValueError(f"Expected dict, got {type(raw).__name__}")

    days = int(raw.get("days", 7))
    role = raw.get("role") or None
    unseen_only = bool(raw.get("unseen_only", False))
    limit = max(1, min(int(raw.get("limit", 10)), 20))

    return {"days": days, "role": role, "unseen_only": unseen_only, "limit": limit}


def _fallback_params(question: str) -> dict[str, Any]:
    """Safe defaults used when the LLM response cannot be parsed."""
    return {"days": 7, "role": question.strip() or None, "unseen_only": False, "limit": 10}
