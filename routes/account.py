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
  STRIPE_SECRET_KEY             — for Customer Portal sessions + invoice listing
  RESEND_API_KEY (optional)     — enables the "resend license email" button
  FROM_EMAIL    (optional)      — defaults to licenses@phantomline.xyz
"""

from __future__ import annotations

import os
import time
from typing import Any
from urllib.parse import quote

import requests
from flask import Blueprint, jsonify, render_template, request

from auth_helpers import (
    supabase_url as _supabase_url_helper,
    supabase_anon_key as _supabase_anon_key_helper,
    supabase_service_role_key as _supabase_service_role_key_helper,
)


account_bp = Blueprint("account", __name__)


def _env(key: str) -> str | None:
    return (os.environ.get(key) or "").strip() or None


def _supabase_url() -> str | None:
    # Delegates to auth_helpers so the baked-in DEFAULT_SUPABASE_URL fallback
    # applies for fresh local installs without a .env file.
    return _supabase_url_helper()


def _service_role_key() -> str | None:
    return _supabase_service_role_key_helper()


def _bearer_from_request() -> str | None:
    """Extract the raw bearer token from the current request. Mirrors the
    parsing in _validate_jwt; returns None when the header is missing or
    malformed. Used to pass the user JWT through to JWT-scoped Postgrest
    calls on local installs (no service_role)."""
    auth = request.headers.get("Authorization") or ""
    if not auth.lower().startswith("bearer "):
        return None
    return auth.split(" ", 1)[-1].strip() or None


def _validate_jwt(authorization: str | None) -> dict[str, Any] | None:
    """Hit Supabase's `/auth/v1/user` with the user's bearer token. If the JWT
    is valid Supabase returns 200 with the user object (id, email, ...). Any
    other status -> None. Avoids needing the JWT secret on this server."""
    if not authorization or not authorization.lower().startswith("bearer "):
        return None
    base = _supabase_url()
    anon = _supabase_anon_key_helper()
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


def _query_licenses_by_email(email: str, jwt: str | None = None) -> list[dict[str, Any]]:
    """Postgrest GET against the licenses table. Two auth paths:

      - JWT path: when `jwt` is provided AND the licenses_select_own_by_email
        RLS policy from migration 0004 is in place. Used by local installs
        (no service_role on user laptops by design).
      - service_role path: bypasses RLS. Used by hosted Render where the
        service_role key is available.

    Returns [] if neither path is usable.

    Email is lowercased before the eq filter — the issue-license edge function
    lowercases on insert, but historical rows or odd Stripe casing could still
    drift, and Postgrest eq is case-sensitive.
    """
    base = _supabase_url()
    if not base:
        return []
    email_norm = (email or "").strip().lower()
    if not email_norm:
        return []
    anon = _supabase_anon_key_helper()
    service = _service_role_key()

    if jwt and anon:
        # Local-install / JWT-scoped: RLS gates rows to JWT email automatically.
        headers = {"apikey": anon, "Authorization": f"Bearer {jwt}"}
    elif service:
        # Hosted: service_role bypasses RLS.
        headers = {"apikey": service, "Authorization": f"Bearer {service}"}
    else:
        return []

    fields = "id,license_id,tier,lifetime,is_founding,founding_seat,issued_at,expires_at,stripe_customer_id,stripe_price_id,created_at,key"
    url = f"{base}/rest/v1/licenses?email=eq.{quote(email_norm)}&select={fields}&order=created_at.desc"
    try:
        res = requests.get(url, headers=headers, timeout=8)
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
        supabase_anon_key=_supabase_anon_key_helper() or "",
    )


@account_bp.route("/api/account/licenses", methods=["GET"])
def api_account_licenses():
    """Return all licenses for the signed-in user's email."""
    user = _validate_jwt(request.headers.get("Authorization"))
    if not user or not user.get("email"):
        return jsonify({"ok": False, "error": "Not signed in."}), 401
    email = user["email"]
    rows = _query_licenses_by_email(email, jwt=_bearer_from_request())
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

    rows = _query_licenses_by_email(email, jwt=_bearer_from_request())
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


