"""License + usage routes.

Mobile + desktop both hit `/api/license` to read the current tier and
post a new key. Render and gate routes call `current_tier()` /
`enforce_tier()` to gate features.

This blueprint is auth-agnostic by design. License keys arrive as
HMAC-signed strings — they could come from your Supabase Edge Function,
a Stripe webhook, an email, or pasted in by hand. This module never
talks to Supabase or Stripe directly; it only validates and stores
whatever string the user pastes in."""

from __future__ import annotations

import calendar
import threading
import time
from typing import Any

import requests
from flask import Blueprint, jsonify, request

import license as license_mod
from core import OUTPUT_DIR, _read_json_file, _write_json_file


billing_bp = Blueprint("billing", __name__)

LICENSE_PATH = OUTPUT_DIR / "license.json"
USAGE_PATH = OUTPUT_DIR / "usage.json"
LICENSE_LOCK = threading.Lock()
USAGE_LOCK = threading.Lock()

# Free-tier monthly limits. Pro/studio override these to "unlimited".
FREE_TIER_LIMITS = {
    "renders_per_month": 5,
    "bundles_per_month": 5,
    "publishes_per_month": 0,  # free can't publish at all
}


# Pricing structure surfaced in the Settings UI so users see what upgrading
# unlocks. Treat this as copy + amounts; the checkout_url field on each tier
# is the Stripe Payment Link.
#
# Stripe live mode (account acct_1TSrCcCl8kpxrM1k). Each tier carries the
# monthly Pro/Studio buy link or the one-time Founding link as its primary
# CTA; checkout_url_yearly holds the annual Pro/Studio link for an upsell
# toggle if the UI ever wants one. Price metadata `tier` and `lifetime` is
# what the Supabase webhook reads to decide which license tier to issue,
# so don't rename keys without updating supabase/functions/issue-license.
PRICING = {
    "currency": "USD",
    "tiers": [
        {
            "id": "free",
            "name": "Free",
            "tagline": "Try the local pipeline",
            "price_monthly": None,
            "price_yearly": None,
            "price_lifetime": None,
            "features": [
                "5 video renders per month",
                "2 cloud AI trial renders",
                "Local AI + WebGPU engines",
                "Project library + bundles",
                "No publish scheduler",
                "Render watermark",
            ],
            "cta": "Open Studio",
            "checkout_url": None,
        },
        {
            "id": "pro",
            "name": "Creator Pro",
            "tagline": "Unlock the full studio",
            "price_monthly": 15,
            "price_yearly": 99,  # ~$8.25/mo annually
            "price_lifetime": None,  # use the founding tier instead
            "features": [
                "Unlimited renders",
                "Cloud AI engine (bring your own Claude/GPT key)",
                "No watermark",
                "Publish scheduler + queue",
                "Optimize Library (vidIQ-aware)",
                "SEO Finder + analytics-fit",
                "Channel insights ingest",
                "Brand presets",
            ],
            "cta": "Upgrade to Pro",
            "checkout_url": "https://buy.stripe.com/aFadR8gT9amQa4v4mm2Nq00",
            "checkout_url_yearly": "https://buy.stripe.com/6oU5kC1YffHa2C30662Nq01",
        },
        {
            "id": "studio",
            "name": "Studio",
            "tagline": "Multi-channel + agencies",
            "price_monthly": 29,
            "price_yearly": 249,
            "price_lifetime": None,
            "features": [
                "Everything in Pro",
                "Multi-channel library",
                "Recurring posting calendar",
                "Bulk scheduling",
                "Priority support",
            ],
            "cta": "Upgrade to Studio",
            "checkout_url": "https://buy.stripe.com/bJeaEW1Yf66AccDcSS2Nq02",
            "checkout_url_yearly": "https://buy.stripe.com/14A28qauL9iMekL7yy2Nq03",
        },
        {
            "id": "founding",
            "name": "Founding Lifetime",
            "tagline": "Pay once, keep forever",
            "price_monthly": None,
            "price_yearly": None,
            "price_lifetime": 79,
            "tier_unlocked": "pro",  # founding gives Pro tier permanently
            "features": [
                "Everything in Creator Pro",
                "One-time payment, lifetime updates",
                "Locked-in price even if Pro raises later",
                "Founding member badge",
                "First 500 buyers only",
            ],
            "cta": "Get founding lifetime",
            "checkout_url": "https://buy.stripe.com/9B63cuauL9iM90rf102Nq04",
        },
    ],
}


def _read_license() -> dict[str, Any]:
    return _read_json_file(LICENSE_PATH, {}) or {}


