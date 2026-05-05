"""Telegram bot — polls for updates and handles inline-button callbacks and commands.

Runs as a standalone process (started by start.py). Uses long-polling so no public
URL or webhook setup is required.

Access control:
  Owner  — identified by TELEGRAM_CHAT_ID; full access to all commands.
  Demo   — any other chat_id; read-only with a capped /ask quota
             (_ASK_DEMO_LIMIT queries per bot session).

Supported interactions:
  Button callbacks (on job notifications):
    block_role:<job_id>:<keyword>  — add keyword to blocklist
    block_city:<job_id>:<city>     — add city to location_blocklist

  Text commands (read — available to all):
    /help                          — overview and quick-start
    /commands                      — full command list
    /prefs                         — show current roles, blocklist, blocked cities
    /listgroups                    — list ALL WhatsApp groups with IDs (owner only)
    /groups                        — list watched WhatsApp groups
    /tgsources                     — list watched Telegram channels
    /jobs [keyword] [unseen]       — browse stored jobs (last 7 days by default)
    /ask <question>                — natural-language job search (demo quota applies)
    /similar <text>                — find semantically similar jobs (vector search)

  Text commands (write — owner only):
    /listgroups                    — list ALL groups on the WhatsApp account with IDs
    /blockrole <keyword>           — add keyword to blocklist
    /blockcity <city>              — add city to location_blocklist
    /addrole <keyword>             — add keyword to the roles allow-list
    /addgroup <id>                 — start watching a WhatsApp group
    /removegroup <id>              — stop watching a WhatsApp group
    /addtgsource <@username|id>    — start watching a Telegram channel
    /removetgsource <@username|id> — stop watching a Telegram channel
"""

from __future__ import annotations

import logging
import os
import time

import requests
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)
for _lvl, _name in [(10, "debug"), (20, "info"), (30, "warning"), (40, "error"), (50, "critical")]:
    logging.addLevelName(_lvl, _name)
logging.basicConfig(level=logging.INFO, format="[%(asctime)s][%(levelname)s] %(message)s", datefmt="%H:%M:%S")

POLL_TIMEOUT = 30  # seconds for each long-poll request

_READONLY_DENIED = (
    "🔒 This is a read-only demo. "
    "Only the owner can modify preferences and sources."
)

# Maximum number of /ask queries a non-owner (demo) user may run per bot session.
# Resets when the bot process restarts.
_ASK_DEMO_LIMIT = 3

# Maps chat_id → number of /ask calls used by that demo user this session.
_ask_counts: dict[int, int] = {}


# ---------------------------------------------------------------------------
# Telegram API helpers
# ---------------------------------------------------------------------------

def _api(method: str, **payload) -> dict:
    token = os.environ["TELEGRAM_BOT_TOKEN"]
    resp = requests.post(
        f"https://api.telegram.org/bot{token}/{method}",
        json=payload,
        timeout=POLL_TIMEOUT + 10,
    )
    resp.raise_for_status()
    return resp.json()


def _answer_callback(callback_id: str, text: str) -> None:
    """Dismiss the loading spinner on the button and show a brief toast."""
    try:
        _api("answerCallbackQuery", callback_query_id=callback_id, text=text, show_alert=False)
    except Exception as exc:
        logger.warning("answerCallbackQuery failed: %s", exc)


def _is_owner(chat_id: int) -> bool:
    """Return True if this chat_id belongs to the configured owner.

    Reads TELEGRAM_CHAT_ID from the environment on every call so that tests
    can use monkeypatch.setenv without needing to reload the module.
    """
    owner_str = os.environ.get("TELEGRAM_CHAT_ID")
    if not owner_str:
        return False
    return chat_id == int(owner_str)


def _send(chat_id: int | str, text: str) -> None:
    try:
        _api("sendMessage", chat_id=chat_id, text=text)
    except Exception as exc:
        logger.warning("sendMessage failed: %s", exc)


# ---------------------------------------------------------------------------
# Update handlers
# ---------------------------------------------------------------------------

