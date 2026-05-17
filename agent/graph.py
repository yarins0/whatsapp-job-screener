"""LangGraph pipeline — stateful graph that replaces the if/elif body in pipeline.py.

LangChain concepts demonstrated:
  * StateGraph   — directed graph of async node functions sharing a TypedDict state
  * TypedDict    — typed shared state passed between nodes; each node returns a partial update
  * add_conditional_edges — runtime branching based on state values
  * graph.compile() — returns a LangChain-compatible Runnable

Graph topology:

    START
      │
      ▼
   [route]  ─── classify (+ extract for combined/web-scraper paths)
      │
      ├─ not a job post ──────────────────────► [finish_skipped] → END
      ├─ separate mode + passed ─────────────► [extract]
      │                                              │
      │                          no jobs extracted ──┤
      │                                              ▼
      └─ combined / web-scraper + has jobs ──► [process_jobs]
                                                     │
                                                     ▼
                                                  [finish] → END
"""

from __future__ import annotations

import logging

from langgraph.graph import END, START, StateGraph
from typing_extensions import TypedDict

from agent.chains.classifier import classify_message
from agent.chains.combined import classify_and_extract
from agent.chains.extractor import extract_job
from agent.tools.dedup_tool import is_duplicate, is_time_duplicate
from agent.tools.filter_tool import filter_job
from agent.tools.stats_tool import get_pipeline_mode, record_message
from agent.tools.store_tool import store_job

logger = logging.getLogger(__name__)

CONFIDENCE_THRESHOLD = 0.6


# ---------------------------------------------------------------------------
# Shared state schema
# ---------------------------------------------------------------------------

class PipelineState(TypedDict, total=False):
    """Shared state dict threaded through every graph node.

    ``total=False`` means every key is optional at the TypedDict level —
    each node only needs to declare what it reads and what it writes.
    LangGraph merges the partial dict returned by each node into this state.
    """

    # Inputs — populated by run_pipeline before invoking the graph.
    message: dict   # raw inbound message: text, group, sender, timestamp
    notify: bool    # whether to fire Telegram notifications

    # Written by the route node.
    is_job: bool
    confidence: float
    jobs: list      # extracted job dicts (from route for combined/web-scraper,
                    # or from the extract node for separate mode)
    mode: str       # "web-scraper" | "combined" | "separate"

    # Written by the process_jobs node.
    stored_jobs: list
    skipped_jobs: list

    # Written by finish or finish_skipped — this is what run_pipeline returns.
    result: dict


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_route_result(
    is_job: bool,
    confidence: float,
    jobs: list,
    mode: str,
    group: str,
) -> dict:
    """Record per-group stats and return the route node's state update.

    Centralises the record_message call and the repeated dict shape so neither
    needs to be written out in each of the three route sub-paths.
    """
    record_message(group, is_job and confidence >= CONFIDENCE_THRESHOLD)
    return {"is_job": is_job, "confidence": confidence, "jobs": jobs, "mode": mode}


# ---------------------------------------------------------------------------
# Node functions
# ---------------------------------------------------------------------------

async def route(state: PipelineState) -> dict:
    """Classify the message and extract jobs for all non-separate paths.

    Three sub-paths:
      web-scraper  — skip classification; go straight to extraction.
      combined     — classify + extract in one API call.
      separate     — classify only; extraction is a separate node.
    Records per-group stats in all cases.
    """
    message: dict = state["message"]
    text: str = message.get("text", "")
    group: str = message.get("group") or ""
    sender: str = message.get("sender") or ""

    if sender == "web-scraper":
        jobs = await extract_job(text)
        return _make_route_result(True, 1.0, jobs, "web-scraper", group)

    mode = get_pipeline_mode(group)

    if mode == "combined":
        try:
            combined = await classify_and_extract(text)
            is_job: bool = combined.get("is_job_post", False)
            confidence: float = float(combined.get("confidence", 0.0))
            return _make_route_result(is_job, confidence, combined.get("jobs") or [], "combined", group)
        except Exception as exc:
            logger.warning("Combined chain failed (%s) — falling back to separate mode", exc)
            mode = "separate"

    classification = await classify_message(text)
    is_job = bool(classification.get("is_job_post"))
    confidence = float(classification.get("confidence", 0.0))
    return _make_route_result(is_job, confidence, [], "separate", group)


async def extract(state: PipelineState) -> dict:
    """Extract structured job fields from the message text.

    Only reached in separate mode after the classifier confirms this is a job post.
    """
    jobs = await extract_job(state["message"].get("text", ""))
    return {"jobs": jobs}


