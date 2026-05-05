"""Telegram bot — polls for updates and handles inline-button callbacks and commands.

Runs as a standalone process (started by start.py). Uses long-polling so no public
URL or webhook setup is required.

Supported interactions:
  Button callbacks (on job notifications):
    block_role:<job_id>:<keyword>  — add keyword to blocklist
    block_city:<job_id>:<city>     — add city to location_blocklist

  Text commands:
    /prefs — show the current preferences
"""

from __future__ import annotations

import logging
import os
import time

import requests
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s — %(message)s")

POLL_TIMEOUT = 30  # seconds for each long-poll request

_READONLY_DENIED = (
    "🔒 This is a read-only demo. "
    "Only the owner can modify preferences and sources."
)


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
            "Use /jobs to browse stored jobs, or /jobs python to filter by keyword.\n\n"
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
                "/jobs <keyword> unseen — keyword + unseen only\n\n"
                "Preferences:\n"
                "/prefs — show current roles, blocklist, and blocked cities\n"
                "/blockrole <keyword> — add a keyword to the role blocklist\n"
                "/blockcity <city> — add a city to the location blocklist\n"
                "/addrole <keyword> — add a keyword to the roles allow-list\n\n"
                "WhatsApp groups:\n"
                "/groups — list watched WhatsApp groups\n"
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
                "/jobs <keyword> unseen — keyword + unseen only\n\n"
                "Preferences (view only):\n"
                "/prefs — show current roles, blocklist, and blocked cities\n"
                "/blockrole <keyword> — 🔒 owner only\n"
                "/blockcity <city> — 🔒 owner only\n"
                "/addrole <keyword> — 🔒 owner only\n\n"
                "WhatsApp groups (view only):\n"
                "/groups — list watched WhatsApp groups\n"
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
    logger.info("Telegram bot started (long-polling).")

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
