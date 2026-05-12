"""FastAPI ingest endpoint — receives messages from the WhatsApp listener."""

from __future__ import annotations

import asyncio
import logging
import re
import time
from contextlib import asynccontextmanager
from html.parser import HTMLParser

import requests
from dotenv import load_dotenv
from fastapi import FastAPI
from pydantic import BaseModel, Field

from agent.pipeline import run_pipeline

load_dotenv()
for _lvl, _name in [(10, "debug"), (20, "info"), (30, "warning"), (40, "error"), (50, "critical")]:
    logging.addLevelName(_lvl, _name)
logging.basicConfig(level=logging.INFO, format="[%(asctime)s][%(levelname)s] %(message)s", datefmt="%H:%M:%S")
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
logging.getLogger("uvicorn.error").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# URL preview enrichment — runs for all sources before the pipeline
# ---------------------------------------------------------------------------

_URL_RE = re.compile(r"https?://\S+")


def _find_first_url(text: str) -> str | None:
    match = _URL_RE.search(text)
    return match.group(0).rstrip(".,)>") if match else None


class _OGParser(HTMLParser):
    """Extract og:title, og:description, and <title> from an HTML snippet."""

    def __init__(self) -> None:
        super().__init__()
        self.og_title: str | None = None
        self.og_description: str | None = None
        self.page_title: str | None = None
        self._in_title = False

    def handle_starttag(self, tag: str, attrs: list) -> None:
        if tag == "title":
            self._in_title = True
            return
        if tag != "meta":
            return
        d = dict(attrs)
        prop = (d.get("property") or d.get("name") or "").lower()
        content = d.get("content") or ""
        if prop == "og:title" and not self.og_title:
            self.og_title = content.strip()
        elif prop == "og:description" and not self.og_description:
            self.og_description = content.strip()

    def handle_data(self, data: str) -> None:
        if self._in_title and not self.page_title:
            self.page_title = data.strip()

    def handle_endtag(self, tag: str) -> None:
        if tag == "title":
            self._in_title = False


def _fetch_url_preview(url: str) -> str | None:
    """Fetch OG metadata from a URL and return labeled preview lines, or None.

    Times out after 5 seconds and reads at most 50 KB so it never blocks
    the event loop thread for long.
    """
    try:
        resp = requests.get(
            url,
            timeout=5,
            headers={"User-Agent": "Mozilla/5.0 (compatible; JobBot/1.0)"},
            allow_redirects=True,
        )
        if not resp.ok or "text/html" not in resp.headers.get("Content-Type", ""):
            return None
        parser = _OGParser()
        parser.feed(resp.text[:50_000])
        title = parser.og_title or parser.page_title
        description = parser.og_description
        if not title and not description:
            return None
        parts = []
        if title:
            parts.append(f"Title: {title}")
        if description:
            parts.append(f"Description: {description}")
        parts.append(f"URL: {url}")
        return "\n".join(parts)
    except Exception:
        return None


async def _enrich_text(text: str) -> str:
    """Append a [Link preview] block to text if one is not already present.

    Runs the HTTP fetch in a thread so the async event loop is not blocked.
    Returns the original text unchanged if no URL is found or the fetch fails.
    """
    if "[Link preview]" in text:
        return text  # Telegram listener already enriched this message
    url = _find_first_url(text)
    if not url:
        return text
    preview = await asyncio.to_thread(_fetch_url_preview, url)
    if not preview:
        return text
    logger.debug("Fetched URL preview for %s", url)
    return f"{text}\n\n[Link preview]\n{preview}"


# ---------------------------------------------------------------------------
# Retry queue
# ---------------------------------------------------------------------------

# Seconds to wait before each successive retry attempt.
_RETRY_DELAYS_S: tuple[int, ...] = (30, 120, 300)
_MAX_RETRIES = len(_RETRY_DELAYS_S)

# Each item: (retry_at: float, attempt: int, msg_data: dict)
# maxsize caps memory usage; excess messages are dropped with a log.error.
_retry_queue: asyncio.Queue = asyncio.Queue(maxsize=500)


