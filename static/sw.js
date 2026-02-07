\
const CACHE = "repair-estimator-v1";
const ASSETS = [
  "/",
  "/static/index.html?v=1",
  "/static/style.css?v=1",
  "/static/app.js?v=1",
  "/manifest.json"
];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(CACHE).then((cache) => cache.addAll(ASSETS)).then(() => self.skipWaiting())
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(keys.map((k) => (k === CACHE ? null : caches.delete(k))))
    ).then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", (event) => {
  const req = event.request;
  const url = new URL(req.url);

  // Network-first for API endpoints
  if (url.pathname.startsWith("/vehicle/") || url.pathname.startsWith("/categories") || url.pathname.startsWith("/services") || url.pathname.startsWith("/estimate")) {
    event.respondWith(
      fetch(req).catch(() => caches.match(req))
    );
    return;
  }

  // Cache-first for static
  event.respondWith(
    caches.match(req).then((hit) => hit || fetch(req).then((resp) => {
      const copy = resp.clone();
      caches.open(CACHE).then((cache) => cache.put(req, copy));
      return resp;
    }))
  );
});
