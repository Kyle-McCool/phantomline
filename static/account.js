// Phantomline /account page. Reads Supabase config from <meta> tags so the
// rendered HTML stays static and CSP-safe (no inline <script> needed).
//
// Talks to Supabase Auth for Google sign-in, then asks the Phantomline server
// (/api/account/me, /licenses, /invoices, /portal) for the user's data — server
// validates the JWT and queries Postgres / Stripe so the browser never holds
// the service-role key.
//
// Robustness: every async path is wrapped so a single failure doesn't leave
// the page stuck with both signin/signed-in cards hidden. If the Supabase
// import or auth call throws, we surface the error in #status and still
// reveal the sign-in card so the user has a way forward.

const SUPABASE_URL = document.querySelector('meta[name="supabase-url"]')?.content || "";
const SUPABASE_ANON_KEY = document.querySelector('meta[name="supabase-anon-key"]')?.content || "";

const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => Array.from(document.querySelectorAll(sel));
const status = $("#status");
const setStatus = (msg, kind = "") => {
  status.className = kind || "";
  status.textContent = msg || "";
};

function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => (
    { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]
  ));
}

function formatDate(iso) {
  if (!iso) return "-";
  try {
    return new Date(iso).toLocaleDateString(undefined, {
      year: "numeric", month: "short", day: "numeric",
    });
  } catch { return iso; }
}

function formatCurrency(amountMinor, currency) {
  if (typeof amountMinor !== "number") return "-";
  try {
    return new Intl.NumberFormat(undefined, {
      style: "currency",
      currency: (currency || "usd").toUpperCase(),
    }).format(amountMinor / 100);
  } catch {
    return `${(amountMinor / 100).toFixed(2)} ${currency?.toUpperCase() || ""}`;
  }
}

function tierLabel(t) {
  if (t === "studio") return "Studio";
  if (t === "pro") return "Creator Pro";
  if (t === "free") return "Free";
  return t || "-";
}

// ---------------------------------------------------------------------------
// Sign-in card copy override — shown regardless of whether Supabase config
// is present so the user always sees the right reason for being here. The
// /app auth gate redirects here with ?next=<path>; that's a different
// motivation than the default "look up your license" flow.
// ---------------------------------------------------------------------------
function safeNextPathTopLevel() {
  try {
    const raw = new URLSearchParams(location.search).get("next");
    if (!raw) return null;
    if (!raw.startsWith("/") || raw.startsWith("//")) return null;
    return raw;
  } catch (_) { return null; }
}
if (safeNextPathTopLevel()) {
  const heading = document.querySelector("#signin-card h2");
  if (heading) heading.textContent = "Sign in to open Studio";
  const sub = document.querySelector("#signin-card .meta");
  if (sub) sub.textContent = "Phantomline saves your projects to your account so they survive across browsers and devices.";
}

