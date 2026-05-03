/**
 * Phantomline license issuer.
 *
 * Stripe webhook -> this function -> license persisted to Postgres -> emailed to customer.
 *
 * Required env vars (set as Supabase function secrets):
 *   GHOSTLINE_LICENSE_SECRET   - same hex secret as the Phantomline server .env
 *   STRIPE_SECRET_KEY          - sk_live_... or sk_test_...
 *   STRIPE_WEBHOOK_SECRET      - whsec_... from the Stripe webhook endpoint
 *   RESEND_API_KEY             - resend.com
 *   FROM_EMAIL                 - e.g. licenses@phantomline.xyz (must be verified in Resend)
 *
 * Auto-injected by Supabase (no setup needed):
 *   SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY
 *
 * Stripe price metadata drives tier resolution:
 *   tier=pro / tier=studio          -> subscription tier
 *   lifetime=true                   -> never expires; treated as a Founding seat
 *
 * Email casing: every email is lowercased before it's signed, stored, or
 * looked up. Stripe sometimes returns "Kyle.Mc@Gmail.com" while Supabase Auth
 * returns "kyle.mc@gmail.com" — without normalization the /account page can't
 * find the license.
 */

// deno-lint-ignore-file no-explicit-any

import { serve } from "https://deno.land/std@0.224.0/http/server.ts";
import Stripe from "https://esm.sh/stripe@14.21.0?target=deno";
import { createClient } from "https://esm.sh/@supabase/supabase-js@2.45.4?target=deno";

function requireEnv(name: string): string {
  const v = Deno.env.get(name);
  if (!v) throw new Error(`Missing required env var: ${name}`);
  return v;
}

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function b64url(bytes: Uint8Array): string {
  return btoa(String.fromCharCode(...bytes))
    .replace(/\+/g, "-")
    .replace(/\//g, "_")
    .replace(/=+$/, "");
}

async function hmacSha256(secret: string, message: string): Promise<Uint8Array> {
  const enc = new TextEncoder();
  const key = await crypto.subtle.importKey(
    "raw",
    enc.encode(secret),
    { name: "HMAC", hash: "SHA-256" },
    false,
    ["sign"],
  );
  const sig = await crypto.subtle.sign("HMAC", key, enc.encode(message));
  return new Uint8Array(sig);
}

type IssuedKey = {
  key: string;
  licenseId: string;
  issuedAt: number;
  expiresAt: number;
};

async function issueLicenseKey(opts: {
  tier: "pro" | "studio";
  email: string;
  lifetime: boolean;
  durationDays?: number;
  licenseSecret: string;
}): Promise<IssuedKey> {
  const now = Math.floor(Date.now() / 1000);
  const expires = opts.lifetime
    ? 0
    : now + (opts.durationDays ?? 365) * 86400;
  const licenseId = crypto.randomUUID();
  const payload = {
    v: 1,
    tier: opts.tier,
    id: licenseId,
    email: opts.email,
    issued_at: now,
    expires_at: expires,
    seats: 1,
    issuer: "ghostline-supabase",
  };
  const body_b64 = b64url(new TextEncoder().encode(JSON.stringify(payload)));
  const sig = await hmacSha256(opts.licenseSecret, body_b64);
  const sig_b64 = b64url(sig);
  return {
    key: `GHL1.${body_b64}.${sig_b64}`,
    licenseId,
    issuedAt: now,
    expiresAt: expires,
  };
}

async function sendLicenseEmail(opts: {
  apiKey: string;
  fromEmail: string;
  toEmail: string;
  key: string;
  tier: string;
  foundingSeat: number | null;
}) {
  const subject = `Your Phantomline ${opts.tier} license`;
  const seatLine = opts.foundingSeat
    ? `\nFounding member #${opts.foundingSeat} of 500 — thank you for being early.\n`
    : "";
  const text = `
Welcome to Phantomline ${opts.tier}!
${seatLine}
Your license key:

${opts.key}

To activate it:
1. Open Phantomline → Settings
2. Paste the key into the License field
3. Click "Apply key"

Save this email — you'll need the key if you reinstall.

Manage your account: https://phantomline.xyz/account

— Phantomline
`.trim();

  const res = await fetch("https://api.resend.com/emails", {
    method: "POST",
    headers: {
      Authorization: `Bearer ${opts.apiKey}`,
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ from: opts.fromEmail, to: opts.toEmail, subject, text }),
  });
  if (!res.ok) {
    const errBody = await res.text();
    throw new Error(`Email send failed: ${res.status} ${errBody}`);
  }
}

