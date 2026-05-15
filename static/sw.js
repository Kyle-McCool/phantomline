/**
 * Phantomline service worker.
 *
 * Strategy:
 *  - Cache-first for /static/* (CSS, JS, images, fonts).
 *  - Network-first for everything else, with offline shell fallback.
 *  - Never cache /api/* — those are state-mutating and must hit the server.
 *
 * Bumping CACHE_VERSION invalidates the previous cache on the next visit.
 */
// Bump on every breaking SW change so the activate handler below can
// drop stale caches. v9 invalidates the cached /account assets so the
// rebuilt account portal (tabs: overview/licenses/billing/settings,
// robust error handling, /api/account/me + /invoices wiring) reaches
// existing visitors immediately on next page load.
const CACHE_VERSION = "phantomline-v34";
const STATIC_CACHE = `${CACHE_VERSION}-static`;
const SHELL_CACHE = `${CACHE_VERSION}-shell`;

// Pre-warm the marketing landing (/), pricing (/pricing), and the studio
// (/app) so users have offline access to whichever they last opened.
// The landing CSS and the studio CSS are different files; cache both.
const SHELL_URLS = [
  "/",
  "/pricing",
  "/app",
  "/static/phantomline.css",
  "/static/phantomline.js",
  "/static/landing.css",
];

self.addEventListener("install", (event) => {
  // Pre-warm the offline shell so a flaky connection still loads the UI.
  event.waitUntil(
    caches.open(SHELL_CACHE).then((cache) =>
      Promise.all(
        SHELL_URLS.map((url) =>
          fetch(url, { cache: "reload" })
            .then((r) => (r.ok ? cache.put(url, r) : null))
            .catch(() => null)
        )
      )
    )
  );
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  // Drop stale caches whenever CACHE_VERSION changes.
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(
        keys
          .filter((k) => !k.startsWith(CACHE_VERSION))
          .map((k) => caches.delete(k))
      )
    )
  );
  self.clients.claim();
});

self.addEventListener("fetch", (event) => {
  const req = event.request;
  if (req.method !== "GET") return;
  const url = new URL(req.url);
  // Same-origin only; never intercept API calls or third-party resources.
  if (url.origin !== self.location.origin) return;
  if (url.pathname.startsWith("/api/")) return;

  // Cache-first for static assets — they're fingerprinted via ?v=mtime.
  if (url.pathname.startsWith("/static/")) {
    event.respondWith(
      caches.open(STATIC_CACHE).then(async (cache) => {
        const cached = await cache.match(req);
        if (cached) return cached;
        try {
          const fresh = await fetch(req);
          if (fresh && fresh.ok) cache.put(req, fresh.clone());
          return fresh;
        } catch (e) {
          return cached || Response.error();
        }
      })
    );
    return;
  }

  // Network-first for HTML pages with offline fallback.
  event.respondWith(
    (async () => {
      try {
        const fresh = await fetch(req);
        const cache = await caches.open(SHELL_CACHE);
        cache.put(req, fresh.clone());
        return fresh;
      } catch (e) {
        const cached = await caches.match(req);
        if (cached) return cached;
        const shell = await caches.match("/");
        if (shell) return shell;
        return new Response(
          "<h1>Offline</h1><p>Phantomline is offline. Reconnect to continue.</p>",
          { status: 503, headers: { "Content-Type": "text/html" } }
        );
      }
    })()
  );
});
