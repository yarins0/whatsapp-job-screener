"""Indeed Israel scraper — fetches job listings from il.indeed.com.

Uses requests + BeautifulSoup to parse the job cards returned by Indeed's
server-rendered search results.

Search URL pattern:
  https://il.indeed.com/jobs?q={keyword}&l=Israel&sort=date&fromage=3

NOTE: Indeed actively blocks automated scraping. This scraper uses a realistic
User-Agent and a conservative request rate. If Indeed blocks requests (HTTP 403
or empty results), consider:
  1. Adding longer delays between requests (DELAY_BETWEEN_REQUESTS_S)
  2. Using rotating proxies or a headless browser (Playwright)
  3. Falling back to the Indeed RSS feed if re-enabled

Run standalone to inspect what the scraper returns:
    python -m sources.web.scrapers.indeed
"""

from __future__ import annotations

import logging
import time

from sources.web.scrapers.base import JobScraper

logger = logging.getLogger(__name__)

_SEARCH_URL = "https://il.indeed.com/jobs?q={keyword}&l=Israel&sort=date&fromage=3"

# Seconds to wait between requests to reduce the chance of rate-limiting.
DELAY_BETWEEN_REQUESTS_S = 5

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}


class IndeedScraper(JobScraper):
    name = "Indeed"

    def fetch(self, keywords: list[str]) -> list[str]:
        """Fetch job cards from Indeed Israel for each keyword."""
        try:
            import requests
            from bs4 import BeautifulSoup
        except ImportError as exc:
            logger.error("Missing dependency: %s — run: pip install beautifulsoup4 lxml requests", exc)
            return []

        seen_ids: set[str] = set()
        results: list[str] = []

        for i, keyword in enumerate(keywords):
            if i > 0:
                # Polite delay between keyword searches to avoid rate-limiting.
                time.sleep(DELAY_BETWEEN_REQUESTS_S)

            url = _SEARCH_URL.format(keyword=keyword.replace(" ", "+"))
            try:
                resp = requests.get(url, headers=_HEADERS, timeout=20)
                if resp.status_code == 403:
                    logger.warning("Indeed blocked request (403) for keyword '%s' — skipping.", keyword)
                    continue
                resp.raise_for_status()
                cards = _parse_cards(resp.text, BeautifulSoup)
                new_cards = [c for job_id, c in cards if job_id not in seen_ids]
                seen_ids.update(job_id for job_id, _ in cards)
                results.extend(new_cards)
                logger.info("Indeed '%s': %d card(s) found", keyword, len(cards))
            except Exception as exc:
                logger.warning("Indeed fetch failed for keyword '%s': %s", keyword, exc)

        return results


def _parse_cards(html: str, BeautifulSoup) -> list[tuple[str, str]]:
    """Extract (job_id, raw_text) tuples from an Indeed search results page.

    Indeed renders job cards with data-testid="job-card" and a data-jk job key.
    Adjust selectors if Indeed changes their markup.
    """
    soup = BeautifulSoup(html, "lxml")

    # Primary selector: Indeed's stable data-testid attribute.
    cards = soup.select("[data-testid='job-card']")

    if not cards:
        # Fallback: look for the job card container by class fragment.
        cards = soup.select("[class*='jobsearch-ResultsList'] li") or soup.select(".job_seen_beacon")

    if not cards:
        logger.warning("Indeed: no job cards found — selectors may need updating.")
        return []

    results = []
    for card in cards:
        job_id = card.get("data-jk") or card.get("id") or ""
        lines = [line.strip() for line in card.get_text(separator="\n").splitlines() if line.strip()]
        if lines:
            results.append((str(job_id), "\n".join(lines)))
    return results


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    scraper = IndeedScraper()
    jobs = scraper.fetch(["python developer", "backend developer"])
    print(f"Found {len(jobs)} job(s):\n")
    for i, job in enumerate(jobs[:3], 1):
        print(f"--- Job {i} ---")
        print(job[:400])
        print()
