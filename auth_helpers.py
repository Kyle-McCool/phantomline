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


# Publishable defaults so a fresh `python server.py` works without any .env
# editing. Both values are public by design — the URL is the project REST
# endpoint, and the anon key is the publishable browser key (RLS-gated, ships
# in deployed JS already). Env vars still win when set.
#
# The anon key intentionally lives in code, not in a placeholder string,
# because every desktop install needs it to talk to Supabase Auth, and
# asking users to copy/paste a JWT before they sign in is exactly the
# UX wall this whole change is removing. If you ever rotate the anon key,
# update this constant.
DEFAULT_SUPABASE_URL = "https://vdzydhrgazqeyaalguuy.supabase.co"
# Publishable Supabase anon key. Same value that ships in the deployed JS at
# phantomline.xyz/account in the <meta name="supabase-anon-key"> tag — RLS-gated,
# safe to embed. If you rotate the anon key in Supabase, update both here and
# Render's SUPABASE_ANON_KEY env var.
DEFAULT_SUPABASE_ANON_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6InZkenlkaHJnYXpxZXlhYWxndXV5Iiwicm9sZSI6ImFub24iLCJpYXQiOjE3Nzc3NzE5OTEsImV4cCI6MjA5MzM0Nzk5MX0.tDZYgABNm5WH4iVWhxBPXM1JiEUt-Rhr2XQoM6OlD1A"


def _env(name: str) -> str | None:
    v = os.environ.get(name)
    return v if v else None


def supabase_url() -> str | None:
    """Project REST endpoint. Falls back to the baked-in default so a
    fresh local install works without editing .env."""
    return _env("SUPABASE_URL") or (DEFAULT_SUPABASE_URL or None)


def supabase_anon_key() -> str | None:
    """Publishable anon key. Falls back to the baked-in default. Note:
    DEFAULT_SUPABASE_ANON_KEY is intentionally empty in source so a fresh
    clone needs Kyle to paste it once — see the FIXME above."""
    return _env("SUPABASE_ANON_KEY") or (DEFAULT_SUPABASE_ANON_KEY or None)


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
    supabase_configured() first.

    USE SPARINGLY. service_role bypasses RLS — the only legitimate use
    case in Phantomline is the licenses-by-email lookup in
    routes/account.py (cross-user, by design). Project store reads/writes
    should go through user_headers(jwt) so RLS enforces ownership.
    """
    key = supabase_service_role_key() or ""
    return {
        "apikey": key,
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        # Postgrest needs Prefer: return=representation to echo back the
        # inserted/updated row body (otherwise we just get 201 with no body).
        "Prefer": "return=representation",
    }


def user_headers(jwt: str | None) -> dict[str, str]:
    """Headers for user-JWT-scoped REST calls. RLS policies enforce
    that the user can only read/write their own rows.

    `jwt` is the access_token from a Supabase auth session — typically
    extracted from an `Authorization: Bearer <token>` request header
    via validate_jwt(). Pass it through here, NOT the service_role.

    `apikey` is still the publishable anon key (Postgrest requires it
    even when the bearer is a user JWT — the anon key authorizes the
    request to even reach Postgrest, then the bearer JWT controls
    which rows the user can see).
    """
    anon = supabase_anon_key() or ""
    bearer = (jwt or "").strip()
    return {
        "apikey": anon,
        "Authorization": f"Bearer {bearer}" if bearer else f"Bearer {anon}",
        "Content-Type": "application/json",
        "Prefer": "return=representation",
    }


def storage_user_headers(jwt: str | None) -> dict[str, str]:
    """Same as user_headers() but for the Storage HTTP API. Storage
    uses the same auth scheme as Postgrest: apikey = anon, bearer = JWT.
    Content-Type is omitted because Storage uploads use the file's
    content type, not application/json."""
    anon = supabase_anon_key() or ""
    bearer = (jwt or "").strip()
    return {
        "apikey": anon,
        "Authorization": f"Bearer {bearer}" if bearer else f"Bearer {anon}",
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