def _enqueue_retry(msg_data: dict, attempt: int) -> None:
    delay = _RETRY_DELAYS_S[attempt]
    retry_at = time.monotonic() + delay
    try:
        _retry_queue.put_nowait((retry_at, attempt, msg_data))
    except asyncio.QueueFull:
        logger.error(
            "Retry queue full — message dropped: group=%s text=%r",
            msg_data.get("group"), (msg_data.get("text") or "")[:60],
        )


async def _retry_worker() -> None:
    """Drain the retry queue: wait until retry_at, run the pipeline, re-enqueue on failure."""
    while True:
        retry_at, attempt, msg_data = await _retry_queue.get()
        try:
            wait = retry_at - time.monotonic()
            if wait > 0:
                await asyncio.sleep(wait)

            result = await run_pipeline(msg_data)
            action = result.get("action", "unknown")
            logger.info(
                "Retry %d/%d succeeded (action=%s) — group=%s",
                attempt + 1, _MAX_RETRIES, action, msg_data.get("group"),
            )
        except Exception as exc:
            next_attempt = attempt + 1
            if next_attempt < _MAX_RETRIES:
                next_delay = _RETRY_DELAYS_S[next_attempt]
                logger.warning(
                    "Retry %d/%d failed, will retry in %ds — group=%s: %s",
                    attempt + 1, _MAX_RETRIES, next_delay,
                    msg_data.get("group"), type(exc).__name__,
                )
                _enqueue_retry(msg_data, next_attempt)
            else:
                logger.error(
                    "Message dropped after %d retries — group=%s text=%r: %s",
                    _MAX_RETRIES, msg_data.get("group"),
                    (msg_data.get("text") or "")[:60], type(exc).__name__,
                )
        finally:
            _retry_queue.task_done()


# ---------------------------------------------------------------------------
# App lifespan — start retry workers on boot
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    workers = [asyncio.create_task(_retry_worker()) for _ in range(3)]
    yield
    for w in workers:
        w.cancel()


app = FastAPI(title="WhatsApp Job Screener", version="0.1.0", lifespan=lifespan)


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------

class Message(BaseModel):
    """Payload posted by listener.js for every WhatsApp group message."""

    group: str = Field(..., description="WhatsApp group display name")
    sender: str = Field(..., description="WhatsApp ID of the sender")
    text: str = Field(..., description="Raw message body")
    timestamp: int = Field(..., description="Unix epoch seconds")


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/ingest")
async def ingest(msg: Message) -> dict:
    msg_data = msg.model_dump()
    msg_data["text"] = await _enrich_text(msg_data["text"])
    try:
        result = await run_pipeline(msg_data)
    except Exception as exc:
        logger.warning(
            "Pipeline error, queuing retry 1/%d in %ds — group=%s: %s",
            _MAX_RETRIES, _RETRY_DELAYS_S[0], msg.group, type(exc).__name__,
        )
        _enqueue_retry(msg_data, attempt=0)
        return {"status": "queued_for_retry", "reason": type(exc).__name__}

    action = result.get("action")
    if action == "stored" and "job" in result:
        # Single stored job — legacy flat shape.
        job = result.get("job") or {}
        title = job.get("title") or "unknown"
        company = job.get("company") or "?"
        location = "Remote" if job.get("remote") else (job.get("location") or "")
        loc_part = f" ({location})" if location else ""
        logger.info("STORED   | %s @ %s%s | id=%s", title, company, loc_part, result.get("job_id"))
    elif action in ("stored", "partial") and "stored" in result:
        # Multiple jobs in one message.
        stored = result.get("stored") or []
        skipped = result.get("skipped") or []
        logger.info(
            "STORED   | %d job(s) stored, %d skipped | group=%s",
            len(stored), len(skipped), msg.group,
        )
    else:
        reason = result.get("reason", "unknown")
        job = result.get("job") or {}
        title = job.get("title") or ""
        company = job.get("company") or ""
        location = "Remote" if job.get("remote") else (job.get("location") or "")
        if title:
            loc_part = f" ({location})" if location else ""
            logger.info("SKIPPED  | %s | %s @ %s%s | group=%s", reason, title, company, loc_part, msg.group)
        else:
            logger.info("SKIPPED  | %s | group=%s", reason, msg.group)

    return {"status": "ok", "result": result}
