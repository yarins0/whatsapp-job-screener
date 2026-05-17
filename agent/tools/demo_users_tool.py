"""Demo users registry — stores chat IDs of non-owner users who have started the bot."""

from __future__ import annotations

import json
import os
from pathlib import Path

DEFAULT_PATH = Path(__file__).resolve().parents[2] / "agent" / "demo_users.json"


def _path() -> Path:
    return Path(os.environ.get("DEMO_USERS_PATH", DEFAULT_PATH))


def load_demo_users() -> list[int]:
    try:
        return json.loads(_path().read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return []


def add_demo_user(chat_id: int) -> bool:
    """Register a demo user. Returns True if newly added, False if already present."""
    users = load_demo_users()
    if chat_id in users:
        return False
    users.append(chat_id)
    _path().write_text(json.dumps(users, indent=2), encoding="utf-8")
    return True
