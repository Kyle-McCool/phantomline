/* Install reminder banner. Polls /api/launch/readiness once on page
 * load; if any required blocker exists (Ollama missing, etc), reveals
 * the sticky banner at the top of the studio with a CTA to jump to
 * the Launch Setup tab where the full checklist + actionable install
 * links live.
 *
 * Why this exists: users were getting deep into the studio (Make
 * Video, Publish, etc) and only finding out they were missing tools
 * when a step failed mid-pipeline. A persistent visible reminder
 * avoids the "broken in the middle" experience.
 *
 * The banner remembers a dismissal in sessionStorage so it doesn't
 * pester returning visitors who already know. it'll come back next
 * tab open or after a real page reload.
 *
 * Hidden on Capacitor (mobile shell) where /api/launch/readiness
 * doesn't apply.
 */
(function () {
  if (typeof window !== 'undefined' && window.Capacitor) return;
  if (sessionStorage.getItem('phantomline-install-banner-dismissed') === '1') return;

  var el = document.getElementById('installBanner');
  if (!el) return;

  function jumpToLaunch() {
    // Find the Launch Setup tab button. .tab-btn[data-tab="launch"].
    var btn = document.querySelector('.tab-btn[data-tab="launch"]');
    if (btn) btn.click();
    el.hidden = true;
  }
  function dismiss() {
    el.hidden = true;
    try { sessionStorage.setItem('phantomline-install-banner-dismissed', '1'); } catch (_) {}
  }

  document.getElementById('installBannerOpen')?.addEventListener('click', jumpToLaunch);
  document.getElementById('installBannerDismiss')?.addEventListener('click', dismiss);

  fetch('/api/launch/readiness', { credentials: 'same-origin' })
    .then(function (r) { return r.ok ? r.json() : null; })
    .then(function (d) {
      if (!d || !d.ok) return;
      var blockers = (d.blockers || []).filter(function (b) { return b.required; });
      if (!blockers.length) return;
      var titleEl = document.getElementById('installBannerTitle');
      var detailEl = document.getElementById('installBannerDetail');
      if (blockers.length === 1) {
        var b = blockers[0];
        if (titleEl) titleEl.textContent = b.label || 'Setup needed';
        if (detailEl) detailEl.textContent = b.detail || 'Install required tools to render videos.';
      } else {
        if (titleEl) titleEl.textContent = blockers.length + ' tools needed';
        if (detailEl) detailEl.textContent =
          blockers.map(function (x) { return x.label || ''; }).filter(Boolean).join(' · ')
          + '. install these to enable rendering.';
      }
      el.hidden = false;
      // De-duplicate the "Ollama offline" signal: the header has its
      // own mode-toggle pill that flashes "ollama offline" with a red
      // dot. Banner and pill say the same thing. pill is redundant
      // when the banner is up. Hide it; show again when banner is
      // dismissed.
      var modeToggle = document.querySelector('.mode-toggle-wrap');
      if (modeToggle) modeToggle.style.display = 'none';
      var origDismiss = document.getElementById('installBannerDismiss');
      if (origDismiss) {
        origDismiss.addEventListener('click', function () {
          if (modeToggle) modeToggle.style.display = '';
        }, { once: true });
      }
      var origOpen = document.getElementById('installBannerOpen');
      if (origOpen) {
        origOpen.addEventListener('click', function () {
          if (modeToggle) modeToggle.style.display = '';
        }, { once: true });
      }
    })
    .catch(function () { /* readiness unavailable; leave banner hidden */ });
})();