async def process_jobs(state: PipelineState) -> dict:
    """Dedup, filter, store, and optionally notify for each extracted job."""
    jobs: list = state.get("jobs") or []
    message: dict = state["message"]
    notify: bool = state.get("notify", True)

    stored_jobs: list = []
    skipped_jobs: list = []

    for job in jobs:
        # Dedup — atomic INSERT OR IGNORE on seen_hashes table.
        # A previously-seen hash is still allowed through if the same role
        # hasn't been stored within the duplicate window (re-post from long ago).
        if is_duplicate(job) and is_time_duplicate(job.get("title"), job.get("company")):
            skipped_jobs.append({"job": job, "reason": "duplicate"})
            continue

        # Filter — reads prefs.json on every call so Telegram-bot changes are live.
        passed, filter_reason = filter_job(job)
        if not passed:
            skipped_jobs.append({"job": job, "reason": filter_reason})
            continue

        # Store — insert into jobs table.
        enriched = {
            **job,
            "group": message.get("group"),
            "timestamp": message.get("timestamp"),
        }
        job_id = store_job(enriched)
        if job_id is None:
            # store_job returns None when the job is a time-duplicate (same
            # title+company stored within DUPLICATE_WINDOW_DAYS days).
            skipped_jobs.append({"job": job, "reason": "time-duplicate"})
            continue

        logger.debug("Stored job id=%s title=%r", job_id, job.get("title"))
        stored_jobs.append({**job, "job_id": job_id})

        # Notify — instant Telegram alert so good jobs aren't missed until the digest.
        if notify:
            _notify_job(enriched, job_id)
            _notify_demo_users(enriched)

    return {"stored_jobs": stored_jobs, "skipped_jobs": skipped_jobs}


def finish(state: PipelineState) -> dict:
    """Assemble the final result dict from stored/skipped job lists.

    Preserves the flat single-job shape that existing tests and the API response expect.
    """
    stored_jobs: list = state.get("stored_jobs") or []
    skipped_jobs: list = state.get("skipped_jobs") or []

    if not stored_jobs:
        first_reason = skipped_jobs[0]["reason"] if skipped_jobs else "unknown"
        first_job = skipped_jobs[0]["job"] if skipped_jobs else None
        result: dict = {"action": "skipped", "reason": first_reason}
        if first_job is not None:
            result["job"] = first_job
        return {"result": result}

    if len(stored_jobs) == 1 and not skipped_jobs:
        # Single stored job — keep the original flat shape so existing tests pass.
        j = stored_jobs[0]
        return {"result": {"action": "stored", "job": j, "job_id": j["job_id"]}}

    # Multiple jobs or a mix of stored + skipped.
    action = "stored" if not skipped_jobs else "partial"
    return {"result": {"action": action, "stored": stored_jobs, "skipped": skipped_jobs}}


def finish_skipped(state: PipelineState) -> dict:
    """Build a skipped result for messages that didn't pass classification or extraction."""
    is_job: bool = state.get("is_job", False)
    confidence: float = state.get("confidence", 0.0)
    jobs: list = state.get("jobs") or []

    if not is_job or confidence < CONFIDENCE_THRESHOLD:
        reason = f"not a job post (confidence={confidence})"
    elif not jobs:
        reason = "extractor returned no jobs"
    else:
        reason = "unknown"

    return {"result": {"action": "skipped", "reason": reason}}


# ---------------------------------------------------------------------------
# Conditional edge routing functions
# ---------------------------------------------------------------------------

def _route_after_classify(state: PipelineState) -> str:
    """Branch after the route node based on classification result and mode.

    Returns:
        "finish_skipped"  — message is not a job post.
        "extract"         — separate mode: classification passed, extract next.
        "process_jobs"    — jobs already available (combined or web-scraper).
    """
    if not state.get("is_job") or state.get("confidence", 0.0) < CONFIDENCE_THRESHOLD:
        return "finish_skipped"
    if state.get("mode") == "separate":
        return "extract"
    # combined / web-scraper: jobs must be non-empty to proceed
    if not state.get("jobs"):
        return "finish_skipped"
    return "process_jobs"


def _route_after_extract(state: PipelineState) -> str:
    """Branch after the extract node.

    Returns:
        "process_jobs"   — extractor returned at least one job.
        "finish_skipped" — extractor returned nothing.
    """
    return "process_jobs" if state.get("jobs") else "finish_skipped"


# ---------------------------------------------------------------------------
# Graph assembly
# ---------------------------------------------------------------------------

