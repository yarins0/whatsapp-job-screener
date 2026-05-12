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
import re
import time
from datetime import datetime, timezone
from html.parser import HTMLParser
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
    """Return labeled preview fields from a Telegram link preview, or None.

    Uses duck typing so no Telethon type import is needed at module level.
    Only MessageMediaWebPage objects have a .webpage attribute with .title /
    .description / .url; all other media types (photos, videos, etc.) are ignored.

    Fields are labeled (Title: / Description: / URL:) so the LLM can reliably
    map each line to the correct schema field without guessing.
    """
    webpage = getattr(getattr(message, "media", None), "webpage", None)
    if webpage is None:
        return None
    title = getattr(webpage, "title", None)
    description = getattr(webpage, "description", None)
    url = getattr(webpage, "url", None)
    parts = []
    if title:       parts.append(f"Title: {title}")
    if description: parts.append(f"Description: {description}")
    if url:         parts.append(f"URL: {url}")
    return "\n".join(parts) if parts else None


_URL_RE = re.compile(r"https?://\S+")


def _find_first_url(text: str) -> str | None:
    """Return the first http/https URL found in text, or None."""
    match = _URL_RE.search(text)
    return match.group(0).rstrip(".,)>") if match else None


class _OGParser(HTMLParser):
    """Extract og:title, og:description, and <title> from an HTML snippet."""

    def __init__(self) -> None:
        super().__init__()
        self.og_title: str | None = None
        self.og_description: str | None = None
        self.page_title: str | None = None
        self._in_title = False

    def handle_starttag(self, tag: str, attrs: list) -> None:
        if tag == "title":
            self._in_title = True
            return
        if tag != "meta":
            return
        d = dict(attrs)
        prop = (d.get("property") or d.get("name") or "").lower()
        content = d.get("content") or ""
        if prop == "og:title" and not self.og_title:
            self.og_title = content.strip()
        elif prop == "og:description" and not self.og_description:
            self.og_description = content.strip()

    def handle_data(self, data: str) -> None:
        if self._in_title and not self.page_title:
            self.page_title = data.strip()

    def handle_endtag(self, tag: str) -> None:
        if tag == "title":
            self._in_title = False


def _fetch_url_preview(url: str) -> str | None:
    """Fetch OG metadata from a URL and return labeled preview lines, or None.

    Falls back to the HTML <title> when og:title is absent.
    Times out after 5 seconds so it never blocks the listener significantly.
    Only parses the first 50 KB of the response to avoid reading large pages.
    """
    try:
        resp = requests.get(
            url,
            timeout=5,
            headers={"User-Agent": "Mozilla/5.0 (compatible; JobBot/1.0)"},
            allow_redirects=True,
        )
        if not resp.ok or "text/html" not in resp.headers.get("Content-Type", ""):
            return None
        parser = _OGParser()
        parser.feed(resp.text[:50_000])
        title = parser.og_title or parser.page_title
        description = parser.og_description
        if not title and not description:
            return None
        parts = []
        if title:
            parts.append(f"Title: {title}")
        if description:
            parts.append(f"Description: {description}")
        parts.append(f"URL: {url}")
        return "\n".join(parts)
    except Exception:
        return None


def _build_text(message) -> str | None:
    """Return the message text with any link preview appended.

    First tries Telegram's own MessageMediaWebPage preview (zero latency).
    Falls back to fetching the first URL in the message body when the
    Telegram preview is empty — which happens when Telegram's crawler
    hasn't finished processing the link yet.

    Returns None if there is neither text nor a usable preview (e.g. photo-only).
    """
    text = message.message or ""
    preview = _extract_preview(message)
    if not preview:
        url = _find_first_url(text)
        if url:
            preview = _fetch_url_preview(url)
            if preview:
                logger.debug("Fetched URL preview for %s", url)
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
