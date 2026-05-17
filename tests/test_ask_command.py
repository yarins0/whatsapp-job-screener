"""Tests for the /ask command handler and the ask_tool extraction logic."""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest
from langchain_core.messages import AIMessage
from langchain_community.llms.fake import FakeListLLM


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_msg(text: str, chat_id: int = 42) -> dict:
    return {"text": text, "chat": {"id": chat_id}}


def _fake_llm(responses: list[str]):
    """Return a FakeListLLM that yields each response string in order.

    FakeListLLM (not FakeListChatModel) is used here because ask_tool
    uses JsonOutputParser which calls the LLM and reads .content —
    either model type works, but FakeListLLM is simpler for plain string responses.
    """
    return FakeListLLM(responses=responses)


# ---------------------------------------------------------------------------
# ask_tool unit tests
# ---------------------------------------------------------------------------

class TestExtractParams:
    """ask_tool._extract_params / _validate_params behaviour."""

    def test_extracts_role_keyword(self, temp_db):
        from agent.tools.ask_tool import ask_jobs

        params_json = json.dumps({"days": 7, "role": "python", "unseen_only": False, "limit": 10})
        llm = _fake_llm([params_json])

        # DB is empty — we just care that ask_jobs runs without error
        result = ask_jobs("python jobs this week", llm=llm)
        assert isinstance(result, str)
        # Should mention no jobs found since DB is empty
        assert "No jobs" in result or "found" in result

    def test_extracts_day_range(self, temp_db):
        from agent.tools.ask_tool import ask_jobs

        params_json = json.dumps({"days": 30, "role": None, "unseen_only": False, "limit": 10})
        llm = _fake_llm([params_json])

        result = ask_jobs("jobs from this month", llm=llm)
        # format_jobs_telegram uses "last 30 days" in the header
        assert "30" in result or "No jobs" in result

    def test_falls_back_on_parse_error(self, temp_db):
        from agent.tools.ask_tool import ask_jobs

        # LLM returns garbage — should not raise, should fall back gracefully
        llm = _fake_llm(["this is not json at all!!!"])

        result = ask_jobs("show me something", llm=llm)
        assert isinstance(result, str)
        # Fallback uses the raw question as the role keyword
        assert "No jobs" in result or "found" in result

    def test_validate_clamps_limit(self):
        from agent.tools.ask_tool import _validate_params

        result = _validate_params({"days": 7, "role": None, "unseen_only": False, "limit": 999})
        assert result["limit"] == 20  # clamped to max

    def test_validate_coerces_types(self):
        from agent.tools.ask_tool import _validate_params

        result = _validate_params({"days": "14", "role": "", "unseen_only": 0, "limit": "5"})
        assert result["days"] == 14
        assert result["role"] is None   # empty string → None
        assert result["unseen_only"] is False
        assert result["limit"] == 5


# ---------------------------------------------------------------------------
# /ask handler tests — bot integration
# ---------------------------------------------------------------------------

class TestAskHandler:
    """Tests for the /ask branch in _handle_message."""

    def test_shows_usage_when_no_question(self, temp_db):
        from telegram_bot import _handle_message

        with patch("telegram_bot._send") as mock_send:
            _handle_message(_make_msg("/ask"))

        reply = mock_send.call_args[0][1]
        assert "Usage" in reply

    def test_owner_gets_result_no_quota_note(self, temp_db, telegram_owner):
        from agent.tools.ask_tool import ask_jobs
        from telegram_bot import _handle_message

        params_json = json.dumps({"days": 7, "role": "python", "unseen_only": False, "limit": 10})
        fake = _fake_llm([params_json])

        with patch("telegram_bot._send") as mock_send, \
             patch("agent.tools.ask_tool._default_llm", return_value=fake):
            _handle_message(_make_msg("/ask python jobs"))

        reply = mock_send.call_args[0][1]
        # Owner should NOT see a quota/remaining note
        assert "remaining" not in reply

    def test_demo_user_sees_quota_note(self, temp_db):
        import telegram_handlers.jobs as jobs_handler
        from telegram_bot import _handle_message

        # Start from a clean count for chat_id=99
        jobs_handler._ask_counts.pop(99, None)

        params_json = json.dumps({"days": 7, "role": None, "unseen_only": False, "limit": 10})
        fake = _fake_llm([params_json])

        with patch("telegram_bot._send") as mock_send, \
             patch("agent.tools.ask_tool._default_llm", return_value=fake):
            _handle_message(_make_msg("/ask any jobs", chat_id=99))

        reply = mock_send.call_args[0][1]
        assert "remaining" in reply

    def test_demo_user_blocked_after_limit(self, temp_db):
        import telegram_handlers.jobs as jobs_handler
        from telegram_handlers.start import _ASK_DEMO_LIMIT
        from telegram_bot import _handle_message

        # Pre-fill the counter to the limit for chat_id=88
        jobs_handler._ask_counts[88] = _ASK_DEMO_LIMIT

        with patch("telegram_bot._send") as mock_send:
            _handle_message(_make_msg("/ask python", chat_id=88))

        reply = mock_send.call_args[0][1]
        assert "free" in reply.lower() or "queries" in reply.lower()
        # The LLM should never have been called — no ask_jobs patch needed
        mock_send.assert_called_once()

    def test_demo_user_count_increments(self, temp_db):
        import telegram_handlers.jobs as jobs_handler
        from telegram_bot import _handle_message

        jobs_handler._ask_counts.pop(77, None)

        params_json = json.dumps({"days": 7, "role": None, "unseen_only": False, "limit": 10})
        fake = _fake_llm([params_json])

        with patch("telegram_bot._send"), \
             patch("agent.tools.ask_tool._default_llm", return_value=fake):
            _handle_message(_make_msg("/ask jobs", chat_id=77))

        assert jobs_handler._ask_counts[77] == 1
