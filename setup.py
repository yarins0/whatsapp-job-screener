# -*- coding: utf-8 -*-
"""
Interactive setup wizard for the Job Screening Agent.

Usage:
    python setup.py
"""

from __future__ import annotations

import getpass
import shutil
import subprocess
import sys
from pathlib import Path

DOTENV_PATH = Path(".env")

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
        if secret:
            try:
                value = getpass.getpass(full_prompt).strip()
                if not value and default:
                    return default
                if value:
                    return value
            except (getpass.GetPassWarning, Exception):
                # getpass doesn't work in all Windows terminals — fall back to input().
                print("  (warning: input will be visible in this terminal)")
                secret = False
                continue
        else:
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


def _done() -> None:
    print()
    print("=" * 58)
    print("  Setup complete!")
    print()
    print("  Next steps:")
    print("  1. Start the agent:    python start.py")
    print("  2. Connect WhatsApp:   scan the QR code that appears")
    print("  3. Add job groups:     use /addgroup in your Telegram bot")
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
    _done()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\n  Setup cancelled.")
        sys.exit(0)
