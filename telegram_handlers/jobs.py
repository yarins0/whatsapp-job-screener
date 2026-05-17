"""Handlers for /jobs, /ask, /similar, /reindex, /resend."""

from __future__ import annotations

from telegram_handlers.start import _ASK_DEMO_LIMIT

READONLY_DENIED = (
    "🔒 This is a read-only demo. "
    "Only the owner can modify preferences and sources."
)

# Maps chat_id → number of /ask calls used this session. Resets on process restart.
_ask_counts: dict[int, int] = {}


def handle_jobs(text: str) -> str:
    from agent.tools.query_tool import format_jobs_telegram, query_jobs

    raw_args = text[len("/jobs"):].strip().lower().split()
    unseen_only = "unseen" in raw_args
    role_parts = [a for a in raw_args if a != "unseen"]
    role = " ".join(role_parts) if role_parts else None

    try:
        jobs = query_jobs(days=7, role=role, unseen_only=unseen_only, limit=10)
        return format_jobs_telegram(jobs, days=7, role=role, unseen_only=unseen_only)
    except Exception as exc:
        return f"Could not query jobs: {exc}"


def handle_ask(chat_id: int, text: str, is_owner: bool) -> str:
    from agent.tools.ask_tool import ask_jobs

    question = text[len("/ask"):].strip()
    if not question:
        return "Usage: /ask <question>\nExample: /ask remote Python jobs this week"

    if not is_owner:
        used = _ask_counts.get(chat_id, 0)
        if used >= _ASK_DEMO_LIMIT:
            return (
                f"🔒 You've used all {_ASK_DEMO_LIMIT} free /ask queries for this session.\n"
                "Only the owner has unlimited access."
            )
        _ask_counts[chat_id] = used + 1
        remaining = _ASK_DEMO_LIMIT - _ask_counts[chat_id]

    try:
        reply = ask_jobs(question)
    except Exception as exc:
        return f"Could not process your query: {exc}"

    if not is_owner:
        quota_note = (
            f"\n\n_{remaining} free /ask {'query' if remaining == 1 else 'queries'} remaining this session._"
        )
        reply = reply + quota_note

    return reply


def handle_similar(text: str) -> str:
    from agent.tools.query_tool import format_jobs_telegram
    from agent.vector_store import find_similar

    query = text[len("/similar"):].strip()
    if not query:
        return "Usage: /similar <text>\nExample: /similar Python backend FastAPI"

    try:
        jobs = find_similar(query, n=5)
        if jobs:
            body = format_jobs_telegram(jobs, days=0, role=None, unseen_only=False)
            return f'Similar to "{query}":\n\n{body}'
        return (
            "No similar jobs found. Jobs are indexed as they arrive — "
            "try again after some jobs have been stored."
        )
    except Exception as exc:
        return f"Could not search for similar jobs: {exc}"


def handle_reindex(is_owner: bool) -> list[str]:
    if not is_owner:
        return [READONLY_DENIED]
    from agent.vector_store import reindex_all
    try:
        count = reindex_all()
        return [
            "Re-indexing all stored jobs… this may take a moment.",
            f"Done. {count} job{'s' if count != 1 else ''} indexed.",
        ]
    except Exception as exc:
        return [
            "Re-indexing all stored jobs… this may take a moment.",
            f"Re-index failed: {exc}",
        ]


def handle_resend(text: str, is_owner: bool) -> list[str]:
    if not is_owner:
        return [READONLY_DENIED]

    from agent.graph import _notify_job
    from agent.tools.query_tool import query_jobs_recent

    arg = text[len("/resend"):].strip()
    try:
        hours = int(arg) if arg else 2
        if hours < 1:
            raise ValueError
    except ValueError:
        return ["Usage: /resend [hours]\nExample: /resend 2"]

    try:
        jobs = query_jobs_recent(hours)
    except Exception as exc:
        return [f"Could not query jobs: {exc}"]

    h_label = f"{hours} hour{'s' if hours != 1 else ''}"
    if not jobs:
        return [f"No jobs stored in the last {h_label}."]

    messages = [f"Resending {len(jobs)} job{'s' if len(jobs) != 1 else ''} from the last {h_label}…"]
    for job in jobs:
        try:
            _notify_job(job, job["id"])
        except Exception:
            pass
    return messages
