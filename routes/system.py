"""System-level routes: persisted user settings, anonymized telemetry, and
inline-feedback collection.

These three are domain-isolated — no other route group reads their state —
so they're cleanly separable from the rest of server.py."""

from __future__ import annotations

import json
import os
import threading
import time
from typing import Any

import requests
from flask import Blueprint, jsonify, request

from core import OUTPUT_DIR, _read_json_file, _write_json_file


system_bp = Blueprint("system", __name__)


# ---------------------------------------------------------------------------
# Health check — quick liveness probe for uptime monitors and Render. Returns
# 200 with build info; never touches disk or third-party services so it can't
# trigger false alarms during a partial outage.
# ---------------------------------------------------------------------------

_BOOT_TIME = time.time()


@system_bp.route("/api/system/health", methods=["GET"])
def api_system_health():
    return jsonify({
        "ok": True,
        "status": "healthy",
        "service": "phantomline",
        "uptime_seconds": int(time.time() - _BOOT_TIME),
    })


# ---------------------------------------------------------------------------
# Settings — persisted to output/settings.json so defaults survive restart.
# ---------------------------------------------------------------------------

SETTINGS_PATH = OUTPUT_DIR / "settings.json"
SETTINGS_LOCK = threading.Lock()
ALLOWED_SETTING_KEYS = {
    "model", "aspect", "captionStyle", "voice",
    "musicLevel", "forgeUrl", "forgeCheckpoint",
    "simpleMode", "telemetry", "musicDuck",
}


def _read_settings() -> dict[str, Any]:
    data = _read_json_file(SETTINGS_PATH, {})
    return data if isinstance(data, dict) else {}


def _write_settings(data: dict[str, Any]) -> None:
    with SETTINGS_LOCK:
        _write_json_file(SETTINGS_PATH, data)


@system_bp.route("/api/settings", methods=["GET"])
def api_settings_get():
    return jsonify({"ok": True, "settings": _read_settings()})


@system_bp.route("/api/settings", methods=["POST"])
def api_settings_save():
    payload = request.get_json(silent=True) or {}
    incoming = payload.get("settings") if isinstance(payload.get("settings"), dict) else payload
    if not isinstance(incoming, dict):
        return jsonify({"ok": False, "error": "settings must be an object"}), 400
    cleaned: dict[str, Any] = {}
    for key, value in incoming.items():
        if key not in ALLOWED_SETTING_KEYS:
            continue
        if isinstance(value, str):
            cleaned[key] = value.strip()[:300]
        elif isinstance(value, bool):
            cleaned[key] = value
        elif isinstance(value, (int, float)):
            cleaned[key] = value
        elif value is None:
            cleaned[key] = None
    merged = {**_read_settings(), **cleaned}
    _write_settings(merged)
    return jsonify({"ok": True, "settings": merged})


# ---------------------------------------------------------------------------
# Telemetry + feedback. Local-first: events stream to JSONL files. A future
# build can ship them to a remote sink (Supabase, Vercel function, Logflare)
# by setting GHOSTLINE_TELEMETRY_URL.
# ---------------------------------------------------------------------------

TELEMETRY_DIR = OUTPUT_DIR / "telemetry"
TELEMETRY_DIR.mkdir(parents=True, exist_ok=True)
TELEMETRY_LOCK = threading.Lock()


def _telemetry_append(filename: str, payload: dict[str, Any]) -> None:
    """Append a JSON line to a telemetry file. Best-effort, never raises."""
    path = TELEMETRY_DIR / filename
    try:
        with TELEMETRY_LOCK:
            with path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(payload, ensure_ascii=False) + "\n")
    except OSError:
        pass


def _telemetry_forward(payload: dict[str, Any]) -> None:
    """If a remote sink is configured, POST the event there. Best-effort."""
    url = os.getenv("GHOSTLINE_TELEMETRY_URL", "").strip()
    if not url:
        return
    try:
        requests.post(url, json=payload, timeout=3)
    except requests.RequestException:
        pass


@system_bp.route("/api/telemetry/event", methods=["POST"])
def api_telemetry_event():
    """Record an anonymized client event. The frontend posts errors, render
    completions, publish actions, etc. so launch-day issues are visible."""
    data = request.get_json(silent=True) or {}
    event = {
        "ts": time.time(),
        "type": str(data.get("type") or "event")[:64],
        "payload": data.get("payload") if isinstance(data.get("payload"), (dict, list, str, int, float, bool, type(None))) else None,
        "session": str(data.get("session") or "")[:64],
        "ua": (request.headers.get("User-Agent") or "")[:300],
    }
    _telemetry_append("events.jsonl", event)
    _telemetry_forward({"kind": "telemetry", **event})
    return jsonify({"ok": True})


@system_bp.route("/api/feedback", methods=["POST"])
def api_feedback():
    """Inline feedback widget posts here. Logged locally and forwarded if a
    remote sink is configured."""
    data = request.get_json(silent=True) or {}
    message = str(data.get("message") or "").strip()
    if not message:
        return jsonify({"ok": False, "error": "Tell us what's on your mind."}), 400
    payload = {
        "ts": time.time(),
        "message": message[:4000],
        "kind": str(data.get("kind") or "general")[:32],
        "context": data.get("context") if isinstance(data.get("context"), dict) else None,
        "email": str(data.get("email") or "").strip()[:200] or None,
        "ua": (request.headers.get("User-Agent") or "")[:300],
    }
    _telemetry_append("feedback.jsonl", payload)
    _telemetry_forward({"kind": "feedback", **payload})
    return jsonify({"ok": True})