def _write_license(payload: dict[str, Any]) -> None:
    with LICENSE_LOCK:
        _write_json_file(LICENSE_PATH, payload)


def _read_usage() -> dict[str, Any]:
    return _read_json_file(USAGE_PATH, {}) or {}


def _write_usage(payload: dict[str, Any]) -> None:
    with USAGE_LOCK:
        _write_json_file(USAGE_PATH, payload)


def _current_month_key(now: float | None = None) -> str:
    t = time.gmtime(now or time.time())
    return f"{t.tm_year:04d}-{t.tm_mon:02d}"


def current_tier() -> dict[str, Any]:
    """Return the active tier with metadata. Always returns a dict —
    never None — so callers can read `current_tier()["tier"]` safely.

    Two activation paths:
      1. Supabase-synced license: written by /api/license/sync after the
         user signs in with Google. The desktop app trusts these because
         Supabase already verified the JWT + email + RLS at sync time —
         re-running HMAC locally is redundant and requires shipping
         GHOSTLINE_LICENSE_SECRET to laptops, which we don't.
      2. Pasted HMAC license: legacy/air-gapped path. Falls back to
         license.validate() against GHOSTLINE_LICENSE_SECRET.
    """
    stored = _read_license()

    # Path 1: Supabase-sourced. The sync route writes
    # {"source": "supabase", "tier": ..., "email": ..., ...} into
    # license.json. We trust those fields verbatim.
    if stored.get("source") == "supabase":
        expires_at = stored.get("expires_at") or 0
        try:
            expires_at = int(expires_at)
        except (TypeError, ValueError):
            expires_at = 0
        # Lifetime/no-expiry rows have expires_at == 0 — always active.
        if expires_at and expires_at < time.time():
            return {
                "tier": "free",
                "source": "supabase_expired",
                "expires_at": expires_at,
                "license_id": stored.get("license_id"),
                "email": stored.get("email"),
                "error": "License expired. Renew to restore Pro features.",
            }
        return {
            "tier": stored.get("tier") or "free",
            "source": "supabase",
            "expires_at": expires_at,
            "license_id": stored.get("license_id"),
            "email": stored.get("email"),
            "lifetime": bool(stored.get("lifetime")),
            "is_founding": bool(stored.get("is_founding")),
            "founding_seat": stored.get("founding_seat"),
            "synced_at": stored.get("stored_at"),
        }

    # Path 2: HMAC key (legacy / pasted manually / air-gapped install).
    key = (stored.get("key") or "").strip()
    if not key:
        return {"tier": "free", "source": "none", "expires_at": 0}
    payload = license_mod.validate(key)
    if not payload:
        return {"tier": "free", "source": "invalid", "expires_at": 0, "error": "Stored license is invalid. Replace it in Settings."}
    if payload.get("_expired"):
        return {
            "tier": "free",
            "source": "expired",
            "expires_at": payload.get("expires_at", 0),
            "license_id": payload.get("id"),
            "email": payload.get("email"),
            "error": "License expired. Renew to restore Pro features.",
        }
    return {
        "tier": payload.get("tier", "free"),
        "source": "license",
        "expires_at": payload.get("expires_at", 0),
        "license_id": payload.get("id"),
        "email": payload.get("email"),
        "issuer": payload.get("issuer"),
    }


def enforce_tier(required: str) -> tuple[bool, str]:
    """Return (allowed, error_message). For Flask handlers:

        ok, err = enforce_tier("pro")
        if not ok:
            return jsonify({"ok": False, "error": err, "code": "UPGRADE"}), 402
    """
    info = current_tier()
    tier = info.get("tier", "free")
    if license_mod.tier_includes(tier, required):
        return True, ""
    return False, (
        f"This feature requires {required.title()} or higher. "
        f"You're currently on {tier.title()}. "
        f"Upgrade to unlock."
    )


def _bump_usage(metric: str) -> dict[str, Any]:
    """Increment a usage counter for the current month. Resets at month
    boundary automatically. Returns the new state."""
    month = _current_month_key()
    state = _read_usage()
    if state.get("month") != month:
        # New month — wipe the counters.
        state = {"month": month, "counters": {}}
    counters = state.setdefault("counters", {})
    counters[metric] = int(counters.get(metric, 0)) + 1
    _write_usage(state)
    return state


