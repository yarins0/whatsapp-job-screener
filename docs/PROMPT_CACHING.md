# Prompt Caching

> **Anthropic only.** `PROMPT_CACHE_TTL` is silently ignored when `LLM_PROVIDER` is set to `openai`, `google`, or `ollama`.

The three LLM chains (classifier, extractor, combined) can cache their system prompts on Anthropic's servers between calls. Controlled by `PROMPT_CACHE_TTL` in `.env`.

## Options

| Value | Behaviour | Write cost | Hit cost |
|---|---|---|---|
| `off` | No caching (default) | — | — |
| `5m` | 5-minute TTL | 1.25× base | 0.10× base |
| `1h` | 1-hour TTL | 2.00× base | 0.10× base |
| `auto` | Dynamic — see below | 1.25× during bursts | 0.10× during bursts |

Add to `.env`:
```
PROMPT_CACHE_TTL=auto   # recommended — or 5m, 1h, off
```

---

## How it works

Anthropic stores the system prompt server-side. The first call in a cold window writes the cache and pays the premium. Every subsequent call that arrives before the TTL expires pays ~10% of the normal token cost for those cached tokens. Each cache hit resets the timer.

System prompts across the three chains total **~530 tokens**.

| Event | Cost at Haiku pricing ($1.00 / 1M tokens) |
|---|---|
| Uncached call | 530 × $0.000001 = **$0.00053** |
| 5-min write | 530 × $0.00000125 = **$0.00066** (+25%) |
| 1-hour write | 530 × $0.000002 = **$0.00106** (+100%) |
| Cache hit | 530 × $0.0000001 = **$0.000053** (−90%) |

---

## Break-even

**5-minute TTL** — you need at least **3 messages in any 5-minute window** to profit.

```
Write 1.25× + n hits × 0.10× = (n + 1) uncached calls × 1.00×
1.25 + 0.10n = n + 1  →  n = 2.8  →  round up to 3
```

**1-hour TTL** — you need at least **12 messages per hour** to profit.

```
Write 2.00× + n hits × 0.10× = (n + 1) uncached calls × 1.00×
2.00 + 0.10n = n + 1  →  n = 11.1  →  round up to 12
```

---

## Auto mode

`auto` turns caching on exactly when it pays off and off when it doesn't.

### The problem with static TTLs

A job screening bot gets messages in bursts, not at a steady rate. After a few hours of quiet (overnight, PC sleep), WhatsApp replays up to 50 missed messages at once — a burst where caching saves 90% per message. Outside of that, traffic is sparse (one message every few hours) and caching just adds a 25% write overhead for no benefit.

### State machine

One in-memory variable, `_burst_mode_until`, drives the logic. On every API call:

1. **Night hours** (default 10 pm – 7 am): return `off`. Reset burst state.
2. **Burst window active** (`now < _burst_mode_until`): return 5m cache. Extend the window by `_BURST_WINDOW_MINUTES` so it stays alive as long as messages keep arriving.
3. **Idle > threshold**: query `group_stats.updated_at` — if the last recorded message was > 30 minutes ago, activate burst mode and return 5m cache.
4. **Otherwise**: return `off`. Normal sparse traffic; not worth caching.

Burst mode expires naturally `_BURST_WINDOW_MINUTES` after the last message — no explicit "caught up" signal needed.

### Tuning constants

These live in `agent/chains/cache_config.py` (not in `.env` — they're not sensitive):

| Constant | Default | Meaning |
|---|---|---|
| `_NIGHT_START_HOUR` | `22` | Hour (24h) when night mode begins |
| `_NIGHT_END_HOUR` | `7` | Hour (24h) when night mode ends |
| `_BURST_THRESHOLD_MINUTES` | `30` | Idle time that triggers burst mode |
| `_BURST_WINDOW_MINUTES` | `10` | How long burst mode stays active after last message |

---

## Which option to choose

**`auto`** — recommended for this bot. Caches during catch-up bursts, stays off overnight and during normal sparse traffic. Zero manual tuning needed.

**`off`** — safe and simple. No risk of paying the write premium unnecessarily. Dollar difference at Haiku prices is fractions of a cent either way.

**`5m`** — worth it if your groups occasionally burst 3+ posts in a few minutes (e.g. a recruiter posting several roles at once).

**`1h`** — worth it only if you consistently see 12+ messages per hour during active periods.
