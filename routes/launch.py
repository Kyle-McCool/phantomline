"""Launch readiness + test render routes.

This blueprint owns the first-run product setup screen: a structured health
check across local AI, voice engine, image generator, YouTube research API,
YouTube publishing, channel insights, and demo assets. Plus a small
fallback-card MP4 render so a non-technical user can verify their machine
can actually encode video before committing to a real workflow."""

from __future__ import annotations

import json
import os
import random
import uuid
import wave
from pathlib import Path

import requests
from flask import Blueprint, jsonify, request

import channel_insights
import projects as project_store
import story_generator as sg
# Heavy ML modules are optional on the hosted tier. Routes that need them
# guard with `if tts_mod is None: return 503`. See server.py for the same
# pattern in the main app.
try:
    import tts as tts_mod
except Exception:
    tts_mod = None
try:
    import video_assembler
except Exception:
    video_assembler = None
try:
    import music as music_mod
except Exception:
    music_mod = None
import youtube_research

from core import (
    BASE_DIR,
    OUTPUT_DIR,
    PROJECTS,
    _read_json_file,
)


launch_bp = Blueprint("launch", __name__)


# ---------------------------------------------------------------------------
# Hosted vs local mode detection.
#
# On localhost, the readiness checklist is the desktop install flow:
# Ollama, Kokoro voices, Forge, etc. — things the user installs locally.
#
# On phantomline.xyz / *.onrender.com, none of that applies — AI inference
# runs in the user's browser via WebLLM, Web Speech, Web Audio, ffmpeg.wasm.
# Showing first-time hosted visitors a red "Ollama MISSING" badge confuses
# them and tanks conversion. So we serve a different checklist tailored to
# browser engines, with capability detection deferred to the client.
#
# Override with ?mode=hosted (or ?mode=local) for previewing the other
# mode from localhost — useful in dev.
# ---------------------------------------------------------------------------
_HOSTED_HOST_SUFFIXES = (".onrender.com", "phantomline.xyz", "phantomline.com", "phantomline.app")


def _is_hosted_request() -> bool:
    """Return True if the request is hitting a hosted (production) domain.

    Order of precedence:
      1. Explicit ?mode= query override (for dev preview)
      2. PHANTOMLINE_HOSTED env var (set by deploy targets)
      3. request.host suffix match against known production domains
    """
    mode_override = (request.args.get("mode") or "").strip().lower()
    if mode_override in ("hosted", "local"):
        return mode_override == "hosted"
    if os.environ.get("PHANTOMLINE_HOSTED", "").lower() in ("1", "true", "yes"):
        return True
    host = (request.host or "").lower()
    return any(host == s.lstrip(".") or host.endswith(s) for s in _HOSTED_HOST_SUFFIXES)


YOUTUBE_CONNECTION_PATH = OUTPUT_DIR / "publishing" / "youtube_connection.json"


def _forge_base_url() -> str:
    """Match server.py's environment variable resolution. Kept here so the
    launch blueprint stays self-contained."""
    return (
        os.getenv("GHOSTLINE_FORGE_URL")
        or os.getenv("BINDERY_FORGE_URL")  # legacy
        or "http://127.0.0.1:7861"
    ).rstrip("/")


def _youtube_connected() -> bool:
    data = _read_json_file(YOUTUBE_CONNECTION_PATH, None)
    return isinstance(data, dict) and bool(data)


@launch_bp.route("/api/launch/readiness")
def api_launch_readiness():
    """First-run product readiness checks for non-technical users.

    Hosted vs local: see _is_hosted_request() above. On hosted we serve a
    completely different checklist that describes the browser AI stack
    (WebLLM, Web Speech, Web Audio, ffmpeg.wasm). The browser-side capability
    checks (status: 'check_client') are evaluated by the frontend on render
    so we don't pretend the server knows whether the user's browser has
    WebGPU / SharedArrayBuffer.

    Each check ships an `actions` array describing how the user can fix
    a missing/optional item — install link, CLI command to copy, or a
    server-side install (e.g. `ollama pull llama3.1`). The frontend
    renders these as buttons so the checklist is actionable, not just
    diagnostic.

    Action shapes:
      {kind: "link",     label, value: <url>}         opens externally
      {kind: "copy",     label, value: <command>}     copies to clipboard
      {kind: "pull",     label, value: <action_id>}   POSTs to /api/launch/install
      {kind: "internal", label, value: <target>}      JS-side handler
    """
    if _is_hosted_request():
        return _readiness_hosted()
    return _readiness_local()


