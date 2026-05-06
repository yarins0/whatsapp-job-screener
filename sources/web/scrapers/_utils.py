"""Shared utilities for web scrapers — retry logic and user-agent rotation."""

from __future__ import annotations

import logging
import random
import time

import requests

logger = logging.getLogger(__name__)

# A small pool of realistic desktop browser user-agents. One is chosen randomly
# per request so repeated calls from the same scraper don't present an identical
# fingerprint to the server.
USER_AGENTS: list[str] = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Safari/605.1.15",
]

_BASE_DELAY_S = 2.0   # starting delay before the first retry
_JITTER_S = 1.0       # max random jitter added to each retry delay


def random_headers() -> dict[str, str]:
    """Return request headers with a randomly chosen user-agent."""
    return {
        "User-Agent": random.choice(USER_AGENTS),
        "Accept-Language": "en-US,en;q=0.9",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    }


def fetch_with_retry(
    url: str,
    *,
    headers: dict[str, str] | None = None,
    timeout: int = 20,
    max_retries: int = 3,
) -> requests.Response:
    """GET a URL, retrying on transient failures.

    Retries on: connection errors, timeouts, and 5xx / 429 responses.
    Returns immediately on any other status (2xx, 4xx) — the caller decides
    whether to treat a 403 or 404 as an error.

    Raises:
        requests.RequestException: after all retries are exhausted.
    """
    if headers is None:
        headers = random_headers()

    last_exc: Exception | None = None
    for attempt in range(max_retries):
        if attempt > 0:
            delay = _BASE_DELAY_S * (2 ** (attempt - 1)) + random.uniform(0, _JITTER_S)
            logger.debug("Retry %d/%d in %.1fs for %s", attempt, max_retries - 1, delay, url)
            time.sleep(delay)

        try:
            resp = requests.get(url, headers=headers, timeout=timeout)
            if resp.status_code in (429, 500, 502, 503, 504):
                last_exc = requests.HTTPError(response=resp)
                logger.warning("HTTP %d from %s — retrying", resp.status_code, url)
                continue
            return resp
        except requests.Timeout:
            last_exc = requests.Timeout(f"Timeout fetching {url}")
            logger.warning("Timeout fetching %s", url)
        except requests.ConnectionError as exc:
            last_exc = exc
            logger.warning("Connection error for %s: %s", url, exc)

    raise last_exc or requests.RequestException(f"All {max_retries} retries failed for {url}")