def build_pipeline_graph():
    """Build and compile the LangGraph pipeline.

    Returns a compiled StateGraph (a LangChain Runnable) that accepts an initial
    PipelineState dict and returns the full updated state after all nodes run.
    """
    graph: StateGraph = StateGraph(PipelineState)

    graph.add_node("route", route)
    graph.add_node("extract", extract)
    graph.add_node("process_jobs", process_jobs)
    graph.add_node("finish", finish)
    graph.add_node("finish_skipped", finish_skipped)

    graph.add_edge(START, "route")

    graph.add_conditional_edges(
        "route",
        _route_after_classify,
        {
            "extract": "extract",
            "process_jobs": "process_jobs",
            "finish_skipped": "finish_skipped",
        },
    )

    graph.add_conditional_edges(
        "extract",
        _route_after_extract,
        {
            "process_jobs": "process_jobs",
            "finish_skipped": "finish_skipped",
        },
    )

    graph.add_edge("process_jobs", "finish")
    graph.add_edge("finish", END)
    graph.add_edge("finish_skipped", END)

    return graph.compile()


# Singleton compiled graph — built once at import time and reused across requests.
_pipeline_graph = build_pipeline_graph()


# ---------------------------------------------------------------------------
# Telegram notification helper (moved here from pipeline.py)
# ---------------------------------------------------------------------------

def _md(text: str) -> str:
    """Escape characters special in Telegram Markdown V1: _ * ` ["""
    for ch in ("_", "*", "`", "["):
        text = text.replace(ch, "\\" + ch)
    return text


def _notify_demo_users(job: dict) -> None:
    """Send a plain-text job notification to every registered demo user.

    No inline buttons — demo users are read-only so the buttons would do nothing.
    Fails silently per user so one bad chat_id never blocks the others.
    """
    try:
        import os

        import requests as _requests

        from agent.tools.demo_users_tool import load_demo_users

        token = os.environ.get("TELEGRAM_BOT_TOKEN")
        if not token:
            return
        users = load_demo_users()
        if not users:
            return

        title = job.get("title") or "Untitled role"
        company = job.get("company") or "Unknown company"
        is_remote = bool(job.get("remote"))
        loc = "Remote" if is_remote else (job.get("location") or "Unknown")
        summary = job.get("summary") or ""
        group = job.get("group") or job.get("group_name") or ""
        contact = job.get("contact") or (
            f"see original message in {group}" if group else "see original message"
        )

        lines = [f"New job: *{_md(title)}* @ {_md(company)} ({_md(loc)})"]
        if summary:
            lines.append(_md(summary[:200]))
        lines.append(f"Contact: {contact}")
        text = "\n".join(lines)

        for chat_id in users:
            try:
                _requests.post(
                    f"https://api.telegram.org/bot{token}/sendMessage",
                    json={"chat_id": chat_id, "text": text, "parse_mode": "Markdown"},
                    timeout=10,
                )
            except Exception as exc:
                logger.warning("Demo notification to chat_id=%s failed: %s", chat_id, exc)
    except Exception as exc:
        logger.warning("_notify_demo_users failed: %s", exc)


def _notify_job(job: dict, job_id: int) -> None:
    """Send a single-job Telegram notification with inline feedback buttons.

    Attaches Block role / Block city buttons so the user can update preferences
    without leaving Telegram. Fails silently if Telegram is not configured.
    """
    try:
        from digest.digest import _send_telegram  # noqa: WPS433

        title = job.get("title") or "Untitled role"
        company = job.get("company") or "Unknown company"
        is_remote = bool(job.get("remote"))
        loc = "Remote" if is_remote else (job.get("location") or "Unknown")
        summary = job.get("summary") or ""
        group = job.get("group") or job.get("group_name") or ""
        contact = job.get("contact") or (
            f"see original message in {group}" if group else "see original message"
        )

        lines = [f"New job: *{_md(title)}* @ {_md(company)} ({_md(loc)})"]
        if summary:
            lines.append(_md(summary[:200]))
        lines.append(f"Contact: {contact}")

        # Callback data is capped at 64 bytes by the Telegram API.
        role_kw = title.lower()[:40]
        buttons = [
            {"text": "Block role", "callback_data": f"block_role:{job_id}:{role_kw}"},
        ]
        city = (job.get("location") or "").strip()
        if city and not is_remote:
            buttons.append(
                {"text": f"Block city: {city[:15]}", "callback_data": f"block_city:{job_id}:{city[:30]}"}
            )

        _send_telegram("\n".join(lines), reply_markup={"inline_keyboard": [buttons]})
    except Exception as exc:  # noqa: BLE001
        logger.warning("Real-time Telegram notification failed: %s", exc)
