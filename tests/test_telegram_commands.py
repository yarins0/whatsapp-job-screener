"""Tests for the Telegram bot command handlers in telegram_bot.py."""

from __future__ import annotations

from unittest.mock import MagicMock, patch


def _make_msg(text: str, chat_id: int = 42) -> dict:
    return {"text": text, "chat": {"id": chat_id}}


# ---------------------------------------------------------------------------
# /blockrole
# ---------------------------------------------------------------------------

def test_blockrole_adds_keyword(temp_prefs, telegram_owner):
    from agent.tools.prefs_tool import load_prefs
    from telegram_bot import _handle_message

    with patch("telegram_bot._send"):
        _handle_message(_make_msg("/blockrole DevOps"))

    assert "devops" in load_prefs()["blocklist"]


def test_blockrole_sends_confirmation(temp_prefs, telegram_owner):
    from telegram_bot import _handle_message

    with patch("telegram_bot._send") as mock_send:
        _handle_message(_make_msg("/blockrole DevOps"))

    mock_send.assert_called_once()
    assert "devops" in mock_send.call_args[0][1].lower()


def test_blockrole_shows_usage_when_no_keyword(temp_prefs, telegram_owner):
    from telegram_bot import _handle_message

    with patch("telegram_bot._send") as mock_send:
        _handle_message(_make_msg("/blockrole"))

    assert "Usage" in mock_send.call_args[0][1]


def test_blockrole_reports_duplicate(temp_prefs, telegram_owner):
    from telegram_bot import _handle_message

    # "unpaid" is already in the fixture blocklist
    with patch("telegram_bot._send") as mock_send:
        _handle_message(_make_msg("/blockrole unpaid"))

    assert "already" in mock_send.call_args[0][1].lower()


# ---------------------------------------------------------------------------
# /blockcity
# ---------------------------------------------------------------------------

def test_blockcity_adds_city(temp_prefs, telegram_owner):
    from agent.tools.prefs_tool import load_prefs
    from telegram_bot import _handle_message

    with patch("telegram_bot._send"):
        _handle_message(_make_msg("/blockcity Beer Sheva"))

    assert "Beer Sheva" in load_prefs()["location_blocklist"]


def test_blockcity_sends_confirmation(temp_prefs, telegram_owner):
    from telegram_bot import _handle_message

    with patch("telegram_bot._send") as mock_send:
        _handle_message(_make_msg("/blockcity Netanya"))

    assert "netanya" in mock_send.call_args[0][1].lower()


def test_blockcity_shows_usage_when_no_city(temp_prefs, telegram_owner):
    from telegram_bot import _handle_message

    with patch("telegram_bot._send") as mock_send:
        _handle_message(_make_msg("/blockcity"))

    assert "Usage" in mock_send.call_args[0][1]


# ---------------------------------------------------------------------------
# /addrole
# ---------------------------------------------------------------------------

def test_addrole_adds_keyword(temp_prefs, telegram_owner):
    from agent.tools.prefs_tool import load_prefs
    from telegram_bot import _handle_message

    with patch("telegram_bot._send"):
        _handle_message(_make_msg("/addrole data engineer"))

    assert "data engineer" in load_prefs()["roles"]


def test_addrole_sends_confirmation(temp_prefs, telegram_owner):
    from telegram_bot import _handle_message

    with patch("telegram_bot._send") as mock_send:
        _handle_message(_make_msg("/addrole devops"))

    assert "devops" in mock_send.call_args[0][1].lower()


def test_addrole_shows_usage_when_no_keyword(temp_prefs, telegram_owner):
    from telegram_bot import _handle_message

    with patch("telegram_bot._send") as mock_send:
        _handle_message(_make_msg("/addrole"))

    assert "Usage" in mock_send.call_args[0][1]


def test_addrole_reports_duplicate(temp_prefs, telegram_owner):
    from telegram_bot import _handle_message

    # "backend" is already in the fixture roles list
    with patch("telegram_bot._send") as mock_send:
        _handle_message(_make_msg("/addrole backend"))

    assert "already" in mock_send.call_args[0][1].lower()


# ---------------------------------------------------------------------------
# /help
# ---------------------------------------------------------------------------

def test_help_mentions_commands(temp_prefs):
    from telegram_bot import _handle_message

    with patch("telegram_bot._send") as mock_send:
        _handle_message(_make_msg("/help"))

    reply = mock_send.call_args[0][1]
    assert "/commands" in reply


def test_help_mentions_buttons(temp_prefs):
    from telegram_bot import _handle_message

    with patch("telegram_bot._send") as mock_send:
        _handle_message(_make_msg("/help"))

    reply = mock_send.call_args[0][1]
    assert "Block role" in reply
    assert "Block city" in reply


# ---------------------------------------------------------------------------
# /start and /commands
# ---------------------------------------------------------------------------

def test_start_shows_command_list(temp_prefs, temp_demo_users):
    from telegram_bot import _handle_message

    with patch("telegram_bot._send") as mock_send:
        _handle_message(_make_msg("/start"))

    # /start sends multiple messages for demo users (command list + recent jobs).
    # Check the first message contains the demo command list.
    first_reply = mock_send.call_args_list[0][0][1]
    assert "/jobs" in first_reply
    assert "/ask" in first_reply
    assert "/prefs" in first_reply


def test_commands_shows_same_as_start(temp_prefs, temp_demo_users):
    from telegram_bot import _handle_message

    with patch("telegram_bot._send") as mock_send:
        _handle_message(_make_msg("/start"))
        # /start sends command list first, then recent jobs — compare first call only.
        start_reply = mock_send.call_args_list[0][0][1]

    with patch("telegram_bot._send") as mock_send:
        _handle_message(_make_msg("/commands"))
        commands_reply = mock_send.call_args[0][1]

    assert start_reply == commands_reply


# ---------------------------------------------------------------------------
# /prefs
# ---------------------------------------------------------------------------

def test_prefs_command_shows_all_sections(temp_prefs):
    from telegram_bot import _handle_message

    with patch("telegram_bot._send") as mock_send:
        _handle_message(_make_msg("/prefs"))

    reply = mock_send.call_args[0][1]
    assert "Roles:" in reply
    assert "Blocklist:" in reply
    assert "Blocked cities:" in reply
