"""Prompt-caching configuration for the LLM chains.

Controls whether system prompts are cached between Anthropic API calls.
All three chains (classifier, extractor, combined) read this via get_cache_control().

Set PROMPT_CACHE_TTL in .env:
  off   — no caching (default)
  5m    — 5-minute TTL  (break-even: ≥ 3 messages per 5-min window)
  1h    — 1-hour TTL    (break-even: ≥ 12 messages per hour)
  auto  — dynamic: caches during catch-up bursts, off at night and during sparse traffic

See docs/PROMPT_CACHING.md for the full break-even math and auto mode details.
"""

from __future__ import annotations

import math
import os
import sqlite3
import time
from datetime import datetime
from typing import Optional

# ---------------------------------------------------------------------------
# Auto mode tuning constants — edit here, not in .env
# ---------------------------------------------------------------------------

# Night window: caching is off between these hours (24h format, local time).
_NIGHT_START_HOUR = 22   # 10 pm
_NIGHT_END_HOUR   = 8    # 9 am

# Idle duration (minutes) that triggers a burst-mode activation.
# After being quiet for this long, the next incoming message is likely the
# start of a WhatsApp catch-up replay — enable caching for the burst.
_BURST_THRESHOLD_MINUTES = 30

# How long (minutes) burst mode stays active after the last message arrived.
# Each new message extends the window, so burst mode expires naturally once
# traffic drops off (i.e. catch-up is complete).
_BURST_WINDOW_MINUTES = 10

# ---------------------------------------------------------------------------
# Static TTL map
# ---------------------------------------------------------------------------

_TTL_MAP: dict[str, Optional[dict]] = {
    "off":  None,
    "5m":   {"type": "ephemeral"},
    "1h":   {"type": "ephemeral", "ttl": "1h"},
    "auto": None,  # placeholder — handled dynamically in get_cache_control()
}

# In-memory state for auto mode: epoch time until which burst mode is active.
_burst_mode_until: float = 0.0


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _is_night_hours() -> bool:
    hour = datetime.now().hour
    if _NIGHT_START_HOUR > _NIGHT_END_HOUR:
        # Spans midnight: e.g. 22–7
        return hour >= _NIGHT_START_HOUR or hour < _NIGHT_END_HOUR
    # Same-day window: e.g. 1–6
    return _NIGHT_START_HOUR <= hour < _NIGHT_END_HOUR


def _minutes_idle() -> float:
    """Return minutes since the last recorded group activity.

    Queries MAX(updated_at) from group_stats. Returns math.inf when the table
    is empty or inaccessible (treats a cold start as a long idle period so the
    first burst is always detected correctly).
    """
    db_path = os.getenv("JOBS_DB_PATH", "db/jobs.db")
    try:
        conn = sqlite3.connect(db_path)
        try:
            row = conn.execute(
                "SELECT MAX(updated_at) FROM group_stats"
            ).fetchone()
        finally:
            conn.close()
    except Exception:
        return math.inf

    if row is None or row[0] is None:
        return math.inf

    try:
        last = datetime.fromisoformat(row[0])
        return (datetime.now() - last).total_seconds() / 60
    except (ValueError, TypeError):
        return math.inf


def _get_auto_cache_control() -> Optional[dict]:
    """Decide at runtime whether caching should be active."""
    global _burst_mode_until
    now = time.time()

    # Night: always off; also reset burst so morning starts fresh.
    if _is_night_hours():
        _burst_mode_until = 0.0
        return None

    # Burst window still active — extend it and keep caching.
    if now < _burst_mode_until:
        _burst_mode_until = now + _BURST_WINDOW_MINUTES * 60
        return {"type": "ephemeral"}

    # Check whether a burst is starting (long idle before this message).
    if _minutes_idle() > _BURST_THRESHOLD_MINUTES:
        _burst_mode_until = now + _BURST_WINDOW_MINUTES * 60
        return {"type": "ephemeral"}

    # Normal sparse traffic: caching not worth the write overhead.
    return None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def get_cache_control() -> Optional[dict]:
    """Return the cache_control dict for the current PROMPT_CACHE_TTL setting.

    Returns None when caching is disabled — the system prompt block is sent
    without a cache_control key and Anthropic will not cache it.
    """
    raw = os.getenv("PROMPT_CACHE_TTL", "off").strip().lower()
    if raw not in _TTL_MAP:
        valid = ", ".join(_TTL_MAP)
        raise ValueError(
            f"Invalid PROMPT_CACHE_TTL={raw!r}. Valid values: {valid}"
        )
    if raw == "auto":
        return _get_auto_cache_control()
    return _TTL_MAP[raw]
