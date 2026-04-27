# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Set up Python environment
python -m venv venv && venv\Scripts\activate
pip install -r requirements.txt

# Initialize the SQLite database
python -m db.init_db

# Run all tests (no API key needed — uses FakeListChatModel)
pytest tests/ -v

# Run a single test file
pytest tests/test_pipeline.py -v

# Run a single test by name
pytest tests/test_pipeline.py::test_stores_a_qualified_job -v

# Live smoke test (requires ANTHROPIC_API_KEY in .env)
python -m agent.pipeline

# Start the FastAPI server
uvicorn api.main:app --reload --port 8000

# Start the daily digest scheduler
python -m digest.digest

# Start the WhatsApp listener (Node.js, separate terminal)
node listener/listener.js
```

## Architecture

This is a single-user LangChain learning project. Messages flow:

```
WhatsApp (Node.js listener) → POST /ingest (FastAPI) → run_pipeline() → SQLite
                                                                              ↓
                                                          Daily digest at 8am (APScheduler)
```

### Core flow: `agent/pipeline.py`

`run_pipeline(message)` is the main entry point. It is `async` and accepts an optional `llm` override (used by tests). The pipeline runs 5 sequential steps:

1. **Classifier** (`agent/chains/classifier.py`) — LCEL chain (`prompt | llm | parser`). Returns `{is_job_post, confidence}`. Jobs with `confidence < 0.6` are dropped.
2. **Extractor** (`agent/chains/extractor.py`) — LCEL chain. Returns a `JobPost` dict with title, company, location, skills, etc.
3. **Dedup** (`agent/tools/dedup_tool.py`) — hashes `title+company+contact`, checks `seen_hashes` table.
4. **Filter** (`agent/tools/filter_tool.py`) — matches against `USER_PREFS` in `agent/memory.py`.
5. **Store** (`agent/tools/store_tool.py`) — inserts into `jobs` table, returns the new row `id`.

### Key design decisions

- **Pipeline is `async/await`, not one LCEL chain** — easier to mock per-step in tests and trace in LangSmith.
- **`llm` is injected** — `_default_llm()` lazy-imports `langchain_anthropic` so tests run offline with `FakeListChatModel`.
- **`JOBS_DB_PATH` env var** — all three DB modules (`dedup_tool`, `store_tool`, `init_db`) read this env var; the `temp_db` pytest fixture sets it to a `tmp_path` file, isolating test state.
- **Model** — `claude-haiku-4-5-20251001` in production (cheap, fast).

### User preferences

Edit `agent/memory.py` → `USER_PREFS` dict to change which jobs are kept. Fields: `roles` (keyword allow-list on title+summary), `blocklist` (auto-reject keywords), `locations`, `min_salary`.

### Database

`db/schema.sql` defines two tables: `jobs` and `seen_hashes`. Initialize with `python -m db.init_db`. The `jobs.seen` column is flipped to `1` after the digest runs.

### Tests

All 20 tests run offline. `conftest.py` provides two fixtures:
- `temp_db` — creates a fresh isolated DB and sets `JOBS_DB_PATH`
- `sample_messages` — loads `tests/sample_messages.json`

Pipeline tests pass scripted JSON strings to `FakeListChatModel` to simulate classifier and extractor responses in sequence.

## Environment variables

Copy `.env.example` to `.env` and fill in:

| Variable | Required | Purpose |
|---|---|---|
| `ANTHROPIC_API_KEY` | Yes (live only) | Claude API access |
| `JOBS_DB_PATH` | No | Override DB path (defaults to `db/jobs.db`) |
| `WATCHED_GROUPS` | No | Comma-separated WhatsApp group names for listener |
| `TELEGRAM_BOT_TOKEN` | No | Digest delivery via Telegram |
| `TELEGRAM_CHAT_ID` | No | Telegram recipient |
| `LANGCHAIN_API_KEY` | No | LangSmith tracing |
| `LANGCHAIN_TRACING_V2` | No | Set to `true` to enable tracing |

## Current status

Scaffolding complete. 20 tests pass offline. No live run against Claude has been done yet. `USER_PREFS` in `agent/memory.py` has placeholder values — edit before running live.

**Suggested next steps:**
- Edit `USER_PREFS` for real preferences
- Set `WATCHED_GROUPS` or edit `listener/listener.js` with actual group names
- Run live smoke test: `python -m agent.pipeline` (needs `ANTHROPIC_API_KEY`)
- Confirm LangSmith traces appear at smith.langchain.com
