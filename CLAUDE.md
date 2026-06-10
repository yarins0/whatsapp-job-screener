# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Set up Python environment
python -m venv venv && venv\Scripts\activate
pip install -r requirements.txt

# Initialize the SQLite database
python -m db.init_db

# Run all Python tests (no API key needed — uses FakeListChatModel)
pytest tests/ -v

# Run a single test file
pytest tests/test_pipeline.py -v

# Run a single test by name
pytest tests/test_pipeline.py::test_stores_a_qualified_job -v

# Run Node.js tests (last-seen state module)
npm test

# Browse stored jobs from the terminal
python -m agent.list_jobs                    # last 20 jobs (past 7 days)
python -m agent.list_jobs --days 0           # all time
python -m agent.list_jobs --role python      # filter by keyword
python -m agent.list_jobs --unseen           # not yet in a digest
python -m agent.list_jobs --limit 50

# Live smoke test (requires ANTHROPIC_API_KEY in .env)
python -m agent.pipeline

# Start everything (API + listener + digest scheduler)
python start.py

# Debug web scrapers — inspect live HTML and extracted fields
python -m sources.web.scrapers.alljobs          # fetches "python" + "backend" by default
python -m sources.web.scrapers.alljobs fullstack
python -m sources.web.scrapers.indeed
```

## Architecture

This is a single-user LangChain learning project. Messages flow from two source types:

```
WhatsApp (Node.js listener)  ─┐
Telegram channels (Telethon) ─┤─► POST /ingest (FastAPI) ─► run_pipeline() ─► SQLite ─► Telegram (instant)
                               │                                                    ↓
Web scrapers (AllJobs etc.)  ──┘─► direct: dedup→filter→store→notify          Daily digest
                                   (no HTTP, no LLM)                          (APScheduler)
