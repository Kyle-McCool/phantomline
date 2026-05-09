@echo off
rem Phantomline launcher (Windows).
rem
rem Double-click this file from your Phantomline folder to start the
rem desktop server. If a `.venv` exists alongside this script, we use it
rem so the system-wide Python install isn't required to be the right
rem version. Otherwise we fall back to whatever `python` resolves to.
rem
rem Before starting the server, we run _updater.py which checks
rem phantomline.xyz/api/system/version, downloads the new source zip if
rem newer, and applies it (skipping output/, .env, etc). Update failures
rem don't block server start — worst case you stay on the current version.
rem
rem After ~3 seconds we open http://localhost:5000 in your default
rem browser. Close this window (or press Ctrl+C) to stop the server.

setlocal
cd /d "%~dp0"

rem Prefer the project venv if it exists.
if exist ".venv\Scripts\python.exe" (
    set "PYTHON=.venv\Scripts\python.exe"
) else (
    set "PYTHON=python"
)

echo.
echo === Phantomline ===
echo.

rem Auto-update check. _updater.py exits 0 even on failure (network down,
rem etc) so this never blocks the server start. Skip with --no-update.
if not "%1"=="--no-update" (
    "%PYTHON%" _updater.py
)

echo.
echo Starting local server on http://localhost:5000
echo Opening your browser in 3 seconds. Leave this window open while you use Phantomline.
echo.

rem Open the browser in the background after a short delay so the server has
rem time to bind. `start` returns immediately so we don't block the python
rem launch below.
start "" /b cmd /c "ping -n 4 127.0.0.1 >nul && start http://localhost:5000"

"%PYTHON%" server.py

echo.
echo Phantomline server stopped. You can close this window.
pause >nul
