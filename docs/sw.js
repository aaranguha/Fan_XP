// Minimal service worker — just enough for Fan XP to qualify as an
// installable PWA. Deliberately does no caching: seat availability,
// bid status, and payment state all need to be live, never stale.
self.addEventListener("install", (event) => {
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  self.clients.claim();
});

self.addEventListener("fetch", (event) => {
  event.respondWith(fetch(event.request));
});