```

### Core flow: `agent/pipeline.py` + `agent/graph.py`

`run_pipeline(message)` is the main entry point. It is `async` and accepts an optional `llm` override (used by tests) and `notify=False` (used by the smoke test to suppress Telegram). It delegates all orchestration to the LangGraph `StateGraph` compiled in `agent/graph.py`.

**Graph nodes** (defined in `agent/graph.py`):

- **`route`** — selects and runs the appropriate classification path, then returns `{is_job, confidence, jobs, mode}`.
- **`extract`** — runs the extractor chain (separate mode only, after classification passes).
- **`process_jobs`** — dedup → filter → store → notify loop over every extracted job.
- **`finish`** / **`finish_skipped`** — assemble the final result dict.

**Adaptive mode selection:** The `route` node calls `get_pipeline_mode(group)` from `agent/tools/stats_tool.py`. Once a group has ≥50 messages and ≥70% are job posts, the mode switches to `"combined"`. Otherwise it uses `"separate"` (the default).

- **Separate mode** — two LLM calls: classifier first (in `route`), then extractor (in `extract` node) only if it passes.
- **Combined mode** — one LLM call: `agent/chains/combined.py` classifies and extracts simultaneously, returning `{is_job_post, confidence, jobs: [...]}`.

In both modes, stats are recorded via `record_message(group, is_job_post)` inside `_make_route_result`.

A message may contain multiple job posts. The extractor normalises to a list via `_normalise_jobs`; `process_jobs` loops over every job:

1. **Dedup** (`agent/tools/dedup_tool.py`) — atomic `INSERT OR IGNORE` on `seen_hashes` table.
2. **Filter** (`agent/tools/filter_tool.py`) — calls `load_prefs()` from `agent/tools/prefs_tool.py` (reads `agent/prefs.json` on every call so Telegram-bot changes take effect immediately). Returns `(passed, reason)`.
3. **Store** (`agent/tools/store_tool.py`) — checks for a time-duplicate (same title+company within `DUPLICATE_WINDOW_DAYS` days), then inserts into `jobs` table. Returns the new row `id`, or `None` if suppressed as a time-duplicate.
4. **Notify** — sends an instant Telegram alert per stored job via `digest.digest._send_telegram`, with two inline buttons: **Block role** and **Block city**. Text is escaped for Telegram Markdown V1 via `_md()` in `graph.py`. `_notify_job` returns whether the alert was actually delivered; on success `process_jobs` calls `store_tool.mark_seen(job_id)` so a delivered job is flagged `seen = 1` immediately (not just after the daily digest).

### Ingest API: `api/main.py`

FastAPI endpoint at `POST /ingest`. Receives `{group, sender, text, timestamp}` from WhatsApp and Telegram source listeners.

**Health endpoint:** `GET /healthz` returns `{"status": "ok"}`.

**Self-ping (Render):** If `RENDER_EXTERNAL_URL` is set, a background task pings `/healthz` every 14 minutes to prevent Render free-tier spin-down (which triggers after 15 min of inactivity). No-op locally.

**Retry queue:** If `run_pipeline()` raises any exception (e.g. `anthropic.APIConnectionError` on a network blip), the message is enqueued for up to 3 retries with 30s / 2min / 5min backoff. Three async workers drain the queue concurrently. The queue is in-memory (lost on restart) and capped at 500 items. Started via FastAPI `lifespan`.

**Log format:** Every processed message logs one of:
```
STORED   | Job Title @ Company (Location) | id=N
SKIPPED  | reason | Job Title @ Company (Location) | group=Name
```

### WhatsApp listener: `sources/whatsapp/listener.js`

On `ready`, reads watched group IDs from `agent/whatsapp_sources.json` (a `{id: name}` map), resolves display names, and calls `catchUp()` for each group. Re-reads the groups file on every `message` event so `/addgroup` and `/removegroup` take effect immediately.

**Reconnect:** The heartbeat (every 2 min) calls `getState()`. If disconnected, calls `reconnect()` which runs `destroy()` → 3-second pause → `initialize()`. The pause is required because Chrome holds a `SingletonLock` on its user data dir until it fully exits; calling `initialize()` without waiting produces a "browser already running" error.

**Puppeteer `protocolTimeout`:** Set to 120 s (up from 60 s) to prevent `Runtime.callFunctionOn timed out` errors on accounts with many chats.

**QR code delivery:**
- Every QR event sends to Telegram. The first sends a PNG photo to the owner chat (requires `TELEGRAM_BOT_TOKEN` + `TELEGRAM_CHAT_ID`); each subsequent QR (WhatsApp regenerates every ~20 s) silently edits that same message in place via `editMessageMedia` — no extra notifications. If the edit fails (e.g. the owner deleted the message), it falls back to sending a fresh photo.
- `lastQrMessageId` is **persisted to `.wwebjs_auth/.qr_message_id`** (same pattern as `.fail_count`), so a needed re-scan keeps editing the one message in place across reconnects *and* crash-restarts instead of spamming a new photo each time. It is cleared only on a successful scan (`resolveQrMessage`).
- Terminal: first QR prints immediately; subsequent QRs show `"QR expired — regen another? y/n:"` and wait for input before printing again. While waiting, `pendingQr` always holds the latest code, so typing `y` prints the freshest valid QR.
- On successful scan, the Telegram message caption is updated to `"WhatsApp connected."` and the persisted id is cleared.
- `qrPrinted`, `pendingQr`, and `regenPromptActive` are reset in `reconnect()`; `lastQrMessageId` is intentionally **not** reset there (it is disk-backed) so a reconnect that needs a re-scan reuses the existing message.

**Version pin maintenance:** the `webVersionCache` pin (currently `2.3000.1040532093-alpha`) rots as WhatsApp deprecates old client builds. Re-check ~quarterly (or sooner if a previously-working session stops connecting): bump to a *recent but not newest* build from [wa-version](https://github.com/wppconnect-team/wa-version) (new builds regularly ship breaking changes), verify it connects before committing, and keep `whatsapp-web.js` up to date on the same cadence. See the inline comment at the `webVersionCache` pin for the full procedure.

`sources/whatsapp/last_seen.js` — state module (`load`, `save`, `update`). Accepts a custom file path for Jest test isolation.

### Telegram source listener: `sources/telegram/listener.py`

Telethon userbot that watches broadcast channels the Telegram account is a member of. On startup, resolves all configured sources, replays missed messages (up to 48h back), then listens for new ones. Forwards to `/ingest` in the same format as the WhatsApp listener.

Config: `agent/telegram_sources.json` — `{channel_id_or_username: display_name}`.

### Web source listener: `sources/web/listener.py`

Polls enabled scrapers on a configurable interval (default: 30 min). **Does not use the HTTP API or LLM.** Each scraper extracts structured job dicts directly from HTML/XML, then the listener applies the same tools as the pipeline:

```
scraper.fetch(keywords) → [dict]
  └─ (title, company) in-memory dedup (within-poll, across keywords)
       └─ is_duplicate(job)    ← SQLite seen_hashes, cross-poll
            └─ filter_job(job) ← prefs.json blocklist/roles/location
                 └─ store_job()← SQLite + ChromaDB vector index
                      └─ _notify_job() ← Telegram alert
