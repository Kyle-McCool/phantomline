/* Landing page progressive enhancement. */

/* ─── Hamburger menu toggle ─────────────────────────────────────────── */
const hamburger = document.querySelector('.nav-hamburger');
const overlay = document.querySelector('.nav-mobile-overlay');

const iconMenu = '<svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><line x1="4" y1="7" x2="20" y2="7"/><line x1="4" y1="12" x2="20" y2="12"/><line x1="4" y1="17" x2="20" y2="17"/></svg>';
const iconClose = '<svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><line x1="6" y1="6" x2="18" y2="18"/><line x1="6" y1="18" x2="18" y2="6"/></svg>';

function closeMenu() {
  if (!overlay) return;
  overlay.classList.remove('open');
  overlay.setAttribute('aria-hidden', 'true');
  if (hamburger) {
    hamburger.setAttribute('aria-expanded', 'false');
    hamburger.innerHTML = iconMenu;
  }
  document.body.style.overflow = '';
}

if (hamburger && overlay) {
  hamburger.addEventListener('click', () => {
    overlay.classList.add('open');
    hamburger.setAttribute('aria-expanded', 'true');
    hamburger.innerHTML = iconClose;
    overlay.setAttribute('aria-hidden', 'false');
    document.body.style.overflow = 'hidden';
  });

  const closeBtn = overlay.querySelector('.nav-mobile-close');
  if (closeBtn) closeBtn.addEventListener('click', closeMenu);

  overlay.querySelectorAll('a').forEach(link => {
    link.addEventListener('click', closeMenu);
  });
}

/* ─── Hero video: respect prefers-reduced-motion ──────────────────────── */
const heroVideo = document.querySelector('.hero-video');
if (heroVideo) {
  const mq = window.matchMedia('(prefers-reduced-motion: reduce)');
  const apply = () => {
    if (mq.matches) {
      heroVideo.pause();
      heroVideo.removeAttribute('autoplay');
    } else if (heroVideo.paused) {
      heroVideo.play().catch(() => {});
    }
  };
  apply();
  mq.addEventListener?.('change', apply);
}

/* ─── Hero Shorts-frame unmute toggle ─────────────────────────────────── */
const heroUnmuteBtn = document.getElementById('heroDemoUnmute');
const heroDemoVideo = document.getElementById('heroDemoVideo');
if (heroUnmuteBtn && heroDemoVideo) {
  heroUnmuteBtn.addEventListener('click', () => {
    const willUnmute = heroDemoVideo.muted;
    heroDemoVideo.muted = !willUnmute;
    heroUnmuteBtn.classList.toggle('is-unmuted', willUnmute);
    heroUnmuteBtn.setAttribute('aria-label', willUnmute ? 'Mute demo video' : 'Unmute demo video');
    if (willUnmute) heroDemoVideo.play().catch(() => {});
  });
}

/* ─── "Try with your API key" CTA: route by auth state ───────────────── */
const heroApiCta = document.getElementById('heroApiCta');
if (heroApiCta) {
  heroApiCta.addEventListener('click', (e) => {
    let hasSession = false;
    try {
      for (let i = 0; i < localStorage.length; i++) {
        const k = localStorage.key(i);
        if (k && k.indexOf('sb-') === 0 && k.indexOf('-auth-token') > 0) {
          const raw = localStorage.getItem(k);
          if (raw) {
            const parsed = JSON.parse(raw);
            const sess = (parsed && parsed.currentSession) || parsed;
            if (sess && sess.access_token) { hasSession = true; break; }
          }
        }
      }
    } catch (_) {}
    e.preventDefault();
    window.location.href = hasSession ? '/app?tab=settings' : '/account';
  });
}
