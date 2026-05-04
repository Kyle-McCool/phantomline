"""Shared Supabase auth + storage helpers.

Extracted from routes/account.py so the project store, file routes, and
profile routes can all reuse the same JWT-validation + service-role
fetch pattern without circular imports.

Two modes of Supabase access:
  - User-scoped via the bearer JWT (validated by hitting /auth/v1/user).
    All RLS policies fire normally.
  - Server-scoped via the service_role key (bypasses RLS). Used for
    cross-user reads like the licenses lookup.
"""
from __future__ import annotations

import os
from typing import Any
from urllib.parse import quote

import requests


def _env(name: str) -> str | None:
    v = os.environ.get(name)
    return v if v else None


def supabase_url() -> str | None:
    return _env("SUPABASE_URL")


def supabase_anon_key() -> str | None:
    return _env("SUPABASE_ANON_KEY")


def supabase_service_role_key() -> str | None:
    return _env("SUPABASE_SERVICE_ROLE_KEY")


def supabase_configured() -> bool:
    """True when all three env vars are present. The hosted store and
    profile routes 503 cleanly when this returns False so a misconfigured
    deploy fails loudly instead of silently corrupting data."""
    return all([supabase_url(), supabase_anon_key(), supabase_service_role_key()])


def validate_jwt(authorization: str | None) -> dict[str, Any] | None:
    """Verify a Bearer JWT against Supabase's /auth/v1/user endpoint.
    Returns the user object on success, None on any failure. Avoids
    needing the JWT signing secret on this server."""
    if not authorization or not authorization.lower().startswith("bearer "):
        return None
    base = supabase_url()
    anon = supabase_anon_key()
    if not base or not anon:
        return None
    try:
        res = requests.get(
            f"{base}/auth/v1/user",
            headers={"Authorization": authorization, "apikey": anon},
            timeout=8,
        )
    except requests.RequestException:
        return None
    if res.status_code != 200:
        return None
    try:
        return res.json()
    except ValueError:
        return None


def service_headers() -> dict[str, str]:
    """Headers for service_role REST calls. Caller must check
    supabase_configured() first."""
    key = supabase_service_role_key() or ""
    return {
        "apikey": key,
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        # Postgrest needs Prefer: return=representation to echo back the
        # inserted/updated row body (otherwise we just get 201 with no body).
        "Prefer": "return=representation",
    }


def rest_url(path: str) -> str:
    """Build a Postgrest REST URL. `path` is everything after /rest/v1/,
    e.g. 'projects?user_id=eq.<uuid>&order=created_at.desc'."""
    base = (supabase_url() or "").rstrip("/")
    return f"{base}/rest/v1/{path.lstrip('/')}"


def storage_url(bucket: str, object_path: str) -> str:
    """Build a Storage HTTP API URL for upload/download."""
    base = (supabase_url() or "").rstrip("/")
    return f"{base}/storage/v1/object/{bucket}/{quote(object_path, safe='/')}"


def storage_signed_url(bucket: str, object_path: str, expires_in: int = 3600) -> str | None:
    """Return a time-limited signed URL for a private-bucket object so the
    browser can stream it directly without proxying through the Flask
    server. Returns None on failure (caller should fall back to a 503
    response or proxy through the server)."""
    if not supabase_configured():
        return None
    base = (supabase_url() or "").rstrip("/")
    url = f"{base}/storage/v1/object/sign/{bucket}/{quote(object_path, safe='/')}"
    try:
        res = requests.post(
            url,
            json={"expiresIn": int(expires_in)},
            headers=service_headers(),
            timeout=8,
        )
    except requests.RequestException:
        return None
    if res.status_code != 200:
        return None
    try:
        body = res.json()
    except ValueError:
        return None
    signed = body.get("signedURL") or body.get("signedUrl")
    if not signed:
        return None
    # Supabase returns a relative path like "/object/sign/...?token=..."
    if signed.startswith("/"):
        return base + "/storage/v1" + signed
    return signed
