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
│  4. Filter tool →  match USER_PREFS              │
│  5. Store tool  →  insert into jobs.db           │
│  6. Notify      →  instant Telegram alert        │
└────────┬────────────────────────────────────────┘
         │
         ▼
┌─────────────────────┐
│  Digest Scheduler   │  APScheduler cron (daily)
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
| Database | SQLite (three tables: `jobs`, `seen_hashes`, `group_stats`) |
| Scheduler | APScheduler |
| Notifications | Telegram Bot API (falls back to stdout) |
| Observability | LangSmith (free tier) |

---

## Setup guide

### Prerequisites

- **Python 3.10+** and **Node.js 18+**
- A phone with WhatsApp installed (to scan the QR code on first run)
- An [Anthropic API key](https://console.anthropic.com/) — the agent uses Claude Haiku, which is cheap (~$0.001 per message screened)
- *(Optional)* A Telegram account for instant notifications and the daily digest

---

### Step 1 — Install dependencies

```bash
# Clone the repo
git clone <repo-url>
cd job-screening-agent

# Python environment
python -m venv venv
venv\Scripts\activate        # Windows
# source venv/bin/activate   # Mac / Linux

pip install -r requirements.txt

# Node environment (for the WhatsApp listener)
npm install
```

---

### Step 2 — Create your `.env`

```bash
copy .env.example .env   # Windows
# cp .env.example .env   # Mac / Linux
```

Open `.env` in any text editor. The only key you *must* fill in to get started is `ANTHROPIC_API_KEY`.

| Variable | Required | How to get it |
|---|---|---|
| `ANTHROPIC_API_KEY` | **Yes** | [console.anthropic.com](https://console.anthropic.com/) → API Keys |
| `WATCHED_GROUPS` | After step 4 | Comma-separated group IDs — discovered in step 4 |
| `TELEGRAM_BOT_TOKEN` | No | Chat with [@BotFather](https://t.me/BotFather) → `/newbot` |
| `TELEGRAM_CHAT_ID` | No | Message your bot, then open `https://api.telegram.org/bot<TOKEN>/getUpdates` and find `"chat": {"id": ...}` |
| `LANGCHAIN_API_KEY` | No | Free account at [smith.langchain.com](https://smith.langchain.com) |
| `LANGCHAIN_TRACING_V2` | No | Set `true` to enable LangSmith tracing |
| `LANGCHAIN_PROJECT` | No | Project name in LangSmith (defaults to `default` if omitted) |

---

### Step 3 — Configure your job preferences

Open `agent/memory.py` and edit `USER_PREFS` to match what you're looking for:

```python
USER_PREFS = {
    "roles": ["backend", "python", "node", "fullstack", "junior"],
    "blocklist": ["unpaid", "volunteer", "senior", "sales", "qa"],
    "location_blocklist": ["Jerusalem", "Haifa"],
    "min_salary": None,
}
```

| Field | Effect |
|---|---|
| `roles` | A job must contain at least one of these keywords (in title or summary) to be kept |
| `blocklist` | Any post matching these words is auto-rejected (checked against title, summary, and skills) |
| `location_blocklist` | Cities to skip. Everything else passes. Remote jobs always pass. |

---

### Step 4 — Discover your WhatsApp group IDs

Leave `WATCHED_GROUPS` empty in `.env` for now and run:

```bash
python start.py
```

A QR code will appear in the terminal. **Scan it with WhatsApp** (Settings → Linked Devices → Link a Device). Once connected, the listener prints all your groups:

```
  120363XXXXXXXXXX@g.us  —  Tech Jobs TLV
  120363YYYYYYYYYY@g.us  —  Junior Dev Positions
```

Copy the IDs of the groups you want to watch into `.env`:

```
WATCHED_GROUPS=120363XXXXXXXXXX@g.us,120363YYYYYYYYYY@g.us
```

Press `Ctrl+C` to stop. Auth is saved to `listener/.wwebjs_auth/` — you won't need to scan the QR code again unless you delete that folder or log out.

---

### Step 5 — Initialize the database

```bash
python -m db.init_db
```

---

### Step 6 — Start the agent

```bash
python start.py
```

Three processes start together and log to the same terminal:

| Prefix | Process | Role |
|---|---|---|
| `[api]` | FastAPI on port 8000 | Receives messages from the listener, runs the pipeline |
| `[digest]` | APScheduler | Sends a Telegram summary digest each morning |
| `[listener]` | whatsapp-web.js | Watches groups, replays missed messages on reconnect |

Press `Ctrl+C` to stop everything cleanly. If the listener crashes and restarts automatically, that's normal — it recovers from WhatsApp disconnects on its own.

---

### What happens next

Every WhatsApp message in a watched group is sent to the pipeline:

1. **Not a job post?** → dropped silently
2. **Already seen?** → dropped (dedup) — each job in a multi-job message is deduped independently
3. **Doesn't match your preferences?** → dropped (logged with reason)
4. **Passes everything?** → stored in `db/jobs.db` + instant Telegram alert (if configured)

A message can contain multiple job posts — each is processed independently through steps 2–4.

The pipeline tracks per-group job-post rates automatically. Once a group reaches 50 messages and ≥70% are job posts, it switches to a single combined LLM call instead of two separate calls, reducing API cost roughly by half for dedicated job groups.

The daily digest fires each morning and summarises everything stored since the last digest.

---

## Tests

**Python (39 tests)** — run offline, no API key needed. The LLM is replaced with `FakeListChatModel` using scripted JSON responses.

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

## LangSmith observability

Set `LANGCHAIN_TRACING_V2=true` and `LANGCHAIN_API_KEY` in `.env`. Every chain invocation appears at [smith.langchain.com](https://smith.langchain.com) — you can inspect the exact prompt, LLM response, latency, and token cost per step. Useful for debugging classifier confidence and extractor output.

---

## Project layout

```
agent/
  pipeline.py        Main async orchestrator — start reading here
  chains/
    classifier.py    LCEL chain: is_job_post + confidence (separate mode)
    extractor.py     LCEL chain: list of JobPost dicts (separate mode)
    combined.py      LCEL chain: classify+extract in one call (combined mode)
  tools/
    filter_tool.py   Match job against USER_PREFS
    dedup_tool.py    Hash-based dedup via seen_hashes table
    store_tool.py    Insert job into jobs table
    stats_tool.py    Per-group job-post rate tracking; drives adaptive mode
  memory.py          USER_PREFS dict (edit this)

api/
  main.py            FastAPI: POST /ingest, GET /healthz

listener/
  listener.js        whatsapp-web.js client; QR auth, heartbeat reconnect, catch-up replay
  last_seen.js       Per-group timestamp persistence (path-injectable for tests)

digest/
  digest.py          APScheduler cron + format_digest() (pure, testable)

db/
  schema.sql         SQLite schema (jobs, seen_hashes, group_stats)
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
