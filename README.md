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
| `TELEGRAM_BOT_TOKEN` | No | Chat with [@BotFather](https://t.me/BotFather) → `/newbot` |
| `TELEGRAM_CHAT_ID` | No | Message your bot, then open `https://api.telegram.org/bot<TOKEN>/getUpdates` and find `"chat": {"id": ...}` |
| `TELEGRAM_API_ID` | No (Telegram source) | [my.telegram.org](https://my.telegram.org) → API Development Tools → your app's `api_id` |
| `TELEGRAM_API_HASH` | No (Telegram source) | Same page — your app's `api_hash` |
| `TELEGRAM_PHONE` | No (Telegram source) | Your phone number, e.g. `+972501234567` |
| `LANGCHAIN_API_KEY` | No | Free account at [smith.langchain.com](https://smith.langchain.com) |
| `LANGCHAIN_TRACING` | No | Set `true` to enable LangSmith tracing |
| `LANGCHAIN_PROJECT` | No | Project name in LangSmith (defaults to `default` if omitted) |

---

### Step 3 — Configure your job preferences

Open `agent/prefs.json` and edit it to match what you're looking for:

```json
{
  "roles": ["backend", "python", "node", "fullstack", "junior"],
  "blocklist": ["unpaid", "volunteer", "senior", "sales", "qa"],
  "location_blocklist": ["Jerusalem", "Haifa"],
  "min_salary": null
}
```

| Field | Effect |
|---|---|
| `roles` | A job must contain at least one of these keywords (in title or summary) to be kept |
| `blocklist` | Any post matching these words is auto-rejected (checked against title, summary, and skills) |
| `location_blocklist` | Cities to skip. Everything else passes. Remote jobs always pass. |

You can also update preferences live via Telegram once the bot is running — no restart needed:

| Command | Effect |
|---|---|
| `/prefs` | Show current preferences |
| `/blockrole <keyword>` | Add to blocklist |
| `/blockcity <city>` | Add to location blocklist |
| `/addrole <keyword>` | Add to roles allow-list |

---

### Step 4 — Discover your WhatsApp group IDs

Leave `agent/whatsapp_sources.json` as an empty object `{}` and run:

```bash
python start.py
```

A QR code will appear in the terminal. **Scan it with WhatsApp** (Settings → Linked Devices → Link a Device). Once connected, the listener prints all your groups:

```
  120363XXXXXXXXXX@g.us  —  Tech Jobs TLV
  120363YYYYYYYYYY@g.us  —  Junior Dev Positions
```

Add the groups you want to watch to `agent/whatsapp_sources.json`:

```json
{
  "120363XXXXXXXXXX@g.us": "",
  "120363YYYYYYYYYY@g.us": ""
}
```

Display names are filled in automatically the next time the listener starts. You can also add and remove groups at any time using `/addgroup` and `/removegroup` in Telegram — live messages are forwarded immediately, catch-up on missed messages happens on the next restart.

Press `Ctrl+C` to stop. Auth is saved to `sources/whatsapp/.wwebjs_auth/` — you won't need to scan the QR code again unless you delete that folder or log out.

---

### Step 5 — Initialize the database


```bash
python -m db.init_db
```

---

### Step 6 — Authenticate the Telegram source listener (first run only)

> Skip this step if you are not using the Telegram source (`TELEGRAM_API_ID/HASH/PHONE` not set).

`start.py` launches all processes without a stdin, so Telethon cannot prompt for the login code interactively. Run the listener once in its own terminal to authenticate:

```bash
python -m sources.telegram.listener
```

Enter the code sent to your Telegram account when prompted. Once you see `Telegram source listener started`, press `Ctrl+C`. The session is saved to `sources/telegram/.session` and will be reused automatically — you will not need to do this again unless the session expires.

---

### Step 7 — Start the agent

```bash
python start.py
```

Six processes start together and log to the same terminal:

| Prefix | Process | Role |
|---|---|---|
| `[api]` | FastAPI on port 8000 | Receives messages from all listeners, runs the pipeline |
| `[digest]` | APScheduler | Sends a Telegram summary digest each morning |
| `[whatsapp]` | whatsapp-web.js | Watches WhatsApp groups, replays missed messages on reconnect |
| `[telegram]` | Telegram bot | Long-polls for button callbacks and `/commands` |
| `[tg-source]` | Telethon userbot | Watches Telegram channels from `agent/telegram_sources.json` (requires `TELEGRAM_API_ID/HASH/PHONE`) |
| `[web-source]` | Web scraper | Polls AllJobs / Indeed every 30 min using your role keywords |

`[tg-source]` and `[web-source]` exit silently if their config or env vars are not set — the other processes are unaffected.

Press `Ctrl+C` to stop everything cleanly.

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

## LangSmith observability

Set these two variables in `.env` to enable tracing:

```
LANGCHAIN_TRACING=true
LANGCHAIN_API_KEY=<your key from smith.langchain.com>
LANGCHAIN_PROJECT=job-screener   # optional — groups traces in the UI
```

Every chain invocation (classifier, extractor, combined) is then visible at [smith.langchain.com](https://smith.langchain.com) with the exact prompt sent, the raw LLM response, latency, and token cost per step.

Useful for:
- Debugging why a message was or wasn't classified as a job post
- Inspecting extractor output when fields are missing or wrong
- Comparing separate vs combined mode cost per group

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
