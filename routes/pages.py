"""Static marketing and informational pages, install guides, and the
self-hosted source download.

Routes: /, /pricing, /releases, /about, /privacy, /terms, /landing,
/install, /install/<tool>, /download, /download/phantomline-source.zip.

No business logic — just template dispatch and the INSTALL_TOOLS data
dict that feeds the install guide templates."""

import os
import threading
import time
from pathlib import Path

from flask import Blueprint, jsonify, redirect, render_template, request, send_file

from core import BASE_DIR, OUTPUT_DIR

pages_bp = Blueprint("pages", __name__)


# ---------------------------------------------------------------------------
# Simple content pages
# ---------------------------------------------------------------------------

@pages_bp.route("/")
def root():
    from routes.billing import PRICING
    return render_template("landing.html", pricing=PRICING)


@pages_bp.route("/pricing")
def pricing_page():
    from routes.billing import PRICING
    return render_template("pricing.html", pricing=PRICING)


@pages_bp.route("/releases")
@pages_bp.route("/releases/")
def releases_page():
    md_path = Path(__file__).resolve().parent.parent / "RELEASES.md"
    raw = ""
    try:
        raw = md_path.read_text(encoding="utf-8")
    except OSError:
        raw = "# Phantomline Release Notes\n\nNo release notes available yet."
    return render_template("releases.html", releases_md=raw)


@pages_bp.route("/about")
def about_page():
    return render_template("about.html")


@pages_bp.route("/privacy")
def privacy_page():
    return render_template("privacy.html")


@pages_bp.route("/terms")
def terms_page():
    return render_template("terms.html")


@pages_bp.route("/landing")
@pages_bp.route("/landing/")
def landing():
    return redirect("/", code=301)


