"""Start all processes in one terminal.

Usage:
    python start.py

Ctrl+C shuts everything down.

Processes:
  [api]        FastAPI ingest endpoint (always on)
  [digest]     Daily Telegram digest scheduler (always on)
  [whatsapp]   WhatsApp listener (auto-restart on crash)
  [telegram]   Telegram bot — commands + button callbacks (auto-restart)
  [tg-source]  Telegram source listener — requires TELEGRAM_API_ID/HASH/PHONE
  [web-source] Web scraper — polls AllJobs / Indeed every 30 min (auto-restart)
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import threading
import time
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

# Force this process's stdout to UTF-8 so Hebrew/emoji printed from subprocess
# output isn't corrupted by the Windows system codepage (e.g. cp1255).
sys.stdout.reconfigure(encoding='utf-8', errors='replace')


def _log(level: str, msg: str) -> None:
    print(f"[start] [{time.strftime('%H:%M:%S')}][{level}] {msg}", flush=True)


def stream(proc: subprocess.Popen, label: str) -> None:
    """Forward a process's stdout+stderr to our stdout with a label prefix."""
    for line in proc.stdout:
        print(f"[{label}] {line}", end="", flush=True)


LISTENER_RESTART_DELAY = 8  # seconds to wait before restarting the listener

class _NullProc:
    """Stand-in for a process that exited cleanly and should not be restarted.

    poll() always returns None so the restart loop treats it as still running.
    """

    returncode: int = 0

    def poll(self):
        return None

    def terminate(self):
        pass

    def wait(self):
        pass


_WHATSAPP_LOCK = Path("sources/whatsapp/.wwebjs_auth/session/SingletonLock")
_API_PORT = 8000


def _cleanup_stale_resources() -> None:
    # Kill any process still holding the API port from a previous run.
    try:
        result = subprocess.run(
            ["netstat", "-ano"],
            capture_output=True, text=True
        )
        for line in result.stdout.splitlines():
            if f":{_API_PORT}" in line and "LISTENING" in line:
                match = re.search(r"(\d+)\s*$", line.strip())
                if match:
                    pid = match.group(1)
                    subprocess.run(["taskkill", "/F", "/PID", pid],
                                   capture_output=True)
                    _log("info", f"Killed stale process on port {_API_PORT} (pid {pid})")
    except Exception:
        pass

    # Remove the Chrome SingletonLock left behind if whatsapp crashed.
    if _WHATSAPP_LOCK.exists():
        try:
            _WHATSAPP_LOCK.unlink()
            _log("info", "Removed stale WhatsApp SingletonLock")
        except Exception:
            pass


def main() -> None:
    python = sys.executable  # same venv Python that's running this script

    _cleanup_stale_resources()

    # (label, command, auto_restart)
    # The API and digest cannot auto-restart safely (hold ports / critical state).
    # All listeners recover cleanly from a crash and are safe to restart.
    process_specs = [
        ("api",        [python, "-m", "uvicorn", "api.main:app", "--port", "8000"], False),
        ("digest",     [python, "-m", "digest.digest"],                             False),
        ("whatsapp",   ["node", "sources/whatsapp/listener.js"],                    True),
        ("telegram",   [python, "telegram_bot.py"],                                 True),
        ("tg-source",  [python, "-m", "sources.telegram.listener"],                 True),
        ("web-source", [python, "-m", "sources.web.listener"],                      True),
    ]

    # Propagate UTF-8 mode to all child processes. Without this, Python
    # subprocesses (api, digest) write their logs in the system codepage,
    # turning Hebrew and emoji into '?' before start.py ever sees them.
    child_env = {**os.environ, 'PYTHONIOENCODING': 'utf-8'}

    def spawn(label: str, cmd: list[str]) -> subprocess.Popen:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=child_env,
            bufsize=1,
        )
        thread = threading.Thread(target=stream, args=(proc, label), daemon=True)
        thread.start()
        _log("info", f"{label} started (pid {proc.pid})")
        return proc

    procs = [spawn(label, cmd) for label, cmd, _ in process_specs]

    _log("info", "All processes running. Press Ctrl+C to stop.")

    try:
        while True:
            for i, (proc, (label, cmd, auto_restart)) in enumerate(zip(procs, process_specs)):
                if proc.poll() is not None:
                    if proc.returncode == 0:
                        # Clean exit — the process decided there was nothing to do
                        # (e.g. no sources configured, no session file). Don't restart.
                        _log("info", f"'{label}' exited cleanly (code 0). Not restarting.")
                        # Replace with a sentinel that never exits so the loop ignores it.
                        procs[i] = _NullProc()
                    elif auto_restart:
                        _log("warning", f"'{label}' crashed (code {proc.returncode}). Restarting in {LISTENER_RESTART_DELAY}s...")
                        time.sleep(LISTENER_RESTART_DELAY)
                        procs[i] = spawn(label, cmd)
                    else:
                        _log("error", f"'{label}' exited with code {proc.returncode}. Shutting down...")
                        raise SystemExit(1)
            threading.Event().wait(timeout=2)
    except KeyboardInterrupt:
        _log("info", "Shutting down...")
    finally:
        for proc in procs:
            proc.terminate()
        for proc in procs:
            proc.wait()
        _log("info", "All processes stopped.")


if __name__ == "__main__":
    main()
