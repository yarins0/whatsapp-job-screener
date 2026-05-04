"""Telegram source listener — reads job posts from watched channels and groups.

Uses Telethon (userbot) so it can access any channel the Telegram account is a
member of, including broadcast-only channels where bots cannot join.

Required env vars (add to .env):
  TELEGRAM_API_ID    — from https://my.telegram.org (App api_id)
  TELEGRAM_API_HASH  — from https://my.telegram.org (App api_hash)
  TELEGRAM_PHONE     — your phone number, e.g. +972501234567

First run: Telethon will prompt for the SMS/app verification code in the terminal.
The session is saved to sources/telegram/.session so subsequent starts need no
interaction.

Source config: agent/telegram_sources.json — a {channel_id_or_username: display_name}
map. Same pattern as agent/groups.json. Add channels here or edit the file directly.

On startup the listener replays any messages received since the last seen timestamp
(stored in sources/telegram/.last_seen.json). Messages are forwarded to the FastAPI
ingest endpoint in the same format as the WhatsApp listener.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s — %(message)s")

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

SESSION_FILE = Path(__file__).parent / ".session"
LAST_SEEN_FILE = Path(__file__).parent / ".last_seen.json"
SOURCES_FILE = Path(__file__).parents[2] / "agent" / "telegram_sources.json"
API_URL = os.environ.get("INGEST_API_URL", "http://localhost:8000/ingest")

# Never replay messages older than this on startup.
CATCHUP_MAX_AGE_S = 48 * 60 * 60


# ---------------------------------------------------------------------------
# Last-seen persistence (mirrors WhatsApp last_seen.js pattern)
# ---------------------------------------------------------------------------

def _load_last_seen() -> dict[str, int]:
    try:
        return json.loads(LAST_SEEN_FILE.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _save_last_seen(state: dict[str, int]) -> None:
    LAST_SEEN_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")


def _update_last_seen(channel_id: str, timestamp: int) -> None:
    state = _load_last_seen()
    state[channel_id] = max(state.get(channel_id, 0), timestamp)
    _save_last_seen(state)


# ---------------------------------------------------------------------------
# Source config
# ---------------------------------------------------------------------------

def _load_sources() -> dict[str, str]:
    """Return {channel_id_or_username: display_name} from telegram_sources.json."""
    try:
        return json.loads(SOURCES_FILE.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


# ---------------------------------------------------------------------------
# Ingest forwarding
# ---------------------------------------------------------------------------

def _forward(text: str, source_name: str, timestamp: int) -> None:
    """POST a message to the FastAPI ingest endpoint."""
    try:
        requests.post(
            API_URL,
            json={
                "group": source_name,
                "sender": "telegram-source",
                "text": text,
                "timestamp": timestamp,
            },
            timeout=15,
        )
    except Exception as exc:
        logger.warning("Failed to forward message to ingest: %s", exc)


# ---------------------------------------------------------------------------
# Listener
# ---------------------------------------------------------------------------

async def run_listener() -> None:
    """Connect to Telegram and start listening for new messages."""
    # Telethon is imported here so the module can be imported in tests without
    # requiring the package to be installed.
    try:
        from telethon import TelegramClient, events
        from telethon.tl.functions.messages import GetHistoryRequest
    except ImportError:
        logger.error(
            "telethon is not installed. Run: pip install telethon"
        )
        return

    api_id = os.environ.get("TELEGRAM_API_ID")
    api_hash = os.environ.get("TELEGRAM_API_HASH")
    phone = os.environ.get("TELEGRAM_PHONE")

    if not api_id or not api_hash or not phone:
        logger.warning(
            "TELEGRAM_API_ID, TELEGRAM_API_HASH, and TELEGRAM_PHONE must all be set "
            "in .env — Telegram source listener not running."
        )
        return

    sources = _load_sources()
    if not sources:
        logger.info(
            "agent/telegram_sources.json is empty — no Telegram sources to watch. "
            "Add channel IDs or usernames (e.g. @jobstlv) to the file."
        )
        return

    client = TelegramClient(str(SESSION_FILE), int(api_id), api_hash)

    @client.on(events.NewMessage(chats=list(sources.keys())))
    async def _on_message(event) -> None:
        text = event.message.message
        if not text:
            return  # skip media-only messages
        chat = await event.get_chat()
        source_name = sources.get(str(chat.id)) or sources.get(getattr(chat, "username", "")) or chat.title or str(chat.id)
        timestamp = int(event.message.date.timestamp())
        _forward(text, source_name, timestamp)
        _update_last_seen(str(chat.id), timestamp)
        logger.info("Forwarded message from %s", source_name)

    await client.start(phone=phone)
    logger.info("Telegram source listener started — watching %d source(s).", len(sources))

    # Catch up on messages missed since last run.
    cutoff = int(time.time()) - CATCHUP_MAX_AGE_S
    last_seen = _load_last_seen()

    for channel_id, display_name in sources.items():
        since = max(last_seen.get(str(channel_id), 0), cutoff)
        since_dt = datetime.fromtimestamp(since, tz=timezone.utc)
        try:
            entity = await client.get_entity(channel_id)
            source_name = display_name or getattr(entity, "title", channel_id)
            messages = await client.get_messages(entity, limit=100, offset_date=since_dt, reverse=True)
            missed = [m for m in messages if m.message and int(m.date.timestamp()) > since]
            if missed:
                logger.info("[catch-up] %s: replaying %d missed message(s)", source_name, len(missed))
                for m in missed:
                    _forward(m.message, source_name, int(m.date.timestamp()))
                _update_last_seen(str(entity.id), int(missed[-1].date.timestamp()))
            else:
                logger.info("[catch-up] %s: no missed messages", source_name)
        except Exception as exc:
            logger.warning("[catch-up] Could not catch up on %s: %s", channel_id, exc)

    logger.info("Ready — listening for new Telegram messages.")
    await client.run_until_disconnected()


if __name__ == "__main__":
    asyncio.run(run_listener())
