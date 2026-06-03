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


def _kill_port(port: int) -> None:
    """Kill whatever process is listening on *port*, cross-platform."""
    try:
        if sys.platform == "win32":
            result = subprocess.run(
                ["netstat", "-ano"], capture_output=True, text=True
            )
            for line in result.stdout.splitlines():
                if f":{port}" in line and "LISTENING" in line:
                    match = re.search(r"(\d+)\s*$", line.strip())
                    if match:
                        pid = match.group(1)
                        subprocess.run(["taskkill", "/F", "/PID", pid],
                                       capture_output=True)
                        _log("info", f"Killed stale process on port {port} (pid {pid})")
        else:
            result = subprocess.run(
                ["lsof", "-ti", f"tcp:{port}"], capture_output=True, text=True
            )
            for pid in result.stdout.strip().splitlines():
                subprocess.run(["kill", "-9", pid], capture_output=True)
                _log("info", f"Killed stale process on port {port} (pid {pid})")
    except Exception:
        pass


def _cleanup_stale_resources() -> None:
    _kill_port(_API_PORT)

    # Remove the Chrome SingletonLock left behind if whatsapp crashed.
    if _WHATSAPP_LOCK.exists():
        try:
            _WHATSAPP_LOCK.unlink()
            _log("info", "Removed stale WhatsApp SingletonLock")
        except Exception:
            pass


def _confirm_shutdown() -> bool:
    """Ask whether to stop everything. Returns True to shut down, False to resume.

    Called when Ctrl+C is caught. Any answer other than 'y' leaves all child
    processes running and resumes monitoring. A second Ctrl+C (or EOF) at the
    prompt is treated as confirmation so repeated presses still quit.
    """
    try:
        answer = input("\n[start] Shut down all processes? [y/N]: ").strip().lower()
    except (KeyboardInterrupt, EOFError):
        return True
    return answer == "y"


def _monitor_once(procs: list, process_specs: list, spawn) -> None:
    """One health-check pass: retire clean exits, restart crashed listeners.

    Mutates *procs* in place. Raises SystemExit if a non-restartable process
    (the API or digest) exits, which propagates up to trigger shutdown.
    """
    for i, (proc, (label, cmd, auto_restart)) in enumerate(zip(procs, process_specs)):
        if proc.poll() is None:
            continue  # still running
        if proc.returncode == 0:
            _log("info", f"'{label}' exited cleanly (code 0). Not restarting.")
            procs[i] = _NullProc()
        elif auto_restart:
            _log("warning", f"'{label}' crashed (code {proc.returncode}). Restarting in {LISTENER_RESTART_DELAY}s...")
            time.sleep(LISTENER_RESTART_DELAY)
            procs[i] = spawn(label, cmd)
        else:
            _log("error", f"'{label}' exited with code {proc.returncode}. Shutting down...")
            raise SystemExit(1)


def _shutdown_all(procs: list) -> None:
    """Terminate every child process and wait for it to exit."""
    for proc in procs:
        proc.terminate()
    for proc in procs:
        proc.wait()
    _log("info", "All processes stopped.")


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

    # On Windows, Ctrl+C is broadcast to the entire console process group, so
    # every child would receive it directly and die before start.py could ask
    # for confirmation. Putting each child in its own group means only start.py
    # gets the signal; children are stopped explicitly via terminate() instead.
    # No-op on POSIX (creationflags must be 0 there).
    creationflags = (
        subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0
    )

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
            creationflags=creationflags,
        )
        thread = threading.Thread(target=stream, args=(proc, label), daemon=True)
        thread.start()
        _log("info", f"{label} started (pid {proc.pid})")
        return proc

    procs = [spawn(label, cmd) for label, cmd, _ in process_specs]

    _log("info", "All processes running. Press Ctrl+C to stop.")

    # Ctrl+C is caught per-iteration so it can prompt for confirmation: only a
    # 'y' breaks the loop, anything else resumes with all children untouched.
    # SystemExit (a non-restartable child died) escapes the inner handler and
    # falls through to the finally block for an unconditional shutdown.
    shutting_down = False
    try:
        while not shutting_down:
            try:
                _monitor_once(procs, process_specs, spawn)
                threading.Event().wait(timeout=2)
            except KeyboardInterrupt:
                shutting_down = _confirm_shutdown()
    finally:
        _shutdown_all(procs)


if __name__ == "__main__":
    main()