```

Config: `agent/web_sources.json` — `{scraper_key: {enabled: bool}}`.

**Scrapers** (`sources/web/scrapers/`):

- **`_utils.py`** — shared: `USER_AGENTS` pool (5 UAs), `random_headers()`, `fetch_with_retry(url, max_retries=3)` with exponential backoff + jitter on connection errors and 5xx/429.
- **`alljobs.py`** (`AllJobsScraper`) — AllJobs.co.il guest search. CSS selectors confirmed against live HTML:
  - Title: `.job-content-top-title a` (link text only)
  - Company: `.job-content-top-title .T14`
  - Location: `.job-content-top-location` (strips `מיקום:` label prefix)
  - Summary: `.job-content-top-desc.AR`
  - Contact: absolute job URL from title link href
- **`indeed.py`** (`IndeedScraper`) — Indeed Israel RSS feed (`il.indeed.com/rss?q=...`). Parses XML with stdlib `xml.etree.ElementTree`. Currently disabled in `web_sources.json` (RSS returns 403).

Run `python -m sources.web.scrapers.alljobs` to print live HTML of the first card and extracted fields — useful when AllJobs changes their markup.

### Telegram bot: `telegram_bot.py` + `telegram_handlers/`

`telegram_bot.py` is a thin dispatcher (~180 lines): `_api`, `_send`, `_is_owner`, `_answer_callback`, `_handle_message` (if/elif router), `_handle_callback`, `run_bot`. All command logic lives in the `telegram_handlers/` package:

| Module | Commands |
|---|---|
| `start.py` | `/help`, `/start`, `/commands`; command list strings; `_ASK_DEMO_LIMIT` |
| `jobs.py` | `/jobs`, `/ask`, `/similar`, `/reindex`, `/resend`; `_ask_counts` |
| `prefs.py` | `/prefs`, `/blockrole`, `/blockcity`, `/addrole` |
| `groups.py` | `/listgroups`, `/groups`, `/addgroup`, `/removegroup` |
| `sources.py` | `/tgsources`, `/addtgsource`, `/removetgsource` |
| `callbacks.py` | inline button handling (`block_role:`, `block_city:`) |

Handler functions return `str | list[str]`; the dispatcher calls `_send` for each — tests patching `telegram_bot._send` work unchanged.

Access control: the owner is identified by `TELEGRAM_CHAT_ID`. Write/discovery commands are owner-only. Read commands are available to all users, but `/ask` is rate-limited for non-owners via `_ask_counts` in `telegram_handlers/jobs.py` (`_ASK_DEMO_LIMIT` = 3 per session).

**Demo users:** Non-owners who type `/start` are registered in `agent/demo_users.json` (gitignored) via `agent/tools/demo_users_tool.py`. They receive: (1) a clean read-only command list, (2) the last 5 stored jobs immediately, and (3) a plain-text notification for every job stored going forward (sent by `_notify_demo_users` in `agent/graph.py`).

`/listgroups` reads `agent/all_whatsapp_groups.json` — a snapshot written by `listener.js` on every `ready` event. The file is gitignored. If it doesn't exist (listener has never connected), the bot tells the user to start the listener first. Watched groups are marked with ✓.

### Browse and query jobs: `agent/list_jobs.py`, `agent/tools/query_tool.py`, and `agent/tools/ask_tool.py`

`query_tool.py` exposes two public functions:
- `query_jobs(days, role, unseen_only, limit)` — queries the `jobs` table with optional filters; returns a list of dicts ordered newest-first.
- `format_jobs_telegram(jobs, days, role, unseen_only)` — renders results in the same Markdown style as the daily digest, respecting Telegram's 4096-char message limit.

`list_jobs.py` is the CLI entry point (`python -m agent.list_jobs`). It calls `query_jobs()` and prints a fixed-width terminal table. Flags: `--days N`, `--role KEYWORD`, `--unseen`, `--limit N`.

`ask_tool.py` exposes one public function:
- `ask_jobs(question, *, llm=None) -> str` — builds an LCEL chain that extracts `{days, role, unseen_only, limit}` from a natural-language question, calls `query_jobs()`, and returns a formatted string.

### Vector search: `agent/vector_store.py`

- `index_job(job_id, job)` — embeds `"{title} at {company} — {summary} — Skills: …"` and upserts into ChromaDB. Called by `store_tool.store_job()` after every insert (errors are non-fatal).
- `find_similar(text, n=5)` — nearest-neighbour query; used by `/similar`.
- `reindex_all()` — back-fills ChromaDB from all SQLite rows; used by `/reindex`.

**Persistence:** `db/chroma/` by default, configurable via `CHROMA_DB_PATH`. **Graceful degradation:** if `chromadb` is not installed, all three are no-ops.

### Key design decisions

- **Web scrapers bypass LLM** — structured data (title, company, location) is extracted from HTML/XML directly. The API and LLM are only used for WhatsApp and Telegram source messages.
- **Web scraper dedup is two-layer** — in-memory `(title, company)` set within each `fetch()` call (across keywords, one poll), then `is_duplicate(job)` against SQLite `seen_hashes` (across polls).
- **Pipeline is a LangGraph `StateGraph`** — `agent/graph.py` defines nodes and conditional edges; `pipeline.py` delegates via `ainvoke`.
- **`llm` is injected** — `_default_llm()` lazy-imports `langchain_anthropic` so tests run offline with `FakeListChatModel`.
- **`JOBS_DB_PATH` env var** — all DB modules read this; the `temp_db` pytest fixture sets it to a `tmp_path` file.
- **`PREFS_PATH` env var** — `prefs_tool` reads this to locate `agent/prefs.json`; `temp_prefs` overrides it per test.
- **`GROUPS_PATH` env var** — `groups_tool` reads this to locate `agent/whatsapp_sources.json`; `temp_groups` overrides it per test.
- **`CHROMA_DB_PATH` env var** — `vector_store` reads this; `temp_chroma` overrides it per test. `temp_db` also sets it so tests using a temp SQLite DB get an isolated Chroma too.
- **Vector indexing is non-fatal** — `store_tool._try_index_job` wraps `index_job` in try/except so a missing Chroma never stops SQLite storage.
- **Time-duplicate dedup** — `is_time_duplicate(title, company)` queries `jobs` for matching title+company within `DUPLICATE_WINDOW_DAYS` days. Skipped when `company` is `None`.
- **Retention cleanup** — `agent/tools/cleanup_tool.py::cleanup_old_records()` deletes records older than `DUPLICATE_WINDOW_DAYS + 7` days (the `+7` grace guarantees nothing inside the active dedup window is touched): all expired `seen_hashes`, plus `jobs` rows that are both past the cutoff **and** `seen = 1` (undelivered jobs are kept so a broken digest never loses one). Deleted job ids are removed from the Chroma index via `vector_store.delete_jobs`. Scheduled in `digest/digest.py` daily at 03:00 **and** once on startup (so intermittently-run deployments still clean up). Idempotent and age-based, so a missed run self-corrects on the next pass.
- **Dedup is atomic** — `INSERT OR IGNORE` + `rowcount` eliminates the SELECT+INSERT race.
- **Filter reads prefs on every call** — Telegram-bot changes take effect on the next message without a restart.
- **Retry queue** — `api/main.py` catches all pipeline exceptions and enqueues retries; the WhatsApp/Telegram listeners never see a 500.
- **Telegram Markdown escaping** — `_md(text)` in `graph.py` and `listener.py` escapes `_`, `*`, `` ` ``, `[` before including user content in notification messages (Telegram Markdown V1 returns 400 on unescaped special chars).
- **Model** — `claude-haiku-4-5-20251001` in production (cheap, fast).

