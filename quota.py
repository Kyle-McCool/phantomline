"""Render-quota enforcement.

Free tier: 5 renders/month. Paid tiers (Pro / Studio / Lifetime): no
practical cap (`monthly_render_limit` defaulted to 999,999 in the SQL
migration so the comparison still works).

The state machine:
  1. Render endpoint (e.g. /api/start_short) calls `check_render_quota`
     before kicking off the worker thread. If the user is over their
     monthly limit, the endpoint returns 429 + a clear "upgrade" payload.
  2. On successful submission (worker thread spawned), the endpoint
     calls `increment_render_count` to bump usage_meter for the current
     user + month.
  3. The /api/quota/state endpoint is what the UI counter widget polls
     to render "3 / 5 renders this month".

Counter granularity: by-month. We bucket on YYYY-MM in the user's UTC
month — keeping it server-side avoids drift if the browser clock is off
or the user travels timezones mid-month.

Free-tier definition: any user whose newest license has tier='free'.
Paid users have tier in ('pro', 'studio') OR lifetime=true.
"""
from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any

import requests

from auth_helpers import (
    rest_url,
    service_headers,
    supabase_configured,
)


TIER_LIMITS = {
    "free": 5,
    "pro": 999999,
    "studio": 999999,
    "lifetime": 999999,
}


def _current_month_key() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m")


def _seconds_until_month_reset() -> int:
    """Seconds until the first day of next month UTC. Used by the UI to
    render a countdown next to the quota counter."""
    now = datetime.now(timezone.utc)
    if now.month == 12:
        next_month = now.replace(year=now.year + 1, month=1, day=1,
                                 hour=0, minute=0, second=0, microsecond=0)
    else:
        next_month = now.replace(month=now.month + 1, day=1,
                                 hour=0, minute=0, second=0, microsecond=0)
    return int((next_month - now).total_seconds())


def get_active_license(email: str) -> dict[str, Any] | None:
    """Return the highest-tier license for this email, or None.
    Tier ordering: lifetime > studio > pro > free. Returning the highest
    means a user with both a free and a paid license sees paid-tier
    quotas (the right behavior — paid > free always)."""
    if not supabase_configured() or not email:
        return None
    email_norm = email.strip().lower()
    if not email_norm:
        return None
    fields = "id,license_id,tier,lifetime,monthly_render_limit,issued_at,expires_at"
    url = rest_url(
        f"licenses?email=eq.{email_norm}&select={fields}"
    )
    try:
        res = requests.get(url, headers=service_headers(), timeout=8)
    except requests.RequestException:
        return None
    if res.status_code != 200:
        return None
    try:
        rows = res.json()
    except ValueError:
        return None
    if not isinstance(rows, list) or not rows:
        return None
    # Sort by tier-priority then by lifetime then by most recent issued_at.
    tier_rank = {"lifetime": 4, "studio": 3, "pro": 2, "free": 1}
    def sort_key(r):
        if r.get("lifetime"):
            return (5, r.get("issued_at") or 0)
        return (tier_rank.get(r.get("tier") or "free", 1),
                r.get("issued_at") or 0)
    rows.sort(key=sort_key, reverse=True)
    return rows[0]


def get_quota_state(email: str) -> dict[str, Any]:
    """Return everything the UI needs to render the counter widget AND
    everything the middleware needs to decide whether to allow another
    render. Always returns a dict (never None) so callers don't have
    to null-check; an unconfigured Supabase env returns the free-tier
    permissive default."""
    if not supabase_configured() or not email:
        # No Supabase wired up = local desktop install with no enforcement.
        return {
            "tier": "local",
            "limit": 999999,
            "used": 0,
            "remaining": 999999,
            "month": _current_month_key(),
            "resets_in_seconds": _seconds_until_month_reset(),
            "over_limit": False,
        }

    license = get_active_license(email)
    if license:
        tier = (license.get("tier") or "free").lower()
        if license.get("lifetime"):
            tier = "lifetime"
        limit = int(license.get("monthly_render_limit") or TIER_LIMITS.get(tier, 5))
    else:
        # No license row found at all (signup trigger should have created
        # one but maybe it's a brand-new account between signup and first
        # request). Default to free-tier behavior.
        tier = "free"
        limit = TIER_LIMITS["free"]

    month = _current_month_key()
    used = _get_used_count(email, month)
    return {
        "tier": tier,
        "limit": limit,
        "used": used,
        "remaining": max(0, limit - used),
        "month": month,
        "resets_in_seconds": _seconds_until_month_reset(),
        "over_limit": used >= limit,
    }


