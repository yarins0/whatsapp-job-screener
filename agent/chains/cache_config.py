"""Prompt-caching configuration for the LLM chains.

Set PROMPT_CACHE_TTL in your .env to control whether system prompts are cached
between API calls. The three chains (classifier, extractor, combined) all read
this setting via get_cache_control().

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
HOW PROMPT CACHING WORKS
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Anthropic caches the system prompt server-side. The first call that hits a cold
cache pays a write premium; subsequent calls within the TTL window pay ~10% of
normal input cost for those cached tokens.

  No cache:   every call costs   1.00×  base input price
  Cache hit:  every call costs   0.10×  base input price  (90% savings)
  5-min write:                   1.25×  (one-time per 5-min window)
  1-hour write:                  2.00×  (one-time per 1-hour window)

The cache TTL resets on every hit. As long as calls keep arriving before the
window expires, the cache stays warm.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
BREAK-EVEN MATH  (for claude-haiku-4-5 at $1.00 / 1M input tokens)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
System prompts across the three chains total ~530 cached tokens.

Uncached cost per call:   530 × $0.000001            = $0.00053
5-min write cost:         530 × $0.00000125           = $0.00066  (+25%)
1-hour write cost:        530 × $0.000002             = $0.00106  (+100%)
Cache hit cost:           530 × $0.0000001            = $0.000053 (−90%)

Break-even for 5-min TTL
  Write 1.25× + n hits at 0.10× = (n+1) calls at 1.00×
  1.25 + 0.10n = n + 1  →  n = 2.8
  → You need ≥ 3 messages in any 5-minute window to profit.
  → At 1 message / 5 min you pay a 25% premium with zero benefit.

Break-even for 1-hour TTL
  Write 2.00× + n hits at 0.10× = (n+1) calls at 1.00×
  2.00 + 0.10n = n + 1  →  n = 11.1
  → You need ≥ 12 messages per hour to profit.
  → At fewer messages you pay up to 2× for nothing.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
WHICH OPTION TO CHOOSE
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  off  — safe default. No risk of paying extra. Best for sparse traffic
         (fewer than 3 messages per 5-min burst, or fewer than 12/hour).
         Dollar difference at Haiku prices is fractions of a cent either way,
         but caching does reduce latency on hits.

  5m   — worth it if your groups occasionally burst 3+ jobs in a few minutes
         (e.g. a recruiter posting several roles at once).

  1h   — worth it if you consistently receive 12+ messages per hour during
         active periods. Pays for itself quickly at higher volume.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
CONFIGURATION
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Add one of these to your .env file:

  PROMPT_CACHE_TTL=off    # no caching (default)
  PROMPT_CACHE_TTL=5m     # 5-minute TTL
  PROMPT_CACHE_TTL=1h     # 1-hour TTL
"""

from __future__ import annotations

import os
from typing import Optional

# Valid options and their Anthropic API representations.
_TTL_MAP: dict[str, Optional[dict]] = {
    "off": None,
    "5m":  {"type": "ephemeral"},
    "1h":  {"type": "ephemeral", "ttl": "1h"},
}


def get_cache_control() -> Optional[dict]:
    """Return the cache_control dict for the current PROMPT_CACHE_TTL setting.

    Returns None when caching is disabled (the block is sent without a
    cache_control key and Anthropic will not cache it).
    """
    raw = os.getenv("PROMPT_CACHE_TTL", "off").strip().lower()
    if raw not in _TTL_MAP:
        valid = ", ".join(_TTL_MAP)
        raise ValueError(
            f"Invalid PROMPT_CACHE_TTL={raw!r}. Valid values: {valid}"
        )
    return _TTL_MAP[raw]
