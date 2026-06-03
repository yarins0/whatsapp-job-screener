"""End-to-end pipeline tests — mock the chain functions, test everything else live."""

from __future__ import annotations

import sqlite3
from unittest.mock import AsyncMock, patch

import pytest

from agent.pipeline import run_pipeline


def _seed_combined_mode(db_path, group: str) -> None:
    """Seed group_stats so get_pipeline_mode() returns 'combined' for this group."""
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            "INSERT INTO group_stats (group_name, total_messages, job_post_messages) VALUES (?,?,?)",
            (group, 100, 95),
        )
        conn.commit()
    finally:
        conn.close()


def _mock_classify(is_job_post: bool, confidence: float = 0.95):
    return AsyncMock(return_value={"is_job_post": is_job_post, "confidence": confidence})


def _mock_extract(jobs):
    jobs_list = jobs if isinstance(jobs, list) else [jobs]
    return AsyncMock(return_value=jobs_list)


def _mock_combined(is_job_post: bool, confidence: float, jobs):
    jobs_list = jobs if isinstance(jobs, list) else [jobs]
    return AsyncMock(return_value={
        "is_job_post": is_job_post,
        "confidence": confidence,
        "jobs": jobs_list,
    })


_JOB_BACKEND = {
    "title": "Backend Engineer",
    "company": "Acme",
    "location": "Tel Aviv",
    "remote": True,
    "skills": ["Python", "FastAPI"],
    "salary": "30-40k",
    "contact": "jobs@acme.io",
    "summary": "Backend role at Acme in Tel Aviv (hybrid).",
}

_JOB_FRONTEND = {
    "title": "Frontend Engineer",
    "company": "Acme",
    "location": "Tel Aviv",
    "remote": True,
    "skills": ["React"],
    "salary": None,
    "contact": "fe@acme.io",
    "summary": "Frontend role at Acme.",
}

_MSG = {"group": "Tech Jobs TLV", "sender": "demo", "text": "Hiring...", "timestamp": 1700000000}


@pytest.mark.asyncio
async def test_stores_a_qualified_job(temp_db):
    with patch("agent.graph.classify_message", _mock_classify(True, 0.95)), \
         patch("agent.graph.extract_job", _mock_extract(_JOB_BACKEND)):
        result = await run_pipeline(_MSG, notify=False)

    assert result["action"] == "stored"
    assert result["job"]["title"] == "Backend Engineer"

    conn = sqlite3.connect(temp_db)
    try:
        n = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
    finally:
        conn.close()
    assert n == 1


def _read_seen(db_path, job_id: int) -> int:
    conn = sqlite3.connect(db_path)
    try:
        return conn.execute("SELECT seen FROM jobs WHERE id = ?", (job_id,)).fetchone()[0]
    finally:
        conn.close()


@pytest.mark.asyncio
async def test_successful_alert_marks_job_seen(temp_db):
    """A delivered instant alert flags the job seen, so the digest won't re-send it."""
    with patch("agent.graph.classify_message", _mock_classify(True, 0.95)), \
         patch("agent.graph.extract_job", _mock_extract(_JOB_BACKEND)), \
         patch("digest.digest._send_telegram", return_value=True):
        result = await run_pipeline(_MSG)  # notify defaults to True

    assert _read_seen(temp_db, result["job_id"]) == 1


@pytest.mark.asyncio
async def test_unsent_alert_leaves_job_unseen(temp_db):
    """If Telegram isn't configured the alert returns False — the job stays unseen
    so the daily digest can still pick it up later."""
    with patch("agent.graph.classify_message", _mock_classify(True, 0.95)), \
         patch("agent.graph.extract_job", _mock_extract(_JOB_BACKEND)), \
         patch("digest.digest._send_telegram", return_value=False):
        result = await run_pipeline(_MSG)

    assert _read_seen(temp_db, result["job_id"]) == 0


@pytest.mark.asyncio
async def test_skips_non_job_post(temp_db):
    msg = {"group": "g", "sender": "s", "text": "anyone tried the new MacBook?", "timestamp": 0}
    with patch("agent.graph.classify_message", _mock_classify(False, 0.02)):
        result = await run_pipeline(msg, notify=False)

    assert result["action"] == "skipped"
    assert "not a job post" in result["reason"]


@pytest.mark.asyncio
async def test_skips_unwanted_role(temp_db):
    job = {
        "title": "Accountant", "company": "Acme", "location": "Tel Aviv",
        "remote": False, "skills": [], "salary": None,
        "contact": "jobs@acme.io", "summary": "Accounting role.",
    }
    msg = {"group": "g", "sender": "s", "text": "Accountant wanted", "timestamp": 0}

    with patch("agent.graph.classify_message", _mock_classify(True, 0.9)), \
         patch("agent.graph.extract_job", _mock_extract(job)):
        result = await run_pipeline(msg, notify=False)

    assert result["action"] == "skipped"
    assert result["reason"] == "no role keyword matched"


@pytest.mark.asyncio
async def test_skips_duplicate(temp_db):
    msg = {"group": "g", "sender": "s", "text": "Hiring backend engineer", "timestamp": 0}

    with patch("agent.graph.classify_message", _mock_classify(True, 0.9)), \
         patch("agent.graph.extract_job", _mock_extract(_JOB_BACKEND)):
        first = await run_pipeline(msg, notify=False)
    assert first["action"] == "stored"

    with patch("agent.graph.classify_message", _mock_classify(True, 0.9)), \
         patch("agent.graph.extract_job", _mock_extract(_JOB_BACKEND)):
        second = await run_pipeline(msg, notify=False)
    assert second["action"] == "skipped"
    assert second["reason"] == "duplicate"


