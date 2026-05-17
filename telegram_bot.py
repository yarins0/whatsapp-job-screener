"""Telegram bot — polls for updates and dispatches commands to handler modules.

Runs as a standalone process (started by start.py). Uses long-polling so no public
URL or webhook setup is required.

Access control:
  Owner  — identified by TELEGRAM_CHAT_ID; full access to all commands.
  Demo   — any other chat_id; read-only with a capped /ask quota.

Command implementations live in telegram_handlers/:
  start.py     — /help, /start, /commands
  jobs.py      — /jobs, /ask, /similar, /reindex, /resend
  prefs.py     — /prefs, /blockrole, /blockcity, /addrole
  groups.py    — /listgroups, /groups, /addgroup, /removegroup
  sources.py   — /tgsources, /addtgsource, /removetgsource
  callbacks.py — inline button handling
"""

from __future__ import annotations

import logging
import os
import time

import requests
from dotenv import load_dotenv

import telegram_handlers.callbacks as _cb
import telegram_handlers.groups as _grp
import telegram_handlers.jobs as _jobs
import telegram_handlers.prefs as _prf
import telegram_handlers.sources as _src
import telegram_handlers.start as _start

load_dotenv()
logger = logging.getLogger(__name__)
for _lvl, _name in [(10, "debug"), (20, "info"), (30, "warning"), (40, "error"), (50, "critical")]:
    logging.addLevelName(_lvl, _name)
logging.basicConfig(level=logging.INFO, format="[%(asctime)s][%(levelname)s] %(message)s", datefmt="%H:%M:%S")

POLL_TIMEOUT = 30


# ---------------------------------------------------------------------------
# Core API helpers
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
    try:
        _api("answerCallbackQuery", callback_query_id=callback_id, text=text, show_alert=False)
    except Exception as exc:
        logger.warning("answerCallbackQuery failed: %s", exc)


def _is_owner(chat_id: int) -> bool:
    owner_str = os.environ.get("TELEGRAM_CHAT_ID")
    if not owner_str:
        return False
    return chat_id == int(owner_str)


def _send(chat_id: int | str, text: str) -> None:
    try:
        _api("sendMessage", chat_id=chat_id, text=text)
    except Exception as exc:
        logger.warning("sendMessage failed: %s", exc)


def _dispatch(chat_id: int, reply: str | list[str]) -> None:
    """Send one or more reply strings to chat_id."""
    if isinstance(reply, str):
        _send(chat_id, reply)
    else:
        for msg in reply:
            _send(chat_id, msg)


# ---------------------------------------------------------------------------
# Update handlers
# ---------------------------------------------------------------------------

def _handle_callback(query: dict) -> None:
    _cb.handle_callback(
        data=query.get("data", ""),
        chat_id=query["from"]["id"],
        callback_id=query["id"],
        is_owner=_is_owner(query["from"]["id"]),
        answer_fn=_answer_callback,
        send_fn=_send,
    )


def _handle_message(msg: dict) -> None:
    text: str = (msg.get("text") or "").strip()
    chat_id: int = msg["chat"]["id"]
    is_owner = _is_owner(chat_id)

    if text.startswith("/help"):
        _dispatch(chat_id, _start.get_help())
    elif text.startswith("/start"):
        _dispatch(chat_id, _start.handle_start(chat_id, is_owner))
    elif text.startswith("/commands"):
        _dispatch(chat_id, _start.handle_commands(is_owner))
    elif text.startswith("/jobs"):
        _dispatch(chat_id, _jobs.handle_jobs(text))
    elif text.startswith("/ask"):
        _dispatch(chat_id, _jobs.handle_ask(chat_id, text, is_owner))
    elif text.startswith("/similar"):
        _dispatch(chat_id, _jobs.handle_similar(text))
    elif text.startswith("/reindex"):
        _dispatch(chat_id, _jobs.handle_reindex(is_owner))
    elif text.startswith("/resend"):
        _dispatch(chat_id, _jobs.handle_resend(text, is_owner))
    elif text.startswith("/prefs"):
        _dispatch(chat_id, _prf.handle_prefs())
    elif text.startswith("/blockrole"):
        _dispatch(chat_id, _prf.handle_blockrole(text, is_owner))
    elif text.startswith("/blockcity"):
        _dispatch(chat_id, _prf.handle_blockcity(text, is_owner))
    elif text.startswith("/addrole"):
        _dispatch(chat_id, _prf.handle_addrole(text, is_owner))
    elif text.startswith("/listgroups"):
        _dispatch(chat_id, _grp.handle_listgroups(is_owner))
    elif text.startswith("/groups"):
        _dispatch(chat_id, _grp.handle_groups())
    elif text.startswith("/addgroup"):
        _dispatch(chat_id, _grp.handle_addgroup(text, is_owner))
    elif text.startswith("/removegroup"):
        _dispatch(chat_id, _grp.handle_removegroup(text, is_owner))
    elif text.startswith("/tgsources"):
        _dispatch(chat_id, _src.handle_tgsources())
    elif text.startswith("/addtgsource"):
        _dispatch(chat_id, _src.handle_addtgsource(text, is_owner))
    elif text.startswith("/removetgsource"):
        _dispatch(chat_id, _src.handle_removetgsource(text, is_owner))


# ---------------------------------------------------------------------------
# Polling loop
# ---------------------------------------------------------------------------

def run_bot() -> None:
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
            pass
        except Exception as exc:
            logger.warning("Polling error: %s — retrying in 5s", exc)
            time.sleep(5)


if __name__ == "__main__":
    run_bot()
