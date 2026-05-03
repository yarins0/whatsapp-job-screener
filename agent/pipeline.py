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
from agent.chains.combined import build_combined_chain
from agent.chains.extractor import build_extractor_chain
from agent.tools.dedup_tool import is_duplicate
from agent.tools.filter_tool import filter_job
from agent.tools.stats_tool import get_pipeline_mode, record_message
from agent.tools.store_tool import store_job

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "claude-haiku-4-5-20251001"
CONFIDENCE_THRESHOLD = 0.6


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
    group: str = message.get("group") or ""

    # Choose pipeline mode based on this group's historical job-post rate.
    # "combined" runs classify+extract in one LLM call; "separate" runs them
    # sequentially. Falls back to "separate" on any DB error.
    mode = get_pipeline_mode(group)

    if mode == "combined":
        # Single LLM call: classify and extract simultaneously.
        try:
            combined_chain = build_combined_chain(llm)
            combined = await combined_chain.ainvoke({"message": text})
            is_job = combined.get("is_job_post", False)
            confidence = combined.get("confidence", 0.0)
            jobs: list[dict] = combined.get("jobs") or []
        except Exception as exc:
            # If the combined chain fails (e.g. parse error), fall back to
            # separate mode so the message is not silently lost.
            logger.warning("Combined chain failed (%s) — falling back to separate mode", exc)
            mode = "separate"

    if mode == "separate":
        # 1. Classify
        classifier = build_classifier_chain(llm)
        classification = await classifier.ainvoke({"message": text})
        is_job = bool(classification.get("is_job_post"))
        confidence = float(classification.get("confidence", 0.0))

        if is_job and confidence >= CONFIDENCE_THRESHOLD:
            # 2. Extract — normalise to list; LLM may return a single dict or
            # a list when the message contains multiple postings.
            extractor = build_extractor_chain(llm)
            raw = await extractor.ainvoke({"message": text})
            jobs = raw if isinstance(raw, list) else [raw]
        else:
            jobs = []

    # Record stats now that we know whether this message was a job post.
    record_message(group, is_job and confidence >= CONFIDENCE_THRESHOLD)

    if not is_job or confidence < CONFIDENCE_THRESHOLD:
        return PipelineResult(
            "skipped",
            reason=f"not a job post (confidence={confidence})",
        ).to_dict()

    if not jobs:
        return PipelineResult("skipped", reason="extractor returned no jobs").to_dict()

    stored_jobs: list[dict] = []
    skipped_jobs: list[dict] = []

    for job in jobs:
        # 3. Dedup
        if is_duplicate(job):
            skipped_jobs.append({"job": job, "reason": "duplicate"})
            continue

        # 4. Filter
        passed, filter_reason = filter_job(job)
        if not passed:
            skipped_jobs.append({"job": job, "reason": filter_reason})
            continue

        # 5. Store
        enriched = {
            **job,
            "group": message.get("group"),
            "timestamp": message.get("timestamp"),
        }
        job_id = store_job(enriched)
        logger.info("Stored job id=%s title=%r", job_id, job.get("title"))
        stored_jobs.append({**job, "job_id": job_id})

        # 6. Notify immediately so good jobs aren't missed until the digest
        if notify:
            _notify_job(job, job_id)

    if not stored_jobs:
        # All jobs in this message were skipped — report the first reason
        first_reason = skipped_jobs[0]["reason"] if skipped_jobs else "unknown"
        # Keep single-job shape for backwards-compat with tests and API response
        first_job = skipped_jobs[0]["job"] if skipped_jobs else None
        return PipelineResult("skipped", reason=first_reason, job=first_job).to_dict()

    if len(stored_jobs) == 1 and not skipped_jobs:
        # Single stored job — keep the original flat shape so existing tests pass
        j = stored_jobs[0]
        return PipelineResult("stored", job=j, job_id=j["job_id"]).to_dict()

    # Multiple jobs or mixed stored/skipped
    action = "stored" if not skipped_jobs else "partial"
    return PipelineResult(
        action=action,
        stored=stored_jobs,
        skipped=skipped_jobs,
    ).to_dict()


def _notify_job(job: dict, job_id: int) -> None:
    """Send a single-job Telegram notification with feedback buttons.

    Attaches two inline buttons so the user can immediately block the role
    keyword or the city without leaving Telegram. Fails silently if Telegram
    is not configured.
    """
    try:
        from digest.digest import _send_telegram  # noqa: WPS433

        title = job.get("title") or "Untitled role"
        company = job.get("company") or "Unknown company"
        is_remote = bool(job.get("remote"))
        loc = "Remote" if is_remote else (job.get("location") or "Unknown")
        summary = job.get("summary") or ""
        contact = job.get("contact") or "see original message"

        lines = [f"New job: *{title}* @ {company} ({loc})"]
        if summary:
            lines.append(summary)
        lines.append(f"Contact: {contact}")

        # Callback data is limited to 64 bytes by the Telegram API.
        # Format: "<action>:<job_id>:<value>"
        role_kw = title.lower()[:40]
        buttons = [
            {"text": "Block role", "callback_data": f"block_role:{job_id}:{role_kw}"},
        ]
        # Only offer "Block city" when a specific city is known (remote jobs have no city to block).
        city = (job.get("location") or "").strip()
        if city and not is_remote:
            buttons.append(
                {"text": f"Block city: {city[:15]}", "callback_data": f"block_city:{job_id}:{city[:30]}"}
            )

        reply_markup = {"inline_keyboard": [buttons]}
        _send_telegram("\n".join(lines), reply_markup=reply_markup)
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
