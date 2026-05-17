"""Query tool — fetch stored jobs from SQLite with optional filters.

Shared by the browse-jobs CLI (agent/list_jobs.py) and the Telegram /jobs command.
"""

from __future__ import annotations

import json
import os
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

DEFAULT_DB_PATH = Path(__file__).resolve().parents[2] / "db" / "jobs.db"

# Telegram messages are capped at 4096 characters; leave headroom for the header.
_TELEGRAM_MAX_CHARS = 3800


def _db_path() -> Path:
    return Path(os.environ.get("JOBS_DB_PATH", DEFAULT_DB_PATH))


def query_jobs(
    *,
    days: int = 7,
    role: str | None = None,
    unseen_only: bool = False,
    limit: int = 20,
) -> list[dict]:
    """Return stored jobs matching the given filters, newest first.

    Args:
        days: include jobs stored within the last N days. 0 means all time.
        role: keyword matched case-insensitively against title and summary.
        unseen_only: when True, only return rows where seen = 0.
        limit: maximum number of rows returned.
    """
    conditions: list[str] = []
    params: list = []

    if days > 0:
        cutoff = datetime.now(UTC) - timedelta(days=days)
        conditions.append("created_at >= ?")
        params.append(cutoff.strftime("%Y-%m-%d %H:%M:%S"))

    if role:
        pattern = f"%{role.lower()}%"
        conditions.append("(LOWER(title) LIKE ? OR LOWER(summary) LIKE ?)")
        params.extend([pattern, pattern])

    if unseen_only:
        conditions.append("seen = 0")

    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
    params.append(limit)

    sql = f"""
        SELECT id, title, company, location, remote, skills,
               salary, contact, summary, group_name, timestamp, seen, created_at
        FROM jobs
        {where}
        ORDER BY created_at DESC
        LIMIT ?
    """

    db = _db_path()
    if not db.exists():
        return []

    conn = sqlite3.connect(db)
    try:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def query_jobs_recent(hours: int) -> list[dict]:
    """Return jobs stored within the last N hours, newest first. No row limit."""
    db = _db_path()
    if not db.exists():
        return []

    cutoff = datetime.utcnow() - timedelta(hours=hours)
    sql = """
        SELECT id, title, company, location, remote, skills,
               salary, contact, summary, group_name, timestamp, seen, created_at
        FROM jobs
        WHERE created_at >= ?
        ORDER BY created_at DESC
    """
    conn = sqlite3.connect(db)
    try:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(sql, [cutoff.strftime("%Y-%m-%d %H:%M:%S")]).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def _format_job_block(job: dict) -> str:
    """Render one job as a Telegram Markdown block (used by digest and /jobs command)."""
    loc = "Remote" if job.get("remote") else (job.get("location") or "Unknown")
    company = job.get("company") or "Unknown company"
    title = job.get("title") or "Untitled role"
    summary = job.get("summary") or ""
    group = job.get("group_name") or job.get("group") or ""
    contact = job.get("contact") or (
        f"see original message in {group}" if group else "see original message"
    )
    try:
        skills = json.loads(job.get("skills") or "[]")
    except (json.JSONDecodeError, TypeError):
        skills = []

    lines = [f"*{title}* @ {company} ({loc})"]
    if summary:
        lines.append(f"  {summary}")
    if skills:
        lines.append(f"  Skills: {', '.join(skills[:6])}")
    lines.append(f"  Contact: {contact}")
    return "\n".join(lines)


def format_jobs_telegram(
    jobs: list[dict],
    *,
    days: int = 7,
    role: str | None = None,
    unseen_only: bool = False,
) -> str:
    """Render jobs as a Telegram-Markdown message in the same style as the daily digest.

    Returns a ready-to-send string. If the result would exceed Telegram's 4096-char
    limit, earlier jobs are dropped and a truncation note is appended.
    """
    period = f"last {days} days" if days > 0 else "all time"
    filters = []
    if role:
        filters.append(f'"{role}"')
    if unseen_only:
        filters.append("unseen only")
    filter_suffix = f" — {', '.join(filters)}" if filters else ""

    if not jobs:
        return f"No jobs found ({period}{filter_suffix})."

    header = f"📋 *Jobs — {period}* ({len(jobs)} found{filter_suffix})"

    blocks = [_format_job_block(j) for j in jobs]

    # Assemble, then trim from the end if over the character limit.
    included: list[str] = []
    budget = _TELEGRAM_MAX_CHARS - len(header) - 2  # 2 for the blank line after header
    for block in blocks:
        if len(block) + 1 > budget:  # +1 for the blank-line separator
            break
        included.append(block)
        budget -= len(block) + 1

    truncated = len(included) < len(blocks)
    body = "\n\n".join(included)
    text = f"{header}\n\n{body}"
    if truncated:
        text += f"\n\n_{len(blocks) - len(included)} more result(s) not shown — use --limit to increase._"
    return text
