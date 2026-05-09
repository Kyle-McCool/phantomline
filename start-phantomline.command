#!/usr/bin/env bash
# Phantomline launcher (macOS).
#
# Double-click this file in Finder to start the desktop server. The .command
# extension makes Finder run it in Terminal automatically. If you get a
# "cannot be opened because it is from an unidentified developer" warning the
# first time, right-click the file → Open → Open in the dialog. macOS only
# asks once per script.
#
# Before starting the server, _updater.py checks phantomline.xyz for a
# newer version and applies it if available. Update failures don't block
# server start. Skip the update check with --no-update.
#
# After ~3 seconds we open http://localhost:5000 in your default browser.
# Close this Terminal window (or press Ctrl+C) to stop the server.

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
echo "Opening your browser in 3 seconds. Leave this window open while you use Phantomline."
echo

( sleep 3 && open "http://localhost:5000" ) &

exec "$PY" server.py