def consume_quota(metric: str) -> tuple[bool, str, dict[str, Any]]:
    """Atomically check the per-month quota for a free-tier user, and
    bump the counter if there's room. Returns (allowed, error, state).
    Pro/studio bypass quotas.

    Use as the first thing in a render/publish handler:

        ok, err, _ = consume_quota("renders_per_month")
        if not ok:
            return jsonify({"ok": False, "error": err, "code": "QUOTA"}), 402
    """
    info = current_tier()
    tier = info.get("tier", "free")
    if license_mod.tier_includes(tier, "pro"):
        return True, "", {"tier": tier, "unlimited": True}
    limit = FREE_TIER_LIMITS.get(metric, 0)
    state = _read_usage()
    month = _current_month_key()
    if state.get("month") != month:
        state = {"month": month, "counters": {}}
    used = int((state.get("counters") or {}).get(metric, 0))
    if used >= limit:
        return (
            False,
            f"Free tier limit reached: {used}/{limit} {metric.replace('_', ' ')} used this month. "
            f"Upgrade to Pro to continue.",
            {"tier": tier, "used": used, "limit": limit, "month": month},
        )
    new_state = _bump_usage(metric)
    return (
        True,
        "",
        {
            "tier": tier,
            "used": (new_state.get("counters") or {}).get(metric, 0),
            "limit": limit,
            "month": month,
        },
    )


@billing_bp.route("/api/license", methods=["GET"])
def api_license_get():
    """Return the current tier + (sanitized) license metadata + pricing."""
    info = current_tier()
    return jsonify({
        "ok": True,
        "license": info,
        "free_limits": FREE_TIER_LIMITS,
        "pricing": PRICING,
    })


@billing_bp.route("/api/pricing", methods=["GET"])
def api_pricing_get():
    """Standalone endpoint for the landing page or pricing screen."""
    return jsonify({"ok": True, "pricing": PRICING})


@billing_bp.route("/api/license", methods=["POST"])
def api_license_post():
    """Accept a new license key. Validates it before storing — invalid
    keys are rejected so users don't end up with broken state on disk."""
    data = request.get_json(silent=True) or {}
    key = (data.get("key") or "").strip()
    if not key:
        # Empty key → clear stored license (downgrade to free).
        _write_license({})
        return jsonify({"ok": True, "license": current_tier(), "cleared": True})
    payload = license_mod.validate(key)
    if not payload:
        return jsonify({
            "ok": False,
            "error": "License key is invalid or the server's license secret env var is not set.",
        }), 400
    if payload.get("_expired"):
        return jsonify({
            "ok": False,
            "error": "License key has expired. Contact support for a renewal.",
            "expires_at": payload.get("expires_at"),
        }), 400
    _write_license({"key": key, "stored_at": time.time()})
    return jsonify({"ok": True, "license": current_tier()})


@billing_bp.route("/api/license", methods=["DELETE"])
def api_license_delete():
    """Drop the stored license. App reverts to free tier."""
    _write_license({})
    return jsonify({"ok": True, "license": current_tier()})