@pytest.mark.asyncio
async def test_stores_multiple_jobs_from_one_message(temp_db):
    msg = {"group": "g", "sender": "s", "text": "Two openings at Acme...", "timestamp": 0}

    with patch("agent.graph.classify_message", _mock_classify(True, 0.97)), \
         patch("agent.graph.extract_job", _mock_extract([_JOB_BACKEND, _JOB_FRONTEND])):
        result = await run_pipeline(msg, notify=False)

    assert result["action"] == "stored"
    assert len(result["stored"]) == 2

    conn = sqlite3.connect(temp_db)
    try:
        n = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
    finally:
        conn.close()
    assert n == 2


@pytest.mark.asyncio
async def test_partial_store_when_one_of_two_jobs_is_duplicate(temp_db):
    msg = {"group": "g", "sender": "s", "text": "Two openings...", "timestamp": 0}

    with patch("agent.graph.classify_message", _mock_classify(True, 0.95)), \
         patch("agent.graph.extract_job", _mock_extract(_JOB_BACKEND)):
        await run_pipeline(msg, notify=False)

    with patch("agent.graph.classify_message", _mock_classify(True, 0.95)), \
         patch("agent.graph.extract_job", _mock_extract([_JOB_BACKEND, _JOB_FRONTEND])):
        result = await run_pipeline(msg, notify=False)

    assert result["action"] == "partial"
    assert len(result["stored"]) == 1
    assert result["stored"][0]["title"] == "Frontend Engineer"
    assert len(result["skipped"]) == 1
    assert result["skipped"][0]["reason"] == "duplicate"


@pytest.mark.asyncio
async def test_pipeline_uses_combined_mode_for_high_density_group(temp_db):
    _seed_combined_mode(temp_db, "jobs@g.us")
    msg = {"group": "jobs@g.us", "sender": "s", "text": "Hiring backend at Acme...", "timestamp": 0}

    with patch("agent.graph.classify_and_extract", _mock_combined(True, 0.96, _JOB_BACKEND)):
        result = await run_pipeline(msg, notify=False)

    assert result["action"] == "stored"
    assert result["job"]["title"] == "Backend Engineer"


@pytest.mark.asyncio
async def test_pipeline_records_stats_for_job_post(temp_db):
    msg = {"group": "stats-group@g.us", "sender": "s", "text": "Hiring...", "timestamp": 0}

    with patch("agent.graph.classify_message", _mock_classify(True, 0.95)), \
         patch("agent.graph.extract_job", _mock_extract(_JOB_BACKEND)):
        await run_pipeline(msg, notify=False)

    conn = sqlite3.connect(temp_db)
    try:
        row = conn.execute(
            "SELECT total_messages, job_post_messages FROM group_stats WHERE group_name=?",
            ("stats-group@g.us",),
        ).fetchone()
    finally:
        conn.close()
    assert row == (1, 1)


@pytest.mark.asyncio
async def test_pipeline_records_stats_for_non_job_post(temp_db):
    msg = {"group": "stats-group@g.us", "sender": "s", "text": "Morning!", "timestamp": 0}

    with patch("agent.graph.classify_message", _mock_classify(False, 0.02)):
        await run_pipeline(msg, notify=False)

    conn = sqlite3.connect(temp_db)
    try:
        row = conn.execute(
            "SELECT total_messages, job_post_messages FROM group_stats WHERE group_name=?",
            ("stats-group@g.us",),
        ).fetchone()
    finally:
        conn.close()
    assert row == (1, 0)


def test_truncate_bytes_respects_byte_budget_for_multibyte_text():
    """Multi-byte (Hebrew) text must be cut on a byte budget, not a char count."""
    from agent.graph import _truncate_bytes

    hebrew = "מרכז" * 10  # each character is 2 bytes in UTF-8
    out = _truncate_bytes(hebrew, 10)
    assert len(out.encode("utf-8")) <= 10
    assert out.encode("utf-8").decode("utf-8") == out  # never splits a character
    assert _truncate_bytes("abc", 64) == "abc"  # short text returned unchanged


def test_notify_job_callback_data_within_telegram_byte_limit():
    """Hebrew titles/cities must not overflow Telegram's 64-byte callback_data limit.

    Regression: slicing the keyword by character count produced 85-byte payloads
    for Hebrew titles, which Telegram rejects with 400 BUTTON_DATA_INVALID.
    """
    from agent.graph import _notify_job

    captured: dict = {}

    def fake_send(text, reply_markup=None):
        captured["reply_markup"] = reply_markup
        return True

    job = {
        "title": "מהנדס ביצוע ג'וניור לתשתיות רכבת במרכז",
        "company": "SVT.jobs",
        "location": "מרכז",
    }
    with patch("digest.digest._send_telegram", side_effect=fake_send):
        _notify_job(job, 625)

    assert "reply_markup" in captured, "_send_telegram was not called"
    buttons = captured["reply_markup"]["inline_keyboard"][0]
    assert buttons, "expected at least the Block role button"
    for button in buttons:
        assert len(button["callback_data"].encode("utf-8")) <= 64