/**
 * Insert the license row, atomically computing the next founding seat number
 * via a subquery + the unique constraint on (founding_seat). Concurrent founding
 * checkouts that race for the same seat will hit the unique constraint; the loser
 * retries once. Subscription rows skip the seat math entirely.
 */
async function insertLicense(supabase: any, row: {
  key: string;
  licenseId: string;
  email: string;
  tier: "pro" | "studio";
  lifetime: boolean;
  isFounding: boolean;
  issuedAt: number;
  expiresAt: number;
  stripeCustomerId: string | null;
  stripeSessionId: string;
  stripePriceId: string | null;
}): Promise<{ foundingSeat: number | null }> {
  const issuedAtIso = new Date(row.issuedAt * 1000).toISOString();
  const expiresAtIso = row.expiresAt > 0 ? new Date(row.expiresAt * 1000).toISOString() : null;

  for (let attempt = 0; attempt < 2; attempt++) {
    let foundingSeat: number | null = null;
    if (row.isFounding) {
      const { data: seatRow, error: seatErr } = await supabase
        .from("licenses")
        .select("founding_seat")
        .eq("is_founding", true)
        .order("founding_seat", { ascending: false, nullsFirst: false })
        .limit(1)
        .maybeSingle();
      if (seatErr) throw new Error(`Founding-seat lookup failed: ${seatErr.message}`);
      foundingSeat = (seatRow?.founding_seat ?? 0) + 1;
    }

    const { error } = await supabase.from("licenses").insert({
      key: row.key,
      license_id: row.licenseId,
      email: row.email,
      tier: row.tier,
      lifetime: row.lifetime,
      is_founding: row.isFounding,
      founding_seat: foundingSeat,
      issued_at: issuedAtIso,
      expires_at: expiresAtIso,
      stripe_customer_id: row.stripeCustomerId,
      stripe_session_id: row.stripeSessionId,
      stripe_price_id: row.stripePriceId,
    });

    if (!error) return { foundingSeat };

    const code = (error as any)?.code;
    if (code === "23505" && /founding_seat/.test(error.message) && attempt === 0) {
      continue;
    }
    throw new Error(`License insert failed (${code}): ${error.message}`);
  }
  throw new Error("License insert failed after retry");
}

