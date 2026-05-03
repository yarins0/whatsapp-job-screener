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
    """Process a text command sent to the bot."""
    from agent.tools.prefs_tool import load_prefs

    text: str = (msg.get("text") or "").strip()
    chat_id: int = msg["chat"]["id"]

    if text.startswith("/prefs"):
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
