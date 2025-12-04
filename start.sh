#!/bin/bash
# Start the Speech Prompter server
# Uses a clean environment to avoid Cursor's Python hijacking

cd "$(dirname "$0")"

# Required environment variables for PipeWire audio
export HOME="$HOME"
export XDG_RUNTIME_DIR="/run/user/$(id -u)"
export PATH="/usr/bin:/bin"

echo "Starting Speech Prompter..."
echo "Open http://localhost:8765 in your browser"
echo ""

exec ./venv/bin/python server.py

