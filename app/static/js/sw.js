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
const CACHE_VERSION = "v5";
const CACHE_NAME = `chalesh-${CACHE_VERSION}`;

const OFFLINE_URL = "/static/offline.html";

/* The smallest set that makes an installed app open to something rather than
   nothing: the offline page and the two files it and every other page need.
   The icons are here so a cold, offline launch still has its own mark. */
const PRECACHE = [
  OFFLINE_URL,
  "/static/css/styles.css",
  "/static/js/app.js",
  // The typeface, self-hosted since it stopped coming from Google's CDN. It
  // belongs in this list for the same reason styles.css does: the offline
  // page is drawn with it, so leaving it out means a cold offline launch
  // renders in a system sans and looks like a different app.
  "/static/fonts/Vazirmatn-Variable.woff2",
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

/* ==========================================================================
   Push — the one thing this worker does that is not about caching.

   It is here because it has to be: a push arrives with no page open and no
   tab running, so the only code the browser can hand it to is a service
   worker. That is also the whole reason a PWA can put a notification on a
   phone's lock screen at all.

   Three rules, and they mirror the server's:

   - **The payload is trusted to be complete.** `app/push.py` builds the title
     and the sentence from NOTIFICATION_META, the same map the bell renders
     with, so nothing is worded here. A push with no readable payload still
     shows *something* — every push service requires a visible notification
     and a browser that gets none shows its own «this site was updated in the
     background», which is worse than a generic line of ours.
   - **`tag` collapses.** The server sends one per kind and subject, so a
     second comment on the same challenge replaces the first rather than
     stacking: a phone is not a place to read a list, and the bell is.
   - **A tap lands on the page the event is about**, reusing a window that is
     already open rather than launching a second copy of the app. That is the
     `clients.matchAll` dance below, and the `focus()` half of it is what
     separates «opened my app» from «opened a new browser tab».
   ========================================================================== */

const FALLBACK_PUSH = {
  title: "چالش",
  body: "خبر تازه‌ای داری.",
  url: "/views/notifications/",
};

function readPush(event) {
  if (!event.data) return FALLBACK_PUSH;
  try {
    return { ...FALLBACK_PUSH, ...event.data.json() };
  } catch (err) {
    return FALLBACK_PUSH;
  }
}

self.addEventListener("push", (event) => {
  const data = readPush(event);
  event.waitUntil(
    self.registration.showNotification(data.title, {
      body: data.body,
      // The app's own mark, precached above, so a notification arriving on a
      // phone with no connection still carries it.
      icon: "/static/img/icon-192.png",
      badge: "/static/img/icon-192.png",
      // The page to open, carried on the notification itself: `notificationclick`
      // fires long after this handler is gone and has no other way to know.
      data: { url: data.url },
      tag: data.tag,
      // Replacing a collapsed notification silently, so a burst of comments
      // buzzes once rather than once per comment.
      renotify: false,
      dir: "rtl",
      lang: "fa",
    })
  );
});

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  const target = new URL(
    (event.notification.data && event.notification.data.url) || "/views/notifications/",
    self.location.origin
  ).href;

  event.waitUntil(
    self.clients
      .matchAll({ type: "window", includeUncontrolled: true })
      .then((windows) => {
        for (const client of windows) {
          // Any window of this app will do: navigating the one the member
          // already has open keeps their session, their scroll and the
          // installed chrome, where openWindow would start a second copy.
          if (client.url.startsWith(self.location.origin) && "focus" in client) {
            return client.focus().then((focused) => {
              if (focused && focused.navigate) return focused.navigate(target);
              return focused;
            });
          }
        }
        return self.clients.openWindow(target);
      })
  );
});

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
