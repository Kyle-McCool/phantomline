/* Update-available banner. Polls /api/system/update-check on load and
 * every 60 minutes after, surfacing a small dismissible bar at the top of
 * the studio when the local install is behind the hosted version.
 *
 * Why this exists: paying customers used to need a manual re-download +
 * file replace whenever Phantomline shipped a fix. Now the studio tells
 * them an update is ready and links them straight to the download.
 *
 * Two paths to actually apply the update:
 *   1. Users running via the launcher scripts (start-phantomline.bat /
 *      .command / .sh) get the auto-update on next launch — the launcher
 *      runs the same /api/system/update-check and offers to download
 *      before starting the server.
 *   2. Users running `python server.py` directly (developers, mostly) see
 *      this banner. Click "Get update" → opens the source-zip download
 *      with concise replacement instructions.
 *
 * Skip cases:
 *   - Capacitor (mobile) — the Phantomline app is a thin wrapper around
 *     phantomline.xyz; updates land via the website, not the wrapper.
 *   - When already on the latest version (or offline / hosted side
 *     unreachable) — the banner stays hidden.
 *
 * Dismissal is per-version: if you dismiss "v1.2.0 available", the banner
 * re-appears when v1.3.0 ships. Stored in localStorage under
 * `phantomline-update-banner-dismissed-version`.
 */
(function () {
  if (typeof window !== 'undefined' && window.Capacitor) return;

  // Only meaningful on the desktop install. Hosted phantomline.xyz/app is
  // always current by definition (Render auto-deploys main).
  var host = location.hostname || '';
  var isLocal = host === 'localhost' || host === '127.0.0.1' || host === '' || host.endsWith('.local');
  if (!isLocal) return;

  var POLL_INTERVAL_MS = 60 * 60 * 1000; // 60 min
  var DISMISS_KEY = 'phantomline-update-banner-dismissed-version';

  function getDismissedVersion() {
    try { return localStorage.getItem(DISMISS_KEY) || ''; } catch (_) { return ''; }
  }
  function setDismissedVersion(v) {
    try { localStorage.setItem(DISMISS_KEY, v || ''); } catch (_) {}
  }

  function ensureBannerEl() {
    var existing = document.getElementById('updateBanner');
    if (existing) return existing;
    // Build the banner element on the fly so the studio template doesn't
    // need to declare it in HTML — keeps the update system self-contained
    // in this JS file. Inserted at the very top of the page so it sits
    // above the studio chrome.
    var div = document.createElement('div');
    div.id = 'updateBanner';
    div.className = 'update-banner';
    div.setAttribute('role', 'status');
    div.hidden = true;
    div.innerHTML =
      '<div class="update-banner-inner">' +
      '  <span class="update-banner-icon" aria-hidden="true">&#x21bb;</span>' +
      '  <div class="update-banner-msg">' +
      '    <strong>Update available</strong> ' +
      '    <span id="updateBannerDetail"></span>' +
      '  </div>' +
      '  <a id="updateBannerGet" class="update-banner-cta" href="#" target="_blank" rel="noopener">Get update &rarr;</a>' +
      '  <a id="updateBannerNotes" class="update-banner-link" href="#" target="_blank" rel="noopener">What\'s new</a>' +
      '  <button id="updateBannerDismiss" class="update-banner-x" aria-label="Dismiss">&times;</button>' +
      '</div>';
    document.body.insertBefore(div, document.body.firstChild);
    return div;
  }

  function ensureStyles() {
    if (document.getElementById('updateBannerStyles')) return;
    var style = document.createElement('style');
    style.id = 'updateBannerStyles';
    style.textContent =
      '.update-banner { position: sticky; top: 0; z-index: 99; background: #06212f; ' +
      'color: #cfeaf6; border-bottom: 1px solid #22e7f5; font-size: 13px; }' +
      '.update-banner-inner { display: flex; align-items: center; gap: 12px; ' +
      'max-width: 1200px; margin: 0 auto; padding: 8px 16px; }' +
      '.update-banner-icon { color: #22e7f5; font-size: 16px; }' +
      '.update-banner-msg { flex: 1; }' +
      '.update-banner-msg strong { color: #fff; margin-right: 6px; }' +
      '.update-banner-cta { color: #22e7f5; text-decoration: none; font-weight: 600; ' +
      'padding: 4px 10px; border: 1px solid #22e7f5; border-radius: 0; }' +
      '.update-banner-cta:hover { background: #22e7f5; color: #06212f; }' +
      '.update-banner-link { color: #7adfb8; text-decoration: underline; font-size: 12px; }' +
      '.update-banner-x { background: transparent; border: 0; color: #7a96ad; ' +
      'cursor: pointer; font-size: 18px; padding: 0 6px; line-height: 1; }' +
      '.update-banner-x:hover { color: #fff; }';
    document.head.appendChild(style);
  }

  function renderBanner(check) {
    if (!check || !check.update_available) return;
    if (check.latest && check.latest === getDismissedVersion()) return;

    ensureStyles();
    var el = ensureBannerEl();
    var detailEl = el.querySelector('#updateBannerDetail');
    var ctaEl = el.querySelector('#updateBannerGet');
    var notesEl = el.querySelector('#updateBannerNotes');
    var dismissBtn = el.querySelector('#updateBannerDismiss');

    if (detailEl) {
      detailEl.textContent =
        'Phantomline ' + (check.latest || '') +
        ' is out (you\'re on ' + (check.current || 'unknown') + ').';
    }
    if (ctaEl) ctaEl.href = check.download_url || '/download';
    if (notesEl) notesEl.href = check.release_notes_url || '/releases';

    // Dismiss button stores the version so we don't re-nag for the same one.
    if (dismissBtn && !dismissBtn.dataset.wired) {
      dismissBtn.dataset.wired = '1';
      dismissBtn.addEventListener('click', function () {
        el.hidden = true;
        setDismissedVersion(check.latest || '');
      });
    }
    el.hidden = false;
  }

  function checkOnce() {
    fetch('/api/system/update-check', { credentials: 'same-origin' })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(renderBanner)
      .catch(function () { /* offline / endpoint missing — stay hidden */ });
  }

  // Initial check on load + periodic re-check so a long-running studio
  // session catches updates the user could install on next start.
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', checkOnce);
  } else {
    checkOnce();
  }
  setInterval(checkOnce, POLL_INTERVAL_MS);
})();
