"""Start all three processes in one terminal.

Usage:
    python start.py

Ctrl+C shuts everything down.
"""

from __future__ import annotations

import subprocess
import sys
import threading
from dotenv import load_dotenv

load_dotenv()


def stream(proc: subprocess.Popen, label: str) -> None:
    """Forward a process's stdout+stderr to our stdout with a label prefix."""
    for line in proc.stdout:
        print(f"[{label}] {line}", end="", flush=True)


def main() -> None:
    python = sys.executable  # same venv Python that's running this script

    processes = [
        ("api",      [python, "-m", "uvicorn", "api.main:app", "--port", "8000"]),
        ("digest",   [python, "-m", "digest.digest"]),
        ("listener", ["node", "listener/listener.js"]),
    ]

    procs: list[subprocess.Popen] = []
    threads: list[threading.Thread] = []

    for label, cmd in processes:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        procs.append(proc)
        thread = threading.Thread(target=stream, args=(proc, label), daemon=True)
        thread.start()
        threads.append(thread)
        print(f"[start] {label} started (pid {proc.pid})")

    print("[start] All processes running. Press Ctrl+C to stop.\n")

    try:
        # Poll until any process exits unexpectedly, then shut everything down.
        while True:
            for proc, (label, _) in zip(procs, processes):
                if proc.poll() is not None:
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
