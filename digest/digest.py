"""Daily digest — pulls unseen jobs from SQLite, formats them, sends via Telegram.

Run as a long-lived process:

    python digest/digest.py

Or fire a single send (e.g. from cron / on-demand) with:

    python -c "from digest.digest import send_digest; send_digest()"
"""

from __future__ import annotations

import logging
import os
import sqlite3
from pathlib import Path
from typing import Iterable

import requests
from apscheduler.schedulers.blocking import BlockingScheduler
from dotenv import load_dotenv

from agent.tools.query_tool import _format_job_block

load_dotenv()
logger = logging.getLogger(__name__)
for _lvl, _name in [(10, "debug"), (20, "info"), (30, "warning"), (40, "error"), (50, "critical")]:
    logging.addLevelName(_lvl, _name)
logging.basicConfig(level=logging.INFO, format="[%(asctime)s][%(levelname)s] %(message)s", datefmt="%H:%M:%S")
logging.getLogger("apscheduler").setLevel(logging.WARNING)

DEFAULT_DB_PATH = Path(__file__).resolve().parents[1] / "db" / "jobs.db"


def _db_path() -> Path:
    return Path(os.environ.get("JOBS_DB_PATH", DEFAULT_DB_PATH))


def _fetch_unseen() -> list[dict]:
    conn = sqlite3.connect(_db_path())
    try:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT id, title, company, location, remote, skills,
                   salary, contact, summary, group_name, timestamp
            FROM jobs WHERE seen = 0
            ORDER BY timestamp ASC
            """
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def _mark_seen(ids: Iterable[int]) -> None:
    ids = list(ids)
    if not ids:
        return
    conn = sqlite3.connect(_db_path())
    try:
        conn.executemany("UPDATE jobs SET seen = 1 WHERE id = ?", [(i,) for i in ids])
        conn.commit()
    finally:
        conn.close()


def format_digest(jobs: list[dict]) -> str:
    """Render jobs as a Telegram-Markdown digest. Pure function so it's easy to test."""
    if not jobs:
        return ""

    lines = [f"🗂 *Daily Job Digest* — {len(jobs)} new"]
    lines.append("")

    for j in jobs:
        lines.append(_format_job_block(j))
        lines.append("")

    return "\n".join(lines).rstrip()


def _send_telegram(text: str, reply_markup: dict | None = None) -> bool:
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        logger.warning("Telegram not configured; printing digest to stdout instead.")
        print(text)
        return False

    payload: dict = {"chat_id": chat_id, "text": text, "parse_mode": "Markdown"}
    if reply_markup:
        payload["reply_markup"] = reply_markup

    resp = requests.post(
        f"https://api.telegram.org/bot{token}/sendMessage",
        json=payload,
        timeout=15,
    )
    resp.raise_for_status()
    return True


def send_digest() -> dict:
    """Fetch + format + send the next digest. Returns a small status dict."""
    jobs = _fetch_unseen()
    if not jobs:
        logger.info("No unseen jobs; skipping digest.")
        return {"sent": False, "count": 0}

    text = format_digest(jobs)
    _send_telegram(text)
    _mark_seen([j["id"] for j in jobs])
    logger.info("Digest sent for %d jobs.", len(jobs))
    return {"sent": True, "count": len(jobs)}


if __name__ == "__main__":  # pragma: no cover
    scheduler = BlockingScheduler()
    scheduler.add_job(send_digest, "cron", hour=8, minute=0)
    print("Digest scheduler running. Daily at 08:00 local time. Ctrl-C to stop.")
    scheduler.start()
