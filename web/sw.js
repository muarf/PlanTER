/* TER Finder — service worker (T7 v2.1) : cache offline partiel. */
"use strict";

const CACHE = "ter-finder-v1";
const SHELL = [
  "/",
  "/styles.css",
  "/app.js",
  "/manifest.webmanifest",
  "/icon-192.png",
  "/icon-512.png"
];

const API_CACHE = "ter-finder-api-v1";
const API_MAX_ENTRIES = 100;

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(CACHE)
      .then((c) => c.addAll(SHELL))
      .then(() => self.skipWaiting())
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(
        keys.filter((k) => k !== CACHE && k !== API_CACHE).map((k) => caches.delete(k))
      ))
      .then(() => self.clients.claim())
  );
});

/* net-first, cache en secours ; mise en cache des réponses. */
async function networkFirst(req, cacheName) {
  try {
    const fresh = await fetch(req);
    if (fresh && fresh.ok) {
      const cache = await caches.open(cacheName);
      await cache.put(req, fresh.clone());
      if (cacheName === API_CACHE) await trimCache(cache);
    }
    return fresh;
  } catch (err) {
    const cached = await caches.match(req);
    return cached || Response.error();
  }
}

/* cache-first, revalidation en arrière-plan (données quasi-statiques). */
async function staleWhileRevalidate(req, cacheName) {
  const cache = await caches.open(cacheName);
  const cached = await cache.match(req);
  const fresh = fetch(req).then(async (res) => {
    if (res && res.ok) {
      await cache.put(req, res.clone());
      if (cacheName === API_CACHE) await trimCache(cache);
    }
    return res;
  }).catch(() => cached);
  return cached || fresh;
}

async function trimCache(cache) {
  const keys = await cache.keys();
  if (keys.length > API_MAX_ENTRIES) {
    await Promise.all(keys.slice(0, keys.length - API_MAX_ENTRIES).map((k) => cache.delete(k)));
  }
}

self.addEventListener("fetch", (event) => {
  const req = event.request;
  if (req.method !== "GET") return;
  const url = new URL(req.url);
  if (url.origin !== self.location.origin) return;

  /* Navigation : réseau d'abord, sinon la page en cache. */
  if (req.mode === "navigate") {
    event.respondWith(networkFirst(req, CACHE));
    return;
  }

  if (url.pathname.startsWith("/v1/journeys")) {
    event.respondWith(networkFirst(req, API_CACHE));
    return;
  }

  if (url.pathname.startsWith("/v1/")) {
    event.respondWith(staleWhileRevalidate(req, API_CACHE));
    return;
  }

  /* Assets du shell : cache-first. */
  if (url.pathname.startsWith("/styles.css") || url.pathname.startsWith("/app.js") ||
      url.pathname.startsWith("/icon-") || url.pathname === "/manifest.webmanifest") {
    event.respondWith(staleWhileRevalidate(req, CACHE));
  }
});
