# Job Screening Agent

A LangChain-powered agent that monitors multiple job sources — WhatsApp groups,
Telegram channels, and job boards — identifies job postings, extracts structured data,
filters by your preferences, and delivers instant Telegram alerts and a daily digest.

Built as a hands-on LangChain learning project — every major LangChain primitive is used somewhere.

---

## Learning goals (LangChain concepts covered)

| Concept | Where it's used |
|---|---|
| `ChatPromptTemplate` | Formatting system + human turns sent to the LLM |
| LCEL pipe operator (`\|`) | `prompt \| llm \| parser` chains in classifier and extractor |
| `JsonOutputParser` + Pydantic | Parsing LLM JSON output into typed dicts |
| `BaseLanguageModel` injection | Swapping real LLM for `FakeListChatModel` in tests |
| `AgentExecutor` / Tools | Filter, dedup, store tools called in pipeline |
| `WebBaseLoader` | Loading job board pages in the web scraper sources |
| LangSmith tracing | Observability — every chain call is visible at smith.langchain.com |

---

## Architecture

```
WhatsApp Groups
      │
      ▼
┌─────────────────────┐
│  Listener Layer     │  whatsapp-web.js (Node.js)
│  (listener.js)      │  Connects via QR, watches named groups
│                     │  Replays up to 48 h of missed messages on reconnect
│                     │  Heartbeat detects silent disconnects after PC sleep
└────────┬────────────┘
         │ HTTP POST (raw message JSON)
         ▼
┌─────────────────────┐
│  FastAPI Ingest     │  POST /ingest  →  run_pipeline()
│  (api/main.py)      │  GET  /healthz
└────────┬────────────┘
         │
         ▼
┌─────────────────────────────────────────────────┐
│              LangChain Pipeline                  │
│              (agent/pipeline.py)                 │
│                                                  │
│  Adaptive mode (per group, auto-selected):       │
│  • separate  — classify then extract (2 calls)   │
│  • combined  — classify+extract in 1 call        │
│    (auto-switches when group ≥70% job posts)     │
│                                                  │
│  Per extracted job (supports multi-job messages):│
│  3. Dedup tool  →  hash check (SQLite)           │
│  4. Filter tool →  match agent/prefs.json        │
│  5. Store tool  →  insert into jobs.db           │
│  6. Notify      →  instant Telegram alert        │
│                    + Block role / Block city      │
│                      inline buttons              │
└────────┬────────────────────────────────────────┘
         │
         ▼
┌─────────────────────┐     ┌──────────────────────┐
│  Digest Scheduler   │     │  Telegram Bot         │
│  (digest/digest.py) │     │  (telegram_bot.py)    │
│  APScheduler cron   │     │  Long-polls updates;  │
│  Daily summary via  │     │  handles button       │
│  Telegram or stdout │     │  callbacks + commands │
└─────────────────────┘     └──────────────────────┘
```

---

## Tech stack

| Layer | Technology |
|---|---|
| WhatsApp listener | Node.js + `whatsapp-web.js` |
| API / ingest | Python + FastAPI |
| LLM framework | LangChain (Python) |
| LLM model | Configurable via `LLM_PROVIDER` + `LLM_MODEL` env vars (default: Claude Haiku) |
| Database | SQLite (three tables: `jobs`, `seen_hashes`, `group_stats`) |
| Scheduler | APScheduler |
| Notifications | Telegram Bot API (falls back to stdout) |
| Observability | LangSmith (free tier) |

---

## Getting started

See **[docs/SETUP.md](docs/SETUP.md)** for the full setup guide — prerequisites, environment variables, WhatsApp auth, Telegram configuration, LLM provider selection, and LangSmith observability.

---

## Tests

**Python (74 tests)** — run offline, no API key needed. The LLM is replaced with `FakeListChatModel` using scripted JSON responses.