@account_bp.route("/api/account/me", methods=["GET"])
def api_account_me():
    """Return basic profile info derived from the JWT plus a one-shot summary
    of the buyer's status (active tier, founding seat number) so the Overview
    tab can render without a second round-trip to /api/account/licenses."""
    user = _validate_jwt(request.headers.get("Authorization"))
    if not user or not user.get("email"):
        return jsonify({"ok": False, "error": "Not signed in."}), 401

    email = user["email"]
    rows = _query_licenses_by_email(email, jwt=_bearer_from_request())

    now = time.time()
    active_tier = "free"
    is_founding = False
    founding_seat: int | None = None
    has_subscription = False
    license_count = 0
    for row in rows:
        license_count += 1
        expires_at_iso = row.get("expires_at")
        is_active = bool(row.get("lifetime")) or not expires_at_iso
        if not is_active and expires_at_iso:
            try:
                from datetime import datetime
                exp = datetime.fromisoformat(expires_at_iso.replace("Z", "+00:00")).timestamp()
                is_active = exp > now
            except (ValueError, TypeError):
                is_active = False
        if is_active:
            tier = row.get("tier") or "free"
            # Studio outranks Pro; never downgrade once we've seen the higher tier.
            if tier == "studio" or active_tier == "free":
                active_tier = tier
            if row.get("is_founding"):
                is_founding = True
                if founding_seat is None or (row.get("founding_seat") or 999999) < founding_seat:
                    founding_seat = row.get("founding_seat")
            if row.get("stripe_customer_id"):
                has_subscription = True

    metadata = user.get("user_metadata") or {}
    return jsonify({
        "ok": True,
        "user": {
            "id": user.get("id"),
            "email": email,
            "name": metadata.get("full_name") or metadata.get("name") or "",
            "avatar_url": metadata.get("avatar_url") or "",
            "provider": (user.get("app_metadata") or {}).get("provider") or "google",
            "created_at": user.get("created_at"),
        },
        "summary": {
            "license_count": license_count,
            "active_tier": active_tier,
            "is_founding": is_founding,
            "founding_seat": founding_seat,
            "has_subscription": has_subscription,
        },
    })


@account_bp.route("/api/account/invoices", methods=["GET"])
def api_account_invoices():
    """List Stripe invoices for the signed-in user's customer record. We pick
    the most recent license with a stripe_customer_id and call Stripe's
    /v1/invoices endpoint. Returns an empty list (not an error) when the buyer
    has no Stripe customer (e.g. only manually-issued licenses)."""
    user = _validate_jwt(request.headers.get("Authorization"))
    if not user or not user.get("email"):
        return jsonify({"ok": False, "error": "Not signed in."}), 401

    rows = _query_licenses_by_email(user["email"], jwt=_bearer_from_request())
    customer_id: str | None = None
    for row in rows:
        cid = row.get("stripe_customer_id")
        if cid:
            customer_id = cid
            break
    if not customer_id:
        return jsonify({"ok": True, "invoices": []})

    sk = _env("STRIPE_SECRET_KEY")
    if not sk:
        return jsonify({"ok": False, "error": "Stripe is not configured on the server."}), 500

    try:
        res = requests.get(
            "https://api.stripe.com/v1/invoices",
            params={"customer": customer_id, "limit": 24},
            auth=(sk, ""),
            timeout=10,
        )
    except requests.RequestException as exc:
        return jsonify({"ok": False, "error": f"Stripe call failed: {exc}"}), 502
    if res.status_code != 200:
        return jsonify({"ok": False, "error": f"Stripe returned {res.status_code}"}), 502

    body = res.json()
    invoices = []
    for inv in body.get("data") or []:
        invoices.append({
            "id": inv.get("id"),
            "number": inv.get("number"),
            "status": inv.get("status"),
            "amount_paid": inv.get("amount_paid"),
            "amount_due": inv.get("amount_due"),
            "currency": inv.get("currency"),
            "created": inv.get("created"),
            "hosted_invoice_url": inv.get("hosted_invoice_url"),
            "invoice_pdf": inv.get("invoice_pdf"),
            "description": inv.get("description"),
        })
    return jsonify({"ok": True, "invoices": invoices})


