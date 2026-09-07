"""The three files a browser has to fetch before it will offer to install.

An installable app is a manifest, a service worker registered at a scope wide
enough to cover the whole app, and an icon set. Two of the three cannot be
served by the ``/static`` mount and that is the whole reason this router
exists:

* **the manifest** must arrive as ``application/manifest+json``. Python's
  ``mimetypes`` does not know ``.webmanifest`` on most machines, so a static
  file would be served as ``text/plain`` and Chrome would reject it -- silently
  from the member's point of view, since the only symptom is that the install
  option never appears.
* **the service worker** must be served from the origin's *root*. A worker
  fetched from ``/static/js/sw.js`` gets a scope of ``/static/`` and can never
  see a page navigation, which is the one thing it is here for. The file still
  *lives* under ``static/js/`` beside the rest of the front end; only its
  address is special.

Nothing here touches a model, a session or a permission, and every route is
open: a manifest is public by definition, and an unsigned-in visitor is
exactly who gets offered the install.

The offline page is deliberately **not** here. It is a plain static document
(``static/offline.html``) with no Jinja in it, because a page whose entire job
is to render when the server is unreachable has no business being rendered by
the server.
"""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import FileResponse, JSONResponse

from app.config import BASE_DIR

router = APIRouter(tags=["pwa"])

#: The manifest, as data rather than a file, so the icon paths and the two
#: colours below are checked by the same tests that check everything else.
#:
#: ``theme_color`` is the light theme's ``--bg-0``: a manifest is one static
#: document while this app's theme has three states, so the colour that
#: actually follows the member's choice is the ``<meta name="theme-color">``
#: pair in each shell's ``<head>`` (which the inline theme script rewrites for
#: an explicit choice). This value is only what a launcher paints *before* the
#: page runs -- the splash screen -- so it takes the app's default ground.
MANIFEST: dict = {
    "id": "/",
    "name": "اکت‌پکت",
    "short_name": "اکت‌پکت",
    "description": "چالش بساز، عضو شو، و هر روز پیشرفتت را ثبت کن.",
    "lang": "fa",
    "dir": "rtl",
    # `/views/today/` rather than `/`: the root is a 307 to it, and a launcher
    # that has to follow a redirect on every cold start shows a blank frame
    # first. `scope` stays `/` so every page of the app is inside the
    # installed window -- a narrower scope would kick `/views/challenges/`
    # out to the browser.
    "start_url": "/views/today/",
    "scope": "/",
    "display": "standalone",
    "orientation": "portrait",
    "background_color": "#f8f4ee",
    "theme_color": "#f8f4ee",
    "icons": [
        {
            "src": "/static/img/icon-192.png",
            "sizes": "192x192",
            "type": "image/png",
            "purpose": "any",
        },
        {
            "src": "/static/img/icon-512.png",
            "sizes": "512x512",
            "type": "image/png",
            "purpose": "any",
        },
        # Drawn with its mark pulled inside the safe area; without a maskable
        # icon Android crops the "any" one to a circle and takes the ring off.
        {
            "src": "/static/img/icon-maskable-512.png",
            "sizes": "512x512",
            "type": "image/png",
            "purpose": "maskable",
        },
    ],
    # The three destinations a member opens the app *for*. Long-pressing the
    # launcher icon is the only place these appear, so they mirror the bottom
    # nav rather than adding anything to it.
    "shortcuts": [
        {
            "name": "امروز",
            "url": "/views/today/",
            "icons": [{"src": "/static/img/icon-192.png", "sizes": "192x192"}],
        },
        {
            "name": "خانه",
            "url": "/views/home/",
            "icons": [{"src": "/static/img/icon-192.png", "sizes": "192x192"}],
        },
        {
            "name": "چالش‌ها",
            "url": "/views/challenges/",
            "icons": [{"src": "/static/img/icon-192.png", "sizes": "192x192"}],
        },
    ],
}


@router.get("/manifest.webmanifest", include_in_schema=False)
async def manifest() -> JSONResponse:
    return JSONResponse(
        MANIFEST,
        media_type="application/manifest+json",
        # Short, not immutable: the manifest names the start URL and the
        # icons, and a stale copy in a browser's cache outlives a deploy that
        # moved either of them.
        headers={"Cache-Control": "public, max-age=3600"},
    )


@router.get("/sw.js", include_in_schema=False)
async def service_worker() -> FileResponse:
    return FileResponse(
        BASE_DIR / "static" / "js" / "sw.js",
        media_type="application/javascript",
        # `no-cache`, never a long max-age: this file is the only thing that
        # can retire a stale cache, so a browser holding an old copy of it
        # holds every old asset with it.
        headers={"Cache-Control": "no-cache", "Service-Worker-Allowed": "/"},
    )