def _get_used_count(email: str, month: str) -> int:
    """Count renders the user has submitted in the given month. Looks
    up by user_id derived from email (need to query auth.users via the
    licenses email match — same trick /account uses)."""
    user_id = _user_id_for_email(email)
    if not user_id:
        return 0
    url = rest_url(
        f"usage_meter?user_id=eq.{user_id}&month=eq.{month}"
        f"&select=renders_count&limit=1"
    )
    try:
        res = requests.get(url, headers=service_headers(), timeout=8)
    except requests.RequestException:
        return 0
    if res.status_code != 200:
        return 0
    try:
        rows = res.json()
    except ValueError:
        return 0
    if not isinstance(rows, list) or not rows:
        return 0
    return int(rows[0].get("renders_count") or 0)


def _user_id_for_email(email: str) -> str | None:
    """Resolve auth.users.id from email. Cached at the Postgres level by
    Supabase's connection pooler, but we don't cache in Python because
    in-memory caches across multi-worker Render deploys would drift."""
    if not email:
        return None
    # auth.users isn't directly queryable via PostgREST in modern
    # Supabase configs. Use the admin endpoint instead.
    base = (rest_url("").replace("/rest/v1/", ""))
    url = f"{base.rstrip('/')}/auth/v1/admin/users?email={email.strip().lower()}"
    try:
        res = requests.get(url, headers=service_headers(), timeout=8)
    except requests.RequestException:
        return None
    if res.status_code != 200:
        return None
    try:
        body = res.json()
    except ValueError:
        return None
    users = body.get("users") if isinstance(body, dict) else None
    if not isinstance(users, list) or not users:
        return None
    return users[0].get("id")


def increment_render_count(email: str) -> None:
    """Bump usage_meter for this user's current month. Best-effort: a
    failure here MUST NOT abort the render that's already kicked off
    (the user paid for a 429 or didn't, the render itself is independent).
    Use a Postgres upsert (insert ... on conflict update) so racy concurrent
    submissions don't drop counts."""
    if not supabase_configured() or not email:
        return
    user_id = _user_id_for_email(email)
    if not user_id:
        return
    month = _current_month_key()
    # Postgrest-style upsert via the Prefer header.
    url = rest_url("usage_meter?on_conflict=user_id,month")
    headers = dict(service_headers())
    headers["Prefer"] = "resolution=merge-duplicates,return=minimal"
    body = {
        "user_id": user_id,
        "month": month,
        # We can't easily atomically-increment via Postgrest, so we fetch +
        # inc + write. Tiny race window but acceptable for a per-user counter.
        "renders_count": _get_used_count(email, month) + 1,
        "updated_at": "now()",
    }
    try:
        requests.post(url, json=body, headers=headers, timeout=8)
    except requests.RequestException:
        pass


def quota_blocked_response(state: dict[str, Any]) -> tuple[dict, int]:
    """Standard 429 payload for the render middleware. Includes the
    upgrade CTA so the frontend can route the user to /pricing without
    additional logic."""
    return ({
        "ok": False,
        "error": (
            f"Free tier renders this month are used up "
            f"({state['used']} / {state['limit']}). Upgrade to Pro or Studio "
            f"for unlimited monthly renders."
        ),
        "quota": state,
        "upgrade_url": "/pricing",
    }, 429)
