#!/usr/bin/env bash
# PlanGrab — macOS / dev launcher.
# Creates a local .venv on first run, installs deps, then starts the web app
# and opens your browser. Ctrl-C to stop.
set -euo pipefail
cd "$(dirname "$0")"

PY="${PYTHON:-python3}"
VENV=".venv"

if [ ! -d "$VENV" ]; then
  echo "Creating virtual environment…"
  "$PY" -m venv "$VENV"
  "$VENV/bin/python" -m pip install --quiet --upgrade pip
  "$VENV/bin/python" -m pip install --quiet -r requirements.txt
fi

exec "$VENV/bin/python" -m plangrab.web.server
