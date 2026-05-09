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


def _read_local_version() -> str:
    """Single source of truth for the running install's version: the VERSION
    file at the repo root. Returns '0.0.0' if the file is missing or unreadable
    (treat unknown installs as ancient — they'll always show as needing
    update against the hosted version). Never raises."""
    try:
        from pathlib import Path
        v = (Path(__file__).resolve().parent.parent / "VERSION").read_text(encoding="utf-8").strip()
        return v if v else "0.0.0"
    except OSError:
        return "0.0.0"


@system_bp.route("/api/system/health", methods=["GET"])
def api_system_health():
    return jsonify({
        "ok": True,
        "status": "healthy",
        "service": "phantomline",
        "uptime_seconds": int(time.time() - _BOOT_TIME),
        "version": _read_local_version(),
    })


@system_bp.route("/api/system/version", methods=["GET", "OPTIONS"])
def api_system_version():
    """Public version endpoint. Two consumers:
      - phantomline.xyz reports its current production version here so local
        installs can compare against it.
      - Local installs report their own version so the hosted /account UI can
        show 'this user is on vX.Y.Z, latest is vA.B.C' (future enhancement).

    Permissive CORS: any origin can read it (the value is public anyway).
    Cache-Control: short max-age to balance freshness vs hammering the
    hosted server when many local installs poll simultaneously."""
    response = jsonify({
        "ok": True,
        "service": "phantomline",
        "version": _read_local_version(),
    })
    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Cache-Control"] = "public, max-age=300"  # 5 min
    return response


def _semver_tuple(v: str) -> tuple[int, int, int]:
    """Parse 'X.Y.Z' into a sortable tuple. Tolerates leading 'v', extra
    components (drops them), and missing components (pads with 0). Returns
    (0,0,0) for completely unparseable input rather than raising."""
    if not v or not isinstance(v, str):
        return (0, 0, 0)
    s = v.strip().lstrip("v").lstrip("V")
    parts = s.split(".")
    out: list[int] = []
    for p in parts[:3]:
        try:
            out.append(int(p.split("-")[0].split("+")[0]))
        except (ValueError, IndexError):
            out.append(0)
    while len(out) < 3:
        out.append(0)
    return (out[0], out[1], out[2])


@system_bp.route("/api/system/update-check", methods=["GET"])
def api_system_update_check():
    """Compare the local install's version against phantomline.xyz's current
    production version. Returns enough for the studio banner to render:
      {
        "ok": true,
        "current": "1.0.0",
        "latest": "1.1.0",
        "update_available": true,
        "download_url": "https://phantomline.xyz/download/phantomline-source.zip",
        "release_notes_url": "https://phantomline.xyz/releases",
        "checked_at": <unix>
      }

    Network failures degrade gracefully — returns update_available=false
    and an error field, so the banner just stays hidden when offline.
    Capped at a 4-second timeout so the studio's polling never blocks the UI.
    """
    current = _read_local_version()
    latest = current
    update_available = False
    error: str | None = None

    # Don't self-poll: when this endpoint is hit on phantomline.xyz itself,
    # the "latest" is whatever this server reports. Skip the round-trip and
    # always return update_available=false. Detection is by request host.
    from flask import request as _req
    host = (_req.host or "").lower()
    is_hosted = host.endswith("phantomline.xyz")

    if not is_hosted:
        try:
            r = requests.get(
                "https://phantomline.xyz/api/system/version",
                timeout=4,
                headers={"User-Agent": "phantomline-update-check"},
            )
            if r.status_code == 200:
                body = r.json() or {}
                latest = (body.get("version") or current).strip() or current
            else:
                error = f"Hosted version endpoint returned {r.status_code}"
        except requests.RequestException as exc:
            error = f"Network error: {exc}"
        except (ValueError, KeyError) as exc:
            error = f"Bad response from hosted version endpoint: {exc}"

    update_available = _semver_tuple(latest) > _semver_tuple(current)

    return jsonify({
        "ok": error is None,
        "current": current,
        "latest": latest,
        "update_available": update_available,
        "download_url": "https://phantomline.xyz/download/phantomline-source.zip",
        "release_notes_url": "https://phantomline.xyz/releases",
        "checked_at": int(time.time()),
        "error": error,
    })