def _readiness_hosted():
    """Browser-mode readiness payload. Server doesn't know browser caps,
    so capability checks are marked status: "check_client" — the JS
    looks at navigator.gpu, window.speechSynthesis, etc. and updates
    the dot color before render.

    Browser-first by design: every required engine runs in the user's
    browser (WebGPU, Web Speech, ffmpeg.wasm). Power-user upgrades
    (Ollama for bigger models, Kokoro for premium voices, Forge for AI
    images, YouTube API key for live keyword data) are exposed as
    optional install links on the same checklist — no desktop app
    download required."""
    youtube_api = youtube_research.health()

    checks = [
        {
            "id": "browser_ai",
            "label": "In-browser AI (WebGPU)",
            "status": "check_client",
            "client_check": "webgpu",
            "required": True,
            "detail": "Llama 3.2 1B via WebGPU. Detecting browser support…",
            # Be explicit that READY = browser supports it, not that the
            # model is already on disk. First render pulls the ~1 GB weights
            # into the browser cache. Power users can swap in Ollama for
            # bigger local models via the action link.
            "ready_detail": "Browser supports WebGPU. First render downloads Llama 3.2 1B (~1 GB) to your browser cache, then runs locally with no server round-trip.",
            "missing_detail": "WebGPU not detected. Try Chrome, Edge, or any Chromium-based browser. Safari support is limited.",
            "actions": [
                {"kind": "link", "label": "Upgrade: install Ollama (1-click guide)",
                 "value": "/install/ollama"},
            ],
        },
        {
            "id": "browser_voice",
            "label": "In-browser voice (Web Speech)",
            "status": "check_client",
            "client_check": "speech_synthesis",
            "required": True,
            "detail": "System TTS via Web Speech API. Detecting voices…",
            "ready_detail": "Browser exposes the Web Speech API — fine for drafts. Kokoro voices sound dramatically better for finished videos — install in 1 minute.",
            "missing_detail": "Web Speech API unavailable. Try a modern Chromium or Safari.",
            "actions": [
                {"kind": "link", "label": "Upgrade: install Kokoro voices (1-click guide)",
                 "value": "/install/kokoro"},
            ],
        },
        {
            "id": "browser_music",
            "label": "Bundled music + Web Audio",
            "status": "ready",
            "required": False,
            "detail": "8-track royalty-free pack ships with the app. Web Audio synthesizes ambient beds.",
            "actions": [],
        },
        {
            "id": "browser_video",
            "label": "In-browser MP4 renderer",
            "status": "check_client",
            "client_check": "ffmpeg_wasm",
            "required": True,
            "detail": "ffmpeg.wasm renders MP4 in the browser. Detecting…",
            "ready_detail": "Browser supports WebAssembly. ffmpeg.wasm renders MP4 locally — no upload, no server cost.",
            "missing_detail": "Browser does not expose WebAssembly. Use a modern browser.",
            "actions": [],
        },
        {
            "id": "browser_images",
            "label": "AI images (optional)",
            "status": "optional",
            "required": False,
            "detail": "Browser uses bundled stock + Pollinations (free public API) by default. For full-control local AI scene art, install Forge.",
            "actions": [
                {"kind": "link", "label": "Install Forge (1-click guide)",
                 "value": "/install/forge"},
            ],
        },
        {
            "id": "youtube_api",
            "label": "YouTube research API",
            "status": "ready" if youtube_api.get("available") else "optional",
            "required": False,
            "detail": "Live keyword ranking available" if youtube_api.get("available") else "Optional. SEO works in candidate mode without a key.",
            "actions": [] if youtube_api.get("available") else [
                {"kind": "link", "label": "Get free API key",
                 "value": "/install/youtube-api"},
            ],
        },
        # The items below need per-user state that the single-tenant hosted
        # server can't safely share across visitors. Instead of pushing a
        # desktop download, surface honest "what works today" copy + the
        # install links power users actually want.
        {
            "id": "youtube",
            "label": "YouTube auto-publish",
            "status": "optional",
            "required": False,
            "detail": "Build and download your MP4 here, then upload to YouTube. Scheduled auto-publish needs a per-user OAuth token — set it up with your own Google API credentials.",
            "actions": [
                {"kind": "link", "label": "Create YouTube OAuth client",
                 "value": "https://console.cloud.google.com/apis/credentials"},
            ],
        },
        {
            "id": "insights",
            "label": "Channel analytics",
            "status": "optional",
            "required": False,
            "detail": "Upload your YouTube Studio analytics CSV in the Insights tab — Phantomline parses it in your browser to suggest titles and topics.",
            "actions": [
                {"kind": "internal", "label": "Open Insights tab",
                 "value": "tab:insights"},
            ],
        },
        {
            "id": "demo",
            "label": "Saved projects",
            "status": "optional",
            "required": False,
            "detail": "Renders persist for this browser session. For permanent storage across devices, sign in (top-right) and your library syncs to your account.",
            "actions": [
                {"kind": "internal", "label": "Load demo workflow",
                 "value": "demo:reddit"},
            ],
        },
    ]
    return jsonify({
        "ok": True,
        "mode": "hosted",
        "score": None,  # client computes after capability checks
        "checks": checks,
        "headline": "Phantomline browser mode",
        "subheadline": "Everything below runs in your browser — no installs required. Power-user upgrades (Ollama, Kokoro, Forge) are linked inline.",
        "launch_ready": False,
        "blockers": [],
    })


