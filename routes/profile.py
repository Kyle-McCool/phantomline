"""User profile API: read profile, update display name, upload avatar.

All routes require a valid Supabase JWT in the Authorization header.
Avatar uploads stream into the public `avatars` bucket under
<user_id>/avatar.<ext>. The previous avatar (if any) is deleted on
replace so we don't leak storage.
"""
from __future__ import annotations

import io
import mimetypes
from typing import Any

import requests
from flask import Blueprint, jsonify, request

from auth_helpers import (
    rest_url,
    service_headers,
    storage_url,
    supabase_configured,
    supabase_service_role_key,
    supabase_url,
    validate_jwt,
)


profile_bp = Blueprint("profile", __name__)


AVATARS_BUCKET = "avatars"
MAX_AVATAR_BYTES = 4 * 1024 * 1024  # 4 MB cap, plenty for a 512x512 PNG/JPG
ALLOWED_AVATAR_TYPES = {"image/png", "image/jpeg", "image/webp", "image/gif"}


def _profile_for(user_id: str) -> dict[str, Any] | None:
    """Fetch the user_profiles row for this user, or None if it hasn't
    been created yet (which is normal for first sign-in)."""
    try:
        res = requests.get(
            rest_url(f"user_profiles?user_id=eq.{user_id}&limit=1"),
            headers=service_headers(),
            timeout=8,
        )
    except requests.RequestException:
        return None
    if res.status_code != 200:
        return None
    try:
        rows = res.json()
    except ValueError:
        return None
    if isinstance(rows, list) and rows:
        return rows[0]
    return None


def _ensure_profile(user_id: str, user: dict[str, Any]) -> dict[str, Any]:
    """Create the user_profiles row if it doesn't exist. Default the
    display_name to the email prefix or the OAuth-provided name."""
    existing = _profile_for(user_id)
    if existing:
        return existing
    # Pull initial display_name from the JWT user object: prefer the
    # OAuth-provided full name, fall back to email prefix.
    meta = user.get("user_metadata") or {}
    display = (
        meta.get("full_name")
        or meta.get("name")
        or (user.get("email") or "").split("@")[0]
        or "Creator"
    )
    body = {"user_id": user_id, "display_name": display[:80]}
    try:
        res = requests.post(
            rest_url("user_profiles"),
            json=body,
            headers=service_headers(),
            timeout=8,
        )
    except requests.RequestException:
        return body
    if res.status_code in (200, 201):
        try:
            rows = res.json()
            if isinstance(rows, list) and rows:
                return rows[0]
        except ValueError:
            pass
    return body


@profile_bp.route("/api/profile/me", methods=["GET"])
def api_profile_me():
    """Return the signed-in user's profile, creating it on first call."""
    if not supabase_configured():
        return jsonify({"ok": False, "error": "Profile API not configured."}), 503
    user = validate_jwt(request.headers.get("Authorization"))
    if not user:
        return jsonify({"ok": False, "error": "Not signed in."}), 401
    user_id = user.get("id")
    if not user_id:
        return jsonify({"ok": False, "error": "Invalid user."}), 401
    profile = _ensure_profile(user_id, user)
    return jsonify({
        "ok": True,
        "profile": {
            "user_id": user_id,
            "email": user.get("email"),
            "display_name": profile.get("display_name") or "",
            "avatar_url": profile.get("avatar_url") or "",
        },
    })


@profile_bp.route("/api/profile/me", methods=["PATCH"])
def api_profile_update():
    """Update display_name. Body: {display_name: str}."""
    if not supabase_configured():
        return jsonify({"ok": False, "error": "Profile API not configured."}), 503
    user = validate_jwt(request.headers.get("Authorization"))
    if not user:
        return jsonify({"ok": False, "error": "Not signed in."}), 401
    user_id = user.get("id")
    if not user_id:
        return jsonify({"ok": False, "error": "Invalid user."}), 401

    data = request.get_json(force=True) or {}
    display_name = (data.get("display_name") or "").strip()[:80]
    if not display_name:
        return jsonify({"ok": False, "error": "display_name is required."}), 400

    # Ensure the row exists, then patch.
    _ensure_profile(user_id, user)
    try:
        res = requests.patch(
            rest_url(f"user_profiles?user_id=eq.{user_id}"),
            json={"display_name": display_name},
            headers=service_headers(),
            timeout=8,
        )
    except requests.RequestException as exc:
        return jsonify({"ok": False, "error": f"Update failed: {exc}"}), 502
    if res.status_code not in (200, 204):
        return jsonify({"ok": False, "error": f"Update failed: HTTP {res.status_code}"}), 502
    profile = _profile_for(user_id) or {}
    return jsonify({
        "ok": True,
        "profile": {
            "user_id": user_id,
            "email": user.get("email"),
            "display_name": profile.get("display_name") or display_name,
            "avatar_url": profile.get("avatar_url") or "",
        },
    })


