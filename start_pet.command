#!/usr/bin/env bash
# Double-click me to launch the screen pet.
# (If nothing happens, right-click -> Open the first time so macOS trusts it.)

set -e
cd "$(dirname "$0")"

VENV_PY="./.venv/bin/python"

# First-run convenience: create the venv and install deps if it's missing.
if [ ! -x "$VENV_PY" ]; then
  echo "Setting up Python environment (first run)…"
  if ! command -v python3 >/dev/null 2>&1; then
    echo "python3 not found. Install Python 3 first, then re-run." >&2
    read -n 1 -s -r -p "Press any key to close..."
    exit 1
  fi
  python3 -m venv .venv
  "$VENV_PY" -m pip install -q --upgrade pip
  "$VENV_PY" -m pip install -q -r requirements.txt
fi

# exec replaces this shell with the Python process; when the pet quits, the
# Terminal window will close automatically if your profile is set to
# "Close if the shell exited cleanly" (Terminal > Settings > Profiles > Shell).
exec "$VENV_PY" main.py
