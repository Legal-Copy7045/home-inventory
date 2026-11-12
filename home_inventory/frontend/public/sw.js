/* Home Inventory service worker: makes the app open fast and work with a
 * weak signal.  It only caches the app itself (HTML, scripts, styles,
 * icons) - never API responses, photos or anything with your inventory in
 * it.  Quick photos taken offline are kept by the page (IndexedDB), not here.
 */
const CACHE = "hinv-shell-v1";

self.addEventListener("install", (event) => {
  event.waitUntil(caches.open(CACHE).then((c) => c.addAll(["./", "./manifest.json", "./favicon.svg", "./icon-192.png"])).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((k) => k.startsWith("hinv-") && k !== CACHE).map((k) => caches.delete(k))))
      .then(() => self.clients.claim()),
  );
});

self.addEventListener("fetch", (event) => {
  const req = event.request;
  if (req.method !== "GET") return;
  const url = new URL(req.url);
  const scope = new URL(self.registration.scope);
  if (url.origin !== scope.origin || !url.pathname.startsWith(scope.pathname)) return;
  const rel = url.pathname.slice(scope.pathname.length);
  if (rel.startsWith("api/")) return; // inventory data: always from the server

  if (req.mode === "navigate" || rel === "" || rel === "index.html") {
    // Network first so updates show at once; the cached copy only when offline.
    event.respondWith(
      fetch(req)
        .then((res) => {
          if (res.ok) caches.open(CACHE).then((c) => c.put("./", res.clone()));
          return res;
        })
        .catch(() => caches.match("./").then((r) => r || Response.error())),
    );
    return;
  }
  if (rel.startsWith("assets/") || /\.(svg|png|json|woff2?)$/.test(rel)) {
    // Built files have content hashes in their names: safe to keep.
    event.respondWith(
      caches.match(req).then((hit) => hit || fetch(req).then((res) => {
        if (res.ok) {
          const copy = res.clone();
          caches.open(CACHE).then((c) => c.put(req, copy));
        }
        return res;
      })),
    );
  }
});
