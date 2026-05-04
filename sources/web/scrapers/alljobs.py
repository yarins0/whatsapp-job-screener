"""AllJobs.co.il scraper — Israel's largest job board.

Uses langchain_community.document_loaders.WebBaseLoader (the LangChain way to
load web pages) then BeautifulSoup to split the page into individual job cards.

Search URL pattern:
  https://www.alljobs.co.il/SearchResult.aspx?pos={keyword}&region=0&type=0&from=0

NOTE: CSS selectors in _parse_cards() may need adjustment if AllJobs changes
their markup. Run this scraper standalone to inspect what it returns:

    python -m sources.web.scrapers.alljobs
"""

from __future__ import annotations

import logging

from sources.web.scrapers.base import JobScraper

logger = logging.getLogger(__name__)

_SEARCH_URL = "https://www.alljobs.co.il/SearchResultsGuest.aspx?page=1&position=&type=&freetxt={keyword}&city=&region="


class AllJobsScraper(JobScraper):
    name = "AllJobs"

    def fetch(self, keywords: list[str]) -> list[str]:
        """Fetch job cards from AllJobs for each keyword; return deduplicated raw texts."""
        try:
            from bs4 import BeautifulSoup
            from langchain_community.document_loaders import WebBaseLoader
        except ImportError as exc:
            logger.error("Missing dependency: %s — run: pip install beautifulsoup4 lxml langchain-community", exc)
            return []

        seen_titles: set[str] = set()
        results: list[str] = []

        for keyword in keywords:
            url = _SEARCH_URL.format(keyword=keyword.replace(" ", "+"))
            try:
                loader = WebBaseLoader(url)
                docs = loader.load()
                if not docs:
                    continue
                cards = _parse_cards(docs[0].page_content, BeautifulSoup, docs[0].metadata.get("source", url))
                for card in cards:
                    # Use the first line (title) as a simple dedup key across keywords.
                    title_key = card.split("\n")[0].strip().lower()
                    if title_key and title_key not in seen_titles:
                        seen_titles.add(title_key)
                        results.append(card)
                logger.info("AllJobs '%s': %d card(s) found", keyword, len(cards))
            except Exception as exc:
                logger.warning("AllJobs fetch failed for keyword '%s': %s", keyword, exc)

        return results


def _parse_cards(page_text: str, BeautifulSoup, source_url: str) -> list[str]:
    """Extract individual job card texts from the AllJobs search results page.

    WebBaseLoader returns plain text; we re-fetch the raw HTML via requests so
    BeautifulSoup can parse the structure properly.

    Each job card is returned as a multi-line string with title, company,
    location, and description — enough for the LLM extractor to work with.
    """
    import requests

    try:
        resp = requests.get(source_url, timeout=15, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        })
        resp.raise_for_status()
    except Exception as exc:
        logger.warning("AllJobs HTML fetch failed: %s", exc)
        return []

    soup = BeautifulSoup(resp.text, "lxml")

    # AllJobs renders each listing inside a .job-box div.
    # Deleted listings have a .job-delete-details element but no .job-content-top-title.
    all_boxes = soup.select(".job-box")
    cards = [c for c in all_boxes if c.select_one(".job-content-top-title")]

    if not cards:
        logger.warning("AllJobs: no job cards found — selectors may need updating.")
        return []

    results = []
    for card in cards:
        lines = [line.strip() for line in card.get_text(separator="\n").splitlines() if line.strip()]
        if lines:
            results.append("\n".join(lines))
    return results


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    scraper = AllJobsScraper()
    jobs = scraper.fetch(["python", "backend"])
    print(f"Found {len(jobs)} job(s):\n")
    for i, job in enumerate(jobs[:3], 1):
        print(f"--- Job {i} ---")
        print(job[:400])
        print()