def _handle_callback(query: dict) -> None:
    """Process an inline-button press."""
    from agent.tools.prefs_tool import add_to_blocklist, add_to_location_blocklist

    data: str = query.get("data", "")
    chat_id: int = query["from"]["id"]
    callback_id: str = query["id"]

    if not _is_owner(chat_id):
        _answer_callback(callback_id, _READONLY_DENIED)
        return

    if data.startswith("block_role:"):
        # format: block_role:<job_id>:<keyword>
        parts = data.split(":", 2)
        keyword = parts[2].strip() if len(parts) == 3 else ""
        if not keyword:
            _answer_callback(callback_id, "Could not read role keyword.")
            return
        added = add_to_blocklist(keyword)
        msg = f"Blocked: '{keyword}'" if added else f"'{keyword}' was already blocked."
        _answer_callback(callback_id, msg)
        _send(chat_id, msg)

    elif data.startswith("block_city:"):
        # format: block_city:<job_id>:<city>
        parts = data.split(":", 2)
        city = parts[2].strip() if len(parts) == 3 else ""
        if not city:
            _answer_callback(callback_id, "Could not read city.")
            return
        added = add_to_location_blocklist(city)
        msg = f"Blocked city: '{city}'" if added else f"'{city}' was already blocked."
        _answer_callback(callback_id, msg)
        _send(chat_id, msg)

    else:
        _answer_callback(callback_id, "Unknown action.")