```bash
pytest tests/ -v                                              # all tests
pytest tests/test_pipeline.py -v                             # single file
pytest tests/test_pipeline.py::test_stores_a_qualified_job   # single test
python -m agent.pipeline                                      # live smoke test (needs API key)
```

**Node.js (7 tests)** — Jest tests for `listener/last_seen.js`, the per-group timestamp module used by the catch-up mechanism. Each test uses a unique tmp file path so tests never touch the real state file.

```bash
npm test
```

---

## Project layout

```
agent/
  pipeline.py        Main async orchestrator — start reading here
  prefs.json         User preferences (roles, blocklist, location_blocklist) — edit directly or via bot
  groups.json        Watched WhatsApp group IDs and display names — edit directly or via bot
  memory.py          UserPreferences TypedDict (type definition only)
  chains/
    classifier.py    LCEL chain: is_job_post + confidence (separate mode)
    extractor.py     LCEL chain: list of JobPost dicts (separate mode)
    combined.py      LCEL chain: classify+extract in one call (combined mode)
  list_jobs.py       CLI: python -m agent.list_jobs [--days N] [--role KW] [--unseen] [--limit N]
  tools/
    filter_tool.py   Match job against prefs.json
    dedup_tool.py              Hash-based dedup; is_duplicate() (structured) + is_raw_duplicate() (pre-LLM)
    store_tool.py              Insert job into jobs table
    stats_tool.py              Per-group job-post rate tracking; drives adaptive mode
    prefs_tool.py              load_prefs(), add_to_blocklist(), add_to_location_blocklist(), add_to_roles()
    groups_tool.py             load_groups(), add_group(), remove_group()
    telegram_sources_tool.py   load_sources(), add_source(), remove_source() for telegram_sources.json
    query_tool.py              query_jobs() + format_jobs_telegram(); shared by CLI and /jobs bot command

api/
  main.py            FastAPI: POST /ingest, GET /healthz

sources/
  whatsapp/
    listener.js      whatsapp-web.js client; reads groups.json; QR auth, heartbeat, catch-up replay
    last_seen.js     Per-group timestamp persistence (path-injectable for tests)
  telegram/
    listener.py      Telethon userbot; watches channels in telegram_sources.json; catch-up replay
                     Requires one-time interactive auth (see Step 6 in setup guide)
  web/
    listener.py      APScheduler-based poller; calls is_raw_duplicate() before forwarding to ingest
    scrapers/
      alljobs.py     AllJobs.co.il scraper (currently disabled — enable in agent/web_sources.json)

digest/
  digest.py          APScheduler cron + format_digest() (pure, testable)

telegram_bot.py      Long-polls Telegram; handles feedback buttons + commands:
                     /help /commands /prefs
                     /blockrole /blockcity /addrole
                     /groups /addgroup /removegroup
                     /tgsources /addtgsource /removetgsource
                     /jobs [keyword] [unseen]

db/
  schema.sql         SQLite schema (jobs, seen_hashes, group_stats)
  init_db.py         python -m db.init_db

tests/
  conftest.py        temp_db, temp_prefs, temp_groups fixtures + sample_messages
  sample_messages.json
```

---

## Phase 2 ideas

- **LangGraph** — convert the pipeline to a stateful graph for retries and branching
- **Conversational interface** — `ConversationChain` so you can query: *"show me remote Python jobs from this week"*
- **Vector search** — `Chroma` or `FAISS` to find similar jobs you've seen before
- ~~**Feedback loop**~~ — ✅ implemented: inline buttons on notifications + Telegram commands to manage preferences and groups
- ~~**Additional sources (Telegram)**~~ — ✅ implemented: Telethon userbot watches any channel the account is a member of; manage via `/tgsources` bot commands
- **Additional sources (LinkedIn / job boards)** — re-enable web scrapers with site-native filters once confirmed URLs are available
- ~~**Browse jobs CLI**~~ — ✅ implemented: `python -m agent.list_jobs` + `/jobs` Telegram command
