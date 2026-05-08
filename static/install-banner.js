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

  // Fan out two parallel readiness checks:
  //   - /api/launch/readiness → tooling (Ollama, ffmpeg, model weights)
  //   - /api/system/setup-status → account state (Supabase config, license sync)
  // We merge their blockers into a single banner so a fresh-install user sees
  // ONE "Setup needed" card listing every missing piece, not two competing
  // banners.
  Promise.all([
    fetch('/api/launch/readiness', { credentials: 'same-origin' })
      .then(function (r) { return r.ok ? r.json() : null; })
      .catch(function () { return null; }),
    fetch('/api/system/setup-status', { credentials: 'same-origin' })
      .then(function (r) { return r.ok ? r.json() : null; })
      .catch(function () { return null; }),
  ]).then(function (results) {
      var readiness = results[0];
      var setupStatus = results[1];
      var blockers = [];
      if (readiness && readiness.ok) {
        blockers = (readiness.blockers || []).filter(function (b) { return b.required; });
      }
      // Merge license + supabase items from setup-status when not OK. Skip
      // license items where source === 'none' AND tier === 'free' — that's
      // a perfectly valid state (free tier user, can still render 5/mo).
      // We only flag licence as a blocker when it's invalid/expired.
      if (setupStatus && setupStatus.ok && Array.isArray(setupStatus.items)) {
        setupStatus.items.forEach(function (item) {
          if (item.ok) return;
          if (item.id === 'license') {
            // Free-tier-no-license is fine; flag only invalid/expired.
            if (item.source === 'invalid' ||
                item.source === 'expired' ||
                item.source === 'supabase_expired') {
              blockers.push({
                label: 'License: ' + (item.hint || 'expired or invalid'),
                detail: item.hint || '',
                required: true,
              });
            }
          } else if (item.id === 'supabase') {
            blockers.push({
              label: 'Sign-in unavailable',
              detail: item.hint || '',
              required: true,
            });
          }
          // Ollama is already covered by /api/launch/readiness — skip to avoid dupes.
        });
      }
      if (!blockers.length) return;
      var titleEl = document.getElementById('installBannerTitle');
      var detailEl = document.getElementById('installBannerDetail');
      if (blockers.length === 1) {
        var b = blockers[0];
        if (titleEl) titleEl.textContent = b.label || 'Setup needed';
        if (detailEl) detailEl.textContent = b.detail || 'Install required tools to render videos.';
      } else {
        if (titleEl) titleEl.textContent = blockers.length + ' items need attention';
        if (detailEl) detailEl.textContent =
          blockers.map(function (x) { return x.label || ''; }).filter(Boolean).join(' · ')
          + '.';
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
    });
})();
