"""Project bundle routes.

A bundle is a Make Video session expressed as a single navigable record:
the idea + script + narration + music + mix + timeline + final video, all
linked. Bundles are first-class projects (kind=KIND_BUNDLE) that store a
`members` map of role -> child project_id, plus the params the user chose
in the Make Video form so the session can be re-opened and edited later.

The Library lists bundles by default; individual artifact projects are
accessible via the "All artifacts" filter."""

from __future__ import annotations

from typing import Any

from flask import Blueprint, jsonify, request

import projects as project_store

from core import PROJECTS


bundles_bp = Blueprint("bundles", __name__)


# Form fields we round-trip through bundles. Only these are persisted; the
# form has dozens of inputs but most are derived from these primary choices.
BUNDLE_PARAM_KEYS = {
    "topic", "title", "preferredTitle", "niche", "audience", "format", "recipe",
    "hookStyle", "tensionFormat", "loopType", "tone", "genre",
    "videoMode", "duration", "aspect", "captions", "captionStyle",
    "voice", "musicPrompt", "visualPreset", "visualStyle", "visualAmbience",
    "visualCharacter", "visualSource", "patternInterrupts", "sourceEnhance",
    "titleStyle", "keywordMode", "pinnedComment", "hashtags",
    "selectedIdea",  # the chosen idea bundle dict
}


def _clean_params(raw) -> dict[str, Any]:
    if not isinstance(raw, dict):
        return {}
    out: dict[str, Any] = {}
    for key, value in raw.items():
        if key not in BUNDLE_PARAM_KEYS:
            continue
        if isinstance(value, str):
            out[key] = value[:4000]
        elif isinstance(value, (bool, int, float)) or value is None:
            out[key] = value
        elif isinstance(value, (list, dict)):
            out[key] = value  # already JSON-serializable from request
    return out


def _clean_members(raw) -> dict[str, str]:
    if not isinstance(raw, dict):
        return {}
    out: dict[str, str] = {}
    for role, pid in raw.items():
        if not isinstance(role, str) or not isinstance(pid, str):
            continue
        if not PROJECTS.get(pid):
            continue  # skip unknown project ids
        out[role[:32]] = pid
    return out


@bundles_bp.route("/api/bundles", methods=["GET"])
def api_bundles_list():
    """Return all bundle projects, newest first. Each bundle includes its
    expanded child records under `children` so the Library can render the
    full session in one card without N+1 lookups."""
    bundles = []
    for p in PROJECTS.all():
        if p.get("kind") != project_store.KIND_BUNDLE:
            continue
        expanded = PROJECTS.expand_bundle(p["id"]) or p
        bundles.append(expanded)
    return jsonify({"ok": True, "bundles": bundles})


@bundles_bp.route("/api/bundles", methods=["POST"])
def api_bundles_create():
    """Create a bundle linking artifact projects from one Make Video session.

    Body: {title, params, members}. `members` maps role -> child project_id."""
    data = request.get_json(silent=True) or {}
    title = (data.get("title") or "Untitled video").strip()[:160]
    params = _clean_params(data.get("params"))
    members = _clean_members(data.get("members"))
    if not members:
        return jsonify({"ok": False, "error": "members is required"}), 400
    bundle = PROJECTS.create_bundle(title=title, params=params, members=members)
    PROJECTS.update(bundle["id"], status="ready")
    return jsonify({"ok": True, "bundle": PROJECTS.expand_bundle(bundle["id"])})


@bundles_bp.route("/api/bundles/<bundle_id>", methods=["GET"])
def api_bundle_get(bundle_id):
    expanded = PROJECTS.expand_bundle(bundle_id)
    if not expanded:
        return jsonify({"ok": False, "error": "Bundle not found."}), 404
    return jsonify({"ok": True, "bundle": expanded})


@bundles_bp.route("/api/bundles/<bundle_id>", methods=["DELETE"])
def api_bundle_delete(bundle_id):
    """Delete the bundle record itself. Child artifact projects are
    untouched — they remain in the Library under "All artifacts" so the
    user doesn't lose work by clearing a bundle."""
    bundle = PROJECTS.get(bundle_id)
    if not bundle or bundle.get("kind") != project_store.KIND_BUNDLE:
        return jsonify({"ok": False, "error": "Bundle not found."}), 404
    PROJECTS.delete(bundle_id)
    return jsonify({"ok": True})
