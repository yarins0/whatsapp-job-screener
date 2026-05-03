"""Groups tool — read and write the list of watched WhatsApp group IDs.

Group IDs are stored in agent/groups.json as a JSON array of strings in the
format "120363XXXXXXXXXX@g.us". The file path can be overridden with the
GROUPS_PATH environment variable for test isolation.

All mutating operations are thread-safe via a module-level lock.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from pathlib import Path

logger = logging.getLogger(__name__)

_lock = threading.Lock()


def _groups_path() -> Path:
    env = os.environ.get("GROUPS_PATH")
    if env:
        return Path(env)
    # agent/tools/groups_tool.py  →  .parent = agent/tools  →  .parent = agent
    return Path(__file__).resolve().parent.parent / "groups.json"


def load_groups() -> list[str]:
    """Return the current list of watched group IDs."""
    with open(_groups_path(), encoding="utf-8") as fh:
        data = json.load(fh)
    return [g for g in data if g]


def _save_groups(groups: list[str]) -> None:
    """Write the groups list back to disk. Must be called while _lock is held."""
    with open(_groups_path(), "w", encoding="utf-8") as fh:
        json.dump(groups, fh, indent=2, ensure_ascii=False)


def add_group(group_id: str) -> bool:
    """Add a group ID to the watch list.

    Returns True if the group was new, False if already present.
    """
    group_id = group_id.strip()
    with _lock:
        groups = load_groups()
        if group_id in groups:
            return False
        groups.append(group_id)
        _save_groups(groups)
    logger.info("groups: added '%s'", group_id)
    return True


def remove_group(group_id: str) -> bool:
    """Remove a group ID from the watch list.

    Returns True if the group was found and removed, False if it wasn't present.
    """
    group_id = group_id.strip()
    with _lock:
        groups = load_groups()
        if group_id not in groups:
            return False
        groups.remove(group_id)
        _save_groups(groups)
    logger.info("groups: removed '%s'", group_id)
    return True


def _group_names_path() -> Path:
    env = os.environ.get("GROUP_NAMES_PATH")
    if env:
        return Path(env)
    return Path(__file__).resolve().parent.parent / "group_names.json"


def load_group_names() -> dict[str, str]:
    """Return the cached {group_id: display_name} mapping written by the listener."""
    try:
        with open(_group_names_path(), encoding="utf-8") as fh:
            return json.load(fh)
    except FileNotFoundError:
        return {}
    except Exception:
        return {}