def _readiness_local():
    models = sg.check_ollama()
    # Disambiguate Ollama states:
    #   models is None  -> Ollama daemon unreachable (install/start it)
    #   models == []    -> Ollama running but no model pulled
    #   models == [...] -> ready
    ollama_unreachable = models is None
    ollama_no_models = models == []

    voices = []
    if tts_mod is not None:
        try:
            import kokoro  # noqa: F401
            voices = getattr(tts_mod, "VOICES", []) or []
        except ImportError:
            pass
    projects = PROJECTS.all()[:200]
    videos = [p for p in projects if p.get("kind") == project_store.KIND_VIDEO]
    narrations = [p for p in projects if p.get("kind") == project_store.KIND_NARRATION]
    insights = channel_insights.load(BASE_DIR)
    youtube_status = _youtube_connected()
    youtube_api = youtube_research.health()

    forge_url = _forge_base_url()
    forge_ok = False
    forge_error = ""
    try:
        resp = requests.get(f"{forge_url}/sdapi/v1/options", timeout=2)
        forge_ok = resp.ok
        if not resp.ok:
            forge_error = f"HTTP {resp.status_code}"
    except Exception as exc:
        forge_error = str(exc)

    # Build per-check action lists. Hidden when status is "ready".
    # Missing-dependency links go to the friendly /install/<tool> guide
    # (OS-detected one-liner + Claude Code paste-prompt + manual steps),
    # NOT the raw vendor README — non-technical users get stranded there.
    ollama_actions = []
    if ollama_unreachable:
        ollama_actions.append({"kind": "link", "label": "Install Ollama (1-click guide)",
                               "value": "/install/ollama"})
    elif ollama_no_models:
        ollama_actions.append({"kind": "pull", "label": "Pull llama3.1 (4.7 GB)",
                               "value": "ollama-model:llama3.1"})
        ollama_actions.append({"kind": "copy", "label": "Or run yourself",
                               "value": "ollama pull llama3.1"})

    voice_actions = []
    if not voices:
        voice_actions.append({"kind": "link", "label": "Install Kokoro voices (1-click guide)",
                              "value": "/install/kokoro"})

    forge_actions = []
    if not forge_ok:
        forge_actions.append({"kind": "link", "label": "Install Forge (1-click guide)",
                              "value": "/install/forge"})
        forge_actions.append({"kind": "copy", "label": "Default URL",
                              "value": forge_url})
    else:
        forge_actions.append({"kind": "link", "label": "Open Forge UI",
                              "value": forge_url})

    youtube_api_actions = []
    if not youtube_api.get("available"):
        youtube_api_actions.append({"kind": "link", "label": "Get free API key",
                                    "value": "/install/youtube-api"})
        youtube_api_actions.append({"kind": "copy", "label": "Add to .env",
                                    "value": "YOUTUBE_API_KEY_2=YOUR_KEY_HERE"})

    youtube_actions = []
    if not youtube_status:
        youtube_actions.append({"kind": "internal", "label": "Connect in Publish",
                                "value": "tab:publish"})

    insights_actions = []
    if not insights:
        insights_actions.append({"kind": "internal", "label": "Upload analytics CSV",
                                 "value": "tab:insights"})

    demo_actions = []
    if not videos and not narrations:
        demo_actions.append({"kind": "internal", "label": "Load demo workflow",
                             "value": "demo:reddit"})

    checks = [
        {
            "id": "ollama",
            "label": "Ollama model",
            "status": "ready" if models else "missing",
            "required": True,
            "detail": (
                f"{len(models)} model(s) available" if models else
                "Ollama isn't running. Install it, or start the desktop app." if ollama_unreachable else
                "Ollama is running but no model is pulled yet."
            ),
            "actions": ollama_actions,
        },
        {
            "id": "voice",
            "label": "Local voice engine",
            "status": "ready" if voices else "missing",
            "required": True,
            "detail": f"{len(voices)} Kokoro voices available" if voices else "Voice list is empty — install desktop deps to enable Kokoro.",
            "actions": voice_actions,
        },
        {
            "id": "forge",
            "label": "Local image generator",
            "status": "ready" if forge_ok else "optional",
            "required": False,
            "detail": f"Forge reachable at {forge_url}" if forge_ok else f"Optional. AI scenes need Forge at {forge_url}. {forge_error[:120]}",
            "actions": forge_actions,
        },
        {
            "id": "youtube_api",
            "label": "YouTube research API",
            "status": "ready" if youtube_api.get("available") else "optional",
            "required": False,
            "detail": "Live keyword ranking available" if youtube_api.get("available") else "Optional. SEO still works in candidate mode without a key.",
            "actions": youtube_api_actions,
        },
        {
            "id": "youtube",
            "label": "YouTube publishing",
            "status": "ready" if youtube_status else "optional",
            "required": False,
            "detail": "YouTube connected" if youtube_status else "Optional until you want scheduling/upload.",
            "actions": youtube_actions,
        },
        {
            "id": "insights",
            "label": "Channel intelligence",
            "status": "ready" if insights else "optional",
            "required": False,
            "detail": "Analytics are feeding ideas and titles" if insights else "Optional. Upload analytics later for smarter recommendations.",
            "actions": insights_actions,
        },
        {
            "id": "demo",
            "label": "Demo assets",
            "status": "ready" if videos else "optional",
            "required": False,
            "detail": f"{len(videos)} rendered video(s), {len(narrations)} narration(s) saved" if videos or narrations else "No finished videos yet. Use the demo workflow to create one.",
            "actions": demo_actions,
        },
    ]
    required = [c for c in checks if c.get("required")]
    ready_required = [c for c in required if c.get("status") == "ready"]
    optional_ready = [c for c in checks if not c.get("required") and c.get("status") == "ready"]
    score = round(((len(ready_required) / max(1, len(required))) * 70) + (min(3, len(optional_ready)) / 3 * 30))
    blockers = [c for c in required if c.get("status") != "ready"]
    return jsonify({
        "ok": True,
        "mode": "local",
        "score": score,
        "checks": checks,
        "launch_ready": not blockers,
        "blockers": blockers,
        "counts": {
            "projects": len(projects),
            "videos": len(videos),
            "narrations": len(narrations),
        },
    })