# ---------------------------------------------------------------------------
# Friendly install pages for the optional power-user engines (Ollama, Kokoro,
# Forge). The hosted readiness checklist links to /install/<tool> instead of
# raw GitHub READMEs so non-technical users get OS-detected one-liners, a
# Claude Code paste-prompt, and manual steps as fallback.
# ---------------------------------------------------------------------------
INSTALL_TOOLS = {
    "ollama": {
        "label": "Ollama",
        "subtitle": "Local LLM runtime. Unlocks bigger models (Llama 3.1 8B, Mistral, Qwen) than the in-browser Llama 3.2 1B.",
        "downloads": [
            {"os": "Windows", "url": "https://ollama.com/download/OllamaSetup.exe",
             "label": "Download OllamaSetup.exe", "size": "~600 MB"},
            {"os": "macOS", "url": "https://ollama.com/download/Ollama-darwin.zip",
             "label": "Download Ollama for macOS", "size": "~250 MB"},
            {"os": "Linux", "url": "https://ollama.com/download/linux",
             "label": "Linux install instructions", "size": "script"},
        ],
        "windows_oneliner": (
            "winget install Ollama.Ollama; "
            "Start-Sleep -Seconds 3; ollama pull llama3.1"
        ),
        "unix_oneliner": (
            "curl -fsSL https://ollama.com/install.sh | sh && "
            "ollama pull llama3.1"
        ),
        "claude_prompt": (
            "Install Ollama on this machine and pull the llama3.1 model so I can use it with "
            "Phantomline.\n\n"
            "1. Detect my OS (Windows / macOS / Linux).\n"
            "2. Install Ollama using the official installer (winget on Windows, the install.sh "
            "script on macOS/Linux, or download the .dmg/.exe if scripts aren't available).\n"
            "3. Wait for the Ollama daemon to start (it runs at http://localhost:11434).\n"
            "4. Run `ollama pull llama3.1` to download the default Phantomline model "
            "(~4.7 GB).\n"
            "5. Verify by running `ollama list` and confirming llama3.1 appears.\n"
            "6. Tell me when it's done so I can reload http://localhost:5000/app and see it "
            "marked READY."
        ),
        "manual_steps": [
            {"title": "Download the installer",
             "body": "Go to <a href=\"https://ollama.com/download\" target=\"_blank\" rel=\"noopener\">ollama.com/download</a> and grab the build for your OS (Windows .exe, macOS .dmg, or Linux script).",
             "command": "",
             "screenshot": "ollama-1-download.png"},
            {"title": "Run the installer",
             "body": "Double-click the .exe / .dmg, or pipe the Linux script into sh. Default options are fine. After install, Ollama runs as a background service. You'll see a small llama icon in your system tray.",
             "command": "curl -fsSL https://ollama.com/install.sh | sh",
             "screenshot": "ollama-2-installer.png"},
            {"title": "Pull the default model",
             "body": "Open a terminal/PowerShell after install and run the command below. Llama 3.1 is ~4.7 GB so this takes 5-15 min depending on your connection.",
             "command": "ollama pull llama3.1",
             "expected": "pulling manifest\npulling 6a0746a1ec1a... 100% ▕████████▏ 4.7 GB\npulling 4fa551d4f938... 100% ▕████████▏  12 KB\nverifying sha256 digest\nwriting manifest\nsuccess",
             "screenshot": "ollama-3-pull.png"},
        ],
        "verify_text": "Run this in a terminal. It should list llama3.1 (size ~4.7 GB).",
        "verify_command": "ollama list",
    },
    "kokoro": {
        "label": "Kokoro voices",
        "subtitle": "High-quality neural TTS. Meaningfully better narration than Web Speech, especially for long-form.",
        "downloads": [
            {"os": "Windows", "url": "https://www.python.org/ftp/python/3.11.9/python-3.11.9-amd64.exe",
             "label": "Download Python 3.11 for Windows", "size": "~25 MB"},
            {"os": "macOS", "url": "https://www.python.org/ftp/python/3.11.9/python-3.11.9-macos11.pkg",
             "label": "Download Python 3.11 for macOS", "size": "~40 MB"},
            {"os": "Linux", "url": "https://www.python.org/downloads/source/",
             "label": "Python source / package manager", "size": "varies"},
        ],
        "downloads_note": "Kokoro is a Python package. Install Python 3.11 first, then run the 1-line install below to fetch Kokoro itself (~327 MB voice model included).",
        "windows_oneliner": (
            "py -3.11 -m pip install --upgrade kokoro soundfile; "
            "py -3.11 -c \"from kokoro import KPipeline; KPipeline(lang_code='a')\""
        ),
        "unix_oneliner": (
            "python3 -m pip install --upgrade kokoro soundfile && "
            "python3 -c \"from kokoro import KPipeline; KPipeline(lang_code='a')\""
        ),
        "claude_prompt": (
            "Install Kokoro TTS (high-quality neural text-to-speech) so Phantomline can use it "
            "for narration.\n\n"
            "1. Verify Python 3.10+ is installed (run `python --version` or `py -3.11 --version` "
            "on Windows). If missing, install Python 3.11 from python.org or via Homebrew/apt.\n"
            "2. Install pip packages: `pip install kokoro soundfile` (use `pip3` or "
            "`py -3.11 -m pip install` if `pip` is wrong version).\n"
            "3. On macOS, also `brew install espeak-ng`. On Debian/Ubuntu: `sudo apt install "
            "espeak-ng`. Windows: skip, Kokoro will use its bundled phonemizer.\n"
            "4. Trigger the first model download by running this Python: `from kokoro import "
            "KPipeline; KPipeline(lang_code='a')`. Pulls ~327 MB of weights from Hugging Face.\n"
            "5. Verify by running: `python -c \"from kokoro import KPipeline; p = "
            "KPipeline(lang_code='a'); print('Kokoro OK:', list(p.voices))\"`. Should print a "
            "list of voice IDs.\n"
            "6. Tell me when done so I can reload Phantomline and see Kokoro voices appear in "
            "the voice picker."
        ),
        "manual_steps": [
            {"title": "Install Python 3.10 or newer",
             "body": "Kokoro needs Python 3.10+. Check with <code>python --version</code>. If you don't have it: download from <a href=\"https://www.python.org/downloads/\" target=\"_blank\" rel=\"noopener\">python.org/downloads</a> (Windows/macOS) or <code>sudo apt install python3.11</code> (Linux).",
             "command": "python --version"},
            {"title": "Install Kokoro and audio deps",
             "body": "From any terminal. Pip will fetch the Kokoro package and the soundfile audio writer.",
             "command": "pip install kokoro soundfile"},
            {"title": "(macOS / Linux only) Install espeak-ng",
             "body": "Kokoro's phonemizer needs espeak-ng on Unix. macOS: <code>brew install espeak-ng</code>. Debian/Ubuntu: <code>sudo apt install espeak-ng</code>. <a href=\"https://brew.sh\" target=\"_blank\" rel=\"noopener\">Get Homebrew</a> if you don't have it.",
             "command": "brew install espeak-ng   # macOS\nsudo apt install espeak-ng   # Linux"},
            {"title": "Pre-download the voice model",
             "body": "First Kokoro use pulls ~327 MB of weights. Doing it once now means your first narration is instant.",
             "command": "python -c \"from kokoro import KPipeline; KPipeline(lang_code='a')\""},
        ],
        "verify_text": "Should print a list of voice IDs (af_bella, af_nicole, etc.) without errors.",
        "verify_command": "python -c \"from kokoro import KPipeline; p = KPipeline(lang_code='a'); print(list(p.voices))\"",
    },
    "forge": {
        "label": "Forge (Stable Diffusion)",
        "subtitle": "Local AI image generation. Full control over scene art, no rate limits, no per-image cost.",
        "downloads": [
            {"os": "Windows", "url": "https://git-scm.com/download/win",
             "label": "Download Git for Windows", "size": "~50 MB"},
            {"os": "macOS", "url": "https://git-scm.com/download/mac",
             "label": "Git for macOS (via brew/installer)", "size": "varies"},
            {"os": "Linux", "url": "https://git-scm.com/download/linux",
             "label": "Git for Linux (apt/dnf)", "size": "varies"},
        ],
        "downloads_note": "Forge isn't a single .exe. It's a Python project you clone with Git. Install Git first, then run the 1-line install below. First launch downloads ~6 GB of model weights (one-time, takes 15-30 min on a typical connection).",
        "windows_oneliner": (
            "git clone https://github.com/lllyasviel/stable-diffusion-webui-forge "
            "$HOME\\forge; cd $HOME\\forge; .\\webui-user.bat"
        ),
        "unix_oneliner": (
            "git clone https://github.com/lllyasviel/stable-diffusion-webui-forge "
            "~/forge && cd ~/forge && ./webui.sh --api"
        ),
        "claude_prompt": (
            "Install Stable Diffusion WebUI Forge so Phantomline can use it for local AI image "
            "generation.\n\n"
            "1. Detect my OS and confirm Git and Python 3.10+ are installed. If missing, "
            "install them first (Git from git-scm.com, Python from python.org).\n"
            "2. Clone the repo into ~/forge (or %USERPROFILE%\\forge on Windows): "
            "`git clone https://github.com/lllyasviel/stable-diffusion-webui-forge ~/forge`.\n"
            "3. cd into the directory.\n"
            "4. On Windows: run `webui-user.bat`. On macOS/Linux: run `./webui.sh --api` (the "
            "--api flag is required so Phantomline can call it). First launch downloads ~4 GB of "
            "Python deps and ~6 GB of SDXL base model. Can take 15-30 min on first run.\n"
            "5. Once it logs 'Running on local URL: http://127.0.0.1:7861', leave the terminal "
            "open. Forge needs to keep running for Phantomline to use it.\n"
            "6. Verify by opening http://127.0.0.1:7861 in a browser. You should see the Forge "
            "UI. Then tell me so I can reload Phantomline."
        ),
        "manual_steps": [
            {"title": "Install Git and Python 3.10+",
             "body": "Forge needs both. Git: <a href=\"https://git-scm.com/downloads\" target=\"_blank\" rel=\"noopener\">git-scm.com/downloads</a>. Python: <a href=\"https://www.python.org/downloads/\" target=\"_blank\" rel=\"noopener\">python.org/downloads</a> (3.10 or 3.11, NOT 3.12+, Forge has compatibility issues with 3.12).",
             "command": "git --version && python --version"},
            {"title": "Clone the Forge repo",
             "body": "Pick a folder with ~20 GB of free space (the model weights are big). Source: <a href=\"https://github.com/lllyasviel/stable-diffusion-webui-forge\" target=\"_blank\" rel=\"noopener\">github.com/lllyasviel/stable-diffusion-webui-forge</a>.",
             "command": "git clone https://github.com/lllyasviel/stable-diffusion-webui-forge ~/forge"},
            {"title": "Launch with API enabled",
             "body": "The <code>--api</code> flag exposes Forge's REST endpoint on port 7861, which is what Phantomline calls. Windows users just double-click <code>webui-user.bat</code> (API is on by default).",
             "command": "cd ~/forge && ./webui.sh --api   # macOS/Linux\n# Windows: just double-click webui-user.bat"},
            {"title": "Wait for first-run downloads (~6 GB)",
             "body": "First launch downloads SDXL base + dependencies. Watch for the line <code>Running on local URL: http://127.0.0.1:7861</code>: that means it's ready.",
             "command": ""},
        ],
        "verify_text": "Open <a href=\"http://127.0.0.1:7861\" target=\"_blank\" rel=\"noopener\">http://127.0.0.1:7861</a> in your browser. You should see the Forge UI. Keep the terminal window open while you use Phantomline.",
        "verify_command": "curl http://127.0.0.1:7861/sdapi/v1/options",
    },
    "phantomline": {
        "label": "Phantomline desktop",
        "subtitle": "The full studio that runs locally on your machine. Uses your own Ollama, Kokoro, and Forge for unlimited high-quality renders. License-key activated. Auto-starts on boot so phantomline.xyz can switch to your local install with one click.",
        "downloads_note": "Phantomline desktop runs as a tiny background service on your computer (auto-starts on boot, lives in your system tray). When you visit phantomline.xyz from any browser, the 'Local Phantomline' button in the studio toggle opens your local install in a new tab. Projects sync between the two via your account so the same library follows you everywhere.",
        "downloads": [
            {"os": "All", "url": "/download/phantomline-source.zip",
             "label": "Download Phantomline source (Python · Windows · macOS · Linux)",
             "size": "~30 MB"},
        ],
        "windows_oneliner": (
            "Invoke-WebRequest https://phantomline.xyz/download/phantomline-source.zip "
            "-OutFile phantomline.zip; Expand-Archive phantomline.zip -DestinationPath . -Force; "
            "cd phantomline; py -3.11 -m venv .venv; .\\.venv\\Scripts\\pip install -r requirements.txt; "
            ".\\.venv\\Scripts\\python server.py"
        ),
        "unix_oneliner": (
            "curl -L https://phantomline.xyz/download/phantomline-source.zip -o phantomline.zip && "
            "unzip phantomline.zip && cd phantomline && python3 -m venv .venv && "
            ".venv/bin/pip install -r requirements.txt && .venv/bin/python server.py"
        ),
        "claude_prompt": (
            "Install Phantomline desktop on this machine so I can use my local Ollama for "
            "high-quality video generation. Set up auto-start so it always runs in the "
            "background and the phantomline.xyz toggle can switch to it with one click.\n\n"
            "1. Verify Python 3.11+ is installed (run `python --version` or `py -3.11 "
            "--version` on Windows). If missing, install Python 3.11 from python.org or via "
            "Homebrew/apt. Phantomline does not work on Python 3.12+ yet (Forge dependency).\n"
            "2. Download the Phantomline source ZIP from "
            "https://phantomline.xyz/download/phantomline-source.zip into a permanent "
            "location (NOT a temp dir — auto-start will reference this path forever). "
            "Use curl/wget on macOS/Linux: "
            "`curl -L https://phantomline.xyz/download/phantomline-source.zip -o phantomline.zip`. "
            "Or Invoke-WebRequest on Windows: "
            "`Invoke-WebRequest https://phantomline.xyz/download/phantomline-source.zip "
            "-OutFile phantomline.zip`. The ZIP is self-hosted on phantomline.xyz so no GitHub "
            "account is needed.\n"
            "3. Extract the ZIP. macOS/Linux: `unzip phantomline.zip`. Windows: "
            "`Expand-Archive phantomline.zip -DestinationPath . -Force`. This creates a "
            "`phantomline/` folder.\n"
            "4. cd into the `phantomline` directory.\n"
            "5. Create a virtual environment: `python -m venv .venv` (use `py -3.11 -m venv "
            ".venv` on Windows). Activate it: `source .venv/bin/activate` on macOS/Linux, "
            "`.venv\\Scripts\\activate` on Windows.\n"
            "6. Install Python dependencies: `pip install -r requirements.txt`. This takes "
            "5-10 min.\n"
            "7. Confirm Ollama is running: `ollama list` should show llama3.1 (or pull it "
            "with `ollama pull llama3.1`).\n"
            "8. SET UP AUTO-START so server.py runs every time the user logs in. Use the "
            "right method for the OS:\n"
            "   - Windows: Create a Scheduled Task that runs at user logon. Use "
            "`schtasks /create /tn Phantomline /tr \"<absolute path to .venv\\Scripts\\"
            "pythonw.exe>\\\" \\\"<absolute path to server.py>\\\"\" /sc onlogon /rl highest`. "
            "Use pythonw.exe (not python.exe) so no terminal window appears.\n"
            "   - macOS: Create ~/Library/LaunchAgents/com.phantomline.server.plist with "
            "<plist><dict><key>Label</key><string>com.phantomline.server</string><key>"
            "ProgramArguments</key><array><string>/absolute/path/to/.venv/bin/python</string>"
            "<string>/absolute/path/to/server.py</string></array><key>RunAtLoad</key><true/>"
            "<key>KeepAlive</key><true/></dict></plist>. Then run `launchctl load ~/Library/"
            "LaunchAgents/com.phantomline.server.plist`.\n"
            "   - Linux: Create ~/.config/systemd/user/phantomline.service with "
            "[Unit]\\nDescription=Phantomline\\n[Service]\\nExecStart=/absolute/path/to/.venv/"
            "bin/python /absolute/path/to/server.py\\nRestart=always\\n[Install]\\nWantedBy="
            "default.target. Then run `systemctl --user enable --now phantomline`.\n"
            "9. Verify auto-start works: open http://localhost:5000/api/health in a browser. "
            "Should return JSON with `\"ok\": true`. If yes, the background service is up.\n"
            "10. Open http://localhost:5000/account, sign in with my Google account. License "
            "auto-issues as Free tier (5 renders/month) or activates whatever paid license is "
            "associated with my email.\n"
            "11. Tell me when it's done so I can open phantomline.xyz, click the studio "
            "header toggle, and switch to my local install with one click."
        ),
        "manual_steps": [
            {"title": "Install Python 3.11",
             "body": "Phantomline desktop runs on Python. Get Python 3.11 from <a href=\"https://www.python.org/downloads/\" target=\"_blank\" rel=\"noopener\">python.org/downloads</a> (NOT 3.12+ yet, has Forge compatibility issues). No Git or GitHub account needed — we host the download directly.",
             "command": "python --version"},
            {"title": "Download Phantomline (ZIP, self-hosted)",
             "body": "Direct download from phantomline.xyz. No GitHub login required. Pick a folder with ~5 GB free (Phantomline + Python deps). Or just click the big download button at the top of this page.",
             "command": "# macOS / Linux:\ncurl -L https://phantomline.xyz/download/phantomline-source.zip -o phantomline.zip\nunzip phantomline.zip\ncd phantomline\n\n# Windows (PowerShell):\nInvoke-WebRequest https://phantomline.xyz/download/phantomline-source.zip -OutFile phantomline.zip\nExpand-Archive phantomline.zip -DestinationPath . -Force\ncd phantomline"},
            {"title": "Install Phantomline's Python dependencies",
             "body": "Use a virtual environment so Phantomline's libs don't collide with your system Python. Takes 5-10 min on first install.",
             "command": "python -m venv .venv\nsource .venv/bin/activate   # macOS/Linux\n# .venv\\Scripts\\activate      # Windows\npip install -r requirements.txt"},
            {"title": "Install Ollama if you haven't yet",
             "body": "Phantomline uses your local Ollama for script + idea + title generation. Get the friendly install guide at <a href=\"/install/ollama\">/install/ollama</a>.",
             "command": "ollama pull llama3.1"},
            {"title": "Start Phantomline",
             "body": "<strong>Easiest:</strong> double-click the launcher file in your phantomline folder. <ul style=\"margin:6px 0 0 20px;\"><li><strong>Windows:</strong> double-click <code>start-phantomline.bat</code></li><li><strong>macOS:</strong> double-click <code>start-phantomline.command</code> (right-click → Open the first time)</li><li><strong>Linux:</strong> run <code>./start-phantomline.sh</code></li></ul>The launcher activates the venv, starts the server on port 5000, and opens your browser automatically. Leave the terminal window open — closing it stops Phantomline. (For an always-on background service, see step 6.)",
             "command": "# Or, if you'd rather run it manually:\npython server.py"},
            {"title": "Set up auto-start so it always runs in the background",
             "body": "Once-and-done. After this, Phantomline runs on every login and the phantomline.xyz toggle can switch to your local install with one click. Pick the command for your OS. Replace <code>&lt;ABSOLUTE_PATH&gt;</code> with the full path to your phantomline folder.",
             "command": "# Windows (PowerShell as Administrator):\nschtasks /create /tn Phantomline /tr \"<ABSOLUTE_PATH>\\.venv\\Scripts\\pythonw.exe <ABSOLUTE_PATH>\\server.py\" /sc onlogon /rl highest\n\n# macOS (creates a LaunchAgent):\ncat > ~/Library/LaunchAgents/com.phantomline.server.plist <<'EOF'\n<?xml version=\"1.0\" encoding=\"UTF-8\"?>\n<plist version=\"1.0\"><dict>\n  <key>Label</key><string>com.phantomline.server</string>\n  <key>ProgramArguments</key>\n  <array>\n    <string><ABSOLUTE_PATH>/.venv/bin/python</string>\n    <string><ABSOLUTE_PATH>/server.py</string>\n  </array>\n  <key>RunAtLoad</key><true/>\n  <key>KeepAlive</key><true/>\n</dict></plist>\nEOF\nlaunchctl load ~/Library/LaunchAgents/com.phantomline.server.plist\n\n# Linux (systemd user service):\nmkdir -p ~/.config/systemd/user\ncat > ~/.config/systemd/user/phantomline.service <<EOF\n[Unit]\nDescription=Phantomline desktop server\n[Service]\nExecStart=<ABSOLUTE_PATH>/.venv/bin/python <ABSOLUTE_PATH>/server.py\nRestart=always\n[Install]\nWantedBy=default.target\nEOF\nsystemctl --user enable --now phantomline"},
            {"title": "Sign in with your license",
             "body": "Open <code>http://localhost:5000/account</code>, sign in with the same Google account. Free tier (5 renders/month) auto-issues; paid licenses activate from your account email. Your projects + library will sync to phantomline.xyz so you can view them from any device.",
             "command": ""},
        ],
        "verify_text": "Open <a href=\"http://localhost:5000/app\" target=\"_blank\" rel=\"noopener\">http://localhost:5000/app</a> in your browser. You should see the Phantomline studio with your local Ollama detected. Make a quick test video to confirm everything works.",
        "verify_command": "curl http://localhost:5000/api/health",
    },
    "youtube-api": {
        "label": "YouTube Data API key",
        "subtitle": "Free API key from Google. Unlocks live keyword ranking, competitor research, and SEO signals in your video packages.",
        "downloads": [],
        "windows_oneliner": "",
        "unix_oneliner": "",
        "claude_prompt": (
            "Help me get a YouTube Data API v3 key from Google Cloud Console and add it to "
            "my Phantomline .env file.\n\n"
            "1. Open https://console.cloud.google.com/ and sign in with my Google account.\n"
            "2. Create a new project called 'Phantomline' (or use an existing one).\n"
            "3. Go to APIs & Services > Library, search for 'YouTube Data API v3', and enable it.\n"
            "4. Go to APIs & Services > Credentials, click 'Create Credentials' > 'API key'.\n"
            "5. Copy the key. Optionally restrict it to YouTube Data API v3 only.\n"
            "6. Open my Phantomline .env file and add: YOUTUBE_API_KEY=<the key>\n"
            "7. Restart Phantomline (python server.py) so it picks up the new key.\n"
            "8. Verify by checking the Launch Setup tab — YouTube research API should show READY."
        ),
        "manual_steps": [
            {"title": "Sign in to Google Cloud Console",
             "body": "Open <a href=\"https://console.cloud.google.com/\" target=\"_blank\" rel=\"noopener\">console.cloud.google.com</a> and sign in with any Google account. The YouTube Data API is free (10,000 units/day — Phantomline uses ~5 units per research query, so you'll never hit the limit).",
             "command": ""},
            {"title": "Create a project",
             "body": "Click the project dropdown at the top of the page, then <strong>New Project</strong>. Name it anything (e.g. 'Phantomline'). If you already have a project you can reuse it.",
             "command": ""},
            {"title": "Enable the YouTube Data API v3",
             "body": "Go to <a href=\"https://console.cloud.google.com/apis/library/youtube.googleapis.com\" target=\"_blank\" rel=\"noopener\">APIs &amp; Services → Library</a>, search for <strong>YouTube Data API v3</strong>, click it, then click <strong>Enable</strong>. This takes a few seconds.",
             "command": ""},
            {"title": "Create an API key",
             "body": "Go to <a href=\"https://console.cloud.google.com/apis/credentials\" target=\"_blank\" rel=\"noopener\">APIs &amp; Services → Credentials</a>. Click <strong>Create Credentials → API key</strong>. A key will appear in a popup — copy it. Optionally click 'Restrict key' and limit it to YouTube Data API v3 only (recommended but not required).",
             "command": ""},
            {"title": "Add the key to Phantomline",
             "body": "Open the <code>.env</code> file in your Phantomline folder (create it if it doesn't exist) and add the line below. Replace <code>YOUR_KEY_HERE</code> with the key you copied.",
             "command": "YOUTUBE_API_KEY=YOUR_KEY_HERE"},
            {"title": "Restart Phantomline",
             "body": "Stop the server (Ctrl+C in the terminal) and start it again. The new key is loaded on startup.",
             "command": "python server.py"},
        ],
        "verify_text": "Reload the studio and open the Launch Setup tab. 'YouTube research API' should show a green dot with 'Live keyword ranking available'.",
        "verify_command": "curl http://localhost:5000/api/launch/readiness",
    },
    "gemini-api": {
        "label": "Google Gemini API key",
        "subtitle": "Free cloud AI for script generation. No credit card required. Generous free tier with Gemini 2.0 Flash and 2.5 Pro.",
        "downloads": [],
        "downloads_note": "No download needed — just grab a free API key from Google AI Studio.",
        "windows_oneliner": "",
        "unix_oneliner": "",
        "claude_prompt": (
            "Help me get a free Google Gemini API key for Phantomline.\n\n"
            "1. Open https://aistudio.google.com/apikey in my browser.\n"
            "2. Sign in with my Google account.\n"
            "3. Click 'Create API Key' and select or create a Google Cloud project.\n"
            "4. Copy the generated key.\n"
            "5. In Phantomline, go to Settings → AI engine → Cloud, choose 'Gemini' as the "
            "provider, and paste the key.\n"
            "6. The key is saved to my browser's localStorage — it never touches Phantomline's "
            "server.\n"
            "7. Test by generating a script. It should complete in a few seconds using Gemini "
            "Flash."
        ),
        "manual_steps": [
            {"title": "Open Google AI Studio",
             "body": "Go to <a href=\"https://aistudio.google.com/apikey\" target=\"_blank\" rel=\"noopener\">aistudio.google.com/apikey</a> and sign in with any Google account. No credit card is required.",
             "command": ""},
            {"title": "Create an API key",
             "body": "Click <strong>Create API Key</strong>. Select an existing Google Cloud project or let it create one for you. The key appears instantly — copy it.",
             "command": ""},
            {"title": "Paste the key into Phantomline",
             "body": "Open <a href=\"/app\">the studio</a>, go to <strong>Settings → AI engine → Cloud</strong>. Select <strong>Gemini</strong> as the provider and paste your key. It's saved to your browser's localStorage and never sent to Phantomline's server.",
             "command": ""},
            {"title": "Generate a test script",
             "body": "Pick any topic and hit Generate. Your browser calls the Gemini API directly. Scripts come back in a few seconds. The free tier supports Gemini 2.0 Flash and Gemini 2.5 Pro with generous rate limits.",
             "command": ""},
        ],
        "verify_text": "Generate a script in the studio. If it completes successfully, your Gemini API key is working.",
        "verify_command": "",
    },
    "openrouter-api": {
        "label": "OpenRouter API key",
        "subtitle": "Unified API gateway to hundreds of AI models. Many models available for free — no credit card required.",
        "downloads": [],
        "downloads_note": "No download needed — just create a free OpenRouter account and grab an API key.",
        "windows_oneliner": "",
        "unix_oneliner": "",
        "claude_prompt": (
            "Help me get an OpenRouter API key for Phantomline.\n\n"
            "1. Open https://openrouter.ai/keys in my browser.\n"
            "2. Sign up or sign in (Google, GitHub, or email).\n"
            "3. Click 'Create Key' and give it a name like 'Phantomline'.\n"
            "4. Copy the generated key (starts with sk-or-).\n"
            "5. In Phantomline, go to Settings → AI engine → Cloud, choose 'OpenRouter' as the "
            "provider, and paste the key.\n"
            "6. The key is saved to my browser's localStorage — it never touches Phantomline's "
            "server.\n"
            "7. Test by generating a script. Many models are free; paid models are billed through "
            "your OpenRouter account."
        ),
        "manual_steps": [
            {"title": "Create an OpenRouter account",
             "body": "Go to <a href=\"https://openrouter.ai/keys\" target=\"_blank\" rel=\"noopener\">openrouter.ai/keys</a> and sign up with Google, GitHub, or email. No credit card is required for free models.",
             "command": ""},
            {"title": "Create an API key",
             "body": "Click <strong>Create Key</strong>, name it anything (e.g. 'Phantomline'), and copy the key. It starts with <code>sk-or-</code>.",
             "command": ""},
            {"title": "Paste the key into Phantomline",
             "body": "Open <a href=\"/app\">the studio</a>, go to <strong>Settings → AI engine → Cloud</strong>. Select <strong>OpenRouter</strong> as the provider and paste your key. It's saved to your browser's localStorage and never sent to Phantomline's server.",
             "command": ""},
            {"title": "Generate a test script",
             "body": "Pick any topic and hit Generate. OpenRouter routes the request to your chosen model. Many models (including Llama, Gemma, and Mistral variants) are available for free. Paid models are billed through your OpenRouter account at provider rates.",
             "command": ""},
        ],
        "verify_text": "Generate a script in the studio. If it completes successfully, your OpenRouter key is working.",
        "verify_command": "",
    },
}


