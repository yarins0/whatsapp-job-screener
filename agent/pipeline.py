"""Main LangChain pipeline — entry point used by the FastAPI ingest endpoint and tests.

Orchestration logic lives in agent/graph.py as a LangGraph StateGraph.
This module owns the public API (run_pipeline), LLM construction, and the
PipelineResult dataclass that describes what happened to each message.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Any, Optional

from langchain_core.language_models import BaseLanguageModel

from agent.graph import CONFIDENCE_THRESHOLD, _pipeline_graph

logger = logging.getLogger(__name__)

# LLM defaults — override via env vars LLM_PROVIDER and LLM_MODEL.
# Supported providers: anthropic (default), openai, google, ollama
DEFAULT_PROVIDER = "anthropic"
DEFAULT_MODEL = "claude-haiku-4-5-20251001"


@dataclass
class PipelineResult:
    action: str                        # "stored" | "skipped" | "partial"
    reason: Optional[str] = None      # set when action == "skipped"
    job: Optional[dict] = None        # single job (legacy, kept for tests)
    job_id: Optional[int] = None      # single job id (legacy, kept for tests)
    stored: Optional[list] = None     # list of stored job dicts (multi-job)
    skipped: Optional[list] = None    # list of {job, reason} dicts (multi-job)

    def to_dict(self) -> dict[str, Any]:
        return {k: v for k, v in self.__dict__.items() if v is not None}


# ---------------------------------------------------------------------------
# LLM construction
# ---------------------------------------------------------------------------

def _default_llm() -> BaseLanguageModel:
    """Build the production LLM from env vars.

    Reads LLM_PROVIDER (default: anthropic) and LLM_MODEL (default depends on provider).
    Imports are lazy so tests can run offline without any provider package installed.

    Supported providers and their default models:
      anthropic → claude-haiku-4-5-20251001  (requires: pip install langchain-anthropic)
      openai    → gpt-4o-mini                (requires: pip install langchain-openai)
      google    → gemini-2.0-flash           (requires: pip install langchain-google-genai)
      ollama    → llama3.2                   (requires: ollama running locally)
    """
    provider = os.getenv("LLM_PROVIDER", DEFAULT_PROVIDER).lower()
    model = os.getenv("LLM_MODEL", "")

    if provider == "anthropic":
        from langchain_anthropic import ChatAnthropic
        return ChatAnthropic(model=model or "claude-haiku-4-5-20251001", temperature=0)

    if provider == "openai":
        from langchain_openai import ChatOpenAI  # type: ignore[import-not-found]
        return ChatOpenAI(model=model or "gpt-4o-mini", temperature=0)

    if provider == "google":
        from langchain_google_genai import ChatGoogleGenerativeAI  # type: ignore[import-not-found]
        return ChatGoogleGenerativeAI(model=model or "gemini-2.0-flash", temperature=0)

    if provider == "ollama":
        from langchain_ollama import ChatOllama  # type: ignore[import-not-found]
        return ChatOllama(model=model or "llama3.2", temperature=0)

    raise ValueError(
        f"Unknown LLM_PROVIDER '{provider}'. "
        "Supported values: anthropic, openai, google, ollama"
    )


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------

async def run_pipeline(
    message: dict,
    *,
    llm: Optional[BaseLanguageModel] = None,
    notify: bool = True,
) -> dict[str, Any]:
    """Run a single message through the full screening pipeline.

    Delegates all orchestration to the LangGraph pipeline in agent/graph.py.
    This function is the stable public API — its signature and return shape
    are unchanged from the pre-LangGraph version.

    Args:
        message: dict with keys ``text``, ``group``, ``sender``, ``timestamp``.
        llm: optional override (for tests). Defaults to the provider set in .env.

    Returns:
        Dict describing what happened — see :class:`PipelineResult`.
    """
    text: str = message.get("text", "")
    if not text.strip():
        return PipelineResult("skipped", reason="empty message").to_dict()

    resolved_llm = llm or _default_llm()

    initial_state = {
        "message": message,
        "llm": resolved_llm,
        "notify": notify,
    }

    final_state = await _pipeline_graph.ainvoke(initial_state)
    return final_state["result"]


# ---------------------------------------------------------------------------
# Manual smoke test:  python -m agent.pipeline
# ---------------------------------------------------------------------------

if __name__ == "__main__":  # pragma: no cover
    import asyncio
    import json

    from dotenv import load_dotenv
    load_dotenv()

    sample = {
        "group": "Tech Jobs TLV",
        "sender": "demo",
        "text": (
            "Hiring a Backend Engineer at Acme — Python/FastAPI, Tel Aviv (hybrid). "
            "Stock options + competitive salary. DM @recruiter or email jobs@acme.io"
        ),
        "timestamp": 1700000000,
    }
    if not os.getenv("ANTHROPIC_API_KEY"):
        print("Error: ANTHROPIC_API_KEY is not set. Add it to your .env file.")
    else:
        from db.init_db import init_db
        init_db()

        print("Running smoke test — sending a sample message through the pipeline...")
        try:
            result = asyncio.run(run_pipeline(sample, notify=False))
            print(json.dumps(result, indent=2, ensure_ascii=False))
        except Exception as error:
            print(f"Error: {error}")