@launch_bp.route("/api/launch/install", methods=["POST"])
def api_launch_install():
    """Server-side installer for items that can be pulled without leaving
    the app. Currently supports:
      action = "ollama-model:<name>"  -> runs `ollama pull <name>` locally

    Streams nothing in v1 — runs to completion and returns success/failure
    plus stdout tail. The frontend triggers a readiness re-check after the
    response, so the checklist refreshes. Future: SSE-stream the pull
    progress so users see download bytes.
    """
    import subprocess

    data = request.get_json(force=True) or {}
    action = data.get("action") or ""

    if action.startswith("ollama-model:"):
        model_name = action.split(":", 1)[1].strip()
        # Whitelist to avoid arbitrary CLI input. Only well-known small/mid
        # models that Phantomline routes use as defaults.
        allowed = {"llama3.1", "llama3.1:8b", "llama3.2", "llama3.2:3b", "qwen2.5", "mistral"}
        if model_name not in allowed:
            return jsonify({"ok": False, "error": f"Model '{model_name}' is not in the allowed pull list."}), 400
        try:
            # 30 minute cap — llama3.1 is ~4.7 GB, plenty even on slow links.
            proc = subprocess.run(
                ["ollama", "pull", model_name],
                capture_output=True, text=True, timeout=1800,
            )
            tail = (proc.stdout + "\n" + proc.stderr)[-1500:]
            if proc.returncode != 0:
                return jsonify({"ok": False, "error": f"ollama pull exited {proc.returncode}", "log": tail}), 502
            return jsonify({"ok": True, "model": model_name, "log": tail})
        except FileNotFoundError:
            return jsonify({"ok": False, "error": "Ollama CLI not found in PATH. Install Ollama first."}), 503
        except subprocess.TimeoutExpired:
            return jsonify({"ok": False, "error": "Ollama pull exceeded 30 min timeout."}), 504
        except Exception as exc:
            return jsonify({"ok": False, "error": str(exc)}), 500

    return jsonify({"ok": False, "error": f"Unknown install action: {action}"}), 400


