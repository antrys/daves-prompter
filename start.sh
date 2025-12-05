#!/bin/bash
# Start Dave's Prompter server
# Uses a clean environment to avoid Cursor's Python hijacking

cd "$(dirname "$0")"

# Required environment variables for PipeWire audio
export HOME="$HOME"
export XDG_RUNTIME_DIR="/run/user/$(id -u)"
export PATH="/usr/bin:/bin"

echo "Starting Speech Prompter..."
echo ""

# Open browser after a short delay (gives server time to start)
(sleep 2 && xdg-open "http://localhost:8765" 2>/dev/null) &

exec ./venv/bin/python server.py

