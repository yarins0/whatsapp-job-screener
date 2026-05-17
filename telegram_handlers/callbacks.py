"""Handler for inline button callback queries (Block role / Block city)."""

from __future__ import annotations

from typing import Callable

READONLY_DENIED = (
    "🔒 This is a read-only demo. "
    "Only the owner can modify preferences and sources."
)


def handle_callback(
    data: str,
    chat_id: int,
    callback_id: str,
    is_owner: bool,
    answer_fn: Callable[[str, str], None],
    send_fn: Callable[[int, str], None],
) -> None:
    """Process an inline-button press. Calls answer_fn to dismiss the spinner."""
    from agent.tools.prefs_tool import add_to_blocklist, add_to_location_blocklist

    if not is_owner:
        answer_fn(callback_id, READONLY_DENIED)
        return

    if data.startswith("block_role:"):
        parts = data.split(":", 2)
        keyword = parts[2].strip() if len(parts) == 3 else ""
        if not keyword:
            answer_fn(callback_id, "Could not read role keyword.")
            return
        added = add_to_blocklist(keyword)
        msg = f"Blocked: '{keyword}'" if added else f"'{keyword}' was already blocked."
        answer_fn(callback_id, msg)
        send_fn(chat_id, msg)

    elif data.startswith("block_city:"):
        parts = data.split(":", 2)
        city = parts[2].strip() if len(parts) == 3 else ""
        if not city:
            answer_fn(callback_id, "Could not read city.")
            return
        added = add_to_location_blocklist(city)
        msg = f"Blocked city: '{city}'" if added else f"'{city}' was already blocked."
        answer_fn(callback_id, msg)
        send_fn(chat_id, msg)

    else:
        answer_fn(callback_id, "Unknown action.")
