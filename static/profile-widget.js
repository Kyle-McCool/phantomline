/* Studio top-right profile widget.
 *
 * Reads the user's Supabase session from localStorage (same key the
 * auth-gate inspects), fetches /api/profile/me, renders the badge +
 * dropdown menu. Avatar upload + display-name edit + sign-out wired
 * inline. Skipped entirely on localhost (desktop install — no auth)
 * and inside the Capacitor Android shell (we don't have native auth
 * wired up there yet).
 */
(function () {
  function $(id) { return document.getElementById(id); }

  var host = location.hostname;
  var isLocal = host === "localhost" || host === "127.0.0.1" || host === "" || host.endsWith(".local");
  var inCapacitor = typeof window !== "undefined" && window.Capacitor;
  // If we're in the desktop install or the mobile shell, the studio
  // doesn't have user accounts, so the widget never shows.
  if (isLocal || inCapacitor) return;

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
  if (!session) return; // auth-gate should have redirected, but bail safely

  var wrap = $("profileWrap");
  if (!wrap) return; // template missing the widget

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
