"""License key validation for Phantomline.

Design goals:

- **Offline-verifiable**: a license key is a self-contained string that
  Phantomline can validate without phoning home. Users can run the app
  fully air-gapped. (The auth flow that *issues* keys is online — that's
  Supabase's job — but the *check* is local.)
- **Tamper-evident**: changing any field in the payload breaks the HMAC.
  Without the server secret, a user can't forge or upgrade a key.
- **Swappable**: the validation function takes a key string and returns
  a dict, so the UI doesn't care whether keys come from Supabase, a
  Stripe webhook, or a manual handout. Swapping issuers later is
  a config change.

Wire format:

    GHL1.<base64url(payload_json)>.<base64url(hmac_sha256)>

Payload fields:

    {
      "v": 1,                          # schema version
      "tier": "free" | "pro" | "studio",
      "id": "<license-id>",            # opaque, unique per license
      "email": "user@example.com",     # optional, for support
      "issued_at": <unix epoch>,
      "expires_at": <unix epoch | 0>,  # 0 = lifetime
      "seats": 1,                      # for future team plans
      "issuer": "ghostline-supabase"   # which backend issued it
    }

Secret rotation: the server reads `GHOSTLINE_LICENSE_SECRET` from env or
.env. To rotate, set both old and new secrets in
`GHOSTLINE_LICENSE_SECRETS_LEGACY` (comma-separated) so existing keys
still validate during the transition window.

Issuing keys (your Supabase Edge Function or Vercel function):

    import hmac, hashlib, base64, json, time
    payload = {"v":1,"tier":"pro","id":"abc","email":"u@x.com",
               "issued_at":int(time.time()),"expires_at":0,"seats":1,
               "issuer":"ghostline-supabase"}
    body = base64.urlsafe_b64encode(json.dumps(payload, separators=(',',':')).encode()).rstrip(b'=')
    sig = hmac.new(SECRET.encode(), body, hashlib.sha256).digest()
    sig_b64 = base64.urlsafe_b64encode(sig).rstrip(b'=')
    license_key = f"GHL1.{body.decode()}.{sig_b64.decode()}"
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time
from pathlib import Path
from typing import Any


LICENSE_PREFIX = "GHL1."
TIERS = ("free", "pro", "studio")


def _load_secrets_from_env_files() -> list[str]:
    """Read GHOSTLINE_LICENSE_SECRET from environ + .env files. Returns
    every candidate so legacy keys keep working through a rotation."""
    secrets: list[str] = []

    def push(value: str | None) -> None:
        if value and value.strip() and value.strip() not in secrets:
            secrets.append(value.strip())

    push(os.environ.get("GHOSTLINE_LICENSE_SECRET"))
    for legacy in (os.environ.get("GHOSTLINE_LICENSE_SECRETS_LEGACY") or "").split(","):
        push(legacy)

    # .env file fallback so the user doesn't have to set a system env var.
    env_path = Path(__file__).resolve().parent / ".env"
    if env_path.exists():
        try:
            for raw in env_path.read_text(encoding="utf-8").splitlines():
                line = raw.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                key = key.strip()
                value = value.strip().strip('"').strip("'")
                if key == "GHOSTLINE_LICENSE_SECRET":
                    push(value)
                elif key == "GHOSTLINE_LICENSE_SECRETS_LEGACY":
                    for legacy in value.split(","):
                        push(legacy)
        except OSError:
            pass

    return secrets


def _b64url_decode(s: str) -> bytes:
    pad = "=" * (-len(s) % 4)
    return base64.urlsafe_b64decode(s + pad)


def validate(key: str) -> dict[str, Any] | None:
    """Validate a license key string. Returns the decoded payload dict if
    valid, None otherwise. Never raises — bad input is just None."""
    if not key or not isinstance(key, str):
        return None
    key = key.strip()
    if not key.startswith(LICENSE_PREFIX):
        return None
    rest = key[len(LICENSE_PREFIX):]
    parts = rest.split(".")
    if len(parts) != 2:
        return None
    body_b64, sig_b64 = parts

    try:
        sig = _b64url_decode(sig_b64)
    except (ValueError, base64.binascii.Error):
        return None

    secrets = _load_secrets_from_env_files()
    if not secrets:
        return None  # no secret configured, refuse to validate
    body_bytes = body_b64.encode("ascii")
    if not any(
        hmac.compare_digest(sig, hmac.new(s.encode(), body_bytes, hashlib.sha256).digest())
        for s in secrets
    ):
        return None

    try:
        payload = json.loads(_b64url_decode(body_b64))
    except (ValueError, json.JSONDecodeError, base64.binascii.Error):
        return None
    if not isinstance(payload, dict):
        return None
    if payload.get("v") != 1:
        return None
    tier = payload.get("tier")
    if tier not in TIERS:
        return None
    expires_at = payload.get("expires_at") or 0
    try:
        expires_at = int(expires_at)
    except (TypeError, ValueError):
        return None
    if expires_at and expires_at < int(time.time()):
        # Expired keys still parse — caller decides whether to allow grace.
        payload["_expired"] = True
    return payload


def tier_includes(tier: str, required: str) -> bool:
    """Studio > Pro > Free. Returns True if the user's tier covers the
    feature's required tier."""
    order = {"free": 0, "pro": 1, "studio": 2}
    return order.get(tier or "free", 0) >= order.get(required, 0)
