/**
 * Ghostline license issuer.
 *
 * Stripe webhook -> this function -> signed license key emailed to customer.
 *
 * Deploy with:
 *   supabase functions deploy issue-license --project-ref <your-ref>
 *
 * Required env vars (set in Supabase dashboard):
 *   GHOSTLINE_LICENSE_SECRET   - the same hex secret in your Flask .env
 *   STRIPE_SECRET_KEY          - sk_live_... or sk_test_...
 *   STRIPE_WEBHOOK_SECRET      - whsec_... from Stripe dashboard
 *   RESEND_API_KEY             - or any other email provider you prefer
 *   FROM_EMAIL                 - e.g. licenses@ghostline.app
 *
 * Wire in Stripe dashboard:
 *   Webhooks -> add endpoint -> URL: https://<project>.supabase.co/functions/v1/issue-license
 *   Listen for: checkout.session.completed
 *
 * Pricing-to-tier map:
 *   Set Stripe price metadata `tier=pro` on Creator Pro prices
 *   Set Stripe price metadata `tier=studio` on Studio prices
 *   Set Stripe price metadata `tier=pro lifetime=true` on Founding Lifetime
 */

// deno-lint-ignore-file no-explicit-any

import { serve } from "https://deno.land/std@0.224.0/http/server.ts";
import Stripe from "https://esm.sh/stripe@14.21.0?target=deno";

const stripe = new Stripe(Deno.env.get("STRIPE_SECRET_KEY")!, {
  apiVersion: "2024-06-20",
  httpClient: Stripe.createFetchHttpClient(),
});

const WEBHOOK_SECRET = Deno.env.get("STRIPE_WEBHOOK_SECRET")!;
const LICENSE_SECRET = Deno.env.get("GHOSTLINE_LICENSE_SECRET")!;
const RESEND_API_KEY = Deno.env.get("RESEND_API_KEY")!;
const FROM_EMAIL = Deno.env.get("FROM_EMAIL") ?? "licenses@ghostline.app";

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

async function issueLicenseKey(opts: {
  tier: "pro" | "studio";
  email: string;
  lifetime: boolean;
  durationDays?: number; // for monthly/yearly
}): Promise<string> {
  const now = Math.floor(Date.now() / 1000);
  const expires = opts.lifetime
    ? 0
    : now + (opts.durationDays ?? 365) * 86400;
  const payload = {
    v: 1,
    tier: opts.tier,
    id: crypto.randomUUID(),
    email: opts.email,
    issued_at: now,
    expires_at: expires,
    seats: 1,
    issuer: "ghostline-supabase",
  };
  const body_b64 = b64url(new TextEncoder().encode(JSON.stringify(payload)));
  const sig = await hmacSha256(LICENSE_SECRET, body_b64);
  const sig_b64 = b64url(sig);
  return `GHL1.${body_b64}.${sig_b64}`;
}

async function sendLicenseEmail(email: string, key: string, tier: string) {
  const subject = `Your Ghostline ${tier} license`;
  const text = `
Welcome to Ghostline ${tier}!

Your license key:

${key}

To activate it:
1. Open Ghostline → Settings
2. Paste the key into the License field
3. Click "Apply key"

Save this email — you'll need the key if you reinstall.

— Ghostline
`.trim();

  const res = await fetch("https://api.resend.com/emails", {
    method: "POST",
    headers: {
      Authorization: `Bearer ${RESEND_API_KEY}`,
      "Content-Type": "application/json",
    },
    body: JSON.stringify({
      from: FROM_EMAIL,
      to: email,
      subject,
      text,
    }),
  });
  if (!res.ok) {
    const errBody = await res.text();
    throw new Error(`Email send failed: ${res.status} ${errBody}`);
  }
}

serve(async (req: Request) => {
  if (req.method !== "POST") {
    return new Response("Method Not Allowed", { status: 405 });
  }

  const body = await req.text();
  const sig = req.headers.get("Stripe-Signature");
  if (!sig) return new Response("Missing signature", { status: 400 });

  let event;
  try {
    event = await stripe.webhooks.constructEventAsync(
      body,
      sig,
      WEBHOOK_SECRET,
    );
  } catch (err) {
    return new Response(`Webhook verification failed: ${(err as Error).message}`, { status: 400 });
  }

  if (event.type !== "checkout.session.completed") {
    return new Response("Ignored", { status: 200 });
  }

  const session = event.data.object as Stripe.Checkout.Session;
  const email = session.customer_details?.email
    ?? session.customer_email
    ?? null;
  if (!email) {
    return new Response("No customer email on session", { status: 400 });
  }

  // Resolve tier + duration from the line item's price metadata.
  const expanded = await stripe.checkout.sessions.retrieve(session.id, {
    expand: ["line_items.data.price.product"],
  });
  const lineItem = expanded.line_items?.data?.[0];
  const price = lineItem?.price;
  const meta = price?.metadata ?? {};
  const tier = (meta.tier as "pro" | "studio") ?? "pro";
  const lifetime = meta.lifetime === "true";
  // Yearly subscriptions use price.recurring.interval == "year"
  const durationDays = price?.recurring?.interval === "month" ? 31 : 365;

  const key = await issueLicenseKey({ tier, email, lifetime, durationDays });
  await sendLicenseEmail(email, key, tier === "studio" ? "Studio" : "Pro");

  // Optional: persist to a `licenses` table in your Supabase Postgres so you
  // can resend keys later. (This is the reason most folks add a DB row here.)
  // await supabase.from("licenses").insert({ email, tier, key, ... });

  return new Response(JSON.stringify({ ok: true }), {
    headers: { "Content-Type": "application/json" },
  });
});
