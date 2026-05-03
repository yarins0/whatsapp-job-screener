"""User preferences — what kinds of jobs the screener should keep.

This is a single-user app, so we keep it as a Python dict for now.
A future version can swap this for ``ConversationBufferMemory`` and let
the user edit their prefs via chat.
"""

from __future__ import annotations

from typing import List, Optional, TypedDict


class UserPreferences(TypedDict, total=False):
    roles: List[str]              # role keywords to keep
    blocklist: List[str]          # words that auto-reject a post (matched on title/summary/skills)
    location_blocklist: List[str] # cities to reject (matched on extracted location field)
    min_salary: Optional[int]


USER_PREFS: UserPreferences = {
    "roles": [
        "backend", "back-end",
        "fullstack", "full-stack", "full stack",
        "software engineer", "software developer",
        "python", "node.js", "nodejs", "react",
        "junior", "junior developer", "junior engineer",
    ],
    "blocklist": ["unpaid", "volunteer", "senior", "sales", "qa",
                   "student", "bookkeeper", "hardware", "marketing",
                   "firmware", "embedded", "electrical"],
    "location_blocklist": ["Jerusalem", "Haifa", "Yokneam", "Kiryat Shmona", "North Israel", "Kiryat Motzkin"],
    "min_salary": None,
}
