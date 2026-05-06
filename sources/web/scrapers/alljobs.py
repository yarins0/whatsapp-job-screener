"""AllJobs.co.il scraper — Israel's largest job board.

Extracts structured job data (title, company, location, summary) directly from
the search results page HTML using BeautifulSoup. No LLM call needed.

Search URL pattern:
  https://www.alljobs.co.il/SearchResultsGuest.aspx?page=1&freetxt={keyword}

Run standalone to inspect live card HTML and fix selectors if needed:
    python -m sources.web.scrapers.alljobs
"""

from __future__ import annotations

import logging
import random
import re
import time

from sources.web.scrapers.base import JobScraper
from sources.web.scrapers._utils import fetch_with_retry, random_headers

logger = logging.getLogger(__name__)

_SEARCH_URL = "https://www.alljobs.co.il/SearchResultsGuest.aspx?page=1&position=&type=&freetxt={keyword}&city=&region="
_BASE_URL = "https://www.alljobs.co.il"

_DELAY_BETWEEN_KEYWORDS_S = (2.0, 5.0)


class AllJobsScraper(JobScraper):
    name = "AllJobs"

    def fetch(self, keywords: list[str]) -> list[dict]:
        """Fetch job cards from AllJobs for each keyword; return deduplicated job dicts."""
        try:
            from bs4 import BeautifulSoup
        except ImportError as exc:
            logger.error("Missing dependency: %s — run: pip install beautifulsoup4 lxml", exc)
            return []

        seen: set[tuple[str, str]] = set()
        results: list[dict] = []

        for i, keyword in enumerate(keywords):
            if i > 0:
                time.sleep(random.uniform(*_DELAY_BETWEEN_KEYWORDS_S))

            url = _SEARCH_URL.format(keyword=keyword.replace(" ", "+"))
            try:
                resp = fetch_with_retry(url, headers=random_headers())
                if resp.status_code == 403:
                    logger.warning("AllJobs blocked request (403) for keyword '%s' — skipping.", keyword)
                    continue
                resp.raise_for_status()
                cards = _parse_page(resp.text, BeautifulSoup)
                new = 0
                for job in cards:
                    key = (job["title"].lower(), (job["company"] or "").lower())
                    if key not in seen:
                        seen.add(key)
                        results.append(job)
                        new += 1
                logger.info("AllJobs '%s': %d card(s) found, %d new", keyword, len(cards), new)
            except Exception as exc:
                logger.warning("AllJobs fetch failed for keyword '%s': %s", keyword, exc)

        return results


def _parse_page(html: str, BeautifulSoup) -> list[dict]:
    """Extract structured job dicts from an AllJobs search results page."""
    soup = BeautifulSoup(html, "lxml")
    card_els = [b for b in soup.select(".job-box") if b.select_one(".job-content-top-title")]
    if not card_els:
        logger.warning("AllJobs: no job cards found — selectors may need updating.")
    return [job for el in card_els if (job := _extract_card(el)) is not None]


def _extract_card(card_el) -> dict | None:
    """Extract a single structured job dict from a .job-box element.

    Selectors confirmed against live AllJobs HTML (guest search results):
      Title:    .job-content-top-title a          — link text only
      Company:  .job-content-top-title .T14       — sibling div inside title container
      Location: .job-content-top-location         — may have "מיקום:" label prefix
      Summary:  .job-content-top-desc.AR          — the RTL description div
    """
    title_el = card_el.select_one(".job-content-top-title")
    if not title_el:
        return None

    title_link = title_el.select_one("a")
    title = (title_link if title_link else title_el).get_text(strip=True)
    if not title:
        return None

    href = title_link.get("href", "") if title_link else ""
    contact = (_BASE_URL + href) if href.startswith("/") else (href or None)

    # Company lives in a .T14 div inside the title container.
    company_el = title_el.select_one(".T14")
    company = company_el.get_text(strip=True) if company_el else None

    # Location text may be prefixed with a Hebrew label like "מיקום:".
    location_raw = _first_text(card_el, ".job-content-top-location")
    if location_raw and ":" in location_raw:
        location_raw = location_raw.split(":", 1)[-1].strip() or None

    remote = bool(re.search(r"remote|מרחוק|hybrid", (location_raw or "").lower()))
    location = None if remote else location_raw

    summary = _first_text(card_el, ".job-content-top-desc.AR", ".job-content-top-acord")

    return {
        "title": title,
        "company": company,
        "location": location,
        "remote": remote,
        "summary": summary,
        "skills": [],
        "contact": contact,
    }


def _first_text(el, *selectors: str) -> str | None:
    """Return the text of the first matching selector, or None."""
    for sel in selectors:
        found = el.select_one(sel)
        if found:
            text = found.get_text(strip=True)
            if text:
                return text
    return None


if __name__ == "__main__":
    import sys
    from bs4 import BeautifulSoup
    import requests

    logging.basicConfig(level=logging.INFO)

    keyword = sys.argv[1] if len(sys.argv) > 1 else "python"
    url = _SEARCH_URL.format(keyword=keyword.replace(" ", "+"))
    resp = requests.get(url, headers=random_headers(), timeout=20)
    soup = BeautifulSoup(resp.text, "lxml")
    boxes = [b for b in soup.select(".job-box") if b.select_one(".job-content-top-title")]

    if not boxes:
        print("No cards found. Full page text (first 2000 chars):")
        print(resp.text[:2000])
    else:
        print(f"Found {len(boxes)} card(s). First card HTML:\n")
        print(boxes[0].prettify()[:3000])
        print("\n--- Extracted structured data ---")
        for i, el in enumerate(boxes[:5], 1):
            job = _extract_card(el)
            print(f"\nJob {i}: {job}")