@account_bp.route("/api/account/licenses/<license_row_id>/resend", methods=["POST"])
def api_resend_license(license_row_id: str):
    """Email the license key to the signed-in user's address. Useful when the
    original purchase email got lost. Looks up the license by row id, verifies
    it belongs to the signed-in email, then dispatches via Resend.

    Resend is optional — without RESEND_API_KEY this returns a 501 so the UI
    can hide the button gracefully."""
    user = _validate_jwt(request.headers.get("Authorization"))
    if not user or not user.get("email"):
        return jsonify({"ok": False, "error": "Not signed in."}), 401
    email = (user["email"] or "").strip().lower()

    resend_key = _env("RESEND_API_KEY")
    if not resend_key:
        return jsonify({"ok": False, "error": "Email resend is not configured on this server."}), 501
    from_email = _env("FROM_EMAIL") or "licenses@phantomline.xyz"

    base = _supabase_url()
    sr = _service_role_key()
    if not base or not sr:
        return jsonify({"ok": False, "error": "Supabase is not configured."}), 500

    try:
        res = requests.get(
            f"{base}/rest/v1/licenses",
            params={
                "id": f"eq.{license_row_id}",
                "select": "id,email,key,tier,lifetime,is_founding,founding_seat",
            },
            headers={"apikey": sr, "Authorization": f"Bearer {sr}"},
            timeout=8,
        )
    except requests.RequestException as exc:
        return jsonify({"ok": False, "error": f"License lookup failed: {exc}"}), 502
    if res.status_code != 200:
        return jsonify({"ok": False, "error": "License lookup failed."}), 502
    rows = res.json() if res.text else []
    if not rows:
        return jsonify({"ok": False, "error": "License not found."}), 404
    row = rows[0]
    if (row.get("email") or "").strip().lower() != email:
        # Don't leak the existence of someone else's license.
        return jsonify({"ok": False, "error": "License not found."}), 404

    tier_label = "Studio" if row.get("tier") == "studio" else "Pro"
    seat_line = (
        f"\nFounding member #{row.get('founding_seat')} of 500 — thank you for being early.\n"
        if row.get("is_founding") and row.get("founding_seat")
        else ""
    )
    text = (
        f"Welcome back to Phantomline {tier_label}!\n"
        f"{seat_line}\n"
        f"Your license key:\n\n"
        f"{row.get('key')}\n\n"
        "To activate it:\n"
        "1. Open Phantomline → Settings\n"
        "2. Paste the key into the License field\n"
        "3. Click \"Apply key\"\n\n"
        "Manage your account: https://phantomline.xyz/account\n\n"
        "— Phantomline"
    )
    try:
        rsp = requests.post(
            "https://api.resend.com/emails",
            headers={
                "Authorization": f"Bearer {resend_key}",
                "Content-Type": "application/json",
            },
            json={
                "from": from_email,
                "to": email,
                "subject": f"Your Phantomline {tier_label} license",
                "text": text,
            },
            timeout=10,
        )
    except requests.RequestException as exc:
        return jsonify({"ok": False, "error": f"Email send failed: {exc}"}), 502
    if not rsp.ok:
        return jsonify({"ok": False, "error": f"Resend returned {rsp.status_code}: {rsp.text[:200]}"}), 502
    return jsonify({"ok": True, "sent_to": email})


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
