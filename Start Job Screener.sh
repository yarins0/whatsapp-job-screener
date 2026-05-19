#!/usr/bin/env bash
# Start all Job Screener processes (Linux / macOS).
# Usage: bash start.sh   or   ./start.sh (after chmod +x start.sh)
set -euo pipefail
cd "$(dirname "$0")"
source venv/bin/activate
python start.py
