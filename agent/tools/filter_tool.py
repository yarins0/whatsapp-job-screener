"""Filter tool — decides whether an extracted job matches the user's preferences."""

from __future__ import annotations

from typing import Optional, Tuple

from agent.memory import USER_PREFS, UserPreferences


def filter_job(job: dict, prefs: Optional[UserPreferences] = None) -> Tuple[bool, str]:
    """Return (passed, reason) indicating whether the job should be kept.

    ``reason`` is an empty string when the job passes, or a human-readable
    explanation of which rule rejected it.
    """
    p = prefs or USER_PREFS

    haystack = " ".join(
        [
            job.get("title", "") or "",
            job.get("summary", "") or "",
            " ".join(job.get("skills") or []),
        ]
    ).lower()

    # 1. Hard blocklist
    matched = [b for b in p.get("blocklist", []) if b.lower() in haystack]
    if matched:
        return False, f"blocklist match: {', '.join(matched)}"

    # 2. Role keywords (at least one must match)
    roles = p.get("roles", [])
    if roles and not any(r.lower() in haystack for r in roles):
        return False, "no role keyword matched"

    # 3. Location: pass unless the extracted location contains a blocked city.
    #    Remote jobs always pass regardless of location.
    location = (job.get("location") or "").lower()
    is_remote = bool(job.get("remote"))
    location_blocklist = p.get("location_blocklist", [])
    blocked_city = next(
        (city for city in location_blocklist if city.lower() in location),
        None,
    )
    if not is_remote and blocked_city:
        return False, f"blocked location: {blocked_city}"

    return True, ""
