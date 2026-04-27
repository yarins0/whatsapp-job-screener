"""Tests for the filter tool — pure Python, no LLM needed."""

from __future__ import annotations

from agent.tools.filter_tool import filter_job


def _job(**overrides) -> dict:
    base = {
        "title": "Backend engineer",
        "company": "Acme",
        "location": "Tel Aviv",
        "remote": False,
        "skills": ["Python"],
        "salary": None,
        "contact": "jobs@acme.io",
        "summary": "Backend role at Acme.",
    }
    base.update(overrides)
    return base


def test_keeps_matching_role_and_location():
    assert filter_job(_job()) is True


def test_keeps_remote_even_if_location_unknown():
    assert filter_job(_job(location=None, remote=True)) is True


def test_rejects_blocklisted_keyword():
    assert filter_job(_job(title="Backend Internship", summary="unpaid internship")) is False


def test_rejects_when_no_role_keyword_matches():
    assert filter_job(_job(title="Marketing manager", summary="Marketing role.", skills=[])) is False


def test_rejects_blocked_city():
    # Jerusalem is in the location_blocklist — should be filtered out
    assert filter_job(_job(location="Jerusalem", remote=False)) is False


def test_keeps_unknown_city():
    # Berlin is not in the location_blocklist — accepted by default
    assert filter_job(_job(location="Berlin", remote=False)) is True


def test_remote_overrides_blocked_city():
    # Remote jobs pass even if the listed location would otherwise be blocked
    assert filter_job(_job(location="Jerusalem", remote=True)) is True


def test_custom_prefs_override_defaults():
    prefs = {
        "roles": ["data"],
        "blocklist": [],
        "location_blocklist": ["berlin"],
        "min_salary": None,
    }
    assert filter_job(_job(title="Data engineer", location="Tel Aviv"), prefs) is True
    assert filter_job(_job(title="Data engineer", location="Berlin"), prefs) is False
    assert filter_job(_job(title="Backend engineer", location="Tel Aviv"), prefs) is False
