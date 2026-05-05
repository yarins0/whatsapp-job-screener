"""Pytest config — make the project root importable and isolate the DB per test."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


@pytest.fixture()
def temp_chroma(tmp_path, monkeypatch):
    """Point CHROMA_DB_PATH at a fresh temp directory for isolation."""
    chroma_dir = tmp_path / "chroma"
    monkeypatch.setenv("CHROMA_DB_PATH", str(chroma_dir))
    return chroma_dir


@pytest.fixture()
def temp_db(tmp_path, monkeypatch):
    """Point the DB modules at a fresh SQLite file and a fresh Chroma dir.

    Both JOBS_DB_PATH and CHROMA_DB_PATH are overridden so tests that call
    store_job() never accidentally read from or write to the real databases.
    """
    db_file = tmp_path / "jobs.db"
    monkeypatch.setenv("JOBS_DB_PATH", str(db_file))
    monkeypatch.setenv("CHROMA_DB_PATH", str(tmp_path / "chroma"))

    from db.init_db import init_db

    init_db(db_file)
    return db_file


@pytest.fixture()
def temp_groups(tmp_path, monkeypatch):
    """Write an empty groups.json map to a temp file and point GROUPS_PATH at it."""
    import json

    groups_file = tmp_path / "groups.json"
    groups_file.write_text(json.dumps({}), encoding="utf-8")
    monkeypatch.setenv("GROUPS_PATH", str(groups_file))
    return groups_file


@pytest.fixture()
def temp_prefs(tmp_path, monkeypatch):
    """Write a minimal prefs.json to a temp file and point PREFS_PATH at it."""
    import json

    prefs = {
        "roles": ["backend", "software engineer", "python", "junior"],
        "blocklist": ["unpaid", "senior"],
        "location_blocklist": ["Jerusalem"],
        "min_salary": None,
    }
    prefs_file = tmp_path / "prefs.json"
    prefs_file.write_text(json.dumps(prefs), encoding="utf-8")
    monkeypatch.setenv("PREFS_PATH", str(prefs_file))
    return prefs_file


@pytest.fixture()
def telegram_owner(monkeypatch):
    """Set TELEGRAM_CHAT_ID=42 so write commands pass the owner check.

    Matches the default chat_id used by _make_msg in test_telegram_commands.py.
    _is_owner reads the env var on every call, so monkeypatch.setenv is enough.
    """
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "42")


@pytest.fixture()
def sample_messages():
    import json

    path = Path(__file__).parent / "sample_messages.json"
    return json.loads(path.read_text(encoding="utf-8"))
