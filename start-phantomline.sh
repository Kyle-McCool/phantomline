#!/usr/bin/env bash
# Phantomline launcher (Linux).
#
# Run from the Phantomline folder:
#     ./start-phantomline.sh
# Or double-click in your file manager (most distros ask once whether to
# "Run in Terminal" or "Run"; pick "Run in Terminal" so you can see logs).
# If the file isn't executable yet:
#     chmod +x start-phantomline.sh
#
# Before starting the server, _updater.py checks phantomline.xyz for a
# newer version and applies it if available. Update failures don't block
# server start. Skip the update check with --no-update.
#
# After ~3 seconds we open http://localhost:5000 in your default browser
# (uses xdg-open). Close this terminal (or press Ctrl+C) to stop the server.

set -e

cd "$(dirname "$0")"

if [ -x ".venv/bin/python" ]; then
    PY=".venv/bin/python"
elif command -v python3 >/dev/null 2>&1; then
    PY="python3"
else
    PY="python"
fi

echo
echo "=== Phantomline ==="
echo

# Auto-update check. _updater.py exits 0 even on failure so this never
# blocks the server start. Pass --no-update to skip.
if [ "$1" != "--no-update" ]; then
    "$PY" _updater.py || true
fi

echo
echo "Starting local server on http://localhost:5000"
echo "Opening your browser in 3 seconds. Leave this terminal open while you use Phantomline."
echo

if command -v xdg-open >/dev/null 2>&1; then
    ( sleep 3 && xdg-open "http://localhost:5000" >/dev/null 2>&1 ) &
fi

exec "$PY" server.py
