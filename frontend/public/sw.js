/* VoterEngine AI service worker: lets the field app open with no connection.
 * - /_next/static/*  cache-first (content-hashed, immutable)
 * - /field page      network-first, falls back to the last good copy
 * - /api/*           never cached here; voter data lives in IndexedDB and is
 *                    wiped on sign-out
 */
const SHELL = "ve-shell-v1";

self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", (e) => {
  e.waitUntil((async () => {
    for (const k of await caches.keys()) if (k !== SHELL) await caches.delete(k);
    await self.clients.claim();
  })());
});

self.addEventListener("message", (e) => {
  if (e.data && e.data.type === "CLEAR_CACHES") e.waitUntil(caches.delete(SHELL));
});

self.addEventListener("fetch", (e) => {
  const req = e.request;
  const url = new URL(req.url);
  if (req.method !== "GET" || url.origin !== self.location.origin || url.pathname.startsWith("/api/")) return;

  if (url.pathname.startsWith("/_next/static/")) {
    e.respondWith((async () => {
      const cache = await caches.open(SHELL);
      const hit = await cache.match(req);
      if (hit) return hit;
      const res = await fetch(req);
      if (res.ok) cache.put(req, res.clone());
      return res;
    })());
    return;
  }

  if (req.mode === "navigate" && url.pathname === "/field") {
    e.respondWith((async () => {
      const cache = await caches.open(SHELL);
      try {
        const res = await fetch(req);
        // only keep a real page, never a redirect to /login
        if (res.ok && !res.redirected) cache.put("/field", res.clone());
        return res;
      } catch {
        return (await cache.match("/field")) ||
          new Response("<h1>You're offline</h1><p>Open the field app once while online so it can work offline.</p>",
            { headers: { "Content-Type": "text/html" }, status: 503 });
      }
    })());
  }
});
