# WhatsApp Job Screener

A LangChain-powered agent that monitors WhatsApp groups, identifies job postings,
extracts structured data, filters by your preferences, and ships you a daily digest.

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
│  1. Classifier chain  →  is_job_post + confidence│
│     (drops if confidence < 0.6)                  │
│                                                  │
│  2. Extractor chain   →  title, company,         │
│     location, skills, salary, remote, contact    │
│                                                  │
│  3. Dedup tool        →  hash check (SQLite)     │
│                                                  │
│  4. Filter tool       →  match USER_PREFS        │
│                                                  │
│  5. Store tool        →  insert into jobs.db     │
└────────┬────────────────────────────────────────┘
         │
         ▼
┌─────────────────────┐
│  Digest Scheduler   │  APScheduler cron @ 8am
│  (digest/digest.py) │  Formats unseen jobs, sends
│                     │  via Telegram (or stdout)
└─────────────────────┘
```

---

## Tech stack

| Layer | Technology |
|---|---|
| WhatsApp listener | Node.js + `whatsapp-web.js` |
| API / ingest | Python + FastAPI |
| LLM framework | LangChain (Python) |
| LLM model | `claude-haiku-4-5-20251001` via `langchain-anthropic` |
| Database | SQLite (two tables: `jobs`, `seen_hashes`) |
| Scheduler | APScheduler |
| Notifications | Telegram Bot API (falls back to stdout) |
| Observability | LangSmith (free tier) |

---

## Quick start

```bash
# 1. Python environment
python -m venv venv
venv\Scripts\activate          # Mac/Linux: source venv/bin/activate
pip install -r requirements.txt

# 2. Node environment (only needed for the WhatsApp listener)
npm install

# 3. Secrets
copy .env.example .env
# Fill in ANTHROPIC_API_KEY (and optionally LangSmith / Telegram keys)

# 4. Initialize the database
python -m db.init_db

# 5. Run the pieces (each in its own terminal)
uvicorn api.main:app --reload --port 8000   # FastAPI ingest
python -m digest.digest                      # Daily digest scheduler
node listener/listener.js                    # WhatsApp listener (scan QR)
```

---

## Environment variables

| Variable | Required | Purpose |
|---|---|---|
| `ANTHROPIC_API_KEY` | Yes (live only) | Claude API access |
| `JOBS_DB_PATH` | No | Override DB path (defaults to `db/jobs.db`) |
| `WATCHED_GROUPS` | No | Comma-separated WhatsApp group names (e.g. `"Jobs IL,Tech Jobs TLV"`) |
| `TELEGRAM_BOT_TOKEN` | No | Digest delivery via Telegram |
| `TELEGRAM_CHAT_ID` | No | Telegram recipient chat ID |
| `LANGCHAIN_API_KEY` | No | LangSmith tracing |
| `LANGCHAIN_TRACING_V2` | No | Set `true` to enable LangSmith tracing |

---

## Configuring your preferences

Edit `agent/memory.py` → `USER_PREFS`:

```python
USER_PREFS = {
    "roles": ["backend", "python", "node", "fullstack", "senior"],
    "blocklist": ["unpaid", "volunteer", "internship"],
    "locations": ["tel aviv", "remote", "tlv", "herzliya"],
    "min_salary": None,
}
```

- `roles` — title/summary must contain at least one keyword to be kept
- `blocklist` — any match auto-rejects the post
- `locations` — job must be remote or match a location keyword

Edit `WATCHED_GROUPS` in `listener/listener.js` (or set the env var) to the actual WhatsApp group names on your phone.

---

## Tests

All 20 tests run offline — no API key needed. The LLM is replaced with `FakeListChatModel` using scripted JSON responses.

```bash
# Run all tests
pytest tests/ -v

# Run a single file
pytest tests/test_pipeline.py -v

# Run a single test
pytest tests/test_pipeline.py::test_stores_a_qualified_job -v

# Live smoke test (requires ANTHROPIC_API_KEY)
python -m agent.pipeline
```

---

## LangSmith observability

Set `LANGCHAIN_TRACING_V2=true` and `LANGCHAIN_API_KEY` in `.env`. Every chain invocation appears at [smith.langchain.com](https://smith.langchain.com) — you can inspect the exact prompt, LLM response, latency, and token cost per step. Useful for debugging classifier confidence and extractor output.

---

## Project layout

```
agent/
  pipeline.py        Main async orchestrator — start reading here
  chains/
    classifier.py    LCEL chain: is_job_post + confidence
    extractor.py     LCEL chain: structured JobPost fields
  tools/
    filter_tool.py   Match job against USER_PREFS
    dedup_tool.py    Hash-based dedup via seen_hashes table
    store_tool.py    Insert job into jobs table
  memory.py          USER_PREFS dict (edit this)

api/
  main.py            FastAPI: POST /ingest, GET /healthz

listener/
  listener.js        whatsapp-web.js client; QR auth

digest/
  digest.py          APScheduler cron + format_digest() (pure, testable)

db/
  schema.sql         SQLite schema (jobs + seen_hashes)
  init_db.py         python -m db.init_db

tests/
  conftest.py        temp_db fixture + sample_messages fixture
  sample_messages.json
```

---

## Phase 2 ideas

- **LangGraph** — convert the pipeline to a stateful graph for retries and branching
- **Conversational interface** — `ConversationChain` so you can query: *"show me remote Python jobs from this week"*
- **Vector search** — `Chroma` or `FAISS` to find similar jobs you've seen before
- **Feedback loop** — thumbs-up/down on digest items to refine `USER_PREFS` automatically
- **Additional sources** — `langchain_community.document_loaders` for Telegram channels or LinkedIn
- **Browse jobs CLI** — `python -m agent.list_jobs` to review stored jobs from the terminal
# whatsapp-job-screener