@pages_bp.route("/install")
def install_index():
    return redirect("/install/phantomline", code=301)


@pages_bp.route("/install/<tool>")
def install_page(tool):
    tool_data = INSTALL_TOOLS.get(tool.lower())
    if not tool_data:
        return jsonify({"ok": False, "error": "Unknown install target"}), 404
    return render_template("install.html", tool=tool_data)


@pages_bp.route("/download")
def download_page():
    return render_template("install.html", tool=INSTALL_TOOLS["phantomline"])


# ---------------------------------------------------------------------------
# Self-hosted source zip
# ---------------------------------------------------------------------------
_SOURCE_ZIP_LOCK = threading.Lock()
_SOURCE_ZIP_PATH = OUTPUT_DIR / "phantomline-source.zip"
_SOURCE_ZIP_BUILT_AT = None


def _build_source_zip() -> Path:
    import zipfile

    EXCLUDE_DIRS = {
        ".git", ".github", ".venv", ".venv312", "venv",
        "__pycache__", "node_modules", "output", ".idea", ".vscode",
        ".pytest_cache", ".mypy_cache", "dist", "build",
    }
    EXCLUDE_FILE_SUFFIXES = (".pyc", ".pyo", ".log", ".tmp", ".broken.json")
    EXCLUDE_FILE_NAMES = {
        ".env", ".env.local", ".env.production", ".env.development",
        "phantomline-source.zip", "tmp_thumb_test.png",
    }
    EXCLUDE_FILE_GLOBS = ("*.key", "*.pem", "*.bak", "*.orig", "tmp_*")

    src_root = BASE_DIR
    out_path = _SOURCE_ZIP_PATH
    out_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = out_path.with_suffix(".tmp")

    def should_skip(p: Path) -> bool:
        for part in p.relative_to(src_root).parts:
            if part in EXCLUDE_DIRS:
                return True
        if p.name in EXCLUDE_FILE_NAMES:
            return True
        if p.suffix in EXCLUDE_FILE_SUFFIXES:
            return True
        from fnmatch import fnmatch
        for pattern in EXCLUDE_FILE_GLOBS:
            if fnmatch(p.name, pattern):
                return True
        return False

    EXECUTABLE_NAMES = {"start-phantomline.command", "start-phantomline.sh"}

    with zipfile.ZipFile(tmp_path, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        for root, dirs, files in os.walk(src_root):
            root_path = Path(root)
            dirs[:] = [d for d in dirs if d not in EXCLUDE_DIRS]
            for fname in files:
                fpath = root_path / fname
                if should_skip(fpath):
                    continue
                arcname = "phantomline/" + str(fpath.relative_to(src_root)).replace("\\", "/")
                try:
                    if fname in EXECUTABLE_NAMES:
                        info = zipfile.ZipInfo.from_file(fpath, arcname)
                        info.external_attr = (0o100755 << 16)
                        info.compress_type = zipfile.ZIP_DEFLATED
                        with open(fpath, "rb") as f:
                            zf.writestr(info, f.read())
                    else:
                        zf.write(fpath, arcname)
                except OSError:
                    pass

    os.replace(tmp_path, out_path)
    return out_path


@pages_bp.route("/download/phantomline-source.zip")
def download_source_zip():
    global _SOURCE_ZIP_BUILT_AT
    with _SOURCE_ZIP_LOCK:
        if _SOURCE_ZIP_BUILT_AT is None or not _SOURCE_ZIP_PATH.exists():
            try:
                _build_source_zip()
                _SOURCE_ZIP_BUILT_AT = time.time()
            except Exception as exc:
                import logging
                logging.getLogger(__name__).exception("Source zip build failed")
                return jsonify({
                    "ok": False,
                    "error": f"Could not build source zip: {exc}",
                }), 500
    return send_file(
        str(_SOURCE_ZIP_PATH),
        mimetype="application/zip",
        as_attachment=True,
        download_name="phantomline-source.zip",
        max_age=3600,
    )
