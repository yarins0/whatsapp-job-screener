"""Start all three processes in one terminal.

Usage:
    python start.py

Ctrl+C shuts everything down.
"""

from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
from dotenv import load_dotenv

load_dotenv()

# Force this process's stdout to UTF-8 so Hebrew/emoji printed from subprocess
# output isn't corrupted by the Windows system codepage (e.g. cp1255).
sys.stdout.reconfigure(encoding='utf-8', errors='replace')


def stream(proc: subprocess.Popen, label: str) -> None:
    """Forward a process's stdout+stderr to our stdout with a label prefix."""
    for line in proc.stdout:
        print(f"[{label}] {line}", end="", flush=True)


LISTENER_RESTART_DELAY = 8  # seconds to wait before restarting the listener

def main() -> None:
    python = sys.executable  # same venv Python that's running this script

    # (label, command, auto_restart)
    # The listener can recover by restarting; the API and digest cannot (they
    # hold critical state or ports that require a clean restart of everything).
    process_specs = [
        ("api",      [python, "-m", "uvicorn", "api.main:app", "--port", "8000"], False),
        ("digest",   [python, "-m", "digest.digest"],                             False),
        ("listener", ["node", "listener/listener.js"],                            True),
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
        print(f"[start] {label} started (pid {proc.pid})")
        return proc

    procs = [spawn(label, cmd) for label, cmd, _ in process_specs]

    print("[start] All processes running. Press Ctrl+C to stop.\n")

    try:
        while True:
            for i, (proc, (label, cmd, auto_restart)) in enumerate(zip(procs, process_specs)):
                if proc.poll() is not None:
                    if auto_restart:
                        print(f"\n[start] '{label}' exited (code {proc.returncode}). "
                              f"Restarting in {LISTENER_RESTART_DELAY}s...")
                        time.sleep(LISTENER_RESTART_DELAY)
                        procs[i] = spawn(label, cmd)
                    else:
                        print(f"\n[start] '{label}' exited with code {proc.returncode}. Shutting down...")
                        raise SystemExit(1)
            threading.Event().wait(timeout=2)
    except KeyboardInterrupt:
        print("\n[start] Shutting down...")
    finally:
        for proc in procs:
            proc.terminate()
        for proc in procs:
            proc.wait()
        print("[start] All processes stopped.")


if __name__ == "__main__":
    main()
