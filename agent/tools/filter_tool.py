"""Filter tool — decides whether an extracted job matches the user's preferences."""

from __future__ import annotations

from typing import Optional

from agent.memory import USER_PREFS, UserPreferences


def filter_job(job: dict, prefs: Optional[UserPreferences] = None) -> bool:
    """Return True if ``job`` should be kept for the digest."""
    p = prefs or USER_PREFS

    haystack = " ".join(
        [
            job.get("title", "") or "",
            job.get("summary", "") or "",
            " ".join(job.get("skills") or []),
        ]
    ).lower()

    # 1. Hard blocklist
    if any(b.lower() in haystack for b in p.get("blocklist", [])):
        return False

    # 2. Role keywords (at least one must match)
    roles = p.get("roles", [])
    if roles and not any(r.lower() in haystack for r in roles):
        return False

    # 3. Location: pass unless the extracted location contains a blocked city.
    #    Remote jobs always pass regardless of location.
    location = (job.get("location") or "").lower()
    is_remote = bool(job.get("remote"))
    location_blocklist = p.get("location_blocklist", [])
    location_blocked = not is_remote and any(
        city.lower() in location for city in location_blocklist
    )

    return not location_blocked
