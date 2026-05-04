"""Telegram sources tool — read and write the list of watched Telegram channels.

Channels are stored in agent/telegram_sources.json as a JSON object mapping
channel username or ID to display name: {"@jobstlv": "Jobs TLV", ...}.

All mutating operations are thread-safe via a module-level lock.
"""

from __future__ import annotations

import json
import logging
import threading
from pathlib import Path

logger = logging.getLogger(__name__)

_lock = threading.Lock()

_SOURCES_FILE = Path(__file__).resolve().parent.parent / "telegram_sources.json"


def _read() -> dict[str, str]:
    try:
        return json.loads(_SOURCES_FILE.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _write(data: dict[str, str]) -> None:
    _SOURCES_FILE.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def load_sources() -> dict[str, str]:
    """Return the full {channel_id: display_name} map."""
    return _read()


def add_source(channel: str) -> bool:
    """Add a channel to the watch list.

    Returns True if added, False if already present.
    """
    channel = channel.strip()
    with _lock:
        data = _read()
        if channel in data:
            return False
        data[channel] = ""
        _write(data)
    logger.info("telegram_sources: added '%s'", channel)
    return True


def remove_source(channel: str) -> bool:
    """Remove a channel from the watch list.

    Returns True if removed, False if not found.
    """
    channel = channel.strip()
    with _lock:
        data = _read()
        if channel not in data:
            return False
        del data[channel]
        _write(data)
    logger.info("telegram_sources: removed '%s'", channel)
    return True
