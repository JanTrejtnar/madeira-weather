// Service worker: shell aplikace z cache (offline start), data.json network-first.
// Při změně shellu zvyš VERSION, stará cache se při aktivaci smaže.
const VERSION = "v8";
const CACHE = `madeirasky-${VERSION}`;
const CDN_HOSTS = ["unpkg.com", "cdn.tailwindcss.com"];
const SHELL = [
  "./", "./index.html", "./escape.html", "./fanal.html", "./webcams.html", "./map.html", "./manifest.json", "./icon.svg", "./icon-192.png", "./icon-512.png",
  "./apple-touch-icon.png", "./flag-cz.svg", "./flag-gb.svg", "./favicon.svg", "./favicon.ico",
  "https://unpkg.com/leaflet@1.9.4/dist/leaflet.css",
  "https://unpkg.com/leaflet@1.9.4/dist/leaflet.js",
  "https://cdn.tailwindcss.com",
];

self.addEventListener("install", (e) => {
  // jeden nedostupný soubor (např. CDN) nesmí shodit instalaci celého workeru
  e.waitUntil(caches.open(CACHE).then((c) => Promise.allSettled(SHELL.map((u) => c.add(u.startsWith("http") ? new Request(u, { mode: "no-cors" }) : u)))).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (e) => {
  e.waitUntil(caches.keys()
    .then((keys) => Promise.all(keys.filter((k) => k.startsWith("madeirasky-") && k !== CACHE).map((k) => caches.delete(k))))
    .then(() => self.clients.claim()));
});

async function networkFirst(req) {
  const cache = await caches.open(CACHE);
  try {
    const res = await fetch(req, { cache: "no-cache" });
    if (res.ok) cache.put(req, res.clone());
    return res;
  } catch (err) {
    const hit = await cache.match(req, { ignoreSearch: true });
    if (hit) return hit;
    throw err;
  }
}

async function cacheFirst(req) {
  const cache = await caches.open(CACHE);
  const hit = await cache.match(req);
  if (hit) return hit;
  const res = await fetch(req);
  if (res.ok || res.type === "opaque") cache.put(req, res.clone());
  return res;
}

self.addEventListener("fetch", (e) => {
  const req = e.request, url = new URL(req.url);
  if (req.method !== "GET") return;
  if (url.origin === location.origin && (url.pathname.endsWith("/data.json") || url.pathname.endsWith("/map_data.json"))) {
    e.respondWith(networkFirst(req));
  } else if (url.origin === location.origin || CDN_HOSTS.includes(url.hostname)) {
    // HTML navigace: síť první (nové verze nezůstanou viset), offline fallback na index; ostatní shell z cache
    if (req.mode === "navigate") e.respondWith(networkFirst(req).catch(() => caches.match("./index.html")));
    else e.respondWith(cacheFirst(req));
  } else if (url.hostname.endsWith("tile.openstreetmap.org")) {
    e.respondWith(cacheFirst(req).catch(() => Response.error()));  // dlaždice navštívených míst offline
  }
});
