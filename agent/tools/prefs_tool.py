"""Preferences tool — read and write user preferences stored in prefs.json.

All mutating operations (add_to_blocklist, add_to_location_blocklist,
add_to_roles) acquire a lock for the full read-modify-write cycle so concurrent
FastAPI requests and the Telegram bot cannot interleave writes.

The file path defaults to agent/prefs.json relative to this module's location.
Tests can override it with the PREFS_PATH environment variable.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from pathlib import Path

logger = logging.getLogger(__name__)

# One lock shared across all callers in this process.
_lock = threading.Lock()


def _prefs_path() -> Path:
    """Return the path to prefs.json, honouring the PREFS_PATH env override."""
    env = os.environ.get("PREFS_PATH")
    if env:
        return Path(env)
    # agent/tools/prefs_tool.py  →  .parent = agent/tools  →  .parent = agent
    return Path(__file__).resolve().parent.parent / "prefs.json"


def load_prefs() -> dict:
    """Load and return the current preferences dict from disk."""
    with open(_prefs_path(), encoding="utf-8") as fh:
        return json.load(fh)


def _save_prefs(prefs: dict) -> None:
    """Write prefs back to disk. Must be called while _lock is held."""
    with open(_prefs_path(), "w", encoding="utf-8") as fh:
        json.dump(prefs, fh, indent=2, ensure_ascii=False)


def add_to_blocklist(keyword: str) -> bool:
    """Add a keyword to the role blocklist.

    Returns True if the keyword was new, False if it was already present.
    The keyword is stored in lowercase so matching stays case-insensitive.
    """
    kw = keyword.lower().strip()
    with _lock:
        prefs = load_prefs()
        if kw in prefs.get("blocklist", []):
            return False
        prefs.setdefault("blocklist", []).append(kw)
        _save_prefs(prefs)
    logger.info("prefs: added '%s' to blocklist", kw)
    return True


def add_to_location_blocklist(city: str) -> bool:
    """Add a city to the location blocklist.

    Returns True if the city was new, False if it was already present.
    City casing is preserved because location matching is case-insensitive
    in filter_tool (it lowercases both sides before comparing).
    """
    city = city.strip()
    with _lock:
        prefs = load_prefs()
        existing = [c.lower() for c in prefs.get("location_blocklist", [])]
        if city.lower() in existing:
            return False
        prefs.setdefault("location_blocklist", []).append(city)
        _save_prefs(prefs)
    logger.info("prefs: added '%s' to location_blocklist", city)
    return True


def add_to_roles(keyword: str) -> bool:
    """Add a keyword to the roles allow-list.

    Returns True if the keyword was new, False if it was already present.
    """
    kw = keyword.lower().strip()
    with _lock:
        prefs = load_prefs()
        if kw in prefs.get("roles", []):
            return False
        prefs.setdefault("roles", []).append(kw)
        _save_prefs(prefs)
    logger.info("prefs: added '%s' to roles", kw)
    return True
