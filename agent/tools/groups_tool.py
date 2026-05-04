"""Groups tool — read and write the list of watched WhatsApp group IDs.

Groups are stored in agent/whatsapp_sources.json as a JSON object mapping
group ID to display name: {"120363XXXXXXXXXX@g.us": "Group Name", ...}.

The name starts as an empty string and is filled in by the listener on each
startup. The file path can be overridden with the GROUPS_PATH environment
variable for test isolation.

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
    return Path(__file__).resolve().parent.parent / "whatsapp_sources.json"


def _read() -> dict[str, str]:
    with open(_groups_path(), encoding="utf-8") as fh:
        return json.load(fh)


def _write(data: dict[str, str]) -> None:
    with open(_groups_path(), "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, ensure_ascii=False)


def load_groups() -> list[str]:
    """Return the list of watched group IDs."""
    return list(_read().keys())


def load_groups_with_names() -> dict[str, str]:
    """Return the full {group_id: display_name} map."""
    return _read()


def add_group(group_id: str) -> bool:
    """Add a group ID to the watch list with an empty name placeholder.

    The listener fills in the name on its next startup.
    Returns True if the group was new, False if already present.
    """
    group_id = group_id.strip()
    with _lock:
        data = _read()
        if group_id in data:
            return False
        data[group_id] = ""
        _write(data)
    logger.info("groups: added '%s'", group_id)
    return True


def remove_group(group_id: str) -> bool:
    """Remove a group ID from the watch list.

    Returns True if the group was found and removed, False if it wasn't present.
    """
    group_id = group_id.strip()
    with _lock:
        data = _read()
        if group_id not in data:
            return False
        del data[group_id]
        _write(data)
    logger.info("groups: removed '%s'", group_id)
    return True