@system_bp.route("/api/system/ping", methods=["GET", "OPTIONS"])
def api_system_ping():
    """Cross-origin probe endpoint.

    Hosted phantomline.xyz/account uses this to detect whether a local
    Phantomline install is running on the user's machine, so the desktop-
    activation flow can show actionable help when the server is down
    instead of bouncing the user to a broken `localhost:5000/account`.

    Specifically allows fetch() from phantomline.xyz origin and answers
    PNA preflights — Chrome's Private Network Access spec requires
    explicit headers when an HTTPS public-internet site probes a private/
    localhost address. Without these, Chrome silently blocks the fetch.

    Body is intentionally minimal: just enough info to render the card
    state and decide whether to enable the "Open desktop" button.
    """
    response = jsonify({
        "ok": True,
        "service": "phantomline",
        "uptime_seconds": int(time.time() - _BOOT_TIME),
    })
    # Only the hosted marketing origin needs this; lock the allow list down
    # rather than `*` to keep the surface tight.
    origin = request.headers.get("Origin", "")
    if origin in ("https://phantomline.xyz", "https://www.phantomline.xyz"):
        response.headers["Access-Control-Allow-Origin"] = origin
    else:
        response.headers["Access-Control-Allow-Origin"] = "https://phantomline.xyz"
    response.headers["Access-Control-Allow-Methods"] = "GET, OPTIONS"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type"
    # Chrome PNA: tells the browser this private-network endpoint accepts
    # requests from the public-internet origin above.
    response.headers["Access-Control-Allow-Private-Network"] = "true"
    response.headers["Vary"] = "Origin"
    # No caching — the ping must reflect current run state.
    response.headers["Cache-Control"] = "no-store"
    return response


@system_bp.route("/api/system/setup-status", methods=["GET"])
def api_system_setup_status():
    """Snapshot of "is this install ready to make videos?".

    The studio polls this on first load to render the setup checklist.
    Each entry is {id, label, ok, hint, action_url} so the UI can render
    a uniform list with green/red badges and an inline fix-it link.

    Designed to be cheap (sub-second) — Ollama check has a 2s timeout, the
    rest are env / file lookups. Polled every ~5s by the studio while the
    setup panel is open, then stops once everything is green.
    """
    from auth_helpers import supabase_url, supabase_anon_key

    items: list[dict[str, Any]] = []

    # ---- 1. Supabase / sign-in availability ---------------------------------
    sb_ok = bool(supabase_url() and supabase_anon_key())
    items.append({
        "id": "supabase",
        "label": "Sign-in (Supabase)",
        "ok": sb_ok,
        "hint": (
            "Supabase config is loaded — sign in on /account to activate your license."
            if sb_ok else
            "Supabase URL or anon key missing. Set DEFAULT_SUPABASE_ANON_KEY in auth_helpers.py."
        ),
        "action_url": "/account" if sb_ok else None,
    })

    # ---- 2. License / tier --------------------------------------------------
    # Cheap import: just reads license.json and (maybe) HMAC-validates a key.
    from routes.billing import current_tier
    tier_info = current_tier()
    tier = tier_info.get("tier", "free")
    source = tier_info.get("source", "none")
    license_ok = source in ("supabase", "license")  # any active path counts
    if source == "supabase":
        license_hint = f"{tier.title()} tier active (synced from your account)."
    elif source == "license":
        license_hint = f"{tier.title()} tier active via offline key."
    elif source in ("supabase_expired", "expired"):
        license_hint = "Your license expired. Renew or sign in again."
    elif source == "invalid":
        license_hint = "Stored license is invalid. Sign in again or paste a fresh key."
    else:
        license_hint = "Free tier (5 renders/month). Sign in on /account to activate a paid tier."
    items.append({
        "id": "license",
        "label": "License",
        "ok": license_ok or source == "none",  # free tier counts as "set up", just limited
        "hint": license_hint,
        "action_url": "/account",
        "tier": tier,
        "source": source,
    })

    # ---- 3. Ollama (local LLM) ----------------------------------------------
    # Hits localhost:11434/api/tags with a 2s timeout. Returns the model
    # list when up so we can also flag "running but no models pulled".
    ollama_running = False
    ollama_models: list[str] = []
    try:
        r = requests.get("http://localhost:11434/api/tags", timeout=2)
        if r.status_code == 200:
            ollama_running = True
            data = r.json() or {}
            ollama_models = [m.get("name", "") for m in (data.get("models") or [])]
    except requests.RequestException:
        pass

    if not ollama_running:
        ollama_hint = "Ollama isn't running. Install from ollama.com, then run `ollama serve` (or open the Ollama app)."
    elif not ollama_models:
        ollama_hint = "Ollama is running but no models pulled. Run `ollama pull llama3.1` in a terminal."
    else:
        ollama_hint = f"Ollama running with {len(ollama_models)} model{'s' if len(ollama_models) != 1 else ''}: {', '.join(ollama_models[:3])}"
    items.append({
        "id": "ollama",
        "label": "Ollama (local LLM)",
        "ok": ollama_running and bool(ollama_models),
        "hint": ollama_hint,
        "action_url": "/install/ollama",
        "running": ollama_running,
        "models": ollama_models,
    })

    # ---- 4. Are we ready to make videos? ------------------------------------
    # The studio uses this top-level "ready" flag to decide whether to
    # show the setup panel as a hard gate or a passive nudge.
    all_ok = all(item["ok"] for item in items)

    return jsonify({
        "ok": True,
        "ready": all_ok,
        "items": items,
        "tier": tier,
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
