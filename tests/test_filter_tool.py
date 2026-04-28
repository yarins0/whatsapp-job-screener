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
    passed, reason = filter_job(_job())
    assert passed is True
    assert reason == ""


def test_keeps_remote_even_if_location_unknown():
    passed, _ = filter_job(_job(location=None, remote=True))
    assert passed is True


def test_rejects_blocklisted_keyword():
    passed, reason = filter_job(_job(title="Backend Internship", summary="unpaid internship"))
    assert passed is False
    assert "unpaid" in reason


def test_rejects_when_no_role_keyword_matches():
    passed, reason = filter_job(_job(title="Marketing manager", summary="Marketing role.", skills=[]))
    assert passed is False
    assert "role keyword" in reason


def test_rejects_blocked_city():
    passed, reason = filter_job(_job(location="Jerusalem", remote=False))
    assert passed is False
    assert "Jerusalem" in reason


def test_keeps_unknown_city():
    passed, _ = filter_job(_job(location="Berlin", remote=False))
    assert passed is True


def test_remote_overrides_blocked_city():
    passed, _ = filter_job(_job(location="Jerusalem", remote=True))
    assert passed is True


def test_custom_prefs_override_defaults():
    prefs = {
        "roles": ["data"],
        "blocklist": [],
        "location_blocklist": ["berlin"],
        "min_salary": None,
    }
    passed, _ = filter_job(_job(title="Data engineer", location="Tel Aviv"), prefs)
    assert passed is True
    passed, reason = filter_job(_job(title="Data engineer", location="Berlin"), prefs)
    assert passed is False
    assert "berlin" in reason.lower()
    passed, _ = filter_job(_job(title="Backend engineer", location="Tel Aviv"), prefs)
    assert passed is False
