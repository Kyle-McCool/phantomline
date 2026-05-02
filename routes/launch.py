"""Launch readiness + test render routes.

This blueprint owns the first-run product setup screen: a structured health
check across local AI, voice engine, image generator, YouTube research API,
YouTube publishing, channel insights, and demo assets. Plus a small
fallback-card MP4 render so a non-technical user can verify their machine
can actually encode video before committing to a real workflow."""

from __future__ import annotations

import os
import uuid
import wave
from pathlib import Path

import requests
from flask import Blueprint, jsonify, request

import channel_insights
import projects as project_store
import story_generator as sg
import tts as tts_mod
import video_assembler
import youtube_research

from core import (
    BASE_DIR,
    OUTPUT_DIR,
    PROJECTS,
    _read_json_file,
)


launch_bp = Blueprint("launch", __name__)


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
    """First-run product readiness checks for non-technical users."""
    models = sg.check_ollama()
    voices = getattr(tts_mod, "VOICES", []) or []
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

    checks = [
        {
            "id": "ollama",
            "label": "Ollama model",
            "status": "ready" if models else "missing",
            "required": True,
            "detail": f"{len(models or [])} model(s) available" if models else "Start Ollama or pull a model.",
        },
        {
            "id": "voice",
            "label": "Local voice engine",
            "status": "ready" if voices else "missing",
            "required": True,
            "detail": f"{len(voices)} Kokoro voices available" if voices else "Voice list is empty.",
        },
        {
            "id": "forge",
            "label": "Local image generator",
            "status": "ready" if forge_ok else "optional",
            "required": False,
            "detail": f"Forge reachable at {forge_url}" if forge_ok else f"Optional. AI scenes need Forge at {forge_url}. {forge_error[:120]}",
        },
        {
            "id": "youtube_api",
            "label": "YouTube research API",
            "status": "ready" if youtube_api.get("available") else "optional",
            "required": False,
            "detail": "Live keyword ranking available" if youtube_api.get("available") else "Optional. SEO still works in candidate mode without a key.",
        },
        {
            "id": "youtube",
            "label": "YouTube publishing",
            "status": "ready" if youtube_status else "optional",
            "required": False,
            "detail": "YouTube connected" if youtube_status else "Optional until you want scheduling/upload.",
        },
        {
            "id": "insights",
            "label": "Channel intelligence",
            "status": "ready" if insights else "optional",
            "required": False,
            "detail": "Analytics are feeding ideas and titles" if insights else "Optional. Upload analytics later for smarter recommendations.",
        },
        {
            "id": "demo",
            "label": "Demo assets",
            "status": "ready" if videos else "optional",
            "required": False,
            "detail": f"{len(videos)} rendered video(s), {len(narrations)} narration(s) saved" if videos or narrations else "No finished videos yet. Use the demo workflow to create one.",
        },
    ]
    required = [c for c in checks if c.get("required")]
    ready_required = [c for c in required if c.get("status") == "ready"]
    optional_ready = [c for c in checks if not c.get("required") and c.get("status") == "ready"]
    score = round(((len(ready_required) / max(1, len(required))) * 70) + (min(3, len(optional_ready)) / 3 * 30))
    blockers = [c for c in required if c.get("status") != "ready"]
    return jsonify({
        "ok": True,
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


@launch_bp.route("/api/launch/test-render", methods=["POST"])
def api_launch_test_render():
    """Render a tiny fallback-card MP4 so users can verify encoding works."""
    test_id = uuid.uuid4().hex[:8]
    tmp_audio = OUTPUT_DIR / f"ghostline_test_{test_id}.wav"
    tmp_video = OUTPUT_DIR / f"ghostline_test_{test_id}.mp4"
    try:
        sample_rate = 24000
        seconds = 8
        with wave.open(str(tmp_audio), "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(sample_rate)
            wf.writeframes(b"\x00\x00" * sample_rate * seconds)
        timeline = {
            "title": "Phantomline Test Render",
            "source_plan_title": "Phantomline Test Render",
            "aspect": "9:16",
            "scenes": [
                {
                    "id": 1,
                    "start": "00:00",
                    "end": "00:04",
                    "duration_seconds": 4,
                    "narration": "Phantomline local render check.",
                    "video_prompt": "premium faceless video studio preview, local render check, teal glow, no text",
                },
                {
                    "id": 2,
                    "start": "00:04",
                    "end": "00:08",
                    "duration_seconds": 4,
                    "narration": "If you can play this, MP4 encoding works.",
                    "video_prompt": "short-form phone preview, caption safe layout, cinematic dark UI, no text",
                },
            ],
        }
        video_assembler.render_draft_video(timeline, tmp_audio, tmp_video, fps=24)
        proj = PROJECTS.create(
            kind=project_store.KIND_VIDEO,
            title="Phantomline Test Render",
            params={"source": "launch_readiness_test", "aspect": "9:16"},
        )
        PROJECTS.attach_file(proj["id"], "video", tmp_video)
        PROJECTS.update(proj["id"], status="ready", duration_seconds=seconds)
        try:
            tmp_audio.unlink()
        except OSError:
            pass
        return jsonify({
            "ok": True,
            "project": PROJECTS.get(proj["id"]),
            "video_url": f"/api/projects/{proj['id']}/file/video?inline=1",
        })
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500
