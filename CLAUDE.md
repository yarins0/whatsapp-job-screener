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
```

## Architecture

This is a single-user LangChain learning project. Messages flow:

```
WhatsApp (Node.js listener) → POST /ingest (FastAPI) → run_pipeline() → SQLite → Telegram (instant)
                                                                              ↓
                                                              Daily digest (APScheduler)
```

### Core flow: `agent/pipeline.py`

`run_pipeline(message)` is the main entry point. It is `async` and accepts an optional `llm` override (used by tests) and `notify=False` (used by the smoke test to suppress Telegram).

**Adaptive mode selection:** Before classifying, the pipeline calls `get_pipeline_mode(group)` from `agent/tools/stats_tool.py`. Once a group has ≥50 messages and ≥70% are job posts, the mode switches to `"combined"`. Otherwise it uses `"separate"` (the default).

- **Separate mode** — two LLM calls: classifier first, then extractor only if it passes.
- **Combined mode** — one LLM call: `agent/chains/combined.py` classifies and extracts simultaneously, returning `{is_job_post, confidence, jobs: [...]}`.

In both modes, after classification, stats are recorded via `record_message(group, is_job_post)`.

A message may contain multiple job posts. The extractor returns a list; steps 3–6 run as a loop over every job:

3. **Dedup** (`agent/tools/dedup_tool.py`) — atomic `INSERT OR IGNORE` on `seen_hashes` table.
4. **Filter** (`agent/tools/filter_tool.py`) — calls `load_prefs()` from `agent/tools/prefs_tool.py` (reads `agent/prefs.json` on every call so Telegram-bot changes take effect immediately). Returns `(passed, reason)`.
5. **Store** (`agent/tools/store_tool.py`) — inserts into `jobs` table, returns the new row `id`.
6. **Notify** — sends an instant Telegram alert per stored job via `digest.digest._send_telegram`, with two inline buttons: **Block role** (adds lowercased title to blocklist) and **Block city** (only for non-remote jobs with a known location).

### Listener: `listener/listener.js`

On `ready`, the listener reads watched group IDs from `agent/groups.json` (a `{id: name}` map), calls `saveGroupNames()` to resolve and cache display names back into the same file, then calls `catchUp()` for each group. The `message` event handler re-reads `groups.json` on every message so `/addgroup` and `/removegroup` take effect immediately without a restart.

`listener/last_seen.js` — extracted state module (`load`, `save`, `update`). Accepts a custom file path, which is how Jest tests isolate state without touching the real file.

### Telegram bot: `telegram_bot.py`

Long-polls `getUpdates`. Handles inline-button callback queries (`block_role:`, `block_city:`) and text commands: `/help`, `/commands`, `/start`, `/prefs`, `/blockrole`, `/blockcity`, `/addrole`, `/groups`, `/addgroup`, `/removegroup`, `/jobs`. All preference and group mutations delegate to `prefs_tool.py` and `groups_tool.py`. The `/jobs` command delegates to `query_tool.py`.

### Browse jobs: `agent/list_jobs.py` and `agent/tools/query_tool.py`

`query_tool.py` exposes two public functions:
- `query_jobs(days, role, unseen_only, limit)` — queries the `jobs` table with optional filters; returns a list of dicts ordered newest-first.
- `format_jobs_telegram(jobs, days, role, unseen_only)` — renders results in the same Markdown style as the daily digest, respecting Telegram's 4096-char message limit.

`list_jobs.py` is the CLI entry point (`python -m agent.list_jobs`). It calls `query_jobs()` and prints a fixed-width terminal table. Flags: `--days N`, `--role KEYWORD`, `--unseen`, `--limit N`.

The Telegram `/jobs` command uses the same `query_jobs()` + `format_jobs_telegram()` path, so both surfaces always produce consistent results from the same query logic.

### Key design decisions

- **Pipeline is `async/await`, not one LCEL chain** — easier to mock per-step in tests and trace in LangSmith.
- **`llm` is injected** — `_default_llm()` lazy-imports `langchain_anthropic` so tests run offline with `FakeListChatModel`.
- **`JOBS_DB_PATH` env var** — all DB modules (`dedup_tool`, `store_tool`, `stats_tool`, `init_db`) read this env var; the `temp_db` pytest fixture sets it to a `tmp_path` file, isolating test state.
- **`PREFS_PATH` env var** — `prefs_tool` reads this to locate `agent/prefs.json`; the `temp_prefs` fixture overrides it per test.
- **`GROUPS_PATH` env var** — `groups_tool` reads this to locate `agent/groups.json`; the `temp_groups` fixture overrides it per test.
- **Dedup is atomic** — `INSERT OR IGNORE` + `rowcount` check eliminates the SELECT+INSERT race condition.
- **Filter reads prefs on every call** — `filter_tool` calls `load_prefs()` each time so Telegram-bot changes take effect on the next message without a restart.
- **Filter returns `(passed, reason)`** — the rejection reason propagates to the API log and the pipeline result.
- **Extractor returns a list** — the LLM may return one job or several for a message with multiple postings; the pipeline normalises to a list and processes each job independently.
- **Stats are advisory** — `stats_tool` errors are caught and logged as warnings; they never interrupt the pipeline.
- **Combined mode fallback** — if the combined chain fails (e.g. parse error), the pipeline falls back to separate mode automatically.
- **Telegram bot uses long-polling** — no public URL or webhook required; works locally and on a remote server.
- **Model** — `claude-haiku-4-5-20251001` in production (cheap, fast).

### User preferences

Edit `agent/prefs.json` directly, or use Telegram commands (`/blockrole`, `/blockcity`, `/addrole`). The filter reads the file on every call so changes take effect on the next incoming message without a restart.

Fields: `roles` (keyword allow-list on title+summary), `blocklist` (auto-reject keywords on title/summary/skills), `location_blocklist` (cities to reject — everything else passes, remote always passes).

`agent/memory.py` now only contains the `UserPreferences` TypedDict — it is no longer the source of truth for preferences.

### Watched groups

`agent/groups.json` — a `{group_id: display_name}` map. Add/remove entries directly or via `/addgroup` / `/removegroup` in Telegram. The listener re-reads this file on every message event so removals and additions of groups take effect immediately; catch-up replay for newly added groups happens on the next listener restart.

### Database

`db/schema.sql` defines three tables:
- `jobs` — stored job posts; `seen` column flipped to `1` after the digest runs.
- `seen_hashes` — MD5 hashes of `title+company+contact` for dedup.
- `group_stats` — cumulative `total_messages` / `job_post_messages` per group; drives adaptive mode selection.

Initialize with `python -m db.init_db`. All tables use `CREATE TABLE IF NOT EXISTS` so re-running is safe.

### Tests

**Python (87 tests)** — all run offline. `conftest.py` provides:
- `temp_db` — creates a fresh isolated DB (all three tables) and sets `JOBS_DB_PATH`
- `temp_prefs` — writes a minimal `prefs.json` to a temp file and sets `PREFS_PATH`
- `temp_groups` — writes an empty `groups.json` map to a temp file and sets `GROUPS_PATH`
- `sample_messages` — loads `tests/sample_messages.json`

Key test patterns:
- Pipeline tests pass scripted JSON to `FakeListChatModel`; separate mode needs two responses (classify, then extract); combined mode needs one combined response.
- To force combined mode in a pipeline test, insert a row into `group_stats` with `total_messages=100, job_post_messages=95` before calling `run_pipeline`.
- Stats tool tests use `monkeypatch.setenv("JOBS_DB_PATH", ...)` directly, bypassing the `temp_db` fixture where needed.
- Telegram bot command tests use `unittest.mock.patch("telegram_bot._send")` to capture replies without making real API calls.
- Query tool tests use the `temp_db` fixture and insert rows directly via `sqlite3` to avoid going through the pipeline.

**Node.js (7 tests)** — Jest tests for `listener/last_seen.js`. Each test uses a unique tmp file path so tests never touch the real state file.

## Environment variables

Copy `.env.example` to `.env` and fill in:

| Variable | Required | Purpose |
|---|---|---|
| `ANTHROPIC_API_KEY` | Yes (live only) | Claude API access |
| `JOBS_DB_PATH` | No | Override DB path (defaults to `db/jobs.db`) |
| `PREFS_PATH` | No | Override prefs file path (defaults to `agent/prefs.json`) |
| `GROUPS_PATH` | No | Override groups file path (defaults to `agent/groups.json`) |
| `TELEGRAM_BOT_TOKEN` | No | Instant notifications, digest delivery, and bot commands |
| `TELEGRAM_CHAT_ID` | No | Telegram recipient |
| `LANGCHAIN_API_KEY` | No | LangSmith tracing |
| `LANGCHAIN_TRACING` | No | Set to `true` to enable tracing |
