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
4. **Filter** (`agent/tools/filter_tool.py`) — matches against `USER_PREFS` in `agent/memory.py`. Returns `(passed, reason)`.
5. **Store** (`agent/tools/store_tool.py`) — inserts into `jobs` table, returns the new row `id`.
6. **Notify** — sends an instant Telegram alert per stored job via `digest.digest._send_telegram`.

### Listener: `listener/listener.js`

On `ready`, the listener calls `catchUp()` for each watched group — fetching the last 100 messages and replaying any newer than the timestamp stored in `listener/.last_seen.json`. This recovers missed messages after a computer sleep or disconnect. The timestamp is updated on every live message and after each catch-up.

`listener/last_seen.js` — extracted state module (`load`, `save`, `update`). Accepts a custom file path, which is how Jest tests isolate state without touching the real file.

### Key design decisions

- **Pipeline is `async/await`, not one LCEL chain** — easier to mock per-step in tests and trace in LangSmith.
- **`llm` is injected** — `_default_llm()` lazy-imports `langchain_anthropic` so tests run offline with `FakeListChatModel`.
- **`JOBS_DB_PATH` env var** — all DB modules (`dedup_tool`, `store_tool`, `stats_tool`, `init_db`) read this env var; the `temp_db` pytest fixture sets it to a `tmp_path` file, isolating test state.
- **Dedup is atomic** — `INSERT OR IGNORE` + `rowcount` check eliminates the SELECT+INSERT race condition.
- **Filter returns `(passed, reason)`** — the rejection reason propagates to the API log and the pipeline result.
- **Extractor returns a list** — the LLM may return one job or several for a message with multiple postings; the pipeline normalises to a list and processes each job independently.
- **Stats are advisory** — `stats_tool` errors are caught and logged as warnings; they never interrupt the pipeline.
- **Combined mode fallback** — if the combined chain fails (e.g. parse error), the pipeline falls back to separate mode automatically.
- **Model** — `claude-haiku-4-5-20251001` in production (cheap, fast).

### User preferences

Edit `agent/memory.py` → `USER_PREFS` dict to change which jobs are kept. Fields: `roles` (keyword allow-list on title+summary), `blocklist` (auto-reject keywords on title/summary/skills), `location_blocklist` (cities to reject — everything else passes, remote always passes).

### Database

`db/schema.sql` defines three tables:
- `jobs` — stored job posts; `seen` column flipped to `1` after the digest runs.
- `seen_hashes` — MD5 hashes of `title+company+contact` for dedup.
- `group_stats` — cumulative `total_messages` / `job_post_messages` per group; drives adaptive mode selection.

Initialize with `python -m db.init_db`. All tables use `CREATE TABLE IF NOT EXISTS` so re-running is safe.

### Tests

**Python (39 tests)** — all run offline. `conftest.py` provides:
- `temp_db` — creates a fresh isolated DB (all three tables) and sets `JOBS_DB_PATH`
- `sample_messages` — loads `tests/sample_messages.json`

Key test patterns:
- Pipeline tests pass scripted JSON to `FakeListChatModel`; separate mode needs two responses (classify, then extract); combined mode needs one combined response.
- To force combined mode in a pipeline test, insert a row into `group_stats` with `total_messages=100, job_post_messages=95` before calling `run_pipeline`.
- Stats tool tests use `monkeypatch.setenv("JOBS_DB_PATH", ...)` directly, bypassing the `temp_db` fixture where needed.

**Node.js (7 tests)** — Jest tests for `listener/last_seen.js`. Each test uses a unique tmp file path so tests never touch the real state file.

## Environment variables

Copy `.env.example` to `.env` and fill in:

| Variable | Required | Purpose |
|---|---|---|
| `ANTHROPIC_API_KEY` | Yes (live only) | Claude API access |
| `JOBS_DB_PATH` | No | Override DB path (defaults to `db/jobs.db`) |
| `WATCHED_GROUPS` | No | Comma-separated WhatsApp group IDs for listener |
| `TELEGRAM_BOT_TOKEN` | No | Instant notifications + digest delivery |
| `TELEGRAM_CHAT_ID` | No | Telegram recipient |
| `LANGCHAIN_API_KEY` | No | LangSmith tracing |
| `LANGCHAIN_TRACING_V2` | No | Set to `true` to enable tracing |
