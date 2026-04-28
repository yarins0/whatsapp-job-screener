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

`run_pipeline(message)` is the main entry point. It is `async` and accepts an optional `llm` override (used by tests) and `notify=False` (used by the smoke test to suppress Telegram). The pipeline runs 6 sequential steps:

1. **Classifier** (`agent/chains/classifier.py`) — LCEL chain (`prompt | llm | parser`). Returns `{is_job_post, confidence}`. Jobs with `confidence < 0.6` are dropped.
2. **Extractor** (`agent/chains/extractor.py`) — LCEL chain. Returns a `JobPost` dict with title, company, location, skills, etc.
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
- **`JOBS_DB_PATH` env var** — all three DB modules (`dedup_tool`, `store_tool`, `init_db`) read this env var; the `temp_db` pytest fixture sets it to a `tmp_path` file, isolating test state.
- **Dedup is atomic** — `INSERT OR IGNORE` + `rowcount` check eliminates the SELECT+INSERT race condition.
- **Filter returns `(passed, reason)`** — the rejection reason propagates to the API log and the pipeline result.
- **Model** — `claude-haiku-4-5-20251001` in production (cheap, fast).

### User preferences

Edit `agent/memory.py` → `USER_PREFS` dict to change which jobs are kept. Fields: `roles` (keyword allow-list on title+summary), `blocklist` (auto-reject keywords on title/summary/skills), `location_blocklist` (cities to reject — everything else passes, remote always passes).

### Database

`db/schema.sql` defines two tables: `jobs` and `seen_hashes`. Initialize with `python -m db.init_db`. The `jobs.seen` column is flipped to `1` after the digest runs.

### Tests

**Python (22 tests)** — all run offline. `conftest.py` provides:
- `temp_db` — creates a fresh isolated DB and sets `JOBS_DB_PATH`
- `sample_messages` — loads `tests/sample_messages.json`

Pipeline tests pass scripted JSON strings to `FakeListChatModel` to simulate classifier and extractor responses in sequence.

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