@profile_bp.route("/api/profile/avatar", methods=["POST"])
def api_profile_avatar():
    """Upload a new avatar. Multipart form: `file` field with image bytes.
    Replaces any previous avatar. Returns the public URL."""
    if not supabase_configured():
        return jsonify({"ok": False, "error": "Profile API not configured."}), 503
    user = validate_jwt(request.headers.get("Authorization"))
    if not user:
        return jsonify({"ok": False, "error": "Not signed in."}), 401
    user_id = user.get("id")
    if not user_id:
        return jsonify({"ok": False, "error": "Invalid user."}), 401

    if "file" not in request.files:
        return jsonify({"ok": False, "error": "No file uploaded."}), 400
    fileobj = request.files["file"]
    raw = fileobj.read(MAX_AVATAR_BYTES + 1)
    if len(raw) > MAX_AVATAR_BYTES:
        return jsonify({"ok": False, "error": "Avatar exceeds 4 MB."}), 400
    if not raw:
        return jsonify({"ok": False, "error": "Empty file."}), 400

    # Sniff content-type from the upload header; default to PNG.
    ctype = (fileobj.mimetype or "").lower()
    if ctype not in ALLOWED_AVATAR_TYPES:
        # Try mimetypes by filename.
        guess, _ = mimetypes.guess_type(fileobj.filename or "")
        if guess in ALLOWED_AVATAR_TYPES:
            ctype = guess
        else:
            return jsonify({
                "ok": False,
                "error": "Unsupported image type. Use PNG, JPEG, WebP, or GIF."
            }), 400

    ext = mimetypes.guess_extension(ctype) or ".png"
    if ext == ".jpe":
        ext = ".jpg"
    object_path = f"{user_id}/avatar{ext}"

    # Best-effort delete of any previous avatar with a different extension
    # so we don't accumulate orphaned files. Service-role bypasses RLS.
    existing = _profile_for(user_id) or {}
    old_path = existing.get("avatar_path")
    if old_path and old_path != object_path:
        try:
            base = (supabase_url() or "").rstrip("/")
            requests.delete(
                f"{base}/storage/v1/object/{AVATARS_BUCKET}/{old_path}",
                headers={
                    "apikey": supabase_service_role_key() or "",
                    "Authorization": f"Bearer {supabase_service_role_key() or ''}",
                },
                timeout=8,
            )
        except requests.RequestException:
            pass

    # Upload.
    upload_url = storage_url(AVATARS_BUCKET, object_path)
    try:
        res = requests.post(
            upload_url,
            data=raw,
            headers={
                "apikey": supabase_service_role_key() or "",
                "Authorization": f"Bearer {supabase_service_role_key() or ''}",
                "Content-Type": ctype,
                "x-upsert": "true",
                "Cache-Control": "max-age=300",  # short cache so updates show fast
            },
            timeout=30,
        )
    except requests.RequestException as exc:
        return jsonify({"ok": False, "error": f"Upload failed: {exc}"}), 502
    if res.status_code not in (200, 201):
        return jsonify({"ok": False, "error": f"Upload failed: HTTP {res.status_code} {res.text[:200]}"}), 502

    # Public URL for the avatars bucket. Cache-bust with updated_at later
    # via a query param if we want to force-refresh in the UI.
    base = (supabase_url() or "").rstrip("/")
    public_url = f"{base}/storage/v1/object/public/{AVATARS_BUCKET}/{object_path}"

    # Ensure profile row exists, then patch with the new avatar URL/path.
    _ensure_profile(user_id, user)
    try:
        requests.patch(
            rest_url(f"user_profiles?user_id=eq.{user_id}"),
            json={"avatar_url": public_url, "avatar_path": object_path},
            headers=service_headers(),
            timeout=8,
        )
    except requests.RequestException:
        pass

    return jsonify({
        "ok": True,
        "avatar_url": public_url,
    })