serve(async (req: Request) => {
  if (req.method !== "POST") {
    return new Response("Method Not Allowed", { status: 405 });
  }

  // Validate env at request time so a missing secret returns a clear message
  // instead of a generic 500. Cheaper than failing at module-load and easier to
  // reason about in the Supabase logs.
  let stripeKey: string;
  let webhookSecret: string;
  let licenseSecret: string;
  let supabaseUrl: string;
  let serviceRoleKey: string;
  let resendKey: string;
  let fromEmail: string;
  try {
    stripeKey = requireEnv("STRIPE_SECRET_KEY");
    webhookSecret = requireEnv("STRIPE_WEBHOOK_SECRET");
    licenseSecret = requireEnv("GHOSTLINE_LICENSE_SECRET");
    supabaseUrl = requireEnv("SUPABASE_URL");
    serviceRoleKey = requireEnv("SUPABASE_SERVICE_ROLE_KEY");
    resendKey = requireEnv("RESEND_API_KEY");
    fromEmail = Deno.env.get("FROM_EMAIL") ?? "licenses@phantomline.xyz";
  } catch (err) {
    const msg = (err as Error).message;
    console.error("[issue-license] env error:", msg);
    return jsonResponse({ ok: false, error: msg }, 500);
  }

  const stripe = new Stripe(stripeKey, {
    apiVersion: "2024-06-20",
    httpClient: Stripe.createFetchHttpClient(),
  });
  const supabase = createClient(supabaseUrl, serviceRoleKey, {
    auth: { persistSession: false },
  });

  const body = await req.text();
  const sig = req.headers.get("Stripe-Signature");
  if (!sig) {
    console.error("[issue-license] missing Stripe-Signature header");
    return jsonResponse({ ok: false, error: "Missing signature" }, 400);
  }

  let event;
  try {
    event = await stripe.webhooks.constructEventAsync(body, sig, webhookSecret);
  } catch (err) {
    const msg = (err as Error).message;
    console.error("[issue-license] webhook verify failed:", msg);
    return jsonResponse({ ok: false, error: `Webhook verify failed: ${msg}` }, 400);
  }

  if (event.type !== "checkout.session.completed") {
    return jsonResponse({ ok: true, ignored: event.type });
  }

  try {
    const session = event.data.object as Stripe.Checkout.Session;
    const rawEmail = session.customer_details?.email ?? session.customer_email ?? null;
    if (!rawEmail) {
      console.error("[issue-license] no email on session", session.id);
      return jsonResponse({ ok: false, error: "No customer email on session" }, 400);
    }
    // Lowercase before any storage / signing so Supabase Auth lookups match.
    const email = rawEmail.trim().toLowerCase();

    // Idempotency: if we've already issued for this session, no-op.
    const { data: existing, error: lookupErr } = await supabase
      .from("licenses")
      .select("id")
      .eq("stripe_session_id", session.id)
      .maybeSingle();
    if (lookupErr) {
      console.error("[issue-license] dedup lookup failed:", lookupErr.message);
      return jsonResponse({ ok: false, error: `License lookup failed: ${lookupErr.message}` }, 500);
    }
    if (existing) {
      console.log("[issue-license] dedup hit for session", session.id);
      return jsonResponse({ ok: true, dedup: true });
    }

    const expanded = await stripe.checkout.sessions.retrieve(session.id, {
      expand: ["line_items.data.price.product"],
    });
    const lineItem = expanded.line_items?.data?.[0];
    const price = lineItem?.price;
    const meta = price?.metadata ?? {};
    const tier = (meta.tier as "pro" | "studio") ?? "pro";
    const lifetime = meta.lifetime === "true";
    const durationDays = price?.recurring?.interval === "month" ? 31 : 365;

    const issued = await issueLicenseKey({ tier, email, lifetime, durationDays, licenseSecret });
    const customerId = typeof session.customer === "string"
      ? session.customer
      : session.customer?.id ?? null;

    const { foundingSeat } = await insertLicense(supabase, {
      key: issued.key,
      licenseId: issued.licenseId,
      email,
      tier,
      lifetime,
      isFounding: lifetime, // only the Founding Lifetime product is lifetime
      issuedAt: issued.issuedAt,
      expiresAt: issued.expiresAt,
      stripeCustomerId: customerId,
      stripeSessionId: session.id,
      stripePriceId: price?.id ?? null,
    });

    console.log(
      `[issue-license] issued ${tier} for ${email} (session=${session.id} seat=${foundingSeat})`,
    );

    // Email is best-effort; license is already persisted, so we can resend later if this fails.
    try {
      await sendLicenseEmail({
        apiKey: resendKey,
        fromEmail,
        toEmail: email,
        key: issued.key,
        tier: tier === "studio" ? "Studio" : "Pro",
        foundingSeat,
      });
    } catch (err) {
      console.error("[issue-license] email send failed (license is in DB):", (err as Error).message);
    }

    return jsonResponse({ ok: true, founding_seat: foundingSeat });
  } catch (err) {
    const msg = (err as Error).message;
    const stack = (err as Error).stack;
    console.error("[issue-license] handler failed:", msg, stack);
    return jsonResponse({ ok: false, error: msg }, 500);
  }
});
