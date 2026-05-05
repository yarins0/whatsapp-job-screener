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

3. **Dedup** (`agent/tools/dedup_tool.py`) — atomic `INSERT OR IGNORE` on `seen_hashes` table.
4. **Filter** (`agent/tools/filter_tool.py`) — calls `load_prefs()` from `agent/tools/prefs_tool.py` (reads `agent/prefs.json` on every call so Telegram-bot changes take effect immediately). Returns `(passed, reason)`.
5. **Store** (`agent/tools/store_tool.py`) — checks for near-duplicate via the vector index, then inserts into `jobs` table. Returns the new row `id`, or `None` if the job was suppressed as a near-duplicate.
6. **Notify** — sends an instant Telegram alert per stored job via `digest.digest._send_telegram`, with two inline buttons: **Block role** (adds lowercased title to blocklist) and **Block city** (only for non-remote jobs with a known location).

### Listener: `listener/listener.js`

On `ready`, the listener reads watched group IDs from `agent/groups.json` (a `{id: name}` map), calls `saveGroupNames()` to resolve and cache display names back into the same file, then calls `catchUp()` for each group. The `message` event handler re-reads `groups.json` on every message so `/addgroup` and `/removegroup` take effect immediately without a restart.

`listener/last_seen.js` — extracted state module (`load`, `save`, `update`). Accepts a custom file path, which is how Jest tests isolate state without touching the real file.

### Telegram bot: `telegram_bot.py`

Long-polls `getUpdates`. Handles inline-button callback queries (`block_role:`, `block_city:`) and text commands: `/help`, `/commands`, `/start`, `/prefs`, `/blockrole`, `/blockcity`, `/addrole`, `/listgroups`, `/groups`, `/addgroup`, `/removegroup`, `/tgsources`, `/addtgsource`, `/removetgsource`, `/jobs`, `/ask`, `/similar`, `/reindex`. All preference and group mutations delegate to `prefs_tool.py` and `groups_tool.py`. The `/jobs` command delegates to `query_tool.py`. The `/ask` command delegates to `ask_tool.ask_jobs()`. The `/similar` command delegates to `vector_store.find_similar()`. The `/reindex` command (owner-only) calls `vector_store.reindex_all()` to back-fill the ChromaDB index from SQLite history.

Access control: the owner is identified by `TELEGRAM_CHAT_ID`. Write/discovery commands (`/blockrole`, `/blockcity`, `/addrole`, `/listgroups`, `/addgroup`, `/removegroup`, `/addtgsource`, `/removetgsource`) are owner-only. Read commands (`/prefs`, `/groups`, `/tgsources`, `/jobs`, `/ask`) are available to all users, but `/ask` is rate-limited for non-owners via the module-level `_ask_counts` dict and `_ASK_DEMO_LIMIT` constant (default 3 per session).

`/listgroups` reads `agent/all_whatsapp_groups.json` — a snapshot written by `listener.js` on every `ready` event. The file is gitignored. If it doesn't exist (listener has never connected), the bot tells the user to start the listener first. Watched groups are marked with ✓.

### Browse and query jobs: `agent/list_jobs.py`, `agent/tools/query_tool.py`, and `agent/tools/ask_tool.py`

`query_tool.py` exposes two public functions:
- `query_jobs(days, role, unseen_only, limit)` — queries the `jobs` table with optional filters; returns a list of dicts ordered newest-first.
- `format_jobs_telegram(jobs, days, role, unseen_only)` — renders results in the same Markdown style as the daily digest, respecting Telegram's 4096-char message limit.

`list_jobs.py` is the CLI entry point (`python -m agent.list_jobs`). It calls `query_jobs()` and prints a fixed-width terminal table. Flags: `--days N`, `--role KEYWORD`, `--unseen`, `--limit N`.

The Telegram `/jobs` command uses the same `query_jobs()` + `format_jobs_telegram()` path, so both surfaces always produce consistent results from the same query logic.

`ask_tool.py` exposes one public function:
- `ask_jobs(question, *, llm=None) -> str` — builds an LCEL chain (`prompt | llm | JsonOutputParser`) that extracts `{days, role, unseen_only, limit}` from a natural-language question, then calls `query_jobs()` and returns a formatted string. Falls back to a keyword search on the raw question if the LLM response cannot be parsed. The `llm` parameter is injectable so tests can pass `FakeListLLM` and run offline.

### Vector search: `agent/vector_store.py`

`vector_store.py` exposes four public functions:
- `index_job(job_id, job, *, embedding_fn=None)` — embeds `"{title} at {company} — {summary} — Skills: …"` and upserts into a ChromaDB collection keyed by SQLite row id. Called automatically by `store_tool.store_job()` after every insert (errors are non-fatal).
- `find_similar(text, n=5, *, embedding_fn=None)` — nearest-neighbour query in Chroma; fetches full job rows from SQLite for the returned ids; returns them in similarity order.
- `is_near_duplicate(job, distance_threshold=0.3, *, embedding_fn=None) -> bool` — builds the same document text as `index_job` and queries for the single closest neighbour. Returns `True` if the L2 distance is ≤ `distance_threshold` (~95 % cosine similarity for normalised embeddings). Returns `False` when the index is empty. Called by `store_tool` before every `INSERT` to suppress cross-source near-duplicates.
- `reindex_all(*, embedding_fn=None) -> int` — reads every row from `jobs` and upserts each into Chroma. Idempotent (safe to re-run). Returns the count of jobs processed. Exposed via the owner-only `/reindex` Telegram command.

