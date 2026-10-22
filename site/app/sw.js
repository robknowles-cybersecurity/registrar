// Offline support. The app shell and pdf.js are cached at install; the OCR
// engine and its language model (about 7 MB) are cached the first time they
// are used, or when you press "Make OCR available offline" in Settings.
// Nothing here talks to any origin but this one.
const VERSION = "90ada12515dc3dc5";
const SHELL = [
 "./",
 "./app.css",
 "./app.js",
 "./data.json",
 "./db.js",
 "./engine.js",
 "./exports.js",
 "./index.html",
 "./ingest.js",
 "./ledger.js",
 "./llm.js",
 "./manifest.webmanifest",
 "./packets.js",
 "./parser.js",
 "./pdftext.js",
 "./sample.js",
 "./sample/try-capture-certificate.pdf",
 "./schema.js",
 "./vendor/pdfjs/LICENSE",
 "./vendor/pdfjs/pdf.min.mjs",
 "./vendor/pdfjs/pdf.worker.min.mjs",
 "./zip.js",
 "./version.json"
];
const CACHE = `registrar-${VERSION}`;
const RUNTIME = "registrar-runtime-vendor";

self.addEventListener("install", (event) => {
  event.waitUntil(caches.open(CACHE).then((c) => c.addAll(SHELL)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (event) => {
  event.waitUntil((async () => {
    for (const key of await caches.keys()) {
      if (key.startsWith("registrar-") && key !== CACHE && key !== RUNTIME) await caches.delete(key);
    }
    await self.clients.claim();
  })());
});

self.addEventListener("fetch", (event) => {
  const req = event.request;
  const url = new URL(req.url);
  if (req.method !== "GET" || url.origin !== self.location.origin) return;
  if (req.mode === "navigate") {
    event.respondWith(fetch(req).catch(() => caches.match("./index.html", { ignoreSearch: true })));
    return;
  }
  event.respondWith((async () => {
    const hit = await caches.match(req, { ignoreSearch: true });
    if (hit) return hit;
    const res = await fetch(req);
    if (res.ok && url.pathname.includes("/vendor/tesseract/")) {
      const c = await caches.open(RUNTIME);
      c.put(req, res.clone());
    }
    return res;
  })());
});
