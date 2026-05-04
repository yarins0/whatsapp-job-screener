"""Browse stored jobs from the terminal.

Usage:
    python -m agent.list_jobs               # last 20 jobs (past 7 days)
    python -m agent.list_jobs --days 14     # past 2 weeks
    python -m agent.list_jobs --days 0      # all time
    python -m agent.list_jobs --role python # keyword filter (title + summary)
    python -m agent.list_jobs --unseen      # only jobs not yet in a digest
    python -m agent.list_jobs --limit 50    # increase page size
"""

from __future__ import annotations

import argparse
from datetime import datetime

from agent.tools.query_tool import query_jobs


# Column widths for the terminal table.
_COL_ID = 4
_COL_TITLE = 30
_COL_COMPANY = 18
_COL_LOCATION = 14
_COL_DATE = 10
_COL_GROUP = 20


def _truncate(text: str | None, width: int) -> str:
    """Right-pad or truncate text to exactly `width` characters."""
    s = text or "—"
    return (s[: width - 1] + "…") if len(s) > width else s.ljust(width)


def _format_date(job: dict) -> str:
    """Return a YYYY-MM-DD string from created_at, falling back to timestamp."""
    created = job.get("created_at") or ""
    if created:
        return created[:10]
    timestamp = job.get("timestamp")
    if timestamp:
        try:
            return datetime.utcfromtimestamp(int(timestamp)).strftime("%Y-%m-%d")
        except (ValueError, OSError):
            pass
    return "—"


def format_table(jobs: list[dict]) -> str:
    """Render jobs as a fixed-width terminal table."""
    if not jobs:
        return "No jobs found."

    header = (
        f"{'ID':<{_COL_ID}}  "
        f"{'Title':<{_COL_TITLE}}  "
        f"{'Company':<{_COL_COMPANY}}  "
        f"{'Location':<{_COL_LOCATION}}  "
        f"{'Date':<{_COL_DATE}}  "
        f"Group"
    )
    separator = "  ".join([
        "-" * _COL_ID,
        "-" * _COL_TITLE,
        "-" * _COL_COMPANY,
        "-" * _COL_LOCATION,
        "-" * _COL_DATE,
        "-" * _COL_GROUP,
    ])

    rows = [header, separator]
    for j in jobs:
        loc = "Remote" if j.get("remote") else (j.get("location") or "—")
        rows.append(
            f"{str(j.get('id', '')):<{_COL_ID}}  "
            f"{_truncate(j.get('title'), _COL_TITLE)}  "
            f"{_truncate(j.get('company'), _COL_COMPANY)}  "
            f"{_truncate(loc, _COL_LOCATION)}  "
            f"{_format_date(j):<{_COL_DATE}}  "
            f"{_truncate(j.get('group_name'), _COL_GROUP)}"
        )

    rows.append(f"\n{len(jobs)} job(s) shown.")
    return "\n".join(rows)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="python -m agent.list_jobs",
        description="Browse stored jobs from the terminal.",
    )
    parser.add_argument(
        "--days", type=int, default=7, metavar="N",
        help="Include jobs stored in the last N days. 0 = all time. Default: 7.",
    )
    parser.add_argument(
        "--role", type=str, default=None, metavar="KEYWORD",
        help="Filter by keyword matched against job title and summary.",
    )
    parser.add_argument(
        "--unseen", action="store_true",
        help="Show only jobs not yet included in a digest.",
    )
    parser.add_argument(
        "--limit", type=int, default=20, metavar="N",
        help="Maximum number of jobs to show. Default: 20.",
    )

    args = parser.parse_args(argv)
    jobs = query_jobs(
        days=args.days,
        role=args.role,
        unseen_only=args.unseen,
        limit=args.limit,
    )
    print(format_table(jobs))


if __name__ == "__main__":
    main()
