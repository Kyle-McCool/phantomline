"""Account portal — Google sign-in via Supabase, view + resend license keys.

The desktop app is still license-key-driven; this is an *optional* web account
for buyers who want to look up their key, manage subscriptions, and resend
emails. By design, never required to use Phantomline — the offline license-key
flow keeps working with no server account.

Auth model
----------
- Browser uses Supabase JS to start Google OAuth.
- After the redirect, the browser holds a Supabase access_token (a JWT).
- Browser hits /api/account/licenses with `Authorization: Bearer <jwt>`.
- This server validates the JWT by hitting Supabase's `/auth/v1/user` endpoint
  (no shared JWT secret on Render — Supabase verifies the JWT for us).
- Server queries the licenses table via Supabase REST (Postgrest) using the
  service_role key, scoped to the user's email.

Env vars (set on Render):
  SUPABASE_URL                  — https://<ref>.supabase.co
  SUPABASE_ANON_KEY             — publishable, used by the browser via the page
  SUPABASE_SERVICE_ROLE_KEY     — server-only, used by this module to query DB
  STRIPE_SECRET_KEY             — for creating Customer Portal sessions
"""

from __future__ import annotations

import os
import time
from typing import Any
from urllib.parse import quote

import requests
from flask import Blueprint, jsonify, render_template, request


account_bp = Blueprint("account", __name__)


def _env(key: str) -> str | None:
    return (os.environ.get(key) or "").strip() or None


def _supabase_url() -> str | None:
    return _env("SUPABASE_URL")


def _service_role_key() -> str | None:
    return _env("SUPABASE_SERVICE_ROLE_KEY")