// ---------------------------------------------------------------------------
// Hard-fail short-circuit if Supabase config is missing on the server. Show
// the signin card with a clear status so the user knows what to fix.
// ---------------------------------------------------------------------------
if (!SUPABASE_URL || !SUPABASE_ANON_KEY) {
  setStatus(
    "Account portal isn't fully configured. Supabase env vars are missing on the server.",
    "error",
  );
  $("#signin-card").hidden = false;
} else {
  // Dynamic import wrapped so a network/CSP error is visible to the user
  // instead of leaving the page silently empty.
  (async () => {
    let supabase;
    try {
      const mod = await import("https://esm.sh/@supabase/supabase-js@2.45.4");
      supabase = mod.createClient(SUPABASE_URL, SUPABASE_ANON_KEY, {
        auth: { persistSession: true, autoRefreshToken: true, detectSessionInUrl: true },
      });
    } catch (err) {
      setStatus(
        "Couldn't load the auth library: " + (err?.message || err) +
        ". Try disabling browser shields/extensions on this page.",
        "error",
      );
      $("#signin-card").hidden = false;
      return;
    }

    let lastSession = null;
    let cachedLicenses = null;
    let cachedSummary = null;

    // -----------------------------------------------------------------------
    // Tab switching
    // -----------------------------------------------------------------------
    function activateTab(name) {
      $$('.tabbar [role="tab"]').forEach((b) => {
        b.setAttribute("aria-selected", b.dataset.tab === name ? "true" : "false");
      });
      $$('[role="tabpanel"]').forEach((p) => {
        p.hidden = p.dataset.panel !== name;
      });
      // Lazy-load tab data on first activation.
      if (name === "billing") loadBilling();
    }

    $$('.tabbar [role="tab"]').forEach((btn) => {
      btn.addEventListener("click", () => activateTab(btn.dataset.tab));
    });

    // -----------------------------------------------------------------------
    // Server fetch with bearer token
    // -----------------------------------------------------------------------
    async function authedFetch(path, opts = {}) {
      if (!lastSession) throw new Error("Not signed in.");
      const headers = Object.assign({}, opts.headers, {
        Authorization: `Bearer ${lastSession.access_token}`,
      });
      const res = await fetch(path, Object.assign({}, opts, { headers }));
      let body;
      try { body = await res.json(); } catch { body = {}; }
      if (!res.ok || body.ok === false) {
        throw new Error(body.error || `Request failed (${res.status})`);
      }
      return body;
    }

    // -----------------------------------------------------------------------
    // Render: header + overview
    // -----------------------------------------------------------------------
    function renderHeaderAndOverview(user, summary) {
      // Header
      $("#acct-name").textContent = user.name || "(no name on file)";
      $("#acct-email").textContent = user.email || "";
      const av = $("#acct-avatar");
      av.innerHTML = "";
      if (user.avatar_url) {
        const img = document.createElement("img");
        img.src = user.avatar_url;
        img.alt = "";
        img.referrerPolicy = "no-referrer";
        av.appendChild(img);
      } else {
        const initial = (user.name || user.email || "?").trim().charAt(0).toUpperCase();
        av.textContent = initial;
      }

      // Overview stats
      $("#ov-tier").textContent = tierLabel(summary.active_tier);
      $("#ov-licenses").textContent = String(summary.license_count ?? 0);
      $("#ov-founding").textContent = summary.is_founding && summary.founding_seat
        ? `#${summary.founding_seat}`
        : (summary.is_founding ? "Yes" : "-");
    }

    // -----------------------------------------------------------------------
    // Render: licenses tab
    // -----------------------------------------------------------------------
    function renderLicenses(licenses) {
      const list = $("#licenses-list");
      if (!licenses || !licenses.length) {
        list.innerHTML = `<div class="empty">No licenses on this email yet. After a Stripe purchase, your key arrives by email and shows up here within a minute.</div>`;
        return;
      }
      list.innerHTML = licenses.map((l) => {
        const expiry = l.lifetime ? "Never expires" : `Expires ${formatDate(l.expires_at)}`;
        const activePill = l.active
          ? `<span class="pill">Active</span>`
          : `<span class="pill expired">Expired</span>`;
        const foundingPill = l.is_founding && l.founding_seat
          ? `<span class="pill founding">Founding #${l.founding_seat}</span>`
          : "";
        return `
          <div class="card">
            <h3>${escapeHtml(tierLabel(l.tier))}${activePill}${foundingPill}</h3>
            <p class="meta">Issued ${formatDate(l.issued_at)} · ${escapeHtml(expiry)}</p>
            <div class="key-box" data-key="${escapeHtml(l.key)}">${escapeHtml(l.key)}</div>
            <div class="row-actions">
              <button class="cta signal copy-btn" type="button" data-key="${escapeHtml(l.key)}">Copy key</button>
              <button class="cta secondary resend-btn" type="button" data-id="${escapeHtml(l.id)}">Resend by email</button>
            </div>
          </div>
        `;
      }).join("");

      list.querySelectorAll(".copy-btn").forEach((btn) => {
        btn.addEventListener("click", async (e) => {
          const key = e.currentTarget.dataset.key;
          try {
            await navigator.clipboard.writeText(key);
            setStatus("Copied license key to clipboard.", "success");
          } catch {
            setStatus("Couldn't copy. Your browser blocked clipboard access. Select the key manually.", "error");
          }
        });
      });

      list.querySelectorAll(".resend-btn").forEach((btn) => {
        btn.addEventListener("click", async (e) => {
          const id = e.currentTarget.dataset.id;
          e.currentTarget.disabled = true;
          setStatus("Sending email…");
          try {
            const body = await authedFetch(`/api/account/licenses/${encodeURIComponent(id)}/resend`, {
              method: "POST",
            });
            setStatus(`Email sent to ${body.sent_to}.`, "success");
          } catch (err) {
            setStatus(err.message, "error");
          } finally {
            e.currentTarget.disabled = false;
          }
        });
      });
    }

    // -----------------------------------------------------------------------
    // Render: billing tab (lazy-loaded)
    // -----------------------------------------------------------------------
    let billingLoaded = false;
    async function loadBilling() {
      if (billingLoaded) return;
      billingLoaded = true;

      const subStatus = $("#billing-sub-status");
      const list = $("#invoices-list");

      if (cachedSummary?.has_subscription) {
        subStatus.textContent = "You have an active subscription on file.";
      } else if (cachedSummary?.is_founding) {
        subStatus.textContent = "Founding lifetime: no recurring subscription to manage.";
      } else {
        subStatus.textContent = "No active subscription on file yet.";
      }

      try {
        const body = await authedFetch("/api/account/invoices");
        const invoices = body.invoices || [];
        if (!invoices.length) {
          list.innerHTML = `<div class="empty">No invoices yet.</div>`;
          return;
        }
        list.innerHTML = invoices.map((inv) => {
          const amt = formatCurrency(inv.amount_paid || inv.amount_due || 0, inv.currency);
          const date = inv.created ? formatDate(new Date(inv.created * 1000).toISOString()) : "-";
          const link = inv.hosted_invoice_url
            ? `<a href="${escapeHtml(inv.hosted_invoice_url)}" target="_blank" rel="noopener">View</a>`
            : (inv.invoice_pdf
                ? `<a href="${escapeHtml(inv.invoice_pdf)}" target="_blank" rel="noopener">PDF</a>`
                : "");
          return `
            <div class="invoice-row">
              <div class="desc">
                <div>${escapeHtml(inv.description || inv.number || inv.id)}</div>
                <div class="num">${escapeHtml(date)} · ${escapeHtml(inv.status || "")}</div>
              </div>
              <div class="amt">${escapeHtml(amt)}</div>
              ${link}
            </div>
          `;
        }).join("");
      } catch (err) {
        list.innerHTML = `<div class="empty">Couldn't load invoices: ${escapeHtml(err.message)}</div>`;
      }
    }

    // -----------------------------------------------------------------------
    // Stripe Customer Portal
    // -----------------------------------------------------------------------
    async function openPortal() {
      setStatus("Opening Stripe Customer Portal…");
      try {
        const body = await authedFetch("/api/account/portal", { method: "POST" });
        window.location.href = body.url;
      } catch (err) {
        setStatus(err.message, "error");
      }
    }
    $("#ov-portal-btn").addEventListener("click", openPortal);
    $("#billing-portal-btn").addEventListener("click", openPortal);

    // -----------------------------------------------------------------------
    // ?next= handling — when the auth gate on /app bounces an unauthed user
    // here, it appends ?next=<original path>. Once a session is detected,
    // refresh() forwards them there. Sanitized to same-origin paths only
    // so a crafted link can't redirect to attacker.com after sign-in.
    // -----------------------------------------------------------------------
    // Local alias for the top-level safeNextPathTopLevel — kept inside the
    // closure so the OAuth redirectTo and refresh() forwarding can call it
    // by the shorter name without leaking a global.
    const safeNextPath = safeNextPathTopLevel;

    // -----------------------------------------------------------------------
    // Sign in / out
    // -----------------------------------------------------------------------
    $("#google-btn").addEventListener("click", async () => {
      setStatus("Redirecting to Google…");
      // Preserve ?next= across the OAuth round-trip so we can forward the
      // user after Google bounces them back here.
      const next = safeNextPath();
      const redirectTo = window.location.origin + "/account" +
        (next ? "?next=" + encodeURIComponent(next) : "");
      const { error } = await supabase.auth.signInWithOAuth({
        provider: "google",
        options: { redirectTo },
      });
      if (error) setStatus("Sign-in failed: " + error.message, "error");
    });

    async function doSignOut() {
      try {
        await supabase.auth.signOut();
      } catch (err) { /* still drop UI state */ }
      setStatus("Signed out.", "success");
      lastSession = null;
      cachedLicenses = null;
      cachedSummary = null;
      billingLoaded = false;
      refresh();
    }
    $("#signout-btn").addEventListener("click", doSignOut);
    $("#settings-signout-btn").addEventListener("click", doSignOut);

    // -----------------------------------------------------------------------
    // Top-level refresh
    // -----------------------------------------------------------------------
    async function refresh() {
      let session = null;
      try {
        const { data } = await supabase.auth.getSession();
        session = data?.session || null;
      } catch (err) {
        // localStorage corruption / Brave shields can throw here.
        setStatus("Couldn't read session: " + (err?.message || err), "error");
        $("#signin-card").hidden = false;
        $("#signed-in").hidden = true;
        return;
      }

      lastSession = session;

      if (!session) {
        $("#signin-card").hidden = false;
        $("#signed-in").hidden = true;
        return;
      }

      // Forward to the deep-link target if the user landed here via the
      // /app auth gate. Skips self-redirects (?next=/account) so we don't
      // bounce in a loop.
      const next = safeNextPath();
      if (next && next !== "/account" && !next.startsWith("/account?")) {
        location.replace(next);
        return;
      }

      $("#signin-card").hidden = true;
      $("#signed-in").hidden = false;
      setStatus("Loading your account…");

      // Pull /me + /licenses in parallel — they're independent.
      try {
        const [me, lic] = await Promise.all([
          authedFetch("/api/account/me"),
          authedFetch("/api/account/licenses"),
        ]);
        cachedSummary = me.summary || {};
        cachedLicenses = lic.licenses || [];
        renderHeaderAndOverview(me.user || { email: session.user.email }, cachedSummary);
        renderLicenses(cachedLicenses);
        setStatus("");
      } catch (err) {
        setStatus("Couldn't load account data: " + err.message, "error");
        // Still render whatever we know from the session so the page isn't blank.
        renderHeaderAndOverview(
          { email: session.user.email, name: "", avatar_url: "" },
          { active_tier: "free", license_count: 0, is_founding: false, founding_seat: null, has_subscription: false },
        );
      }
    }

    supabase.auth.onAuthStateChange((event) => {
      if (event === "SIGNED_OUT") {
        lastSession = null;
        cachedLicenses = null;
        cachedSummary = null;
        billingLoaded = false;
      }
      refresh();
    });

    // Initial render.
    refresh();
  })();
}