DEMO_SCRIPTS = [
    {
        "narration": "Every night, a new Reddit confession goes viral. "
                     "Millions watch. Nobody knows who's behind the camera.",
        "voice": "af_heart",
        "caption_style": "tiktok",
        "music_mood": "mystery",
        "title": "Reddit Confessions Demo",
    },
    {
        "narration": "Three survival mistakes that make people panic. "
                     "Number one: running downhill from a bear.",
        "voice": "am_adam",
        "caption_style": "clean",
        "music_mood": "cinematic",
        "title": "Survival Tips Demo",
    },
    {
        "narration": "There was one rule in the house. Never open the red door. "
                     "Last Tuesday, someone did.",
        "voice": "af_heart",
        "caption_style": "horror",
        "music_mood": "horror",
        "title": "Horror Story Demo",
    },
    {
        "narration": "This AI tool saved our team forty hours a week. "
                     "Here's the one workflow that changed everything.",
        "voice": "am_adam",
        "caption_style": "documentary",
        "music_mood": "cinematic",
        "title": "AI Tool Review Demo",
    },
    {
        "narration": "Scientists found something at the bottom of the ocean. "
                     "It was older than anything on the surface.",
        "voice": "bf_emma",
        "caption_style": "tiktok",
        "music_mood": "mystery",
        "title": "Science Mystery Demo",
    },
]


def _download_demo_clip():
    """Pick a random clip from the public footage library and download it.

    Clips are cached in OUTPUT_DIR so subsequent demo renders are instant.
    Returns the local path or None on failure."""
    manifest_path = BASE_DIR / "static" / "library" / "footage-manifest.json"
    if not manifest_path.exists():
        return None
    try:
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
        clips = data.get("clips", [])
        if not clips:
            return None
        clip = random.choice(clips)
        url = clip.get("url", "")
        filename = clip.get("filename", "")
        if not url or not filename:
            return None
        cached = OUTPUT_DIR / f"demo_footage_{filename}"
        if cached.exists() and cached.stat().st_size > 100_000:
            return cached
        r = requests.get(url, timeout=30, stream=True)
        r.raise_for_status()
        cached.parent.mkdir(parents=True, exist_ok=True)
        with open(cached, "wb") as f:
            for chunk in r.iter_content(chunk_size=1024 * 256):
                f.write(chunk)
        return cached
    except Exception:
        return None


def _find_bundled_music(mood):
    """Return the path to a bundled music MP3 matching the mood, or None."""
    manifest_path = BASE_DIR / "static" / "library" / "music.json"
    if not manifest_path.exists():
        return None
    try:
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
        tracks = data.get("tracks", [])
        match = [t for t in tracks if t.get("mood", "").lower() == mood.lower()]
        if not match:
            match = tracks[:1]
        if not match:
            return None
        url_path = match[0].get("url", "")
        file_path = BASE_DIR / url_path.lstrip("/")
        if file_path.exists():
            return file_path
    except Exception:
        pass
    return None


