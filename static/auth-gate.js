// Phantomline auth gate. Runs as a synchronous <script> in the <head> of
// /app and /studio so it can redirect *before* the studio HTML paints.
//
// Why: anonymous users used to drop straight into the studio with no email
// captured. Now hosted visitors get bounced to /account to sign in via
// Google (Supabase) before they can use the workspace.
//
// What this is NOT: a security control. The studio runs entirely in the
// browser — there's no server secret to protect. This is a conversion
// funnel. A determined visitor can still load index.html in devtools.
// That's fine; the goal is "make signup the path of least resistance."
//
// Why localStorage instead of importing the Supabase SDK: the SDK is
// async, ~80 KB, and already runs on /account. Loading it here too just to
// answer "is the user signed in?" would flash the studio HTML on every
// page load. Supabase persists its session as `sb-<projectref>-auth-token`
// — a synchronous lookup is enough for a UX gate.
//
// Skip cases:
// 1. localhost / 127.0.0.1 / *.local — the user is running `python
//    server.py` on their own machine. They already have the codebase;
//    nagging them to sign in is dumb.
// 2. Capacitor Android shell — `window.Capacitor` is set inside the APK.
//    The app is targeted at owners; eventually we'll wire native auth, but
//    don't redirect them in the meantime.

(function () {
  var host = location.hostname;
  if (
    host === "localhost" ||
    host === "127.0.0.1" ||
    host === "" ||
    host.endsWith(".local")
  ) {
    return;
  }
  if (typeof window !== "undefined" && window.Capacitor) {
    return;
  }

  var hasSession = false;
  try {
    for (var i = 0; i < localStorage.length; i++) {
      var k = localStorage.key(i);
      if (!k || k.indexOf("sb-") !== 0 || k.indexOf("-auth-token") < 0) continue;
      var raw = localStorage.getItem(k);
      if (!raw) continue;
      try {
        var parsed = JSON.parse(raw);
        // Supabase JS v2 stores the session object directly; older
        // shapes wrap it in {currentSession: {...}}. Accept both.
        var sess = (parsed && parsed.currentSession) || parsed;
        if (sess && (sess.access_token || sess.refresh_token)) {
          hasSession = true;
          break;
        }
      } catch (_) {
        // Corrupt entry — ignore and keep scanning.
      }
    }
  } catch (_) {
    // localStorage disabled (private mode, strict shields). Treat as "no
    // session" so the user gets a clear sign-in prompt instead of a studio
    // that can't persist anything.
  }

  if (!hasSession) {
    var next = location.pathname + location.search + location.hash;
    location.replace("/account?next=" + encodeURIComponent(next));
  }
})();
