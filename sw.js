// Offline support: keep a copy of the app, card list, pictures and text reader on the phone.
const CACHE = "rcs-v1";
const CORE = ["./", "index.html", "cards.json", "words.txt", "eng.traineddata", "manifest.webmanifest", "icon-192.png"];
self.addEventListener("install", e => {
  e.waitUntil(caches.open(CACHE).then(c => c.addAll(CORE)).then(() => self.skipWaiting()));
});
self.addEventListener("activate", e => {
  e.waitUntil(caches.keys().then(keys => Promise.all(keys.filter(k => k !== CACHE).map(k => caches.delete(k)))).then(() => self.clients.claim()));
});
self.addEventListener("fetch", e => {
  const req = e.request;
  if (req.method !== "GET") return;
  const url = new URL(req.url);
  const ours = url.origin === location.origin;
  const reader = url.hostname === "cdn.jsdelivr.net";
  if (!ours && !reader) return;
  // Show the saved copy right away, refresh it in the background for next time.
  e.respondWith(caches.open(CACHE).then(async c => {
    const hit = await c.match(req, { ignoreSearch: ours });
    const fresh = fetch(req).then(res => { if (res && (res.ok || res.type === "opaque")) c.put(req, res.clone()); return res; }).catch(() => hit);
    return hit || fresh;
  }));
});
