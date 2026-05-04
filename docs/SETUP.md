# Setup Guide

## Prerequisites

- **Python 3.10+** and **Node.js 18+**
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

Open `.env` in any text editor. Set the API key for whichever LLM provider you choose (see [Choosing an LLM](#choosing-an-llm)).

| Variable | Required | How to get it |
|---|---|---|
| `LLM_PROVIDER` | No | `anthropic` (default), `openai`, `google`, or `ollama` |
| `LLM_MODEL` | No | Model name for your provider (see [Choosing an LLM](#choosing-an-llm)) |
| `ANTHROPIC_API_KEY` | If using Anthropic | [console.anthropic.com](https://console.anthropic.com/) → API Keys |
| `OPENAI_API_KEY` | If using OpenAI | [platform.openai.com](https://platform.openai.com/) → API Keys |
| `GOOGLE_API_KEY` | If using Google | [aistudio.google.com](https://aistudio.google.com/) → Get API key |
| `TELEGRAM_BOT_TOKEN` | No | Chat with [@BotFather](https://t.me/BotFather) → `/newbot` |
| `TELEGRAM_CHAT_ID` | No | Message your bot, then open `https://api.telegram.org/bot<TOKEN>/getUpdates` and find `"chat": {"id": ...}` |
| `TELEGRAM_API_ID` | No (Telegram source) | [my.telegram.org](https://my.telegram.org) → API Development Tools → your app's `api_id` |
| `TELEGRAM_API_HASH` | No (Telegram source) | Same page — your app's `api_hash` |
| `TELEGRAM_PHONE` | No (Telegram source) | Your phone number, e.g. `+972501234567` |
| `LANGSMITH_API_KEY` | No | Free account at [smith.langchain.com](https://smith.langchain.com) |
| `LANGSMITH_TRACING` | No | Set `true` to enable LangSmith tracing |
| `LANGSMITH_ENDPOINT` | No | `https://api.smith.langchain.com` (default, rarely needs changing) |
| `LANGSMITH_PROJECT` | No | Project name in LangSmith (defaults to `default` if omitted) |

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
```env
LLM_PROVIDER=ollama
LLM_MODEL=llama3.2
```
```bash
pip install langchain-ollama
ollama pull llama3.2
```

If `LLM_PROVIDER` is not set, the agent defaults to Anthropic Claude Haiku.

---

## LangSmith observability

Set these variables in `.env` to enable tracing:

```
LANGSMITH_TRACING=true
LANGSMITH_API_KEY=<your key from smith.langchain.com>
LANGSMITH_ENDPOINT=https://api.smith.langchain.com
LANGSMITH_PROJECT=job-screener   # optional — groups traces in the UI
```

Every chain invocation (classifier, extractor, combined) is then visible at [smith.langchain.com](https://smith.langchain.com) with the exact prompt sent, the raw LLM response, latency, and token cost per step.

Useful for:
- Debugging why a message was or wasn't classified as a job post
- Inspecting extractor output when fields are missing or wrong
- Comparing separate vs combined mode cost per group
