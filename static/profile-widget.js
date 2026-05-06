/* Studio top-right profile widget.
 *
 * Reads the user's Supabase session from localStorage (same key the
 * auth-gate inspects), fetches /api/profile/me, renders the badge +
 * dropdown menu. Avatar upload + display-name edit + sign-out wired
 * inline.
 *
 * Behavior by environment:
 * - Hosted (phantomline.xyz): normal flow — auth-gate redirects to
 *   /account if no session, this widget renders the badge after sign-in.
 * - Local install (localhost / 127.0.0.1 / *.local): if a session is
 *   present in localStorage, render normally — the user signed in on
 *   phantomline.xyz and the studio inherits it. If no session,
 *   render a discoverable "Sign in to sync" button that opens
 *   /account in a new tab. Without this, local users have no path to
 *   cloud sync.
 * - Capacitor (Android shell): native auth not wired yet — bail.
 */
(function () {
  function $(id) { return document.getElementById(id); }

  var host = location.hostname;
  var isLocal = host === "localhost" || host === "127.0.0.1" || host === "" || host.endsWith(".local");
  var inCapacitor = typeof window !== "undefined" && window.Capacitor;
  if (inCapacitor) return;

  function readSession() {
    try {
      for (var i = 0; i < localStorage.length; i++) {
        var k = localStorage.key(i);
        if (!k || k.indexOf("sb-") !== 0 || k.indexOf("-auth-token") < 0) continue;
        var raw = localStorage.getItem(k);
        if (!raw) continue;
        var parsed = JSON.parse(raw);
        var sess = (parsed && parsed.currentSession) || parsed;
        if (sess && sess.access_token) return { token: sess.access_token, key: k };
      }
    } catch (e) { /* localStorage disabled; treat as signed out */ }
    return null;
  }

  function initialsOf(s) {
    if (!s) return "·";
    var parts = String(s).trim().split(/\s+/);
    if (parts.length >= 2) return (parts[0][0] + parts[1][0]).toUpperCase();
    return parts[0].slice(0, 2).toUpperCase();
  }

  function setAvatar(imgEl, initialsEl, url, label) {
    if (url) {
      imgEl.src = url;
      imgEl.alt = label || "";
    } else {
      imgEl.removeAttribute("src");
      imgEl.alt = "";
    }
    initialsEl.textContent = initialsOf(label || "");
  }

  function applyProfile(profile) {
    var displayName = profile.display_name || (profile.email || "").split("@")[0] || "Creator";
    $("profileName").textContent = displayName;
    $("profileMenuName").textContent = displayName;
    $("profileMenuEmail").textContent = profile.email || "";
    setAvatar($("profileAvatar"), $("profileInitials"), profile.avatar_url, displayName);
    setAvatar($("profileMenuAvatar"), $("profileMenuInitials"), profile.avatar_url, displayName);
  }

  var session = readSession();
  var wrap = $("profileWrap");
  if (!wrap) return; // template missing the widget

  // Local install + no session = render a discoverable "Sign in to sync"
  // button. Clicks open the LOCAL /account (same origin as the studio)
  // so the Supabase session writes to localhost's localStorage, which
  // is what fetch() calls from this page can actually read. Going to
  // phantomline.xyz/account would land the session on the wrong
  // origin — same Google account but different localStorage, no help.
  if (!session && isLocal) {
    wrap.style.display = "inline-flex";
    wrap.innerHTML = ''
      + '<a href="/account" '
      +    'class="profile-btn" '
      +    'title="Sign in here to sync your library to the cloud" '
      +    'style="text-decoration:none; gap:8px;">'
      + '  <span class="profile-initials" aria-hidden="true">→</span>'
      + '  <span class="profile-name">Sign in to sync</span>'
      + '</a>';
    return;
  }

  if (!session) return; // auth-gate should have redirected; bail safely

  function authedFetch(url, opts) {
    opts = opts || {};
    opts.headers = opts.headers || {};
    opts.headers.Authorization = "Bearer " + session.token;
    return fetch(url, opts);
  }

  // Fetch profile + reveal the widget once we have data.
  authedFetch("/api/profile/me")
    .then(function (r) { return r.json(); })
    .then(function (d) {
      if (!d || !d.ok) return;
      applyProfile(d.profile);
      wrap.style.display = "";
    })
    .catch(function () { /* offline / 503 — widget stays hidden */ });

  // Dropdown open/close
  var menu = $("profileMenu");
  $("profileBtn").addEventListener("click", function (ev) {
    ev.stopPropagation();
    menu.hidden = !menu.hidden;
  });
  document.addEventListener("click", function (ev) {
    if (menu.hidden) return;
    if (!wrap.contains(ev.target)) menu.hidden = true;
  });
  document.addEventListener("keydown", function (ev) {
    if (ev.key === "Escape") menu.hidden = true;
  });

  // Avatar upload
  $("profileAvatarInput").addEventListener("change", function (ev) {
    var file = ev.target.files && ev.target.files[0];
    if (!file) return;
    if (file.size > 4 * 1024 * 1024) {
      alert("Avatar must be under 4 MB.");
      ev.target.value = "";
      return;
    }
    var fd = new FormData();
    fd.append("file", file);
    authedFetch("/api/profile/avatar", { method: "POST", body: fd })
      .then(function (r) { return r.json(); })
      .then(function (d) {
        if (!d || !d.ok) {
          alert((d && d.error) || "Avatar upload failed.");
          return;
        }
        // Cache-bust the URL so the new image shows immediately.
        var bustUrl = d.avatar_url + (d.avatar_url.indexOf("?") < 0 ? "?" : "&") + "t=" + Date.now();
        var name = $("profileMenuName").textContent;
        setAvatar($("profileAvatar"), $("profileInitials"), bustUrl, name);
        setAvatar($("profileMenuAvatar"), $("profileMenuInitials"), bustUrl, name);
      })
      .catch(function () { alert("Avatar upload failed (network)."); })
      .finally(function () { ev.target.value = ""; });
  });

  // Display-name edit (simple prompt — keeps this widget self-contained
  // without pulling in a modal system).
  $("profileEditNameBtn").addEventListener("click", function () {
    var current = $("profileMenuName").textContent || "";
    var next = prompt("Display name", current);
    if (next === null) return;
    next = next.trim();
    if (!next || next === current) return;
    authedFetch("/api/profile/me", {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ display_name: next }),
    })
      .then(function (r) { return r.json(); })
      .then(function (d) {
        if (!d || !d.ok) {
          alert((d && d.error) || "Update failed.");
          return;
        }
        applyProfile(d.profile);
      })
      .catch(function () { alert("Update failed (network)."); });
  });

  // Render-quota counter widget. Fetches /api/quota/state on load and
  // every 60s after that (so the counter ticks up after a render
  // submission without needing a page refresh). Hides itself when the
  // user is on a paid tier with no practical limit.
  function refreshQuota() {
    authedFetch("/api/quota/state")
      .then(function (r) { return r.json(); })
      .then(function (d) {
        if (!d || !d.ok || !d.quota) return;
        var q = d.quota;
        var widget = $("quotaWidget");
        if (!widget) return;
        // Hide the widget for unlimited tiers — paying users don't need
        // a "renders left" badge cluttering their header.
        if (q.tier !== "free" || q.limit >= 999999) {
          widget.style.display = "none";
          return;
        }
        widget.style.display = "";
        $("quotaUsed").textContent = String(q.used);
        $("quotaLimit").textContent = String(q.limit);
        var upgrade = $("quotaUpgrade");
        if (q.over_limit) {
          widget.classList.add("over");
          if (upgrade) upgrade.hidden = false;
          widget.title = "Over your monthly limit. Upgrade for unlimited renders.";
        } else if (q.remaining <= 1) {
          // Last render of the month — show upgrade prompt early so the
          // user doesn't get stuck mid-workflow.
          widget.classList.remove("over");
          if (upgrade) upgrade.hidden = false;
          widget.title = "Last free render this month. Upgrade to keep going.";
        } else {
          widget.classList.remove("over");
          if (upgrade) upgrade.hidden = true;
          widget.title = q.remaining + " free renders left this month";
        }
      })
      .catch(function () { /* unauth or 503 — widget stays hidden */ });
  }
  refreshQuota();
  setInterval(refreshQuota, 60000);

  // Sign out: clear the Supabase session key and bounce to /account.
  $("profileSignOutBtn").addEventListener("click", function () {
    try {
      // Wipe all sb-*-auth-token entries; Supabase JS will re-create the
      // anon session on next /account visit.
      var toRemove = [];
      for (var i = 0; i < localStorage.length; i++) {
        var k = localStorage.key(i);
        if (k && k.indexOf("sb-") === 0 && k.indexOf("-auth-token") >= 0) {
          toRemove.push(k);
        }
      }
      toRemove.forEach(function (k) { localStorage.removeItem(k); });
    } catch (e) { /* ignore */ }
    location.href = "/account?signed_out=1";
  });
})();
