"""Tests for agent/tools/prefs_tool.py."""

from __future__ import annotations

import json


def test_load_prefs_returns_dict(temp_prefs):
    from agent.tools.prefs_tool import load_prefs

    prefs = load_prefs()
    assert isinstance(prefs, dict)
    assert "roles" in prefs
    assert "blocklist" in prefs
    assert "location_blocklist" in prefs


def test_add_to_blocklist_adds_new_keyword(temp_prefs):
    from agent.tools.prefs_tool import add_to_blocklist, load_prefs

    added = add_to_blocklist("DevOps")
    assert added is True
    assert "devops" in load_prefs()["blocklist"]


def test_add_to_blocklist_normalises_to_lowercase(temp_prefs):
    from agent.tools.prefs_tool import add_to_blocklist, load_prefs

    add_to_blocklist("QA Engineer")
    assert "qa engineer" in load_prefs()["blocklist"]


def test_add_to_blocklist_returns_false_for_duplicate(temp_prefs):
    from agent.tools.prefs_tool import add_to_blocklist

    add_to_blocklist("sales")
    # "unpaid" is already in the fixture's blocklist
    added = add_to_blocklist("unpaid")
    assert added is False


def test_add_to_location_blocklist_adds_new_city(temp_prefs):
    from agent.tools.prefs_tool import add_to_location_blocklist, load_prefs

    added = add_to_location_blocklist("Haifa")
    assert added is True
    assert "Haifa" in load_prefs()["location_blocklist"]


def test_add_to_location_blocklist_is_case_insensitive_for_duplicate_check(temp_prefs):
    from agent.tools.prefs_tool import add_to_location_blocklist

    # "Jerusalem" is already in the fixture; adding "jerusalem" should be a no-op.
    added = add_to_location_blocklist("jerusalem")
    assert added is False


def test_add_to_roles_adds_new_keyword(temp_prefs):
    from agent.tools.prefs_tool import add_to_roles, load_prefs

    added = add_to_roles("DevOps")
    assert added is True
    assert "devops" in load_prefs()["roles"]


def test_add_to_roles_returns_false_for_duplicate(temp_prefs):
    from agent.tools.prefs_tool import add_to_roles

    # "backend" is already in the fixture's roles list.
    added = add_to_roles("backend")
    assert added is False


def test_prefs_persisted_across_load_calls(temp_prefs):
    """Verify that a mutation is visible in a subsequent load_prefs() call."""
    from agent.tools.prefs_tool import add_to_blocklist, load_prefs

    add_to_blocklist("consulting")
    prefs = load_prefs()
    assert "consulting" in prefs["blocklist"]