**Persistence:** ChromaDB writes to `db/chroma/` by default, configurable via the `CHROMA_DB_PATH` env var. The `temp_chroma` fixture (defined in `conftest.py`) points it at `tmp_path/chroma`.

**Embedding model:** ChromaDB's default (`all-MiniLM-L6-v2` from `sentence-transformers`) — downloaded ~80 MB on first use, then runs offline. In tests, a `_FakeEmbeddingFunction` (deterministic, 8-dim vectors) is injected to avoid any download.

**Graceful degradation:** if `chromadb` is not installed, all four functions are no-ops / return safe defaults (0 or `[]` or `False`).

### Key design decisions

- **Pipeline is a LangGraph `StateGraph`** — `agent/graph.py` defines nodes and conditional edges; `pipeline.py` delegates via `ainvoke`. Easier to extend, visualise, and add per-node retries than a flat `if/elif` body.
- **`llm` is injected** — `_default_llm()` lazy-imports `langchain_anthropic` so tests run offline with `FakeListChatModel`.
- **`JOBS_DB_PATH` env var** — all DB modules (`dedup_tool`, `store_tool`, `stats_tool`, `init_db`) read this env var; the `temp_db` pytest fixture sets it to a `tmp_path` file, isolating test state.
- **`PREFS_PATH` env var** — `prefs_tool` reads this to locate `agent/prefs.json`; the `temp_prefs` fixture overrides it per test.
- **`GROUPS_PATH` env var** — `groups_tool` reads this to locate `agent/groups.json`; the `temp_groups` fixture overrides it per test.
- **`CHROMA_DB_PATH` env var** — `vector_store` reads this to locate the ChromaDB persistence directory; the `temp_chroma` fixture (in `conftest.py`) overrides it per test. `temp_db` also sets `CHROMA_DB_PATH` so any test using a temp SQLite DB automatically gets an isolated Chroma too.
- **Vector operations are non-fatal** — `store_tool._try_index_job` and `_try_is_near_duplicate` both wrap their Chroma calls in try/except so a missing or broken Chroma installation never stops a job from being stored in SQLite.
- **Near-duplicate dedup** — `is_near_duplicate` returns `False` when the index is empty, so the very first posting of any text is never suppressed. The L2 `distance_threshold=0.3` corresponds to ~95 % cosine similarity for the `all-MiniLM-L6-v2` embeddings.
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

**Python (118 tests)** — all run offline. `conftest.py` provides:
- `temp_db` — creates a fresh isolated DB (all three tables), sets `JOBS_DB_PATH` **and** `CHROMA_DB_PATH` (so any test that calls `store_job` never touches the real vector index)
- `temp_chroma` — sets `CHROMA_DB_PATH` to `tmp_path/chroma`; use in tests that need Chroma without a full DB
- `temp_prefs` — writes a minimal `prefs.json` to a temp file and sets `PREFS_PATH`
- `temp_groups` — writes an empty `whatsapp_sources.json` map to a temp file and sets `GROUPS_PATH`
- `telegram_owner` — sets `TELEGRAM_CHAT_ID=42` so write commands pass `_is_owner`; needed by all write-command bot tests
- `sample_messages` — loads `tests/sample_messages.json`

Key test patterns:
- Pipeline tests pass scripted JSON to `FakeListChatModel`; separate mode needs two responses (classify, then extract); combined mode needs one combined response.
- To force combined mode in a pipeline test, insert a row into `group_stats` with `total_messages=100, job_post_messages=95` before calling `run_pipeline`.
- Stats tool tests use `monkeypatch.setenv("JOBS_DB_PATH", ...)` directly, bypassing the `temp_db` fixture where needed.
- Telegram bot command tests use `unittest.mock.patch("telegram_bot._send")` to capture replies without making real API calls.
- Query tool tests use the `temp_db` fixture and insert rows directly via `sqlite3` to avoid going through the pipeline.
- Ask tool tests inject `FakeListLLM` (scripted JSON responses) via the `llm` parameter; the bot handler tests patch `agent.tools.ask_tool._default_llm` to return the fake. Demo rate-limit tests manipulate `telegram_bot._ask_counts` directly.
- Vector store tests (`test_vector_store.py`) use `temp_chroma` (from `conftest.py`) and inject a `_FakeEmbeddingFunction` to avoid downloading the sentence-transformer model. Store-tool integration tests patch `agent.vector_store.index_job` and `agent.vector_store.is_near_duplicate` (source module attributes, not local imports) to verify call-through behaviour.

**Node.js (7 tests)** — Jest tests for `sources/whatsapp/last_seen.js`. Each test uses a unique tmp file path so tests never touch the real state file.

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
| `LANGSMITH_API_KEY` | No | LangSmith tracing |
| `LANGSMITH_TRACING` | No | Set to `true` to enable tracing |
| `LANGSMITH_ENDPOINT` | No | LangSmith API endpoint (defaults to `https://api.smith.langchain.com`) |
| `LANGSMITH_PROJECT` | No | LangSmith project name (defaults to `default`) |
