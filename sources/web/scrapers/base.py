"""Base class for job board scrapers.

Each scraper fetches job listings from one source and extracts structured data
directly from the page — no LLM required. The pipeline tools (dedup, filter,
store) are applied by the listener after fetch() returns.

A scraper receives the user's role keywords from agent/prefs.json so it can
construct relevant search queries without duplicating config.
"""

from __future__ import annotations

from abc import ABC, abstractmethod


class JobScraper(ABC):
    """Abstract base for a single job-board source."""

    # Human-readable name shown in Telegram notifications and logs.
    name: str

    @abstractmethod
    def fetch(self, keywords: list[str]) -> list[dict]:
        """Fetch job listings matching the given keywords.

        Args:
            keywords: role keywords from agent/prefs.json (e.g. ["python", "backend"]).

        Returns:
            A list of job dicts with the following keys:
                title:    str            — job title
                company:  str | None     — company name
                location: str | None     — city / region (None if remote)
                remote:   bool           — True if the role is remote
                summary:  str | None     — job description / body text
                skills:   list[str]      — extracted skill keywords (may be empty)
                contact:  str | None     — application link or contact info

            Deduplication within a single call (across keywords) is handled by
            the scraper using a (title, company) key. Cross-poll deduplication
            is handled by the listener using dedup_tool.is_duplicate().
        """
