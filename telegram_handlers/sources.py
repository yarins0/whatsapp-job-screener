"""Handlers for /tgsources, /addtgsource, /removetgsource."""

from __future__ import annotations

READONLY_DENIED = (
    "🔒 This is a read-only demo. "
    "Only the owner can modify preferences and sources."
)


def handle_tgsources() -> str:
    from agent.tools.telegram_sources_tool import load_sources
    try:
        sources = load_sources()
        if sources:
            lines = ["Watched Telegram channels:"]
            for channel, name in sources.items():
                lines.append(f"  {channel}" + (f" — {name}" if name else ""))
            return "\n".join(lines)
        return "No Telegram channels are being watched yet.\nUse /addtgsource @username to add one."
    except Exception as exc:
        return f"Could not read Telegram sources: {exc}"


def handle_addtgsource(text: str, is_owner: bool) -> str:
    if not is_owner:
        return READONLY_DENIED
    from agent.tools.telegram_sources_tool import add_source
    channel = text[len("/addtgsource"):].strip()
    if not channel:
        return "Usage: /addtgsource <@username or channel_id>"
    added = add_source(channel)
    if added:
        return f"Added Telegram channel: {channel}\n\nRestart tg-source for it to take effect."
    return f"{channel} is already in the watch list."


def handle_removetgsource(text: str, is_owner: bool) -> str:
    if not is_owner:
        return READONLY_DENIED
    from agent.tools.telegram_sources_tool import remove_source
    channel = text[len("/removetgsource"):].strip()
    if not channel:
        return "Usage: /removetgsource <@username or channel_id>\n\nUse /tgsources to see watched channels."
    removed = remove_source(channel)
    return f"Removed Telegram channel: {channel}" if removed else f"'{channel}' was not in the watch list."
