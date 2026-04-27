"""Daily digest — pulls unseen jobs from SQLite, formats them, sends via Telegram.

Run as a long-lived process:

    python digest/digest.py

Or fire a single send (e.g. from cron / on-demand) with:

    python -c "from digest.digest import send_digest; send_digest()"
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
from pathlib import Path
from typing import Iterable

import requests
from apscheduler.schedulers.blocking import BlockingScheduler
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s — %(message)s")

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
        loc = "Remote" if j.get("remote") else (j.get("location") or "Unknown")
        company = j.get("company") or "Unknown company"
        title = j.get("title") or "Untitled role"
        summary = j.get("summary") or ""
        contact = j.get("contact") or "see original message"
        try:
            skills = json.loads(j.get("skills") or "[]")
        except (json.JSONDecodeError, TypeError):
            skills = []

        lines.append(f"*{title}* @ {company} ({loc})")
        if summary:
            lines.append(f"  {summary}")
        if skills:
            lines.append(f"  Skills: {', '.join(skills[:6])}")
        lines.append(f"  Contact: {contact}")
        lines.append("")

    return "\n".join(lines).rstrip()


def _send_telegram(text: str) -> bool:
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        logger.warning("Telegram not configured; printing digest to stdout instead.")
        print(text)
        return False

    resp = requests.post(
        f"https://api.telegram.org/bot{token}/sendMessage",
        json={"chat_id": chat_id, "text": text, "parse_mode": "Markdown"},
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