def _handle_message(msg: dict) -> None:
    """Process a text command sent to the bot.

    Supported commands:
      /prefs                — show current roles, blocklist, and blocked cities
      /blockrole <keyword>  — add a keyword to the role blocklist
      /blockcity <city>     — add a city to the location blocklist
      /addrole <keyword>    — add a keyword to the roles allow-list
    """
    from agent.tools.prefs_tool import (
        add_to_blocklist,
        add_to_location_blocklist,
        add_to_roles,
        load_prefs,
    )

    text: str = (msg.get("text") or "").strip()
    chat_id: int = msg["chat"]["id"]

    if text.startswith("/help"):
        _send(chat_id, (
            "This bot screens WhatsApp job groups and forwards relevant postings to you.\n\n"
            "When a new job is stored you'll get an instant notification with two buttons:\n"
            "  • Block role — never see this type of job again\n"
            "  • Block city — skip jobs in that city\n\n"
            "Use /jobs to browse stored jobs, or /jobs python to filter by keyword.\n"
            "Use /ask to search in plain English — e.g. /ask remote Python jobs this week.\n\n"
            "You can also manage your preferences any time using text commands.\n\n"
            "For a full list of commands, use /commands."
        ))

    elif text.startswith("/start") or text.startswith("/commands"):
        if _is_owner(chat_id):
            _send(chat_id, (
                "Job Screener Bot — available commands:\n\n"
                "Jobs:\n"
                "/jobs — recent jobs (last 7 days)\n"
                "/jobs <keyword> — filter by keyword\n"
                "/jobs <keyword> unseen — keyword + unseen only\n"
                "/ask <question> — natural-language search (e.g. remote Python jobs this week)\n"
                "/similar <text> — find semantically similar jobs (e.g. Python backend FastAPI)\n"
                "/reindex — re-index all stored jobs in the vector store\n\n"
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
            ))
        else:
            _send(chat_id, (
                "Job Screener Bot — available commands:\n"
                "(You are in read-only mode — view commands are available, write commands are owner-only.)\n\n"
                "Jobs:\n"
                "/jobs — recent jobs (last 7 days)\n"
                "/jobs <keyword> — filter by keyword\n"
                "/jobs <keyword> unseen — keyword + unseen only\n"
                f"/ask <question> — natural-language search ({_ASK_DEMO_LIMIT} free queries per session)\n"
                "/similar <text> — find semantically similar jobs\n\n"
                "Preferences (view only):\n"
                "/prefs — show current roles, blocklist, and blocked cities\n"
                "/blockrole <keyword> — 🔒 owner only\n"
                "/blockcity <city> — 🔒 owner only\n"
                "/addrole <keyword> — 🔒 owner only\n\n"
                "WhatsApp groups (view only):\n"
                "/listgroups — 🔒 owner only\n"
                "/groups — list currently watched groups\n"
                "/addgroup <id> — 🔒 owner only\n"
                "/removegroup <id> — 🔒 owner only\n\n"
                "Telegram channels (view only):\n"
                "/tgsources — list watched Telegram channels\n"
                "/addtgsource <@username or id> — 🔒 owner only\n"
                "/removetgsource <@username or id> — 🔒 owner only"
            ))

    elif text.startswith("/jobs"):
        from agent.tools.query_tool import format_jobs_telegram, query_jobs

        # Parse optional arguments: /jobs [keyword] [unseen]
        # Examples: /jobs  /jobs python  /jobs python unseen  /jobs unseen
        raw_args = text[len("/jobs"):].strip().lower().split()
        unseen_only = "unseen" in raw_args
        role_parts = [a for a in raw_args if a != "unseen"]
        role = " ".join(role_parts) if role_parts else None

        try:
            jobs = query_jobs(days=7, role=role, unseen_only=unseen_only, limit=10)
            reply = format_jobs_telegram(jobs, days=7, role=role, unseen_only=unseen_only)
        except Exception as exc:
            reply = f"Could not query jobs: {exc}"
        _send(chat_id, reply)

    elif text.startswith("/ask"):
        from agent.tools.ask_tool import ask_jobs

        question = text[len("/ask"):].strip()
        if not question:
            _send(chat_id, "Usage: /ask <question>\nExample: /ask remote Python jobs this week")
            return

        if not _is_owner(chat_id):
            used = _ask_counts.get(chat_id, 0)
            if used >= _ASK_DEMO_LIMIT:
                _send(
                    chat_id,
                    f"🔒 You've used all {_ASK_DEMO_LIMIT} free /ask queries for this session.\n"
                    "Only the owner has unlimited access.",
                )
                return
            _ask_counts[chat_id] = used + 1
            remaining = _ASK_DEMO_LIMIT - _ask_counts[chat_id]

        try:
            reply = ask_jobs(question)
        except Exception as exc:
            logger.warning("ask_jobs failed: %s", exc)
            reply = f"Could not process your query: {exc}"

        if not _is_owner(chat_id):
            quota_note = (
                f"\n\n_{remaining} free /ask {'query' if remaining == 1 else 'queries'} remaining this session._"
            )
            reply = reply + quota_note

        _send(chat_id, reply)

    elif text.startswith("/similar"):
        from agent.vector_store import find_similar
        from agent.tools.query_tool import format_jobs_telegram

        query = text[len("/similar"):].strip()
        if not query:
            _send(chat_id, "Usage: /similar <text>\nExample: /similar Python backend FastAPI")
            return

        try:
            jobs = find_similar(query, n=5)
            if jobs:
                body = format_jobs_telegram(jobs, days=0, role=None, unseen_only=False)
                reply = f'Similar to "{query}":\n\n{body}'
            else:
                reply = (
                    "No similar jobs found. Jobs are indexed as they arrive — "
                    "try again after some jobs have been stored."
                )
        except Exception as exc:
            logger.warning("find_similar failed: %s", exc)
            reply = f"Could not search for similar jobs: {exc}"
        _send(chat_id, reply)

    elif text.startswith("/reindex"):
        if not _is_owner(chat_id):
            _send(chat_id, _READONLY_DENIED)
            return
        from agent.vector_store import reindex_all
        _send(chat_id, "Re-indexing all stored jobs… this may take a moment.")
        try:
            count = reindex_all()
            _send(chat_id, f"Done. {count} job{'s' if count != 1 else ''} indexed.")
        except Exception as exc:
            logger.warning("reindex_all failed: %s", exc)
            _send(chat_id, f"Re-index failed: {exc}")

    elif text.startswith("/prefs"):
        try:
            prefs = load_prefs()
            roles = ", ".join(prefs.get("roles") or []) or "(none)"
            blocked = ", ".join(prefs.get("blocklist") or []) or "(none)"
            cities = ", ".join(prefs.get("location_blocklist") or []) or "(none)"
            reply = (
                "Current preferences:\n"
                f"Roles: {roles}\n"
                f"Blocklist: {blocked}\n"
                f"Blocked cities: {cities}"
            )
        except Exception as exc:
            reply = f"Could not read preferences: {exc}"
        _send(chat_id, reply)

    elif text.startswith("/blockrole"):
        if not _is_owner(chat_id):
            _send(chat_id, _READONLY_DENIED)
            return
        keyword = text[len("/blockrole"):].strip()
        if not keyword:
            _send(chat_id, "Usage: /blockrole <keyword>")
            return
        added = add_to_blocklist(keyword)
        _send(chat_id, f"Blocked: '{keyword.lower()}'" if added else f"'{keyword.lower()}' was already blocked.")

    elif text.startswith("/blockcity"):
        if not _is_owner(chat_id):
            _send(chat_id, _READONLY_DENIED)
            return
        city = text[len("/blockcity"):].strip()
        if not city:
            _send(chat_id, "Usage: /blockcity <city>")
            return
        added = add_to_location_blocklist(city)
        _send(chat_id, f"Blocked city: '{city}'" if added else f"'{city}' was already blocked.")

    elif text.startswith("/addrole"):
        if not _is_owner(chat_id):
            _send(chat_id, _READONLY_DENIED)
            return
        keyword = text[len("/addrole"):].strip()
        if not keyword:
            _send(chat_id, "Usage: /addrole <keyword>")
            return
        added = add_to_roles(keyword)
        _send(chat_id, f"Added role: '{keyword.lower()}'" if added else f"'{keyword.lower()}' is already in your roles list.")

    elif text.startswith("/listgroups"):
        if not _is_owner(chat_id):
            _send(chat_id, _READONLY_DENIED)
            return

        import json
        from pathlib import Path

        snapshot_path = Path(__file__).parent / "agent" / "all_whatsapp_groups.json"
        if not snapshot_path.exists():
            _send(chat_id, (
                "No group snapshot found yet.\n\n"
                "Start the WhatsApp listener — it writes a full list of your groups "
                "to all_whatsapp_groups.json on every connect.\n\n"
                "Then run /listgroups again."
            ))
            return

        try:
            from agent.tools.groups_tool import load_groups
            all_groups: dict = json.loads(snapshot_path.read_text(encoding="utf-8"))
            watched = set(load_groups())
        except Exception as exc:
            _send(chat_id, f"Could not read group list: {exc}")
            return

        if not all_groups:
            _send(chat_id, "No groups found on the connected WhatsApp account.")
            return

        # Build one entry per group; keep entries together when paginating.
        footer = "✓ = currently being watched\nUse /addgroup <id> to start watching a group."
        header = f"All WhatsApp groups ({len(all_groups)} found):\n"
        entries = []
        for gid, name in all_groups.items():
            marker = " ✓" if gid in watched else ""
            display = name or "unknown"
            entries.append(f"  {display}{marker}\n  {gid}")

        # Paginate: Telegram caps messages at 4096 chars.
        # Emit header on the first page, footer on the last.
        _TELEGRAM_LIMIT = 4000
        pages: list[str] = []
        current_lines: list[str] = [header]
        current_len = len(header)

        for entry in entries:
            chunk = entry + "\n"
            if current_len + len(chunk) > _TELEGRAM_LIMIT and len(current_lines) > 1:
                pages.append("\n".join(current_lines))
                current_lines = []
                current_len = 0
            current_lines.append(entry)
            current_len += len(chunk)

        if current_lines:
            current_lines.append(footer)
            pages.append("\n".join(current_lines))

        for page in pages:
            _send(chat_id, page)

    elif text.startswith("/groups"):
        from agent.tools.groups_tool import load_groups_with_names
        try:
            groups = load_groups_with_names()
            if groups:
                lines = ["Watched groups:"]
                for gid, name in groups.items():
                    display = name if name else "unknown"
                    lines.append(f"  {display}  ({gid})")
                lines.append("")
                lines.append("Names refresh when the listener restarts.")
            else:
                lines = [
                    "No groups are being watched yet.",
                    "Use /addgroup <group_id> to add one.",
                    "Restart the listener with an empty list to discover group IDs.",
                ]
            _send(chat_id, "\n".join(lines))
        except Exception as exc:
            _send(chat_id, f"Could not read groups: {exc}")

    elif text.startswith("/addgroup"):
        if not _is_owner(chat_id):
            _send(chat_id, _READONLY_DENIED)
            return
        from agent.tools.groups_tool import add_group
        group_id = text[len("/addgroup"):].strip()
        if not group_id:
            _send(chat_id, (
                "Usage: /addgroup <group_id>\n\n"
                "The group ID is the WhatsApp internal ID (not the group name), "
                "e.g. 120363XXXXXXXXXX@g.us\n\n"
                "To find IDs: empty agent/groups.json and restart the listener — "
                "it will print all your groups and their IDs."
            ))
            return
        added = add_group(group_id)
        if added:
            _send(chat_id, (
                f"Added group: {group_id}\n\n"
                "Live messages from this group will be forwarded immediately.\n"
                "Restart the listener to also catch up on any missed messages."
            ))
        else:
            _send(chat_id, f"'{group_id}' is already in the watch list.")

    elif text.startswith("/removegroup"):
        if not _is_owner(chat_id):
            _send(chat_id, _READONLY_DENIED)
            return
        from agent.tools.groups_tool import remove_group
        group_id = text[len("/removegroup"):].strip()
        if not group_id:
            _send(chat_id, "Usage: /removegroup <group_id>\n\nUse /groups to see the IDs of currently watched groups.")
            return
        removed = remove_group(group_id)
        _send(chat_id, f"Removed group: {group_id}" if removed else f"'{group_id}' was not in the watch list.")

    elif text.startswith("/tgsources"):
        from agent.tools.telegram_sources_tool import load_sources
        try:
            sources = load_sources()
            if sources:
                lines = ["Watched Telegram channels:"]
                for channel, name in sources.items():
                    lines.append(f"  {channel}" + (f" — {name}" if name else ""))
                _send(chat_id, "\n".join(lines))
            else:
                _send(chat_id, "No Telegram channels are being watched yet.\nUse /addtgsource @username to add one.")
        except Exception as exc:
            _send(chat_id, f"Could not read Telegram sources: {exc}")

    elif text.startswith("/addtgsource"):
        if not _is_owner(chat_id):
            _send(chat_id, _READONLY_DENIED)
            return
        from agent.tools.telegram_sources_tool import add_source
        channel = text[len("/addtgsource"):].strip()
        if not channel:
            _send(chat_id, "Usage: /addtgsource <@username or channel_id>")
            return
        added = add_source(channel)
        if added:
            _send(chat_id, f"Added Telegram channel: {channel}\n\nRestart tg-source for it to take effect.")
        else:
            _send(chat_id, f"{channel} is already in the watch list.")

    elif text.startswith("/removetgsource"):
        if not _is_owner(chat_id):
            _send(chat_id, _READONLY_DENIED)
            return
        from agent.tools.telegram_sources_tool import remove_source
        channel = text[len("/removetgsource"):].strip()
        if not channel:
            _send(chat_id, "Usage: /removetgsource <@username or channel_id>\n\nUse /tgsources to see watched channels.")
            return
        removed = remove_source(channel)
        _send(chat_id, f"Removed Telegram channel: {channel}" if removed else f"'{channel}' was not in the watch list.")


# ---------------------------------------------------------------------------
# Polling loop
# ---------------------------------------------------------------------------

def run_bot() -> None:
    """Start the long-poll loop. Blocks indefinitely."""
    if not os.environ.get("TELEGRAM_BOT_TOKEN"):
        logger.warning("TELEGRAM_BOT_TOKEN not set — Telegram bot not running.")
        return

    offset = 0
    logger.info("Telegram bot started")

    while True:
        try:
            data = _api("getUpdates", offset=offset, timeout=POLL_TIMEOUT)
            for update in data.get("result", []):
                offset = update["update_id"] + 1
                try:
                    if "callback_query" in update:
                        _handle_callback(update["callback_query"])
                    elif "message" in update:
                        _handle_message(update["message"])
                except Exception as exc:
                    logger.warning("Error handling update %s: %s", update.get("update_id"), exc)
        except requests.exceptions.Timeout:
            # Normal — long-poll expired with no updates.
            pass
        except Exception as exc:
            logger.warning("Polling error: %s — retrying in 5s", exc)
            time.sleep(5)


if __name__ == "__main__":
    run_bot()
