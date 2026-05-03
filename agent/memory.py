"""User preferences — type definitions for the screener's preference schema.

Preferences are stored in agent/prefs.json and accessed at runtime via
agent.tools.prefs_tool.load_prefs(). Edit prefs.json directly to change
which jobs are kept, or use the Telegram bot commands.
"""

from __future__ import annotations

from typing import List, Optional, TypedDict


class UserPreferences(TypedDict, total=False):
    roles: List[str]              # role keywords to keep
    blocklist: List[str]          # words that auto-reject a post (matched on title/summary/skills)
    location_blocklist: List[str] # cities to reject (matched on extracted location field)
    min_salary: Optional[int]