@billing_bp.route("/api/license/sync", methods=["POST"])
def api_license_sync():
    """Sync the active license from Supabase using the user's bearer JWT.

    This is the new "sign in once on /account, instantly activated" path.
    The browser POSTs with `Authorization: Bearer <supabase_access_token>`.
    The server:
      1. Validates the JWT against Supabase /auth/v1/user.
      2. Queries public.licenses for the user's own rows (RLS allowlists
         these via 0004_licenses_user_select.sql — no service_role needed).
      3. Picks the best license (highest tier among active rows, prefer
         lifetime, prefer most recent issued_at) and writes it to
         output/license.json with source="supabase".
      4. Subsequent current_tier() calls trust the stored row.

    Returns 401 if the JWT is missing/invalid, 503 if Supabase isn't
    configured, 502 on Supabase fetch failure, 200 with the new tier on
    success (including a "no licenses found" success that downgrades to free).
    """
    # Lazy import to avoid a hard dep at module load — auth_helpers reaches
    # into env + baked defaults; we only need it on this code path.
    from auth_helpers import (
        supabase_url,
        supabase_anon_key,
        validate_jwt,
        rest_url,
        user_headers,
    )

    base = supabase_url()
    anon = supabase_anon_key()
    if not base or not anon:
        return jsonify({
            "ok": False,
            "error": "Supabase isn't configured on this install. Sign-in sync is unavailable; paste a license key in Settings instead.",
        }), 503

    user = validate_jwt(request.headers.get("Authorization"))
    if not user or not user.get("email"):
        return jsonify({"ok": False, "error": "Not signed in."}), 401

    email = (user.get("email") or "").strip().lower()
    if not email:
        return jsonify({"ok": False, "error": "Token has no email claim."}), 401

    # Pull the JWT back out for the user-scoped REST call. validate_jwt
    # already confirmed the bearer header exists in a valid form.
    bearer = (request.headers.get("Authorization") or "").split(" ", 1)[-1].strip()

    fields = "id,license_id,tier,lifetime,is_founding,founding_seat,issued_at,expires_at,key"
    url = rest_url(f"licenses?select={fields}&order=created_at.desc")
    try:
        res = requests.get(url, headers=user_headers(bearer), timeout=8)
    except requests.RequestException as exc:
        return jsonify({"ok": False, "error": f"Supabase fetch failed: {exc}"}), 502
    if res.status_code != 200:
        return jsonify({
            "ok": False,
            "error": f"Supabase returned {res.status_code}: {res.text[:200]}",
        }), 502
    try:
        rows = res.json() or []
    except ValueError:
        rows = []
    if not isinstance(rows, list):
        rows = []

    # Pick the "best" license. Priority order:
    #   1. Active (not expired) over expired.
    #   2. Higher tier (studio > pro > free).
    #   3. Lifetime over time-bounded.
    #   4. Most recent issued_at as tiebreaker.
    tier_rank = {"free": 0, "pro": 1, "studio": 2}
    now = time.time()

    def _to_unix(value: Any) -> int:
        """Coerce a Supabase timestamp value to unix epoch seconds. Returns
        0 if the value is missing or unparseable. Handles all three shapes
        seen in the licenses table:
          - bigint epoch (issued by the 0002 trigger): 1714234567
          - numeric string from older edge function: "1714234567"
          - ISO 8601 string from Postgres timestamptz columns:
            "2026-05-03T16:11:05+00:00"
        """
        if value in (None, "", 0):
            return 0
        # Try as a number first — fast path for the trigger-issued rows.
        try:
            return int(value)
        except (TypeError, ValueError):
            pass
        # ISO 8601 fallback. fromisoformat in 3.11+ handles "+00:00" natively;
        # older Pythons need the Z→+00:00 swap, which is harmless here.
        try:
            from datetime import datetime
            return int(datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp())
        except (ValueError, TypeError):
            return 0

    def is_active(row: dict[str, Any]) -> bool:
        if row.get("lifetime"):
            return True
        exp_unix = _to_unix(row.get("expires_at"))
        if not exp_unix:
            return True  # no expiry set OR unparseable — treat as active
        return exp_unix > now

    def score(row: dict[str, Any]) -> tuple[int, int, int, int]:
        return (
            1 if is_active(row) else 0,
            tier_rank.get(row.get("tier") or "free", 0),
            1 if row.get("lifetime") else 0,
            _to_unix(row.get("issued_at")),
        )

    if not rows:
        # No licenses found at all — clear any stale local license and
        # surface "you're free tier" so the UI can render accordingly.
        _write_license({})
        return jsonify({
            "ok": True,
            "license": current_tier(),
            "synced": False,
            "email": email,
            "message": "No licenses on file for this email.",
        })

    best = max(rows, key=score)
    expires_at_unix = _to_unix(best.get("expires_at"))

    _write_license({
        "source": "supabase",
        "tier": best.get("tier") or "free",
        "license_id": best.get("license_id") or best.get("id"),
        "email": email,
        "lifetime": bool(best.get("lifetime")),
        "is_founding": bool(best.get("is_founding")),
        "founding_seat": best.get("founding_seat"),
        "expires_at": expires_at_unix,
        "key": best.get("key") or "",  # kept for "Resend by email" UX, not validation
        "stored_at": time.time(),
    })

    return jsonify({
        "ok": True,
        "license": current_tier(),
        "synced": True,
        "email": email,
    })


@billing_bp.route("/api/usage", methods=["GET"])
def api_usage_get():
    """Return this-month counters + the user's effective limits."""
    info = current_tier()
    state = _read_usage()
    month = _current_month_key()
    if state.get("month") != month:
        state = {"month": month, "counters": {}}
    counters = state.get("counters") or {}
    pro = license_mod.tier_includes(info.get("tier", "free"), "pro")
    limits = {k: ("unlimited" if pro else v) for k, v in FREE_TIER_LIMITS.items()}
    # Days remaining in the calendar month so the UI can say "resets in 3 days".
    now = time.gmtime()
    days_in_month = calendar.monthrange(now.tm_year, now.tm_mon)[1]
    days_remaining = max(0, days_in_month - now.tm_mday)
    return jsonify({
        "ok": True,
        "tier": info.get("tier", "free"),
        "month": month,
        "counters": counters,
        "limits": limits,
        "days_until_reset": days_remaining,
    })
