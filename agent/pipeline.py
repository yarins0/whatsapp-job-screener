"""Main LangChain pipeline — orchestrates classify → extract → dedup → filter → store.

This module is the entry point used by the FastAPI ingest endpoint and by tests.

For learning purposes the flow is written as plain async/await rather than as one
giant LCEL chain, so each step is easy to swap, mock, or trace in LangSmith.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Any, Optional

from langchain_core.language_models import BaseLanguageModel

from agent.chains.classifier import build_classifier_chain
from agent.chains.extractor import build_extractor_chain
from agent.tools.dedup_tool import is_duplicate
from agent.tools.filter_tool import filter_job
from agent.tools.store_tool import store_job

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "claude-haiku-4-5-20251001"
CONFIDENCE_THRESHOLD = 0.6


@dataclass
class PipelineResult:
    action: str            # "stored" | "skipped"
    reason: Optional[str] = None
    job: Optional[dict] = None
    job_id: Optional[int] = None

    def to_dict(self) -> dict[str, Any]:
        return {k: v for k, v in self.__dict__.items() if v is not None}


# ---------------------------------------------------------------------------
# LLM construction
# ---------------------------------------------------------------------------

def _default_llm() -> BaseLanguageModel:
    """Build the production LLM. Imported lazily so tests don't need the package."""
    from langchain_anthropic import ChatAnthropic  # noqa: WPS433 (local import on purpose)

    return ChatAnthropic(model=DEFAULT_MODEL, temperature=0)


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

    Args:
        message: dict with keys ``text``, ``group``, ``sender``, ``timestamp``.
        llm: optional override (for tests). Defaults to ChatAnthropic(haiku).

    Returns:
        Dict describing what happened — see :class:`PipelineResult`.
    """
    text: str = message.get("text", "")
    if not text.strip():
        return PipelineResult("skipped", reason="empty message").to_dict()

    llm = llm or _default_llm()

    # 1. Classify
    classifier = build_classifier_chain(llm)
    classification = await classifier.ainvoke({"message": text})
    if not classification.get("is_job_post") or classification.get("confidence", 0.0) < CONFIDENCE_THRESHOLD:
        return PipelineResult(
            "skipped",
            reason=f"not a job post (confidence={classification.get('confidence')})",
        ).to_dict()

    # 2. Extract
    extractor = build_extractor_chain(llm)
    job = await extractor.ainvoke({"message": text})

    # 3. Dedup
    if is_duplicate(job):
        return PipelineResult("skipped", reason="duplicate", job=job).to_dict()

    # 4. Filter
    passed, filter_reason = filter_job(job)
    if not passed:
        return PipelineResult("skipped", reason=filter_reason, job=job).to_dict()

    # 5. Store
    enriched = {
        **job,
        "group": message.get("group"),
        "timestamp": message.get("timestamp"),
    }
    job_id = store_job(enriched)
    logger.info("Stored job id=%s title=%r", job_id, job.get("title"))

    # 6. Notify immediately via Telegram so good jobs aren't missed until 8am
    if notify:
        _notify_job(job)

    return PipelineResult("stored", job=job, job_id=job_id).to_dict()


def _notify_job(job: dict) -> None:
    """Send a single-job Telegram notification. Fails silently if not configured."""
    try:
        from digest.digest import _send_telegram  # noqa: WPS433

        title = job.get("title") or "Untitled role"
        company = job.get("company") or "Unknown company"
        loc = "Remote" if job.get("remote") else (job.get("location") or "Unknown")
        summary = job.get("summary") or ""
        contact = job.get("contact") or "see original message"

        lines = [f"New job: *{title}* @ {company} ({loc})"]
        if summary:
            lines.append(summary)
        lines.append(f"Contact: {contact}")

        _send_telegram("\n".join(lines))
    except Exception as exc:  # noqa: BLE001
        logger.warning("Real-time Telegram notification failed: %s", exc)


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
