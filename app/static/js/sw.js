/* ==========================================================================
   Service worker — «چالش»

   Served from `/sw.js` by app/routers/pwa.py, never from this directory's own
   URL: a worker's scope is the directory it is fetched from, and one fetched
   from /static/js/ could never see a page navigation.

   What it is for, in one line: the installed app must open with its own
   chrome and its own offline page rather than the browser's dinosaur, and it
   must not go blank because a phone lost signal between the icon tap and the
   first paint.

   What it deliberately does NOT do, because this app reads everything live:

   - **No HTML is ever cached.** Every SSR page here computes the ring, the
     streaks and the counters from `CheckIns` at request time, so a cached
     page is a page of confidently wrong numbers. Navigations are
     network-first with the offline page as the only fallback, and that
     fallback happens on a *network failure*, never on a status code.
   - **No response is rewritten and no status is inspected as a reason to
     substitute something else.** The app's whole 303/401 split -- a page
     redirects to the login screen, a `/fragment` answers a real 401 that
     `createInfiniteScroller()` reads -- depends on those responses reaching
     the page exactly as the server wrote them.
   - **The JSON API and every `/fragment` are not touched at all.** They fall
     through to the network with no handler, which also means the session
     cookie rides along the way it always has: this file never constructs a
     Request of its own for them, so there is nowhere for `credentials` to be
     dropped.
   ========================================================================== */

/* Bumping this retires every old cache on the next activation. It has to move
   with any change to the precached assets below -- the styles and the app
   script are in there, so a deploy that edits either and leaves this alone
   hands returning members the previous release's CSS. Same discipline as
   TOUR_VERSION in tour.js. */
const CACHE_VERSION = "v2";
const CACHE_NAME = `chalesh-${CACHE_VERSION}`;

const OFFLINE_URL = "/static/offline.html";

/* The smallest set that makes an installed app open to something rather than
   nothing: the offline page and the two files it and every other page need.
   The icons are here so a cold, offline launch still has its own mark. */
const PRECACHE = [
  OFFLINE_URL,
  "/static/css/styles.css",
  "/static/js/app.js",
  "/static/img/icon-192.png",
  "/static/img/icon-512.png",
];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches
      .open(CACHE_NAME)
      // Individually, not addAll: one asset renamed in a deploy would
      // otherwise reject the whole install and leave the member on the
      // previous worker with no way forward.
      .then((cache) =>
        Promise.all(PRECACHE.map((url) => cache.add(url).catch(() => {})))
      )
      .then(() => self.skipWaiting())
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((names) =>
        Promise.all(
          names
            .filter((name) => name.startsWith("chalesh-") && name !== CACHE_NAME)
            .map((name) => caches.delete(name))
        )
      )
      .then(() => self.clients.claim())
  );
});

/* `/static/` revalidates on the server (RevalidatedStaticFiles) and `/media/`
   is immutable by construction -- a media key is generated per upload and
   never reused. Both are safe to serve from the cache first; the version bump
   above is what retires the first one. */
function isAsset(url) {
  return url.pathname.startsWith("/static/") || url.pathname.startsWith("/media/");
}

/* Cache-first, with the network refreshing the entry behind it.

   The refresh is what makes the version above a safety net rather than the
   only mechanism: a deploy that edits styles.css and forgets to bump
   CACHE_VERSION hands the member one stale paint and then corrects itself,
   instead of pinning them to the previous release until the next bump.

   A response is stored only when it is a real 200 from this origin -- a 206,
   an opaque cross-origin answer or a redirect kept in a cache comes back
   later as a broken asset with no way to tell why. */
function cacheAsset(request, response) {
  if (response && response.status === 200 && response.type === "basic") {
    const copy = response.clone();
    caches.open(CACHE_NAME).then((cache) => cache.put(request, copy));
  }
  return response;
}

async function assetFirst(request) {
  const cached = await caches.match(request);
  const network = fetch(request)
    .then((response) => cacheAsset(request, response))
    .catch((err) => {
      if (cached) return cached;
      throw err;
    });
  return cached || network;
}

/* Network-first and nothing else: whatever the server says is what the page
   gets, including a 303 to the login screen. The cache is consulted only when
   `fetch` rejects, which is a lost connection and never a refusal. */
async function pageFirst(request) {
  try {
    return await fetch(request);
  } catch (err) {
    const offline = await caches.match(OFFLINE_URL);
    if (offline) return offline;
    throw err;
  }
}

self.addEventListener("fetch", (event) => {
  const request = event.request;

  // Anything that changes state goes straight out. A worker that retried or
  // replayed a POST would break the one guarantee check-ins have.
  if (request.method !== "GET") return;

  const url = new URL(request.url);
  if (url.origin !== self.location.origin) return;

  if (isAsset(url)) {
    event.respondWith(assetFirst(request));
    return;
  }

  // A page the member navigated to. `mode === "navigate"` is what separates
  // it from the `fetch()` the same page makes a moment later against the JSON
  // API -- which is left alone entirely, with no handler and no cache.
  if (request.mode === "navigate") {
    event.respondWith(pageFirst(request));
  }
});
