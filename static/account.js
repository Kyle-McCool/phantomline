// Phantomline /account page. Reads Supabase config from <meta> tags so the
// rendered HTML stays static and CSP-safe (no inline <script> needed).
//
// Talks to Supabase Auth for Google sign-in, then asks the Phantomline server
// (/api/account/licenses, /api/account/portal) for the user's data — server
// validates the JWT and queries Postgres so the browser never holds the
// service-role key.

import { createClient } from "https://esm.sh/@supabase/supabase-js@2.45.4";

const SUPABASE_URL = document.querySelector('meta[name="supabase-url"]')?.content || "";
const SUPABASE_ANON_KEY = document.querySelector('meta[name="supabase-anon-key"]')?.content || "";

const $ = (sel) => document.querySelector(sel);
const status = $("#status");
const setStatus = (msg) => { status.textContent = msg || ""; };

if (!SUPABASE_URL || !SUPABASE_ANON_KEY) {
  setStatus("Account portal isn't configured yet — Supabase env vars are missing on the server.");
  $("#signin-card").hidden = false;  // still surface the button so it's obvious what's missing
} else {
  const supabase = createClient(SUPABASE_URL, SUPABASE_ANON_KEY, {
    auth: { persistSession: true, autoRefreshToken: true, detectSessionInUrl: true },
  });

  function escapeHtml(s) {
    return String(s).replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", "\"": "&quot;", "'": "&#39;" }[c]));
  }

  function formatDate(iso) {
    if (!iso) return "—";
    try { return new Date(iso).toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" }); }
    catch { return iso; }
  }

  function tierLabel(t) {
    if (t === "studio") return "Studio";
    if (t === "pro") return "Creator Pro";
    return t;
  }

  function renderLicenses(licenses) {
    const list = $("#licenses-list");
    if (!licenses || !licenses.length) {
      list.innerHTML = `<div class="empty">No licenses on this email yet. After a Stripe purchase, your key arrives by email and shows up here within a minute.</div>`;
      return;
    }
    list.innerHTML = licenses.map(l => {
      const expiry = l.lifetime ? "Never expires" : `Expires ${formatDate(l.expires_at)}`;
      const activePill = l.active
        ? `<span class="pill">Active</span>`
        : `<span class="pill expired">Expired</span>`;
      const foundingPill = l.is_founding && l.founding_seat
        ? `<span class="pill founding">Founding #${l.founding_seat}</span>`
        : "";
      return `
        <div class="account-card">
          <h3>${escapeHtml(tierLabel(l.tier))}${activePill}${foundingPill}</h3>
          <p class="meta">Issued ${formatDate(l.issued_at)} · ${escapeHtml(expiry)}</p>
          <div class="key-box" data-key="${escapeHtml(l.key)}">${escapeHtml(l.key)}</div>
          <div class="row-actions">
            <button class="cta signal copy-btn" type="button" data-key="${escapeHtml(l.key)}">Copy key</button>
          </div>
        </div>
      `;
    }).join("");

    list.querySelectorAll(".copy-btn").forEach(btn => {
      btn.addEventListener("click", async (e) => {
        const key = e.currentTarget.dataset.key;
        try { await navigator.clipboard.writeText(key); setStatus("Copied license key to clipboard."); }
        catch { setStatus("Couldn't copy — your browser blocked clipboard access. Select the key manually."); }
      });
    });
  }

  async function refresh() {
    const { data: { session } } = await supabase.auth.getSession();
    if (!session) {
      $("#signin-card").hidden = false;
      $("#signed-in").hidden = true;
      return;
    }
    $("#signin-card").hidden = true;
    $("#signed-in").hidden = false;
    $("#who-email").textContent = session.user.email || "(no email)";
    setStatus("Loading licenses…");
    try {
      const res = await fetch("/api/account/licenses", {
        headers: { Authorization: `Bearer ${session.access_token}` },
      });
      const body = await res.json();
      if (!body.ok) throw new Error(body.error || "Failed to load licenses");
      renderLicenses(body.licenses);
      setStatus("");
    } catch (err) {
      setStatus("Couldn't load licenses: " + err.message);
    }
  }

  $("#google-btn").addEventListener("click", async () => {
    setStatus("Redirecting to Google…");
    const { error } = await supabase.auth.signInWithOAuth({
      provider: "google",
      options: { redirectTo: window.location.origin + "/account" },
    });
    if (error) setStatus("Sign-in failed: " + error.message);
  });

  $("#signout-btn").addEventListener("click", async () => {
    await supabase.auth.signOut();
    setStatus("Signed out.");
    refresh();
  });

  $("#portal-btn").addEventListener("click", async () => {
    const { data: { session } } = await supabase.auth.getSession();
    if (!session) return;
    setStatus("Opening Stripe Customer Portal…");
    try {
      const res = await fetch("/api/account/portal", {
        method: "POST",
        headers: { Authorization: `Bearer ${session.access_token}` },
      });
      const body = await res.json();
      if (!body.ok) throw new Error(body.error || "Portal unavailable");
      window.location.href = body.url;
    } catch (err) {
      setStatus(err.message);
    }
  });

  supabase.auth.onAuthStateChange(() => refresh());
  refresh();
}
