/* Supplies the headers SharedArrayBuffer needs, for hosts that can't set them
   (GitHub Pages and friends), and re-labels CDN responses so they survive
   COEP: require-corp. No caching, no offline behaviour, no body rewriting. */
"use strict";

self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", (e) => e.waitUntil(self.clients.claim()));

// The only third-party origin this site loads: xterm and the Pyodide runtime.
// Scoped deliberately. Re-issuing *any* cross-origin request as credential-free
// CORS and stamping CORP on the reply turns the worker into a general-purpose
// COEP bypass for whatever else ends up running on this origin, and the service
// worker's own fetches are not bound by the page's CSP.
const CDN_ORIGINS = new Set(["https://cdn.jsdelivr.net"]);

self.addEventListener("fetch", (event) => {
  const req = event.request;
  if (req.cache === "only-if-cached" && req.mode !== "same-origin") return;

  const origin = new URL(req.url).origin;
  const isCrossOrigin = origin !== self.location.origin;
  if (isCrossOrigin && !CDN_ORIGINS.has(origin)) return;   // untouched, browser decides

  let outgoing = req;
  if (isCrossOrigin && req.mode === "no-cors") {
    // importScripts() and friends fetch in no-cors mode, which COEP rejects unless
    // the CDN sets CORP. Re-issue those as credential-free CORS requests instead.
    outgoing = new Request(req.url, {
      mode: "cors",
      credentials: "omit",
      headers: req.headers,
      method: req.method,
      redirect: "follow"
    });
  } else if (!isCrossOrigin && req.method === "GET") {
    // Revalidate our own assets every time. A cached app.js against a fresh
    // index.html is the single most common way this site breaks.
    outgoing = new Request(req, { cache: "no-cache" });
  }

  event.respondWith(
    fetch(outgoing).then((res) => {
      if (res.status === 0) return res;
      const headers = new Headers(res.headers);
      if (isCrossOrigin) {
        headers.set("Cross-Origin-Resource-Policy", "cross-origin");
      } else {
        headers.set("Cross-Origin-Embedder-Policy", "require-corp");
        headers.set("Cross-Origin-Opener-Policy", "same-origin");
      }
      return new Response(res.body, {
        status: res.status,
        statusText: res.statusText,
        headers
      });
    })
  );
});
