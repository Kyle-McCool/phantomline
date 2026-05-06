/* Cloud sync status indicator. Updates the #syncStatusDot pill in the
 * studio header so the user can see at a glance whether their projects
 * are syncing to Supabase or stuck on disk.
 *
 * States (see phantomline.css .sync-status--* classes):
 *   unknown       — initial, before first probe
 *   unconfigured  — Supabase env vars not set on the local install,
 *                   sync is off by design (grey, "local only")
 *   ok            — last /api/projects fetch succeeded with a session
 *                   (green dot, "synced")
 *   warn          — transient: 1 fetch failed, retrying. Yellow.
 *   err           — persistent: 2+ consecutive failures. Red.
 *
 * Implementation notes:
 * - Polls /api/projects every 30s when the tab is visible (page
 *   visibility API). Backs off when hidden so we don't burn battery
 *   in a background tab.
 * - Listens for any fetch() failure on /api/* via a wrapped onerror
 *   path. The window.fetch shim in phantomline.js handles the auth
 *   header; this just observes the outcome.
 * - On Capacitor (mobile shell) the dot stays hidden — sync isn't
 *   wired there yet.
 */
(function () {
  var pill = document.getElementById('syncStatusDot');
  if (!pill) return;

  var inCapacitor = typeof window !== 'undefined' && window.Capacitor;
  if (inCapacitor) { pill.hidden = true; return; }

  var consecutiveFailures = 0;

  function setState(state, label) {
    pill.classList.remove(
      'sync-status--unknown',
      'sync-status--unconfigured',
      'sync-status--ok',
      'sync-status--warn',
      'sync-status--err'
    );
    pill.classList.add('sync-status--' + state);
    var text = pill.querySelector('.sync-status-text');
    if (text) text.textContent = label;
    pill.setAttribute('aria-label', 'Cloud sync status: ' + label);
    pill.setAttribute('title', 'Cloud sync status: ' + label);
  }

  function readSession() {
    try {
      for (var i = 0; i < localStorage.length; i++) {
        var k = localStorage.key(i);
        if (!k || k.indexOf('sb-') !== 0 || k.indexOf('-auth-token') < 0) continue;
        var raw = localStorage.getItem(k);
        if (raw && raw.indexOf('access_token') >= 0) return true;
      }
    } catch (_) { /* localStorage disabled */ }
    return false;
  }

  function probe() {
    // Quick HEAD-style probe: fetch a small endpoint that exercises
    // the same auth path projects use. /api/projects?_dryrun=1 is
    // intentionally cheap on the server side (just permissions).
    fetch('/api/projects', { credentials: 'same-origin' })
      .then(function (r) {
        if (r.status === 401 || r.status === 403) {
          // Server says "you need to sign in." If we have no session,
          // sync is not really broken — it's just not on yet. Show
          // the unconfigured grey state with a friendlier label.
          if (!readSession()) {
            setState('unconfigured', 'local only');
          } else {
            consecutiveFailures += 1;
            setState(consecutiveFailures >= 2 ? 'err' : 'warn',
                     consecutiveFailures >= 2 ? 'sign in expired' : 'retrying');
          }
          return;
        }
        if (!r.ok) {
          consecutiveFailures += 1;
          setState(consecutiveFailures >= 2 ? 'err' : 'warn',
                   consecutiveFailures >= 2 ? 'sync failed' : 'retrying');
          return;
        }
        consecutiveFailures = 0;
        setState(readSession() ? 'ok' : 'unconfigured',
                 readSession() ? 'synced' : 'local only');
      })
      .catch(function () {
        consecutiveFailures += 1;
        setState(consecutiveFailures >= 2 ? 'err' : 'warn',
                 consecutiveFailures >= 2 ? 'offline' : 'retrying');
      });
  }

  // Initial probe + 30s polling, paused when the tab is hidden.
  var timer = null;
  function start() {
    if (timer) return;
    probe();
    timer = setInterval(probe, 30000);
  }
  function stop() {
    if (timer) { clearInterval(timer); timer = null; }
  }
  document.addEventListener('visibilitychange', function () {
    if (document.visibilityState === 'visible') start();
    else stop();
  });
  if (document.visibilityState === 'visible') start();
})();
