"""Indeed Israel scraper — fetches job listings via Indeed's RSS feed.

Indeed publishes an official RSS feed for job searches. It yields structured
data (title, company, description) directly — no LLM extraction needed.

Feed URL pattern:
  https://il.indeed.com/rss?q={keyword}&l=Israel&sort=date&fromage=3

Run standalone to inspect what the scraper returns:
    python -m sources.web.scrapers.indeed
"""

from __future__ import annotations

import logging
import random
import re
import time
import xml.etree.ElementTree as ET

from sources.web.scrapers.base import JobScraper
from sources.web.scrapers._utils import fetch_with_retry, random_headers

logger = logging.getLogger(__name__)

_RSS_URL = "https://il.indeed.com/rss?q={keyword}&l=Israel&sort=date&fromage=3"

_DELAY_BETWEEN_KEYWORDS_S = (3.0, 7.0)


class IndeedScraper(JobScraper):
    name = "Indeed"

    def fetch(self, keywords: list[str]) -> list[dict]:
        """Fetch job listings from the Indeed Israel RSS feed for each keyword."""
        # Dedup key: (title, company) — same posting may appear under multiple keywords.
        seen: set[tuple[str, str]] = set()
        results: list[dict] = []

        for i, keyword in enumerate(keywords):
            if i > 0:
                time.sleep(random.uniform(*_DELAY_BETWEEN_KEYWORDS_S))

            url = _RSS_URL.format(keyword=keyword.replace(" ", "+"))
            try:
                resp = fetch_with_retry(url, headers=random_headers())
                if resp.status_code == 403:
                    logger.warning("Indeed blocked request (403) for keyword '%s' — skipping.", keyword)
                    continue
                resp.raise_for_status()
                jobs = _parse_rss(resp.text)
                new = 0
                for job in jobs:
                    key = (job["title"].lower(), (job["company"] or "").lower())
                    if key not in seen:
                        seen.add(key)
                        results.append(job)
                        new += 1
                logger.info("Indeed '%s': %d listing(s) found, %d new", keyword, len(jobs), new)
            except Exception as exc:
                logger.warning("Indeed fetch failed for keyword '%s': %s", keyword, exc)

        return results


def _strip_html(text: str) -> str:
    """Remove HTML tags and decode common entities."""
    text = re.sub(r"<[^>]+>", " ", text)
    for entity, char in [("&amp;", "&"), ("&lt;", "<"), ("&gt;", ">"), ("&#xa0;", " "), ("&nbsp;", " ")]:
        text = text.replace(entity, char)
    return re.sub(r"\s+", " ", text).strip()


def _parse_rss(xml_text: str) -> list[dict]:
    """Parse an Indeed RSS feed into structured job dicts."""
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        logger.warning("Indeed RSS parse error: %s", exc)
        return []

    items = root.findall(".//item")
    if not items:
        logger.warning("Indeed RSS: no items found — feed may be empty or the request was blocked.")
        return []

    return [job for item in items if (job := _extract_item(item)) is not None]


def _extract_item(item) -> dict | None:
    """Extract a structured job dict from a single RSS <item>."""
    raw_title = (item.findtext("title") or "").strip()
    link = (item.findtext("link") or "").strip()
    company = (item.findtext("source") or "").strip() or None
    description = _strip_html(item.findtext("description") or "")

    # Indeed sometimes appends location to the title: "Python Developer - Tel Aviv"
    # Split it off if the suffix looks like a location (short, contains comma or city name).
    title = raw_title
    location_raw: str | None = None
    if " - " in raw_title and not company:
        head, tail = raw_title.rsplit(" - ", 1)
        # Treat the tail as a location if it's short (≤4 words) or contains a comma.
        if "," in tail or len(tail.split()) <= 4:
            title = head.strip()
            location_raw = tail.strip()
            # Company might now be in the description first line — leave as None for now.

    if not title:
        return None

    full_text = (title + " " + (description or "")).lower()
    remote = bool(re.search(r"\bremote\b|מרחוק|hybrid", full_text))
    location = None if remote else location_raw

    return {
        "title": title,
        "company": company,
        "location": location,
        "remote": remote,
        "summary": description[:500] if description else None,
        "skills": [],
        "contact": link or None,
    }


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    scraper = IndeedScraper()
    jobs = scraper.fetch(["python developer", "backend developer"])
    print(f"Found {len(jobs)} job(s):\n")
    for i, job in enumerate(jobs[:3], 1):
        print(f"--- Job {i} ---")
        for k, v in job.items():
            print(f"  {k}: {v!r}")
        print()
