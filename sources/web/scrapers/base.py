"""Base class for job board scrapers.

Each scraper is responsible for fetching job listings from one source and
returning them as a list of raw text strings. The pipeline's LLM handles
extraction into structured JobPost dicts — scrapers never do that themselves.

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
    def fetch(self, keywords: list[str]) -> list[str]:
        """Fetch job listings matching the given keywords.

        Args:
            keywords: role keywords from agent/prefs.json (e.g. ["python", "backend"]).

        Returns:
            A list of raw text strings, one per job listing. The text should
            include as much of the job post as possible (title, company,
            location, description, contact). The LLM will extract structure.
        """
