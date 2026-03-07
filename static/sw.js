/* static/sw.js */

const VERSION = "torquemech-v4"; // bump this anytime you change sw.js
const STATIC_CACHE = `static-${VERSION}`;
const RUNTIME_CACHE = `runtime-${VERSION}`;

const APP_SHELL = [
  "/",
  "/static/style.css",
  "/static/app.js",
  "/manifest.webmanifest",
  "/static/icon-192.png",
  "/static/icon-512.png",
];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(STATIC_CACHE).then(async (cache) => {
      // If any file is missing, addAll() fails and SW install fails.
      // So we add individually and ignore failures.
      await Promise.all(
        APP_SHELL.map(async (url) => {
          try { await cache.add(url); } catch (e) {}
        })
      );
    })
  );
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil((async () => {
    const keys = await caches.keys();
    await Promise.all(keys.map((key) => {
      if (key !== STATIC_CACHE && key !== RUNTIME_CACHE) return caches.delete(key);
    }));
    await self.clients.claim();
  })());
});

self.addEventListener("fetch", (event) => {
  const req = event.request;
  const url = new URL(req.url);

  // Same-origin only
  if (url.origin !== self.location.origin) return;

  // Never intercept non-GET (this is HUGE for /estimate/pdf POST)
  if (req.method !== "GET") return;

  // Never cache API or PDF routes — always go to network
  if (url.pathname.startsWith("/api/") || url.pathname.startsWith("/estimate/")) {
  event.respondWith(
    fetch(req).catch(() =>
      new Response(JSON.stringify({ error: "Backend unavailable" }), {
        status: 503,
        headers: { "Content-Type": "application/json" },
      })
    )
  );
  return;
}

  // Navigations: try network, fallback to cached "/"
  if (req.mode === "navigate") {
    event.respondWith((async () => {
      try {
        const res = await fetch(req);
        const cache = await caches.open(RUNTIME_CACHE);
        cache.put("/", res.clone());
        return res;
      } catch {
        return (await caches.match("/")) || new Response("Offline", { status: 503 });
      }
    })());
    return;
  }

  // Static assets: stale-while-revalidate
  if (url.pathname.startsWith("/static/") || url.pathname.endsWith(".webmanifest")) {
    event.respondWith(staleWhileRevalidate(req));
    return;
  }

  // Default: cache-first (safe)
  event.respondWith(cacheFirst(req));
});

async function cacheFirst(request) {
  const cached = await caches.match(request);
  if (cached) return cached;

  try {
    const res = await fetch(request);
    if (res && res.ok && res.type !== "opaque") {
      const cache = await caches.open(RUNTIME_CACHE);
      cache.put(request, res.clone());
    }
    return res;
  } catch {
    return new Response("", { status: 504, statusText: "Offline" });
  }
}

async function staleWhileRevalidate(request) {
  const cache = await caches.open(RUNTIME_CACHE);
  const cached = await cache.match(request);

  const fetchPromise = fetch(request)
    .then((res) => {
      if (res && res.ok && res.type !== "opaque") cache.put(request, res.clone());
      return res;
    })
    .catch(() => null);

  return cached || (await fetchPromise) || new Response("Offline", { status: 503 });
}
