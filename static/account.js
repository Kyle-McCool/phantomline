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
// the signin card without a scary red banner on local dev — desktop installs
// don't have Supabase env vars by design, so a "this site is broken" warning
// is a false positive. On hosted (phantomline.xyz) the warning still fires
// loudly because it IS a misconfigured deploy.
// ---------------------------------------------------------------------------
const _hostname = location.hostname || "";
const _isLocal =
  _hostname === "localhost" ||
  _hostname === "127.0.0.1" ||
  _hostname === "" ||
  _hostname.endsWith(".local");

if (!SUPABASE_URL || !SUPABASE_ANON_KEY) {
  if (_isLocal) {
    // Friendly local message: this is your desktop install, no panic.
    setStatus(
      "Local install detected — sign-in lives on phantomline.xyz. Open the hosted account portal to manage your license.",
    );
  } else {
    setStatus(
      "Account portal isn't fully configured. Supabase env vars are missing on the server.",
      "error",
    );
  }
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
      // Roving-tabindex pattern: only the active tab is in the tab order;
      // the others are reachable via arrow keys (handled below). This is
      // the WAI-ARIA Authoring Practices recommendation for tabs.
      $$('.tabbar [role="tab"]').forEach((b) => {
        const isActive = b.dataset.tab === name;
        b.setAttribute("aria-selected", isActive ? "true" : "false");
        b.setAttribute("tabindex", isActive ? "0" : "-1");
      });
      $$('[role="tabpanel"]').forEach((p) => {
        p.hidden = p.dataset.panel !== name;
      });
      // Lazy-load tab data on first activation.
      if (name === "billing") loadBilling();
    }

    $$('.tabbar [role="tab"]').forEach((btn) => {
      btn.addEventListener("click", () => activateTab(btn.dataset.tab));
      // Arrow-key nav per WAI-ARIA tabs pattern. Left/Right move between
      // tabs and activate the new one; Home/End jump to first/last.
      btn.addEventListener("keydown", (e) => {
        const tabs = $$('.tabbar [role="tab"]');
        const i = tabs.indexOf(btn);
        let next = -1;
        if (e.key === "ArrowRight") next = (i + 1) % tabs.length;
        else if (e.key === "ArrowLeft") next = (i - 1 + tabs.length) % tabs.length;
        else if (e.key === "Home") next = 0;
        else if (e.key === "End") next = tabs.length - 1;
        if (next >= 0) {
          e.preventDefault();
          activateTab(tabs[next].dataset.tab);
          tabs[next].focus();
        }
      });
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
        // Friendly empty state for new (free-tier) users. The previous copy
        // ("No licenses on this email yet") read as a dead end — but a fresh
        // sign-in IS a free account, so we lead with the positive frame and
        // give them two next-steps: open Studio (use what they have) or
        // upgrade (convert).
        list.innerHTML = `
          <div class="card">
            <h3>You're on the Free tier <span class="pill">Active</span></h3>
            <p class="meta">5 video renders per month, full local AI pipeline, project library. No card on file.</p>
            <div class="row-actions">
              <a class="cta signal" href="/app">Open Studio</a>
              <a class="cta secondary" href="/pricing">See paid plans</a>
            </div>
            <p class="meta" style="margin-top:14px;">
              Already bought with a different email? Email
              <a href="mailto:support@phantomline.xyz">support@phantomline.xyz</a>
              with your Stripe receipt and we'll move the license over.
            </p>
          </div>
        `;
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
    // YouTube incremental-authorization (PRIORITY 5).
    // The studio's "Connect YouTube" button opens this page with
    // ?yt-connect=1. We trigger an additional Supabase OAuth round-trip
    // requesting youtube.upload + youtube.readonly scopes. Because the
    // user is already signed in, Google shows ONLY the new-scopes
    // consent dialog (incremental auth), not a fresh sign-in.
    //
    // After the round-trip, Supabase puts the Google access_token +
    // refresh_token in session.provider_token + provider_refresh_token.
    // We POST those to /api/youtube/store-token so the server can
    // upsert into user_youtube_tokens (RLS-scoped). Then we close
    // the popup so the studio knows the connect succeeded.
    // -----------------------------------------------------------------------
    async function startYouTubeConnect() {
      setStatus("Requesting YouTube access from Google…");
      const redirectTo = window.location.origin + "/account?yt-connected=1";
      const { error } = await supabase.auth.signInWithOAuth({
        provider: "google",
        options: {
          redirectTo,
          scopes: "https://www.googleapis.com/auth/youtube.upload "
                + "https://www.googleapis.com/auth/youtube.readonly",
          queryParams: {
            access_type: "offline",   // ask Google for a refresh_token
            prompt: "consent",        // force the YT-scopes consent dialog
            include_granted_scopes: "true",  // incremental auth
          },
        },
      });
      if (error) setStatus("YouTube connect failed: " + error.message, "error");
    }

    async function captureYouTubeTokens(session) {
      // session.provider_token + provider_refresh_token are populated
      // by Supabase right after the OAuth round-trip. They live on the
      // session object only briefly — we ship them to the server here
      // and never store them client-side.
      const provider_token = session?.provider_token;
      const provider_refresh_token = session?.provider_refresh_token;
      if (!provider_token) return;
      // Best-effort channel info: hit YouTube /channels?mine=true so we
      // can cache the channel id + title alongside the tokens.
      let channel_id, channel_title;
      try {
        const r = await fetch(
          "https://www.googleapis.com/youtube/v3/channels?part=snippet&mine=true",
          { headers: { Authorization: "Bearer " + provider_token } }
        );
        const d = await r.json();
        const item = (d?.items || [])[0];
        if (item) {
          channel_id = item.id;
          channel_title = item.snippet?.title;
        }
      } catch (_) { /* non-fatal, server will discover later */ }
      try {
        await authedFetch("/api/youtube/store-token", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            provider_token,
            provider_refresh_token,
            expires_in: 3600,
            channel_id,
            channel_title,
          }),
        });
        setStatus(
          channel_title
            ? `YouTube connected: ${channel_title}`
            : "YouTube connected.",
          "success"
        );
      } catch (err) {
        setStatus("Stored token failed: " + err.message, "error");
        return;
      }
      // If this page was opened as a popup from the studio, close it so
      // the studio's Connect button gets the focus back.
      try {
        if (window.opener && !window.opener.closed) {
          window.opener.postMessage({ type: "phantomline:yt-connected" }, location.origin);
          setTimeout(() => window.close(), 1000);
        }
      } catch (_) { /* opener access can throw across origins */ }
    }

    // Param triggers: ?yt-connect=1 starts the flow, ?yt-connected=1
    // (Supabase redirect target) means we just got back, capture tokens.
    const urlParams = new URLSearchParams(location.search);
    if (urlParams.get("yt-connect") === "1") {
      startYouTubeConnect();
    }

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

      // If we just came back from the YouTube incremental-auth grant,
      // capture the provider tokens before they fall off the session
      // object. Done before the licenses/me load so any fetch race is
      // benign.
      if (urlParams.get("yt-connected") === "1") {
        await captureYouTubeTokens(session);
        // Strip the param so a refresh doesn't re-fire the capture.
        try {
          const cleanUrl = location.pathname + (safeNextPath()
            ? "?next=" + encodeURIComponent(safeNextPath()) : "");
          history.replaceState({}, "", cleanUrl);
        } catch (_) {}
      }

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