### User preferences

Edit `agent/prefs.json` directly, or use Telegram commands (`/blockrole`, `/blockcity`, `/addrole`). The filter reads the file on every call.

Fields: `roles` (keyword allow-list on title+summary), `blocklist` (auto-reject on title/summary/skills), `location_blocklist` (cities to reject — remote always passes).

### Watched groups

`agent/whatsapp_sources.json` — `{group_id: display_name}`. Add/remove via `/addgroup` / `/removegroup` in Telegram or edit directly. The listener re-reads on every message event.

### Database

`db/schema.sql` defines three tables:
- `jobs` — stored job posts; `seen = 1` means "delivered to the owner" — flipped either by a successful instant alert (`store_tool.mark_seen` from `process_jobs`) or by the daily digest (`_mark_seen`). The digest only sends `seen = 0` jobs, so it acts as a catch-up for any alert that never delivered.
- `seen_hashes` — MD5 hashes for dedup (both `title+company+contact` structured hashes and `raw:` prefix hashes for web scraper raw text).
- `group_stats` — cumulative message counts per group; drives adaptive mode selection.

Initialize with `python -m db.init_db`. All tables use `CREATE TABLE IF NOT EXISTS` so re-running is safe.

### Tests

**Python (122 tests)** — all run offline. `conftest.py` provides:
- `temp_db` — creates a fresh isolated DB (all three tables), sets `JOBS_DB_PATH` and `CHROMA_DB_PATH`
- `temp_chroma` — sets `CHROMA_DB_PATH` to `tmp_path/chroma`
- `temp_prefs` — writes a minimal `prefs.json` to a temp file and sets `PREFS_PATH`
- `temp_groups` — writes an empty map to a temp file and sets `GROUPS_PATH`
- `temp_demo_users` — writes an empty list to a temp file and sets `DEMO_USERS_PATH` (isolates `/start` tests from the real registry)
- `telegram_owner` — sets `TELEGRAM_CHAT_ID=42` so write commands pass `_is_owner`
- `sample_messages` — loads `tests/sample_messages.json`

