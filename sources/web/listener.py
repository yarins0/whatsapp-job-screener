"""Web source listener — periodically scrapes job boards and stores new listings.

Runs as a long-lived process (started by start.py). Uses APScheduler to poll
each enabled scraper on a configurable interval (default: 30 minutes).

Scrapers return structured job dicts directly (no LLM needed). The listener
applies dedup → filter → store → notify using the same pipeline tools that
the WhatsApp/Telegram paths use.

Config: agent/web_sources.json — controls which scrapers are active.
Keywords: loaded from agent/prefs.json (roles field) so there is one config
file for what you're looking for.

Env vars:
  WEB_SCRAPER_INTERVAL_MINUTES  — poll interval (default: 30)
"""

from __future__ import annotations

import logging
import os
import time
from pathlib import Path

from apscheduler.schedulers.blocking import BlockingScheduler
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)
for _lvl, _name in [(10, "debug"), (20, "info"), (30, "warning"), (40, "error"), (50, "critical")]:
    logging.addLevelName(_lvl, _name)
logging.basicConfig(level=logging.INFO, format="[%(asctime)s][%(levelname)s] %(message)s", datefmt="%H:%M:%S")
logging.getLogger("apscheduler").setLevel(logging.WARNING)

INTERVAL_MINUTES = int(os.environ.get("WEB_SCRAPER_INTERVAL_MINUTES", "30"))

_SOURCES_FILE = Path(__file__).parents[2] / "agent" / "web_sources.json"
_PREFS_FILE = Path(__file__).parents[2] / "agent" / "prefs.json"


# ---------------------------------------------------------------------------
# Config helpers
# ---------------------------------------------------------------------------

def _load_keywords() -> list[str]:
    """Read role keywords from agent/prefs.json so scraper searches stay in sync."""
    import json
    try:
        prefs = json.loads(_PREFS_FILE.read_text(encoding="utf-8"))
        return [k for k in (prefs.get("roles") or []) if k]
    except (FileNotFoundError, json.JSONDecodeError):
        return []


def _load_sources_config() -> dict:
    """Return the web_sources.json config dict."""
    import json
    try:
        return json.loads(_SOURCES_FILE.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _is_enabled(config: dict, scraper_key: str) -> bool:
    return config.get(scraper_key, {}).get("enabled", True)


# ---------------------------------------------------------------------------
# Job processing — dedup → filter → store → notify (no HTTP, no LLM)
# ---------------------------------------------------------------------------

def _process_job(job: dict, source_name: str) -> str:
    """Run one scraped job through the pipeline tools and return the outcome.

    Returns one of: "stored", "duplicate", "time-duplicate", or the filter
    rejection reason (e.g. "blocklist match: sales").
    """
    from agent.tools.dedup_tool import is_duplicate
    from agent.tools.filter_tool import filter_job
    from agent.tools.store_tool import store_job

    # Cross-poll dedup: title+company+contact hash stored in seen_hashes.
    if is_duplicate(job):
        logger.debug("Skipping duplicate: %s @ %s", job.get("title"), job.get("company"))
        return "duplicate"

    passed, reason = filter_job(job)
    if not passed:
        loc = "Remote" if job.get("remote") else (job.get("location") or "")
        loc_part = f" ({loc})" if loc else ""
        logger.info(
            "SKIPPED  | %s | %s @ %s%s",
            reason, job.get("title", "?"), job.get("company") or "", loc_part,
        )
        return reason

    enriched = {**job, "group": source_name, "timestamp": int(time.time())}
    job_id = store_job(enriched)
    if job_id is None:
        # store_job returns None for time-duplicates (same title+company within 7 days).
        logger.debug("Skipping time-duplicate: %s @ %s", job.get("title"), job.get("company"))
        return "time-duplicate"

    loc = "Remote" if job.get("remote") else (job.get("location") or "")
    loc_part = f" ({loc})" if loc else ""
    logger.info(
        "STORED   | %s @ %s%s | id=%s",
        job.get("title", "?"), job.get("company") or "?", loc_part, job_id,
    )
    _notify_job(job, job_id)
    return "stored"


def _notify_job(job: dict, job_id: int) -> None:
    """Send a Telegram notification with inline block buttons for a stored job."""
    try:
        from digest.digest import _send_telegram

        title = job.get("title") or "Untitled role"
        company = job.get("company") or "Unknown company"
        is_remote = bool(job.get("remote"))
        loc = "Remote" if is_remote else (job.get("location") or "Unknown")
        summary = job.get("summary") or ""
        contact = job.get("contact") or "see original posting"

        lines = [f"New job: *{title}* @ {company} ({loc})"]
        if summary:
            lines.append(summary[:200])
        lines.append(f"Contact: {contact}")

        role_kw = title.lower()[:40]
        buttons = [{"text": "Block role", "callback_data": f"block_role:{job_id}:{role_kw}"}]
        city = (job.get("location") or "").strip()
        if city and not is_remote:
            buttons.append({"text": f"Block city: {city[:15]}", "callback_data": f"block_city:{job_id}:{city[:30]}"})

        _send_telegram("\n".join(lines), reply_markup={"inline_keyboard": [buttons]})
    except Exception as exc:
        logger.warning("Telegram notification failed: %s", exc)


# ---------------------------------------------------------------------------
# Poll job
# ---------------------------------------------------------------------------

def poll() -> None:
    """Run all enabled scrapers and process new listings directly."""
    from sources.web.scrapers.alljobs import AllJobsScraper
    from sources.web.scrapers.indeed import IndeedScraper

    config = _load_sources_config()
    keywords = _load_keywords()

    if not keywords:
        logger.warning(
            "No role keywords found in agent/prefs.json — scrapers have nothing to search for."
        )
        return

    scrapers = [
        ("alljobs", AllJobsScraper()),
        ("indeed", IndeedScraper()),
    ]

    for key, scraper in scrapers:
        if not _is_enabled(config, key):
            logger.debug("%s scraper disabled in web_sources.json — skipping.", scraper.name)
            continue
        try:
            jobs = scraper.fetch(keywords)
            stored = sum(1 for job in jobs if _process_job(job, scraper.name) == "stored")
            logger.info("%s: %d new job(s) stored this poll.", scraper.name, stored)
        except Exception as exc:
            logger.warning("%s scraper failed: %s", scraper.name, exc)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def run() -> None:
    """Start the blocking scheduler. Runs poll() immediately then every N minutes."""
    logger.info("Web source listener started — polling every %d minute(s)", INTERVAL_MINUTES)
    poll()

    scheduler = BlockingScheduler()
    scheduler.add_job(poll, "interval", minutes=INTERVAL_MINUTES)
    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        pass


if __name__ == "__main__":
    run()
