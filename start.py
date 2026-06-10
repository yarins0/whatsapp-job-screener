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
import signal
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

# A listener that crashes again within this many seconds of starting is
# "thrashing" (failing during init) rather than recovering from a one-off
# fault. A crash after a longer healthy run resets the thrash counter so a
# stable process that dies once is not penalised for earlier trouble.
HEALTHY_UPTIME_SECONDS = 120

# After this many consecutive fast crashes a listener is retired (left down)
# instead of restarted forever. This decouples a persistently-broken source
# (e.g. an upstream WhatsApp Web breakage) from the rest of the stack: the
# other processes keep running and the logs stop thrashing. Restart start.py
# to retry a retired listener.
MAX_FAST_RESTARTS = 5

# Graceful-shutdown coordination. A custom SIGINT handler (installed in main)
# sets this event instead of raising KeyboardInterrupt, so no amount of Ctrl+C
# mashing can interrupt teardown with a traceback. Shutdown needs two presses
# within CONFIRM_WINDOW_SECONDS so a single stray Ctrl+C never quits.
_shutdown_event = threading.Event()
_last_sigint_at = [0.0]  # list so the handler can mutate it without `global`
CONFIRM_WINDOW_SECONDS = 5.0

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


def _ensure_schema() -> None:
    """Create the SQLite schema if it's missing so startup can't crash on it.

    The digest runs a retention cleanup the instant it boots, querying the
    ``jobs`` table directly. If the DB file is absent or empty (e.g. deleted
    out-of-band), that query raises and — because the digest is non-restartable
    — takes the whole stack down. Initialising here first makes a missing DB
    self-heal into a valid empty one. ``init_db`` is idempotent
    (CREATE TABLE IF NOT EXISTS), so running it on every launch is safe.
    """
    from db.init_db import init_db

    init_db()
    _log("info", "Database schema verified.")


def _cleanup_stale_resources() -> None:
    _kill_port(_API_PORT)

    # Remove the Chrome SingletonLock left behind if whatsapp crashed.
    if _WHATSAPP_LOCK.exists():
        try:
            _WHATSAPP_LOCK.unlink()
            _log("info", "Removed stale WhatsApp SingletonLock")
        except Exception:
            pass


def _request_shutdown(signum, frame) -> None:
    """SIGINT/SIGBREAK handler — confirm-on-second-press, never raises.

    Installed once at startup so Ctrl+C can never surface as a KeyboardInterrupt
    at an arbitrary point (which previously crashed teardown mid-kill). The first
    press arms shutdown and prints a hint; a second press within
    CONFIRM_WINDOW_SECONDS sets the event the main loop watches.
    """
    if _shutdown_event.is_set():
        return  # already shutting down — ignore further presses
    now = time.monotonic()
    if now - _last_sigint_at[0] <= CONFIRM_WINDOW_SECONDS:
        print("\n[start] Shutting down...", flush=True)
        _shutdown_event.set()
    else:
        _last_sigint_at[0] = now
        print("\n[start] Press Ctrl+C again to shut everything down.", flush=True)


def _monitor_once(procs: list, process_specs: list, spawn,
                  restart_counts: list, start_times: list) -> None:
    """One health-check pass: retire clean exits, restart crashed listeners.

    Mutates *procs*, *restart_counts*, and *start_times* in place. Raises
    SystemExit if a non-restartable process (the API or digest) exits, which
    propagates up to trigger shutdown. An auto-restart listener that crashes
    faster than HEALTHY_UPTIME_SECONDS more than MAX_FAST_RESTARTS times in a
    row is retired instead of looped on, so a broken source can't thrash.
    """
    for i, (proc, (label, cmd, auto_restart)) in enumerate(zip(procs, process_specs)):
        if proc.poll() is None:
            continue  # still running
        if proc.returncode == 0:
            _log("info", f"'{label}' exited cleanly (code 0). Not restarting.")
            procs[i] = _NullProc()
        elif auto_restart:
            # A long, healthy run before this crash means earlier failures are
            # ancient history — reset the budget so a one-off death recovers.
            if time.monotonic() - start_times[i] >= HEALTHY_UPTIME_SECONDS:
                restart_counts[i] = 0
            restart_counts[i] += 1
            if restart_counts[i] > MAX_FAST_RESTARTS:
                _log("error", f"'{label}' crashed {MAX_FAST_RESTARTS} times in a row — leaving it down. The rest of the stack keeps running; restart start.py to retry it.")
                procs[i] = _NullProc()
                continue
            _log("warning", f"'{label}' crashed (code {proc.returncode}). Restart {restart_counts[i]}/{MAX_FAST_RESTARTS} in {LISTENER_RESTART_DELAY}s...")
            time.sleep(LISTENER_RESTART_DELAY)
            procs[i] = spawn(label, cmd)
            start_times[i] = time.monotonic()
        else:
            _log("error", f"'{label}' exited with code {proc.returncode}. Shutting down...")
            raise SystemExit(1)


def _terminate_tree(proc) -> None:
    """Kill a child process *and its entire descendant tree*.

    ``terminate()`` only kills the direct child, orphaning grandchildren — most
    importantly the Chrome instance puppeteer launches for the WhatsApp
    listener, which would keep running after start.py exits. On Windows
    ``taskkill /F /T`` takes down the whole tree; elsewhere terminate() is used.
    """
    if not isinstance(proc, subprocess.Popen):
        proc.terminate()  # _NullProc — no real OS process behind it
        return
    if proc.poll() is not None:
        return  # already exited

    if sys.platform == "win32":
        subprocess.run(
            ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
            capture_output=True,
        )
    else:
        proc.terminate()


def _shutdown_all(procs: list) -> None:
    """Kill every child process tree and wait (bounded) for each to exit."""
    # Fully ignore Ctrl+C now that teardown has begun — silences the "press
    # again" hint and stops the handler firing mid-kill. Safe to set here because
    # the active handler (_request_shutdown) never raises, so even a press landing
    # on this very line can't produce a traceback.
    try:
        signal.signal(signal.SIGINT, signal.SIG_IGN)
    except (ValueError, OSError):
        pass  # best effort — not callable outside the main thread

    for proc in procs:
        _terminate_tree(proc)
    for proc in procs:
        # Bounded wait so a stuck child can never hang the shutdown.
        try:
            proc.wait(timeout=10)
        except Exception:
            pass
    _log("info", "All processes stopped.")


def main() -> None:
    python = sys.executable  # same venv Python that's running this script

    _ensure_schema()
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
    # Per-process circuit-breaker state, parallel to procs/process_specs.
    restart_counts = [0] * len(process_specs)
    start_times = [time.monotonic()] * len(process_specs)

    # Install the custom Ctrl+C handler *before* announcing readiness, so the
    # very first press is handled gracefully (sets _shutdown_event) instead of
    # raising KeyboardInterrupt. SIGBREAK covers Ctrl+Break on Windows.
    signal.signal(signal.SIGINT, _request_shutdown)
    if hasattr(signal, "SIGBREAK"):
        signal.signal(signal.SIGBREAK, _request_shutdown)

    _log("info", "All processes running. Press Ctrl+C twice to stop.")

    # SystemExit (a non-restartable child died) escapes _monitor_once and falls
    # through to the finally block for an unconditional shutdown.
    try:
        while not _shutdown_event.is_set():
            _monitor_once(procs, process_specs, spawn, restart_counts, start_times)
            _shutdown_event.wait(timeout=2)
    finally:
        _shutdown_all(procs)


if __name__ == "__main__":
    main()