Key test patterns:
- Pipeline tests pass scripted JSON to `FakeListChatModel`; separate mode needs two responses (classify, then extract); combined mode needs one.
- To force combined mode in a pipeline test, insert a row into `group_stats` with `total_messages=100, job_post_messages=95`.
- Stats tool tests use `monkeypatch.setenv("JOBS_DB_PATH", ...)` directly.
- Telegram bot command tests patch `telegram_bot._send` to capture replies. Handler modules return strings and never call `_send` directly, so the patch reaches all commands.
- `/ask` quota tests manipulate `telegram_handlers.jobs._ask_counts` directly (moved out of `telegram_bot` during refactor).
- Query tool tests use `temp_db` and insert rows directly via `sqlite3`.
- Ask tool tests inject `FakeListLLM` via the `llm` parameter.
- Vector store tests use `temp_chroma` and inject `_FakeEmbeddingFunction`.
- Store-tool integration tests patch `agent.vector_store.index_job` and `agent.tools.dedup_tool.is_time_duplicate` (source module attributes).
- Time-duplicate tests use `temp_db`; the `within_days` parameter avoids touching the env var.

**Node.js (14 tests)** — Jest tests for `sources/whatsapp/last_seen.js`. Each test uses a unique tmp file path.

## Environment variables

Copy `.env.example` to `.env` and fill in:

| Variable | Required | Purpose |
|---|---|---|
| `LLM_PROVIDER` | No | `anthropic` (default), `openai`, `google`, or `ollama` |
| `LLM_MODEL` | No | Model name — defaults to provider's cheapest/fastest model |
| `ANTHROPIC_API_KEY` | If using Anthropic | Claude API access |
| `OPENAI_API_KEY` | If using OpenAI | OpenAI API access |
| `GOOGLE_API_KEY` | If using Google | Gemini API access |
| `JOBS_DB_PATH` | No | Override DB path (defaults to `db/jobs.db`) |
| `PREFS_PATH` | No | Override prefs file path (defaults to `agent/prefs.json`) |
| `GROUPS_PATH` | No | Override WhatsApp sources file path (defaults to `agent/whatsapp_sources.json`) |
| `CHROMA_DB_PATH` | No | Override ChromaDB persistence directory (defaults to `db/chroma`) |
| `DUPLICATE_WINDOW_DAYS` | No | How many days back to look for same-title+company duplicates (default: 7) |
| `TELEGRAM_BOT_TOKEN` | No | Instant notifications, digest delivery, and bot commands |
| `TELEGRAM_CHAT_ID` | No | Telegram recipient |
| `TELEGRAM_API_ID` | No (Telegram source) | From [my.telegram.org](https://my.telegram.org) → API Development Tools |
| `TELEGRAM_API_HASH` | No (Telegram source) | Same page as `TELEGRAM_API_ID` |
| `TELEGRAM_PHONE` | No (Telegram source) | Phone number for the Telethon userbot, e.g. `+972501234567` |
| `WEB_SCRAPER_INTERVAL_MINUTES` | No | Web scraper poll interval (default: 30) |
| `INGEST_API_URL` | No | Ingest endpoint for WhatsApp/Telegram listeners (default: `http://localhost:8000/ingest`) |
| `RENDER_EXTERNAL_URL` | No (Render only) | Set automatically by Render — enables self-ping every 14 min to prevent free-tier spin-down |
| `LANGSMITH_API_KEY` | No | LangSmith tracing |
| `LANGSMITH_TRACING` | No | Set to `true` to enable tracing |
| `LANGSMITH_ENDPOINT` | No | LangSmith API endpoint (defaults to `https://api.smith.langchain.com`) |
| `LANGSMITH_PROJECT` | No | LangSmith project name (defaults to `default`) |
