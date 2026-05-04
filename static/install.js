/* Install-page tab switcher + copy buttons + OS detection. Lives as an
 * external file because the site CSP forbids inline scripts. */
(function () {
  // OS detection — highlight the matching download button so the user's
  // eye snaps to the right one. Uses userAgentData when available
  // (Chromium 90+) and falls back to navigator.platform parsing.
  function detectOS() {
    var ua = navigator.userAgent || '';
    var p = (navigator.userAgentData && navigator.userAgentData.platform) || navigator.platform || '';
    if (/Win/i.test(p) || /Windows/i.test(ua)) return 'Windows';
    if (/Mac/i.test(p) || /Mac OS|Macintosh/i.test(ua)) return 'macOS';
    if (/Linux|X11/i.test(p) || /Linux/i.test(ua)) return 'Linux';
    return null;
  }
  // Mobile detection — installs only work on desktop. Show a banner.
  var isMobile = /Mobi|Android|iPhone|iPad|iPod/i.test(navigator.userAgent || '');
  if (isMobile) {
    var hint = document.getElementById('mobileHint');
    if (hint) hint.style.display = 'block';
  }

  var os = detectOS();
  if (os) {
    // Only classify download buttons that have a data-os attribute — those
    // are the OS-specific installer downloads. Other .download-btn elements
    // (e.g. "Get Claude" in the Claude Code pane) keep their default styling.
    document.querySelectorAll('.download-btn[data-os]').forEach(function (a) {
      if (a.dataset.os === os) {
        a.classList.add('detected');
      } else {
        a.classList.add('secondary');
      }
    });
    var pill = document.getElementById('osPill');
    if (pill) {
      pill.textContent = os + ' detected';
      pill.style.display = 'inline-block';
    }
  }

  // Tab switching
  document.querySelectorAll('.install-options-tabs button').forEach(function (b) {
    b.addEventListener('click', function () {
      document.querySelectorAll('.install-options-tabs button').forEach(function (x) {
        x.classList.remove('active');
      });
      document.querySelectorAll('.install-pane').forEach(function (x) {
        x.classList.remove('active');
      });
      b.classList.add('active');
      var pane = document.getElementById('pane-' + b.dataset.pane);
      if (pane) pane.classList.add('active');
    });
  });

  // Copy buttons
  document.querySelectorAll('.copy-btn').forEach(function (btn) {
    btn.addEventListener('click', async function () {
      var id = btn.dataset.copy;
      var el = document.getElementById(id);
      if (!el) return;
      var text = el.textContent.trim();
      var original = btn.textContent;
      try {
        await navigator.clipboard.writeText(text);
      } catch (e) {
        // Fallback for non-HTTPS / older browsers
        try {
          var range = document.createRange();
          range.selectNode(el);
          var sel = window.getSelection();
          sel.removeAllRanges();
          sel.addRange(range);
          document.execCommand('copy');
          sel.removeAllRanges();
        } catch (e2) {
          btn.textContent = 'Copy failed';
          setTimeout(function () { btn.textContent = original; }, 1600);
          return;
        }
      }
      btn.textContent = 'Copied!';
      btn.classList.add('copied');
      setTimeout(function () {
        btn.textContent = original;
        btn.classList.remove('copied');
      }, 1600);
    });
  });
})();
