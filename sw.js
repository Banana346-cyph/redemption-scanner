// Offline support: keep a copy of the app, card list, pictures and text reader on the phone.
const CACHE = "rcs-v2";
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
  if (!ours && url.hostname !== "cdn.jsdelivr.net") return;
  // The page itself: try the internet first so updates show up right away; use the saved copy offline.
  if (req.mode === "navigate") {
    e.respondWith(fetch(req).then(res => { caches.open(CACHE).then(c => c.put("index.html", res.clone())); return res; })
      .catch(() => caches.match("index.html")));
    return;
  }
  // Everything else: saved copy first, refreshed in the background.
  e.respondWith(caches.open(CACHE).then(async c => {
    const hit = await c.match(req, { ignoreSearch: ours });
    const fresh = fetch(req).then(res => { if (res && (res.ok || res.type === "opaque")) c.put(req, res.clone()); return res; }).catch(() => hit);
    return hit || fresh;
  }));
});