@launch_bp.route("/api/launch/test-render", methods=["POST"])
def api_launch_test_render():
    """Render an 8-second demo video with real narration, music, and captions."""
    if video_assembler is None:
        return jsonify({"ok": False, "error": "Video assembler not available"}), 503

    demo = random.choice(DEMO_SCRIPTS)
    test_id = uuid.uuid4().hex[:8]
    tmp_narration = OUTPUT_DIR / f"demo_narration_{test_id}.wav"
    tmp_mixed = OUTPUT_DIR / f"demo_mixed_{test_id}.wav"
    tmp_video = OUTPUT_DIR / f"demo_render_{test_id}.mp4"
    cleanup = []

    try:
        # 1. Generate narration with Kokoro TTS
        if tts_mod is None:
            return jsonify({"ok": False, "error": "Kokoro TTS not available"}), 503
        import numpy as np
        audio_f32, sr, segments = tts_mod.synthesize_with_timing(
            demo["narration"], voice=demo["voice"], speed=1.0,
        )
        if len(audio_f32) == 0:
            return jsonify({"ok": False, "error": "TTS produced no audio"}), 500

        # Save narration WAV
        wav_bytes = tts_mod.to_wav_bytes(audio_f32, sr)
        tmp_narration.parent.mkdir(parents=True, exist_ok=True)
        tmp_narration.write_bytes(wav_bytes)
        cleanup.append(tmp_narration)
        narration_seconds = len(audio_f32) / sr

        # 2. Mix with bundled music if available
        render_audio = tmp_narration
        music_path = _find_bundled_music(demo.get("music_mood", "cinematic"))
        if music_path and music_mod is not None:
            try:
                m_audio, m_sr = music_mod.load_audio_file(str(music_path))
                mixed, mixed_sr = music_mod.mix_narration_and_music(
                    audio_f32, sr, m_audio, m_sr, music_db_below_speech=16.0,
                )
                mixed_wav = tts_mod.to_wav_bytes(mixed, mixed_sr)
                tmp_mixed.write_bytes(mixed_wav)
                cleanup.append(tmp_mixed)
                render_audio = tmp_mixed
            except Exception:
                pass  # fall back to narration-only

        # 3. Download a public demo clip from the footage library
        source_video = _download_demo_clip()
        if source_video:
            video_assembler.render_source_video(
                source_video, render_audio, tmp_video,
                caption_text=demo["narration"],
                captions=True,
                caption_style=demo.get("caption_style", "tiktok"),
                caption_segments=segments,
                title_text=demo["title"],
                aspect="9:16",
                fps=24,
            )
        else:
            # No existing videos — use fallback card render
            duration = narration_seconds
            mid = duration / 2
            timeline = {
                "title": demo["title"],
                "source_plan_title": demo["title"],
                "aspect": "9:16",
                "scenes": [
                    {
                        "id": 1, "start": "00:00",
                        "end": f"00:{int(mid):02d}",
                        "duration_seconds": mid,
                        "narration": demo["narration"][:len(demo["narration"])//2],
                        "video_prompt": "cinematic dark studio, teal glow, premium faceless content",
                    },
                    {
                        "id": 2, "start": f"00:{int(mid):02d}",
                        "end": f"00:{int(duration):02d}",
                        "duration_seconds": duration - mid,
                        "narration": demo["narration"][len(demo["narration"])//2:],
                        "video_prompt": "short-form video preview, caption safe, dark cinematic",
                    },
                ],
            }
            video_assembler.render_draft_video(timeline, render_audio, tmp_video, fps=24)

        proj = PROJECTS.create(
            kind=project_store.KIND_VIDEO,
            title=demo["title"],
            params={"source": "launch_readiness_test", "aspect": "9:16"},
        )
        PROJECTS.attach_file(proj["id"], "video", tmp_video)
        PROJECTS.update(proj["id"], status="ready", duration_seconds=narration_seconds)

        return jsonify({
            "ok": True,
            "project": PROJECTS.get(proj["id"]),
            "video_url": f"/api/projects/{proj['id']}/file/video?inline=1",
        })
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500
    finally:
        for f in cleanup:
            try:
                f.unlink()
            except OSError:
                pass
