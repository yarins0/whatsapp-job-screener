# Setup Guide

## Quick Setup (recommended)

If you have **Python 3.9+** and **Node.js** installed, the setup wizard handles everything else:

```bash
python setup.py
```

The wizard will:
1. Check prerequisites (Python version, Node.js, npm)
2. Walk you through creating `.env` with plain-English prompts
3. Install Python and Node.js dependencies
4. Initialize the database

Once it finishes, jump straight to [Step 3 — Configure your job preferences](#step-3--configure-your-job-preferences).

---

## Manual Setup

Follow the steps below if you prefer to configure things by hand, or if the wizard fails.

### Prerequisites

- **Python 3.9+** and **Node.js 18+**
- A phone with WhatsApp installed (to scan the QR code on first run)
- An API key for whichever LLM provider you choose (see [Choosing an LLM](#choosing-an-llm) below)
- *(Optional)* A Telegram account for instant notifications and the daily digest

---

## Step 1 — Install dependencies

```bash
# Clone the repo
git clone https://github.com/yarins0/whatsapp-job-screener.git
cd whatsapp-job-screener

# Python environment
python -m venv venv
venv\Scripts\activate        # Windows
# source venv/bin/activate   # Mac / Linux

pip install -r requirements.txt

# Node environment (for the WhatsApp listener)
npm install
```

---

## Step 2 — Create your `.env`

```bash
copy .env.example .env   # Windows
# cp .env.example .env   # Mac / Linux
```

Open `.env` in any text editor and fill in the sections below.

### LLM provider (required — pick one)

See [Choosing an LLM](#choosing-an-llm) for the full comparison. Default is Anthropic.

| Variable | Purpose |
|---|---|
| `LLM_PROVIDER` | `anthropic` (default), `openai`, `google`, or `ollama` |
| `LLM_MODEL` | Override the default model for your chosen provider |
| `ANTHROPIC_API_KEY` | Required when `LLM_PROVIDER=anthropic` — [console.anthropic.com](https://console.anthropic.com/) → API Keys |
| `OPENAI_API_KEY` | Required when `LLM_PROVIDER=openai` — [platform.openai.com](https://platform.openai.com/) → API Keys |
| `GOOGLE_API_KEY` | Required when `LLM_PROVIDER=google` — [aistudio.google.com](https://aistudio.google.com/) → Get API key |

### Telegram notifications (recommended)

Without these the agent still screens jobs and stores them in the database — you just won't get real-time alerts or the daily digest. **Strongly recommended** for a useful experience.

| Variable | Purpose |
|---|---|
| `TELEGRAM_BOT_TOKEN` | Enables instant job alerts, daily digest, and bot commands. Create a bot via [@BotFather](https://t.me/BotFather) → `/newbot` |
| `TELEGRAM_CHAT_ID` | Your Telegram user ID — the bot sends alerts here. Message your bot, then open `https://api.telegram.org/bot<TOKEN>/getUpdates` and find `"chat": {"id": ...}` |

### Telegram source listener (optional)

Only needed if you want to watch **Telegram channels** for job posts (in addition to WhatsApp groups).

| Variable | Purpose |
|---|---|
| `TELEGRAM_API_ID` | From [my.telegram.org](https://my.telegram.org) → API Development Tools → `api_id` |
| `TELEGRAM_API_HASH` | Same page — `api_hash` |
| `TELEGRAM_PHONE` | Your phone number, e.g. `+972501234567` |

### Behaviour tuning (optional)

| Variable | Default | Purpose |
|---|---|---|
| `PROMPT_CACHE_TTL` | `off` | Anthropic only — cache TTL in seconds (e.g. `300`) or `off` to disable. Reduces API cost on repeated similar requests. |
| `DUPLICATE_WINDOW_DAYS` | `7` | How many days back to check for the same title + company. Set `0` to disable duplicate detection. |

### Observability (optional)

LangSmith gives you a trace of every LLM call — useful for debugging why a message was or wasn't classified correctly.

| Variable | Purpose |
|---|---|
| `LANGSMITH_API_KEY` | Free account at [smith.langchain.com](https://smith.langchain.com) |
| `LANGSMITH_TRACING` | Set `true` to enable tracing |
| `LANGSMITH_PROJECT` | Groups traces in the UI (defaults to `default`) |

---

## Step 3 — Configure your job preferences

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

## Step 4 — Discover your WhatsApp group IDs

Leave `agent/whatsapp_sources.json` as an empty object `{}` and run:

```bash
python start.py
```

A QR code will appear in the terminal. **Scan it with WhatsApp** (Settings → Linked Devices → Link a Device). Once connected, the listener prints all your groups:

```
  120363XXXXXXXXXX@g.us  —  Tech Jobs TLV
  120363YYYYYYYYYY@g.us  —  Junior Dev Positions
```

> **Tip:** Once the QR code is scanned and the listener connects, `agent/all_whatsapp_groups.json` is written automatically with all your groups. If you configured Telegram in Step 2, send `/listgroups` to your bot to see the same list with watched groups marked ✓. You can also use `/addgroup <id>` and `/removegroup <id>` any time later to add or remove groups without editing the file.

Add the groups you want to watch to `agent/whatsapp_sources.json`:

```json
{
  "120363XXXXXXXXXX@g.us": "",
  "120363YYYYYYYYYY@g.us": ""
}
```

Display names are filled in automatically the next time the listener starts. You can also add and remove groups at any time using `/addgroup` and `/removegroup` in Telegram — live messages are forwarded immediately, catch-up on missed messages happens on the next restart.

Press `Ctrl+C` to stop. Auth is saved to `sources/whatsapp/.wwebjs_auth/` — you won't need to scan the QR code again unless you delete that folder or log out.

> **Session self-healing:** If the WhatsApp listener crashes 3 times in a row (e.g. because a saved session has expired), it automatically clears the stale session and shows a fresh QR code on the next restart. The QR code is also sent once to your Telegram chat so you can scan it remotely.

> **QR in Telegram:** The QR code is sent to your Telegram chat once per login session. WhatsApp regenerates it every ~20 s, but only the terminal shows the updated code. If the first QR expires before you scan it, type `y` at the `QR expired — regen another?` prompt in the terminal.

---

## Step 5 — Initialize the database

```bash
python -m db.init_db
```

---

## Step 6 — Authenticate the Telegram source listener (first run only)

> Skip this step if you are not using the Telegram source (`TELEGRAM_API_ID/HASH/PHONE` not set).

`start.py` launches all processes without a stdin, so Telethon cannot prompt for the login code interactively. Run the listener once in its own terminal to authenticate:

```bash
python -m sources.telegram.listener
```

Enter the code sent to your Telegram account when prompted. Once you see `Telegram source listener started`, press `Ctrl+C`. The session is saved to `sources/telegram/.session` and will be reused automatically — you will not need to do this again unless the session expires.

---

## Step 7 — Start the agent
### This is the only step you will have to repeat

**Windows** — double-click `Start Job Screener.bat` (or create a desktop shortcut to it).

**macOS / Linux** — run in a terminal:

```bash
bash start.sh
```

> Tip: make it executable once with `chmod +x start.sh`, then you can just run `./start.sh`.

**Or directly on any OS:**

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

## What happens next

Every WhatsApp message in a watched group is sent to the pipeline:

1. **Not a job post?** → dropped silently
2. **Already seen?** → dropped (dedup) — each job in a multi-job message is deduped independently
3. **Doesn't match your preferences?** → dropped (logged with reason)
4. **Passes everything?** → stored in `db/jobs.db` + instant Telegram alert (if configured)

A message can contain multiple job posts — each is processed independently through steps 2–4.

The pipeline tracks per-group job-post rates automatically. Once a group reaches 50 messages and ≥70% are job posts, it switches to a single combined LLM call instead of two separate calls, reducing API cost roughly by half for dedicated job groups.

The daily digest fires each morning and summarises everything stored since the last digest.

---

## Choosing an LLM

Set `LLM_PROVIDER` and optionally `LLM_MODEL` in your `.env`. Then install the matching package and set its API key.

| Provider | `LLM_PROVIDER` | Default model | Package to install | API key var |
|---|---|---|---|---|
| Anthropic (default) | `anthropic` | `claude-haiku-4-5-20251001` | `langchain-anthropic` | `ANTHROPIC_API_KEY` |
| OpenAI | `openai` | `gpt-4o-mini` | `langchain-openai` | `OPENAI_API_KEY` |
| Google Gemini | `google` | `gemini-2.0-flash` | `langchain-google-genai` | `GOOGLE_API_KEY` |
| Ollama (local) | `ollama` | `llama3.2` | `langchain-ollama` | *(none — run Ollama locally)* |

**Example — switch to OpenAI:**
```env
LLM_PROVIDER=openai
LLM_MODEL=gpt-4o-mini
OPENAI_API_KEY=sk-...
```
```bash
pip install langchain-openai
```

**Example — run fully locally with Ollama:**

First, install Ollama itself from **[ollama.com/download](https://ollama.com/download)** and reopen your terminal so it's on your PATH. Then:

```env
LLM_PROVIDER=ollama
LLM_MODEL=llama3.2
```
```bash
pip install langchain-ollama
ollama pull llama3.2   # one-time ~2GB download
```

If `LLM_PROVIDER` is not set, the agent defaults to Anthropic Claude Haiku.

---

## LangSmith observability

Set `LANGSMITH_TRACING=true` and `LANGSMITH_API_KEY` in `.env` (free account at [smith.langchain.com](https://smith.langchain.com)).

Every chain invocation is then visible with the exact prompt, raw LLM response, latency, and token cost — useful for debugging why a message was or wasn't classified correctly.
