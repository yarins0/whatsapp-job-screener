"""Web source listener — periodically scrapes job boards and forwards new listings.

Runs as a long-lived process (started by start.py). Uses APScheduler to poll
each enabled scraper on a configurable interval (default: 30 minutes).

Job posts are forwarded to the FastAPI ingest endpoint in the same format as
the WhatsApp and Telegram listeners. The pipeline's dedup layer handles any
listings that were already seen in a previous poll.

Config: agent/web_sources.json — controls which scrapers are active.
Keywords: loaded from agent/prefs.json (roles field) so there is one config
file for what you're looking for.

Env vars:
  WEB_SCRAPER_INTERVAL_MINUTES  — poll interval (default: 30)
  INGEST_API_URL                — ingest endpoint (default: http://localhost:8000/ingest)
"""

from __future__ import annotations

import logging
import os
import time
from pathlib import Path

import requests
from apscheduler.schedulers.blocking import BlockingScheduler
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s — %(message)s")
# APScheduler logs "Running job" and "Job executed successfully" on every poll tick — not useful.
logging.getLogger("apscheduler").setLevel(logging.WARNING)

API_URL = os.environ.get("INGEST_API_URL", "http://localhost:8000/ingest")
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
# Ingest forwarding
# ---------------------------------------------------------------------------

def _forward(text: str, source_name: str) -> None:
    """POST a scraped job listing to the FastAPI ingest endpoint.

    Checks the raw-text hash against seen_hashes before POSTing so that
    listings seen in a previous poll cycle never reach the LLM again.
    """
    from agent.tools.dedup_tool import is_raw_duplicate

    if is_raw_duplicate(text):
        logger.debug("Skipping already-seen listing from %s (raw dedup).", source_name)
        return

    try:
        requests.post(
            API_URL,
            json={
                "group": source_name,
                "sender": "web-scraper",
                "text": text,
                "timestamp": int(time.time()),
            },
            timeout=15,
        )
    except Exception as exc:
        logger.warning("Failed to forward listing from %s: %s", source_name, exc)


# ---------------------------------------------------------------------------
# Poll job
# ---------------------------------------------------------------------------

def poll() -> None:
    """Run all enabled scrapers and forward new listings to the pipeline."""
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
            listings = scraper.fetch(keywords)
            logger.info("%s: forwarding %d listing(s) to pipeline.", scraper.name, len(listings))
            for text in listings:
                _forward(text, scraper.name)
        except Exception as exc:
            logger.warning("%s scraper failed: %s", scraper.name, exc)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def run() -> None:
    """Start the blocking scheduler. Runs poll() immediately then every N minutes."""
    logger.info(
        "Web source listener started — polling every %d minute(s). "
        "Sources: AllJobs (enabled), Indeed (check web_sources.json).",
        INTERVAL_MINUTES,
    )
    # Run once immediately so new listings appear without waiting a full interval.
    poll()

    scheduler = BlockingScheduler()
    scheduler.add_job(poll, "interval", minutes=INTERVAL_MINUTES)
    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        pass


if __name__ == "__main__":
    run()
