"""Handlers for /listgroups, /groups, /addgroup, /removegroup."""

from __future__ import annotations

READONLY_DENIED = (
    "🔒 This is a read-only demo. "
    "Only the owner can modify preferences and sources."
)

_TELEGRAM_LIMIT = 4000


def handle_listgroups(is_owner: bool) -> list[str]:
    if not is_owner:
        return [READONLY_DENIED]

    import json
    from pathlib import Path

    snapshot_path = Path(__file__).resolve().parents[1] / "agent" / "all_whatsapp_groups.json"
    if not snapshot_path.exists():
        return [(
            "No group snapshot found yet.\n\n"
            "Start the WhatsApp listener — it writes a full list of your groups "
            "to all_whatsapp_groups.json on every connect.\n\n"
            "Then run /listgroups again."
        )]

    try:
        from agent.tools.groups_tool import load_groups
        all_groups: dict = json.loads(snapshot_path.read_text(encoding="utf-8"))
        watched = set(load_groups())
    except Exception as exc:
        return [f"Could not read group list: {exc}"]

    if not all_groups:
        return ["No groups found on the connected WhatsApp account."]

    footer = "✓ = currently being watched\nUse /addgroup <id> to start watching a group."
    header = f"All WhatsApp groups ({len(all_groups)} found):\n"
    entries = []
    for gid, name in all_groups.items():
        marker = " ✓" if gid in watched else ""
        display = name or "unknown"
        entries.append(f"  {display}{marker}\n  {gid}")

    pages: list[str] = []
    current_lines: list[str] = [header]
    current_len = len(header)

    for entry in entries:
        chunk = entry + "\n"
        if current_len + len(chunk) > _TELEGRAM_LIMIT and len(current_lines) > 1:
            pages.append("\n".join(current_lines))
            current_lines = []
            current_len = 0
        current_lines.append(entry)
        current_len += len(chunk)

    if current_lines:
        current_lines.append(footer)
        pages.append("\n".join(current_lines))

    return pages


def handle_groups() -> str:
    from agent.tools.groups_tool import load_groups_with_names
    try:
        groups = load_groups_with_names()
        if groups:
            lines = ["Watched groups:"]
            for gid, name in groups.items():
                display = name if name else "unknown"
                lines.append(f"  {display}  ({gid})")
            lines.append("")
            lines.append("Names refresh when the listener restarts.")
        else:
            lines = [
                "No groups are being watched yet.",
                "Use /addgroup <group_id> to add one.",
                "Restart the listener with an empty list to discover group IDs.",
            ]
        return "\n".join(lines)
    except Exception as exc:
        return f"Could not read groups: {exc}"


def handle_addgroup(text: str, is_owner: bool) -> str:
    if not is_owner:
        return READONLY_DENIED
    from agent.tools.groups_tool import add_group
    group_id = text[len("/addgroup"):].strip()
    if not group_id:
        return (
            "Usage: /addgroup <group_id>\n\n"
            "The group ID is the WhatsApp internal ID (not the group name), "
            "e.g. 120363XXXXXXXXXX@g.us\n\n"
            "To find IDs: empty agent/groups.json and restart the listener — "
            "it will print all your groups and their IDs."
        )
    added = add_group(group_id)
    if added:
        return (
            f"Added group: {group_id}\n\n"
            "Live messages from this group will be forwarded immediately.\n"
            "Restart the listener to also catch up on any missed messages."
        )
    return f"'{group_id}' is already in the watch list."


def handle_removegroup(text: str, is_owner: bool) -> str:
    if not is_owner:
        return READONLY_DENIED
    from agent.tools.groups_tool import remove_group
    group_id = text[len("/removegroup"):].strip()
    if not group_id:
        return "Usage: /removegroup <group_id>\n\nUse /groups to see the IDs of currently watched groups."
    removed = remove_group(group_id)
    return f"Removed group: {group_id}" if removed else f"'{group_id}' was not in the watch list."
