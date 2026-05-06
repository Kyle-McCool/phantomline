/* Mobile interstitial for the studio (/app).
   Phantomline's render pipeline (TTS, video assembly, image gen) needs
   real desktop hardware — phones can't run it. Rather than dropping
   mobile users into a layout-broken /app, show them an honest banner
   explaining what they CAN do here (account, library, license,
   download). Same detection pattern as static/install.js.

   Loaded as an external file because the server's CSP has no
   'unsafe-inline' on script-src.
*/
(function () {
  var ua = navigator.userAgent || "";
  var isMobile =
    /Mobi|Android|iPhone|iPod/i.test(ua) ||
    (window.matchMedia && window.matchMedia("(max-width: 720px)").matches);
  if (!isMobile) return;
  var el = document.getElementById("mobileInterstitial");
  if (el) el.hidden = false;

  // "Continue to studio anyway" — for the rare desktop-class tablet
  // user. Hides the interstitial and tells the CSS to un-hide the
  // studio .wrap. localStorage so the choice sticks for the session.
  var bypass = document.getElementById("mobileBypassBtn");
  if (bypass) {
    bypass.addEventListener("click", function () {
      if (el) el.hidden = true;
      document.documentElement.classList.add("pl-mobile-bypass-on");
      try { sessionStorage.setItem("pl-mobile-bypass", "1"); } catch (_) {}
    });
  }
  // Restore bypass on subsequent navigations within the session.
  try {
    if (sessionStorage.getItem("pl-mobile-bypass") === "1") {
      if (el) el.hidden = true;
      document.documentElement.classList.add("pl-mobile-bypass-on");
    }
  } catch (_) {}
})();
