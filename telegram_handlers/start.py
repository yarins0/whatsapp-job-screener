"""Handlers for /help, /start, and /commands."""

from __future__ import annotations

_ASK_DEMO_LIMIT = 3  # imported by jobs.py too — kept here as the single source

_READONLY_NOTE = "(You are in read-only mode — view commands are available, write commands are owner-only.)"


def _owner_command_list() -> str:
    return (
        "Job Screener Bot — available commands:\n\n"
        "Jobs:\n"
        "/jobs — recent jobs (last 7 days)\n"
        "/jobs <keyword> — filter by keyword\n"
        "/jobs <keyword> unseen — keyword + unseen only\n"
        "/ask <question> — natural-language search (e.g. remote Python jobs this week)\n"
        "/similar <text> — find semantically similar jobs (e.g. Python backend FastAPI)\n"
        "/reindex — re-index all stored jobs in the vector store\n"
        "/resend [N] — re-send notifications for jobs stored in last N hours (default 2)\n\n"
        "Preferences:\n"
        "/prefs — show current roles, blocklist, and blocked cities\n"
        "/blockrole <keyword> — add a keyword to the role blocklist\n"
        "/blockcity <city> — add a city to the location blocklist\n"
        "/addrole <keyword> — add a keyword to the roles allow-list\n\n"
        "WhatsApp groups:\n"
        "/listgroups — list ALL groups on your WhatsApp account (with IDs)\n"
        "/groups — list currently watched groups\n"
        "/addgroup <id> — start watching a group\n"
        "/removegroup <id> — stop watching a group\n\n"
        "Telegram channels:\n"
        "/tgsources — list watched Telegram channels\n"
        "/addtgsource <@username or id> — start watching a channel\n"
        "/removetgsource <@username or id> — stop watching a channel\n\n"
        "You can also tap the Block role / Block city buttons on any job notification."
    )


def _demo_command_list() -> str:
    return (
        "Job Screener Bot — this bot monitors job groups and surfaces relevant postings automatically.\n\n"
        f"/jobs — browse recent jobs (last 7 days)\n"
        f"/jobs <keyword> — filter by keyword, e.g. /jobs python\n"
        f"/ask <question> — search in plain English ({_ASK_DEMO_LIMIT} free queries per session)\n"
        "/similar <text> — find jobs similar to a description\n"
        "/prefs — see the active role filters and blocklists\n\n"
        "You'll receive a message here whenever a new matching job is found."
    )


def get_help() -> str:
    return (
        "This bot screens WhatsApp job groups and forwards relevant postings to you.\n\n"
        "When a new job is stored you'll get an instant notification with two buttons:\n"
        "  • Block role — never see this type of job again\n"
        "  • Block city — skip jobs in that city\n\n"
        "Use /jobs to browse stored jobs, or /jobs python to filter by keyword.\n"
        "Use /ask to search in plain English — e.g. /ask remote Python jobs this week.\n\n"
        "You can also manage your preferences any time using text commands.\n\n"
        "For a full list of commands, use /commands."
    )


def handle_start(chat_id: int, is_owner: bool) -> list[str]:
    """Register demo users and return messages to send: command list + recent jobs."""
    if is_owner:
        return [_owner_command_list()]

    from agent.tools.demo_users_tool import add_demo_user
    add_demo_user(chat_id)

    messages = [_demo_command_list()]
    try:
        from agent.tools.query_tool import format_jobs_telegram, query_jobs
        jobs = query_jobs(days=7, limit=5)
        if jobs:
            messages.append(
                "Here are the latest jobs the bot found:\n\n"
                + format_jobs_telegram(jobs, days=7, role=None, unseen_only=False)
            )
        else:
            messages.append(
                "No jobs stored yet — they'll appear here as the bot finds them. "
                "You'll also get a message for each new one."
            )
    except Exception:
        pass
    return messages


def handle_commands(is_owner: bool) -> str:
    return _owner_command_list() if is_owner else _demo_command_list()
