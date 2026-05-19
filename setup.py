# -*- coding: utf-8 -*-
"""
Interactive setup wizard for the Job Screening Agent.

Usage:
    python setup.py
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

DOTENV_PATH = Path(".env")
PREFS_PATH = Path("agent/prefs.json")

_PROVIDER_EXTRA_PACKAGES: dict[str, str] = {
    "openai": "langchain-openai>=0.2.0",
    "google": "langchain-google-genai>=2.0.0",
    "ollama": "langchain-ollama>=0.2.0",
}

_PROVIDER_KEY_ENV: dict[str, str] = {
    "anthropic": "ANTHROPIC_API_KEY",
    "openai": "OPENAI_API_KEY",
    "google": "GOOGLE_API_KEY",
}

_PROVIDER_KEY_HINTS: dict[str, str] = {
    "anthropic": "Get yours at: https://console.anthropic.com/  ->  API Keys",
    "openai": "Get yours at: https://platform.openai.com/api-keys",
    "google": "Get yours at: https://aistudio.google.com/app/apikey",
}

# Keys the wizard writes - used to preserve unknown fields on reconfigure.
_WIZARD_KEYS = {
    "LLM_PROVIDER", "LLM_MODEL",
    "ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GOOGLE_API_KEY",
    "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID",
    "TELEGRAM_API_ID", "TELEGRAM_API_HASH", "TELEGRAM_PHONE",
    "PROMPT_CACHE_TTL", "DUPLICATE_WINDOW_DAYS",
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _banner() -> None:
    print()
    print("=" * 58)
    print("   Job Screening Agent  -  Setup Wizard")
    print("=" * 58)
    print()


def _step(n: int, title: str) -> None:
    print(f"\n[Step {n}]  {title}")
    print("-" * 42)


def _ask(prompt: str, default: str | None = None, secret: bool = False) -> str:
    hint = f" [{default}]" if default else ""
    full_prompt = f"  {prompt}{hint}: "
    while True:
        value = input(full_prompt).strip()
        if not value and default:
            return default
        if value:
            return value
        print("  (required - please enter a value)")


def _ask_yn(prompt: str, default_yes: bool = True) -> bool:
    hint = "[Y/n]" if default_yes else "[y/N]"
    answer = input(f"  {prompt} {hint}: ").strip().lower()
    if not answer:
        return default_yes
    return answer in ("y", "yes")


def _load_dotenv() -> dict[str, str]:
    env: dict[str, str] = {}
    if not DOTENV_PATH.exists():
        return env
    for line in DOTENV_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            env[k.strip()] = v.strip()
    return env


def _run(cmd: list[str], label: str) -> None:
    print(f"  Running: {label}...")
    result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if result.returncode != 0:
        # Show the tail of output so the user can see what went wrong.
        output = (result.stdout + result.stderr).strip()
        if output:
            print()
            for line in output.splitlines()[-20:]:
                print(f"    {line}")
        print(f"\n  ✗  {label} failed (exit code {result.returncode}).")
        sys.exit(1)
    print(f"  ✓  {label}")


# ---------------------------------------------------------------------------
# Steps
# ---------------------------------------------------------------------------

def check_prerequisites() -> None:
    _step(1, "Checking prerequisites")

    v = sys.version_info
    if v < (3, 9):
        print(f"  ✗  Python 3.9 or newer is required (you have {v.major}.{v.minor}).")
        print("     Download it from: https://www.python.org/downloads/")
        sys.exit(1)
    print(f"  ✓  Python {v.major}.{v.minor}.{v.micro}")

    for tool, url in (("node", "https://nodejs.org/"), ("npm", "https://nodejs.org/")):
        tool_path = shutil.which(tool)
        if not tool_path:
            print(f"  ✗  '{tool}' is not installed.")
            print(f"     Download Node.js from: {url}")
            sys.exit(1)
        result = subprocess.run([tool_path, "--version"], capture_output=True, text=True)
        print(f"  ✓  {tool} {result.stdout.strip()}")


def configure_env() -> str:
    _step(2, "Configuring your settings  (.env)")

    existing = _load_dotenv()

    if DOTENV_PATH.exists():
        print("  A .env file already exists.")
        if not _ask_yn("  Reconfigure it?", default_yes=False):
            print("  Skipping - keeping your existing .env.")
            return existing.get("LLM_PROVIDER", "anthropic")

    wizard_lines: list[str] = []

    # --- LLM provider --------------------------------------------------------
    print()
    print("  The agent uses an AI model to read job posts.")
    print("  Which provider do you have an API key for?")
    print()
    providers = ["anthropic", "openai", "google", "ollama"]
    for i, p in enumerate(providers, 1):
        suffix = " (Ollama runs locally - no API key needed)" if p == "ollama" else ""
        print(f"    {i}. {p.capitalize()}{suffix}")
    print()

    while True:
        raw = input("  Your choice [1]: ").strip() or "1"
        if raw in ("1", "2", "3", "4"):
            provider = providers[int(raw) - 1]
            break
        print("  Please enter a number between 1 and 4.")

    wizard_lines.append(f"LLM_PROVIDER={provider}")
    wizard_lines.append("LLM_MODEL=")

    env_var = _PROVIDER_KEY_ENV.get(provider)
    if env_var:
        print()
        print(f"  {_PROVIDER_KEY_HINTS[provider]}")
        key_value = _ask(
            f"{env_var}",
            default=existing.get(env_var),
            secret=True,
        )
        wizard_lines.append(f"{env_var}={key_value}")

    # --- Telegram bot --------------------------------------------------------
    print()
    print("  -- Telegram Bot --")
    print("  The bot sends you job alerts and lets you manage the agent via commands.")
    print("  Create a bot with @BotFather on Telegram if you don't have one yet.")
    print()
    bot_token = _ask("Telegram bot token", default=existing.get("TELEGRAM_BOT_TOKEN"), secret=True)
    wizard_lines.append(f"TELEGRAM_BOT_TOKEN={bot_token}")

    print()
    print("  To find your chat ID:")
    print("  1. Send any message to your bot.")
    print("  2. Open this URL in a browser:")
    print(f"       https://api.telegram.org/bot<TOKEN>/getUpdates")
    print('  3. Find the "id" field inside "chat".')
    print()
    chat_id = _ask("Your Telegram chat ID", default=existing.get("TELEGRAM_CHAT_ID"))
    wizard_lines.append(f"TELEGRAM_CHAT_ID={chat_id}")

    # --- Anthropic prompt cache (only relevant for Anthropic) ----------------
    if provider == "anthropic":
        print()
        print("  -- Prompt Cache --")
        print("  Caching reuses system prompts across API calls, lowering cost.")
        print("  Valid values:")
        print("    off   — disabled (default)")
        print("    5m    — 5-minute cache  (good for message bursts)")
        print("    1h    — 1-hour cache    (good for sustained traffic)")
        print("    auto  — auto-detects burst vs sparse traffic")
        print()
        _VALID_CACHE_TTLS = {"off", "5m", "1h", "auto"}
        while True:
            cache_ttl = _ask("PROMPT_CACHE_TTL", default=existing.get("PROMPT_CACHE_TTL", "off"))
            if cache_ttl in _VALID_CACHE_TTLS:
                break
            print(f"  Invalid value — must be one of: {', '.join(sorted(_VALID_CACHE_TTLS))}")
        wizard_lines.append(f"PROMPT_CACHE_TTL={cache_ttl}")

    # --- Duplicate window ----------------------------------------------------
    print()
    print("  -- Duplicate Detection --")
    print("  How many days back to check for the same job title + company.")
    print("  Enter 0 to disable duplicate checking.")
    print()
    dup_days = _ask("DUPLICATE_WINDOW_DAYS", default=existing.get("DUPLICATE_WINDOW_DAYS", "7"))
    wizard_lines.append(f"DUPLICATE_WINDOW_DAYS={dup_days}")

    # --- Telegram source (optional) ------------------------------------------
    print()
    print("  -- Telegram Channel Source (optional) --")
    print("  This lets the agent watch Telegram channels for job posts.")
    print()
    if _ask_yn("Set up Telegram channel source?", default_yes=False):
        print()
        print("  Get API ID and API hash from:")
        print("    https://my.telegram.org  ->  API Development Tools")
        print()
        api_id = _ask("Telegram API ID", default=existing.get("TELEGRAM_API_ID"))
        api_hash = _ask("Telegram API hash", default=existing.get("TELEGRAM_API_HASH"), secret=True)
        phone = _ask(
            "Your phone number (e.g. +972501234567)",
            default=existing.get("TELEGRAM_PHONE"),
        )
        wizard_lines.append(f"TELEGRAM_API_ID={api_id}")
        wizard_lines.append(f"TELEGRAM_API_HASH={api_hash}")
        wizard_lines.append(f"TELEGRAM_PHONE={phone}")

    # Preserve any fields from the old .env that the wizard doesn't cover.
    leftovers = {k: v for k, v in existing.items() if k not in _WIZARD_KEYS}
    if leftovers:
        wizard_lines.append("")
        wizard_lines.append("# -- Preserved from previous configuration --")
        for k, v in leftovers.items():
            wizard_lines.append(f"{k}={v}")

    DOTENV_PATH.write_text("\n".join(wizard_lines) + "\n", encoding="utf-8")
    print()
    print(f"  ✓  Saved to {DOTENV_PATH}")
    return provider


def install_python_deps(provider: str) -> None:
    _step(3, "Installing Python dependencies")
    print("  (This may take a minute on first run)")
    print()
    _run([sys.executable, "-m", "pip", "install", "-r", "requirements.txt"], "Core packages")

    extra = _PROVIDER_EXTRA_PACKAGES.get(provider)
    if extra:
        _run([sys.executable, "-m", "pip", "install", extra], f"{provider} provider package")


def install_node_deps() -> None:
    _step(4, "Installing Node.js dependencies")
    # shutil.which resolves npm.cmd on Windows; subprocess needs the full path.
    npm = shutil.which("npm") or "npm"
    _run([npm, "install"], "npm install")


def init_database() -> None:
    _step(5, "Initializing the database")
    result = subprocess.run(
        [sys.executable, "-m", "db.init_db"],
        capture_output=True, text=True, encoding="utf-8",
    )
    if result.returncode != 0:
        print(result.stderr.strip())
        print("  ✗  Database initialization failed.")
        sys.exit(1)
    print(f"  ✓  {result.stdout.strip()}")


def configure_prefs() -> None:
    _step(6, "Configuring job preferences  (agent/prefs.json)")

    existing: dict = {}
    if PREFS_PATH.exists():
        try:
            existing = json.loads(PREFS_PATH.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            print("  Warning: existing prefs.json is invalid — using defaults.")

    defaults: dict = {
        "roles": ["backend", "python", "node", "fullstack", "junior"],
        "blocklist": ["unpaid", "volunteer", "senior", "sales", "qa"],
        "location_blocklist": [],
        "min_salary": None,
    }
    prefs: dict = {**defaults, **existing}

    print("  These preferences control which jobs the agent keeps.")
    print()
    if PREFS_PATH.exists():
        print(f"  roles:              {', '.join(prefs.get('roles', []))}")
        print(f"  blocklist:          {', '.join(prefs.get('blocklist', []))}")
        loc = prefs.get("location_blocklist", [])
        print(f"  location_blocklist: {', '.join(loc) if loc else '(none)'}")
        print()
        if not _ask_yn("  Reconfigure preferences?", default_yes=False):
            print("  Skipping — keeping your existing preferences.")
            return

    print()
    print("  Enter keywords as comma-separated lists.")
    print()

    roles_default = ", ".join(prefs.get("roles", []))
    roles_raw = _ask("Roles to keep (match in title or summary)", default=roles_default)
    roles = [r.strip() for r in roles_raw.split(",") if r.strip()]

    blocklist_default = ", ".join(prefs.get("blocklist", []))
    bl_hint = f" [{blocklist_default}]" if blocklist_default else " [none]"
    blocklist_raw = input(f"  Blocklist (auto-reject these keywords){bl_hint}: ").strip()
    blocklist = (
        [b.strip() for b in blocklist_raw.split(",") if b.strip()]
        if blocklist_raw
        else prefs.get("blocklist", [])
    )

    loc_default = ", ".join(prefs.get("location_blocklist", []))
    loc_hint = f" [{loc_default}]" if loc_default else " [none]"
    loc_raw = input(f"  Location blocklist (cities to skip){loc_hint}: ").strip()
    loc_blocklist = (
        [c.strip() for c in loc_raw.split(",") if c.strip()]
        if loc_raw
        else prefs.get("location_blocklist", [])
    )

    new_prefs = {
        "roles": roles,
        "blocklist": blocklist,
        "location_blocklist": loc_blocklist,
        "min_salary": prefs.get("min_salary"),
    }

    PREFS_PATH.parent.mkdir(parents=True, exist_ok=True)
    PREFS_PATH.write_text(
        json.dumps(new_prefs, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print()
    print(f"  ✓  Saved to {PREFS_PATH}")


def authenticate_telegram_source() -> None:
    _step(7, "Authenticating Telegram source listener")

    env = _load_dotenv()
    if not all(env.get(k) for k in ("TELEGRAM_API_ID", "TELEGRAM_API_HASH", "TELEGRAM_PHONE")):
        print("  Telegram source not configured — skipping.")
        print("  (Set TELEGRAM_API_ID, TELEGRAM_API_HASH, and TELEGRAM_PHONE in .env to enable.)")
        return

    print("  The Telegram source listener needs to log in once.")
    print("  It will send a verification code to your Telegram account.")
    print("  Once you see 'Telegram source listener started', press Ctrl+C to continue setup.")
    print()
    if not _ask_yn("  Authenticate now?", default_yes=True):
        print()
        print("  Skipping — run this manually before starting the agent:")
        print("    python -m sources.telegram.listener")
        return

    print()
    print("  Starting listener for authentication (press Ctrl+C after login succeeds)...")
    print()
    try:
        subprocess.run([sys.executable, "-m", "sources.telegram.listener"])
    except KeyboardInterrupt:
        pass

    print()
    print("  ✓  Telegram source authentication complete.")


def _done() -> None:
    print()
    print("=" * 58)
    print("  Setup complete!")
    print()
    print("  The wizard handled SETUP.md steps 1–5.")
    print("  One step remains:")
    print()
    print("  Step 6 — Start the agent:")
    print("    Windows:  double-click  Start Job Screener.bat")
    print("    Any OS:   python start.py")
    print()
    print("  On first run, scan the QR code with WhatsApp")
    print("  (Settings → Linked Devices → Link a Device).")
    print("  Your groups are listed in the terminal — use")
    print("  /addgroup <id> in Telegram to start watching them.")
    print()
    print("  This is also the command you run every future session.")
    print()
    print("  Run  python setup.py  again at any time to reconfigure.")
    print("=" * 58)
    print()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    _banner()
    check_prerequisites()
    provider = configure_env()
    install_python_deps(provider)
    install_node_deps()
    init_database()
    configure_prefs()
    authenticate_telegram_source()
    _done()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\n  Setup cancelled.")
        sys.exit(0)
