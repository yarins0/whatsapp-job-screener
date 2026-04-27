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
def temp_db(tmp_path, monkeypatch):
    """Point the DB modules at a fresh SQLite file inside ``tmp_path``."""
    db_file = tmp_path / "jobs.db"
    monkeypatch.setenv("JOBS_DB_PATH", str(db_file))

    from db.init_db import init_db

    init_db(db_file)
    return db_file


@pytest.fixture()
def sample_messages():
    import json

    path = Path(__file__).parent / "sample_messages.json"
    return json.loads(path.read_text(encoding="utf-8"))
