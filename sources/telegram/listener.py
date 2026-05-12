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
for _lvl, _name in [(10, "debug"), (20, "info"), (30, "warning"), (40, "error"), (50, "critical")]:
    logging.addLevelName(_lvl, _name)
logging.basicConfig(level=logging.INFO, format="[%(asctime)s][%(levelname)s] %(message)s", datefmt="%H:%M:%S")
logging.getLogger("telethon").setLevel(logging.WARNING)

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
# Link preview extraction
# ---------------------------------------------------------------------------

def _extract_preview(message) -> str | None:
    """Return title + description from a Telegram link preview, or None.

    Uses duck typing so no Telethon type import is needed at module level.
    Only MessageMediaWebPage objects have a .webpage attribute with .title /
    .description; all other media types (photos, videos, etc.) are ignored.
    """
    webpage = getattr(getattr(message, "media", None), "webpage", None)
    if webpage is None:
        return None
    parts = [p for p in (getattr(webpage, "title", None), getattr(webpage, "description", None)) if p]
    return "\n".join(parts) if parts else None


def _build_text(message) -> str | None:
    """Return the message text with any link preview appended.

    Returns None if there is neither text nor a usable preview (e.g. photo-only).
    """
    text = message.message or ""
    preview = _extract_preview(message)
    if not text and not preview:
        return None
    if not preview:
        return text
    if not text:
        return preview
    return f"{text}\n\n[Link preview]\n{preview}"


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

    # Telethon calls input() interactively when no session file exists, which
    # fails when launched as a subprocess (no stdin). Detect this upfront and
    # exit with a clear message so start.py doesn't crash-loop.
    # Telethon saves the session as either "<name>.session" or just "<name>"
    # depending on the version. Check both.
    session_path = SESSION_FILE if SESSION_FILE.exists() else Path(str(SESSION_FILE) + ".session")
    if not session_path.exists():
        logger.error(
            "No Telegram session found. Run once interactively to authenticate:\n"
            "  python -m sources.telegram.listener\n"
            "Then restart start.py."
        )
        return

    client = TelegramClient(str(SESSION_FILE), int(api_id), api_hash)

    await client.start(phone=phone)

    # Resolve every configured source to a real Telegram entity now, before
    # registering the event handler. Passing raw strings (usernames / IDs) to
    # events.NewMessage and letting Telethon resolve them lazily at dispatch time
    # causes an unhandled exception in _dispatch_update when a username doesn't
    # exist. Pre-resolving gives us a clean log warning and keeps the listener
    # running for all valid sources.
    resolved: list[tuple[object, str]] = []  # (entity, display_name)
    for key, display_name in sources.items():
        try:
            entity = await client.get_entity(key)
            name = display_name or getattr(entity, "title", None) or key
            resolved.append((entity, name))

        except Exception as exc:
            logger.warning(
                "[source] Could not resolve source %r — skipping. %s: %s",
                key, type(exc).__name__, exc,
            )

    if not resolved:
        # All configured sources failed to resolve — bad IDs or usernames.
        # Do NOT exit: exiting would cause start.py to restart in a loop and
        # spam the logs. Stay alive (idle) so the operator can fix the IDs
        # in agent/telegram_sources.json and then do a clean restart.
        logger.error(
            "[source] None of the configured sources could be resolved. "
            "Fix the usernames/IDs in agent/telegram_sources.json, then restart. "
            "Waiting idle — not listening for any messages."
        )
        await client.run_until_disconnected()
        return

    # Build a fast id→name lookup used inside the event handler.
    entity_names: dict[int, str] = {entity.id: name for entity, name in resolved}
    entity_objects = [entity for entity, _ in resolved]

    logger.info("Ready — watching %d Telegram source(s)", len(resolved))

    @client.on(events.NewMessage(chats=entity_objects))
    async def _on_message(event) -> None:
        text = _build_text(event.message)
        if not text:
            return  # skip media-only messages with no preview
        chat = await event.get_chat()
        source_name = entity_names.get(chat.id) or getattr(chat, "title", None) or str(chat.id)
        timestamp = int(event.message.date.timestamp())
        _forward(text, source_name, timestamp)
        _update_last_seen(str(chat.id), timestamp)
        logger.info("Forwarded message from %s", source_name)

    # Catch up on messages missed since last run using the already-resolved entities.
    cutoff = int(time.time()) - CATCHUP_MAX_AGE_S
    last_seen = _load_last_seen()

    for entity, source_name in resolved:
        entity_id = str(entity.id)
        since = max(last_seen.get(entity_id, 0), cutoff)
        since_dt = datetime.fromtimestamp(since, tz=timezone.utc)
        try:
            messages = await client.get_messages(entity, limit=100, offset_date=since_dt, reverse=True)
            missed = [m for m in messages if _build_text(m) and int(m.date.timestamp()) > since]
            if missed:
                logger.info("[catch-up] %s: replaying %d missed message(s)", source_name, len(missed))
                for m in missed:
                    _forward(_build_text(m), source_name, int(m.date.timestamp()))
                _update_last_seen(entity_id, int(missed[-1].date.timestamp()))
        except Exception as exc:
            logger.warning("[catch-up] Could not catch up on %s — skipping. %s: %s", source_name, type(exc).__name__, exc)

    await client.run_until_disconnected()


if __name__ == "__main__":
    try:
        asyncio.run(run_listener())
    except KeyboardInterrupt:
        # Ctrl+C during asyncio.run() always re-raises KeyboardInterrupt after
        # cancelling pending tasks. This is normal shutdown, not an error.
        pass
