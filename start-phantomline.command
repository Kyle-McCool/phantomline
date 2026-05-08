#!/usr/bin/env bash
# Phantomline launcher (macOS).
#
# Double-click this file in Finder to start the desktop server. The .command
# extension makes Finder run it in Terminal automatically. If you get a
# "cannot be opened because it is from an unidentified developer" warning the
# first time, right-click the file → Open → Open in the dialog. macOS only
# asks once per script.
#
# If a `.venv` exists alongside this script, we use it so the system-wide
# Python install isn't required to be the right version. Otherwise we fall
# back to whatever `python3` resolves to.
#
# After ~3 seconds we open http://localhost:5000 in your default browser.
# Close this Terminal window (or press Ctrl+C) to stop the server.

set -e

# cd to the directory this script lives in (handles spaces in the path).
cd "$(dirname "$0")"

# Prefer the project venv.
if [ -x ".venv/bin/python" ]; then
    PY=".venv/bin/python"
elif command -v python3 >/dev/null 2>&1; then
    PY="python3"
else
    PY="python"
fi

echo
echo "=== Phantomline ==="
echo "Starting local server on http://localhost:5000"
echo "Opening your browser in 3 seconds. Leave this window open while you use Phantomline."
echo

# Open the browser in the background after a short delay so the server has
# time to bind. The subshell + & makes it non-blocking.
( sleep 3 && open "http://localhost:5000" ) &

exec "$PY" server.py
