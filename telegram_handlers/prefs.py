"""Handlers for /prefs, /blockrole, /blockcity, /addrole."""

from __future__ import annotations

READONLY_DENIED = (
    "🔒 This is a read-only demo. "
    "Only the owner can modify preferences and sources."
)


def handle_prefs() -> str:
    from agent.tools.prefs_tool import load_prefs
    try:
        prefs = load_prefs()
        roles = ", ".join(prefs.get("roles") or []) or "(none)"
        blocked = ", ".join(prefs.get("blocklist") or []) or "(none)"
        cities = ", ".join(prefs.get("location_blocklist") or []) or "(none)"
        return (
            "Current preferences:\n"
            f"Roles: {roles}\n"
            f"Blocklist: {blocked}\n"
            f"Blocked cities: {cities}"
        )
    except Exception as exc:
        return f"Could not read preferences: {exc}"


def handle_blockrole(text: str, is_owner: bool) -> str:
    if not is_owner:
        return READONLY_DENIED
    from agent.tools.prefs_tool import add_to_blocklist
    keyword = text[len("/blockrole"):].strip()
    if not keyword:
        return "Usage: /blockrole <keyword>"
    added = add_to_blocklist(keyword)
    return f"Blocked: '{keyword.lower()}'" if added else f"'{keyword.lower()}' was already blocked."


def handle_blockcity(text: str, is_owner: bool) -> str:
    if not is_owner:
        return READONLY_DENIED
    from agent.tools.prefs_tool import add_to_location_blocklist
    city = text[len("/blockcity"):].strip()
    if not city:
        return "Usage: /blockcity <city>"
    added = add_to_location_blocklist(city)
    return f"Blocked city: '{city}'" if added else f"'{city}' was already blocked."


def handle_addrole(text: str, is_owner: bool) -> str:
    if not is_owner:
        return READONLY_DENIED
    from agent.tools.prefs_tool import add_to_roles
    keyword = text[len("/addrole"):].strip()
    if not keyword:
        return "Usage: /addrole <keyword>"
    added = add_to_roles(keyword)
    return (
        f"Added role: '{keyword.lower()}'" if added
        else f"'{keyword.lower()}' is already in your roles list."
    )