def _validate_jwt(authorization: str | None) -> dict[str, Any] | None:
    """Hit Supabase's `/auth/v1/user` with the user's bearer token. If the JWT
    is valid Supabase returns 200 with the user object (id, email, ...). Any
    other status -> None. Avoids needing the JWT secret on this server."""
    if not authorization or not authorization.lower().startswith("bearer "):
        return None
    base = _supabase_url()
    anon = _env("SUPABASE_ANON_KEY")
    if not base or not anon:
        return None
    try:
        res = requests.get(
            f"{base}/auth/v1/user",
            headers={
                "Authorization": authorization,
                "apikey": anon,
            },
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


def _query_licenses_by_email(email: str) -> list[dict[str, Any]]:
    """Postgrest GET against the licenses table. service_role bypasses RLS so
    the server can return licenses for any verified email without policy
    plumbing. We never expose the raw key in this list — buyers fetch it via
    /api/account/licenses/<id>/key once we know they're signed in."""
    base = _supabase_url()
    key = _service_role_key()
    if not base or not key:
        return []
    fields = "id,license_id,tier,lifetime,is_founding,founding_seat,issued_at,expires_at,stripe_customer_id,stripe_price_id,created_at,key"
    url = f"{base}/rest/v1/licenses?email=eq.{quote(email)}&select={fields}&order=created_at.desc"
    try:
        res = requests.get(
            url,
            headers={
                "apikey": key,
                "Authorization": f"Bearer {key}",
            },
            timeout=8,
        )
    except requests.RequestException:
        return []
    if res.status_code != 200:
        return []
    try:
        rows = res.json()
    except ValueError:
        return []
    return rows if isinstance(rows, list) else []


@account_bp.route("/account")
def account_page():
    """Render the account portal. The page itself is public — sign-in is
    triggered client-side via the Supabase JS SDK. We pass the public Supabase
    URL + anon key into the template so the browser can start the OAuth flow."""
    return render_template(
        "account.html",
        supabase_url=_supabase_url() or "",
        supabase_anon_key=_env("SUPABASE_ANON_KEY") or "",
    )


@account_bp.route("/api/account/licenses", methods=["GET"])
def api_account_licenses():
    """Return all licenses for the signed-in user's email."""
    user = _validate_jwt(request.headers.get("Authorization"))
    if not user or not user.get("email"):
        return jsonify({"ok": False, "error": "Not signed in."}), 401
    email = user["email"]
    rows = _query_licenses_by_email(email)
    # Mark expired vs active in a way the UI can render without re-doing the
    # math, and decide whether the row owns a Customer Portal handle.
    now = time.time()
    out: list[dict[str, Any]] = []
    for row in rows:
        expires_at_iso = row.get("expires_at")
        expires_at_unix: float | None = None
        if expires_at_iso:
            try:
                # Postgrest emits ISO 8601 with timezone; let Python parse it.
                from datetime import datetime
                expires_at_unix = datetime.fromisoformat(expires_at_iso.replace("Z", "+00:00")).timestamp()
            except (ValueError, TypeError):
                expires_at_unix = None
        active = bool(row.get("lifetime")) or (expires_at_unix is None) or (expires_at_unix > now)
        out.append({
            "id": row.get("id"),
            "tier": row.get("tier"),
            "lifetime": row.get("lifetime"),
            "is_founding": row.get("is_founding"),
            "founding_seat": row.get("founding_seat"),
            "issued_at": row.get("issued_at"),
            "expires_at": expires_at_iso,
            "active": active,
            "key": row.get("key"),
            "has_stripe_customer": bool(row.get("stripe_customer_id")),
        })
    return jsonify({"ok": True, "email": email, "licenses": out})


@account_bp.route("/api/account/portal", methods=["POST"])
def api_account_portal():
    """Create a Stripe Customer Portal session for the signed-in user. We pick
    the most recent license that has a stripe_customer_id and use that customer.
    Free or manually-issued licenses won't have a customer id — those just
    surface a 'no managed subscriptions' message in the UI."""
    user = _validate_jwt(request.headers.get("Authorization"))
    if not user or not user.get("email"):
        return jsonify({"ok": False, "error": "Not signed in."}), 401
    email = user["email"]

    rows = _query_licenses_by_email(email)
    customer_id: str | None = None
    for row in rows:
        cid = row.get("stripe_customer_id")
        if cid:
            customer_id = cid
            break
    if not customer_id:
        return jsonify({
            "ok": False,
            "error": "No subscription tied to this account. Founding Lifetime is one-time and has nothing to manage.",
        }), 404

    sk = _env("STRIPE_SECRET_KEY")
    if not sk:
        return jsonify({"ok": False, "error": "Stripe is not configured on the server."}), 500

    return_url = request.host_url.rstrip("/") + "/account"
    try:
        res = requests.post(
            "https://api.stripe.com/v1/billing_portal/sessions",
            data={"customer": customer_id, "return_url": return_url},
            auth=(sk, ""),
            timeout=10,
        )
    except requests.RequestException as exc:
        return jsonify({"ok": False, "error": f"Stripe call failed: {exc}"}), 502

    if res.status_code != 200:
        return jsonify({"ok": False, "error": f"Stripe returned {res.status_code}: {res.text[:200]}"}), 502
    body = res.json()
    return jsonify({"ok": True, "url": body.get("url")})


@account_bp.route("/api/account/founding-seats", methods=["GET"])
def api_founding_seats():
    """Public endpoint for the marketing page to show 'X / 500 seats taken'.
    Reads the founding_seats view, which is granted to anon."""
    base = _supabase_url()
    anon = _env("SUPABASE_ANON_KEY")
    if not base or not anon:
        return jsonify({"ok": False, "error": "Supabase not configured."}), 503
    try:
        res = requests.get(
            f"{base}/rest/v1/founding_seats?select=*",
            headers={"apikey": anon, "Authorization": f"Bearer {anon}"},
            timeout=8,
        )
    except requests.RequestException:
        return jsonify({"ok": False, "error": "Counter unavailable."}), 503
    if res.status_code != 200:
        return jsonify({"ok": False, "error": "Counter unavailable."}), 503
    try:
        rows = res.json()
    except ValueError:
        return jsonify({"ok": False, "error": "Bad counter response."}), 503
    if not rows:
        return jsonify({"ok": True, "seats_taken": 0, "seats_remaining": 500, "seats_total": 500})
    row = rows[0]
    return jsonify({
        "ok": True,
        "seats_taken": int(row.get("seats_taken") or 0),
        "seats_remaining": int(row.get("seats_remaining") or 500),
        "seats_total": int(row.get("seats_total") or 500),
    })
