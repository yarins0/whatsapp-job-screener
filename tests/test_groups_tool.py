"""Tests for agent/tools/groups_tool.py."""

from __future__ import annotations

GROUP_A = "120363111111111111@g.us"
GROUP_B = "120363222222222222@g.us"


def test_load_groups_returns_empty_list(temp_groups):
    from agent.tools.groups_tool import load_groups

    assert load_groups() == []


def test_load_groups_with_names_returns_empty_dict(temp_groups):
    from agent.tools.groups_tool import load_groups_with_names

    assert load_groups_with_names() == {}


def test_add_group_adds_id(temp_groups):
    from agent.tools.groups_tool import add_group, load_groups

    added = add_group(GROUP_A)
    assert added is True
    assert GROUP_A in load_groups()


def test_add_group_stores_empty_name(temp_groups):
    from agent.tools.groups_tool import add_group, load_groups_with_names

    add_group(GROUP_A)
    assert load_groups_with_names()[GROUP_A] == ""


def test_add_group_returns_false_for_duplicate(temp_groups):
    from agent.tools.groups_tool import add_group

    add_group(GROUP_A)
    assert add_group(GROUP_A) is False


def test_add_multiple_groups(temp_groups):
    from agent.tools.groups_tool import add_group, load_groups

    add_group(GROUP_A)
    add_group(GROUP_B)
    groups = load_groups()
    assert GROUP_A in groups
    assert GROUP_B in groups


def test_remove_group_removes_id(temp_groups):
    from agent.tools.groups_tool import add_group, load_groups, remove_group

    add_group(GROUP_A)
    removed = remove_group(GROUP_A)
    assert removed is True
    assert GROUP_A not in load_groups()


def test_remove_group_returns_false_when_not_present(temp_groups):
    from agent.tools.groups_tool import remove_group

    assert remove_group(GROUP_A) is False


def test_remove_group_leaves_other_groups_intact(temp_groups):
    from agent.tools.groups_tool import add_group, load_groups, remove_group

    add_group(GROUP_A)
    add_group(GROUP_B)
    remove_group(GROUP_A)
    assert GROUP_B in load_groups()
    assert GROUP_A not in load_groups()


def test_add_group_strips_whitespace(temp_groups):
    from agent.tools.groups_tool import add_group, load_groups

    add_group(f"  {GROUP_A}  ")
    assert GROUP_A in load_groups()
