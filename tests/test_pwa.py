"""Installability is a contract spread across files that never import each
other, and every joint in it fails *silently*.

A missing manifest link does not raise; the install option simply never
appears. A shell that forgot the iOS metas still renders perfectly and just
produces a bookmark instead of an app. An icon named in the manifest but
absent from disk is a 404 nobody sees. So this file walks the joints:

* the three standalone documents (this app has no template inheritance, so
  each carries its own ``<head>`` and each has to be checked),
* the two routes the ``/static`` mount cannot serve -- the manifest, which
  needs a media type Python does not know, and the worker, which needs the
  origin's root for its scope,
* every icon the manifest names, against the files actually committed.

It also pins the two rules the service worker must not break, by reading the
worker's own source: HTML is never put in a cache, and non-GET requests are
never intercepted. Both are cheap to violate in a later edit and expensive to
notice -- a cached SSR page shows confidently wrong streaks, and an
intercepted POST breaks check-in idempotency.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import create_session_cookie, hash_password
from app.models.user import User
from app.routers.pwa import MANIFEST

ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / "app" / "static"

#: Every standalone document in the app, with a URL that renders it and
#: whether that URL needs a session. The list is written out rather than
#: discovered so *adding* a shell without its head block fails here too.
#: ``/views/auth/`` is the one that must be fetched signed *out* -- signed in
#: it bounces straight back out with a 303.
SHELLS = {
    "layout.html": ("/views/settings/", True),
    "user/auth.html": ("/views/auth/", False),
    "common/error.html": ("/views/challenges/999999", True),
}

#: What a head owes the two install paths: the manifest for Chromium, and the
#: four Safari reads instead of it.
REQUIRED_HEAD = (
    '<link rel="manifest" href="/manifest.webmanifest">',
    'name="apple-mobile-web-app-capable"',
    'name="apple-mobile-web-app-status-bar-style"',
    'name="apple-mobile-web-app-title"',
    'rel="apple-touch-icon"',
)


async def sign_in(client: AsyncClient, db: AsyncSession) -> User:
    user = User(name="PWA User", password_hash=hash_password("password123"))
    db.add(user)
    await db.commit()
    await db.refresh(user)
    client.cookies.set("session", create_session_cookie(user.id))
    return user


@pytest.mark.parametrize("shell,target", SHELLS.items(), ids=list(SHELLS))
async def test_every_shell_can_be_installed_from(
    client: AsyncClient, db: AsyncSession, shell: str, target: tuple[str, bool]
):
    url, needs_session = target
    if needs_session:
        await sign_in(client, db)
    body = (await client.get(url)).text
    for needle in REQUIRED_HEAD:
        assert needle in body, f"{shell} is missing {needle}"


@pytest.mark.parametrize("shell,target", SHELLS.items(), ids=list(SHELLS))
async def test_every_shell_declares_both_theme_colours(
    client: AsyncClient, db: AsyncSession, shell: str, target: tuple[str, bool]
):
    """The browser chrome's colour, one per device preference.

    The manifest carries a single static value (it is one document; the app's
    theme has three states), so these are what actually follow the reader --
    and the pre-paint script replaces them when an explicit choice contradicts
    the device.
    """
    url, needs_session = target
    if needs_session:
        await sign_in(client, db)
    body = (await client.get(url)).text
    assert 'name="theme-color" content="#f8f4ee"' in body
    assert 'name="theme-color" content="#16120d"' in body


async def test_manifest_is_served_with_the_type_chrome_requires(client: AsyncClient):
    response = await client.get("/manifest.webmanifest")

    assert response.status_code == 200
    # text/plain -- what a static mount would answer, since mimetypes does not
    # know .webmanifest -- is rejected outright and the install never offered.
    assert response.headers["content-type"].startswith("application/manifest+json")

    payload = json.loads(response.text)
    assert payload["display"] == "standalone"
    assert payload["dir"] == "rtl"
    assert payload["lang"] == "fa"
    # `/` is a 307 to this; starting there costs the launcher a blank frame.
    assert payload["start_url"] == "/views/today/"
    # Anything narrower than `/` pushes the rest of the app out of the
    # installed window and into the browser.
    assert payload["scope"] == "/"


def test_manifest_carries_the_three_icons_android_needs():
    by_purpose: dict[str, set[str]] = {}
    for icon in MANIFEST["icons"]:
        by_purpose.setdefault(icon["purpose"], set()).add(icon["sizes"])

    assert {"192x192", "512x512"} <= by_purpose["any"]
    # Without a maskable icon Android crops the "any" one to the launcher's
    # shape and takes the ring off the mark.
    assert "512x512" in by_purpose["maskable"]


def test_every_icon_the_manifest_names_is_committed():
    """The avatars' and the emoji catalogue's rule: generated once, committed,
    never fetched at render time -- the app runs in an image with no network.
    """
    named = [icon["src"] for icon in MANIFEST["icons"]]
    named += [
        i["src"] for s in MANIFEST.get("shortcuts", []) for i in s.get("icons", [])
    ]
    named.append("/static/img/apple-touch-icon.png")

    for src in named:
        assert src.startswith("/static/")
        assert (STATIC / src[len("/static/") :]).is_file(), f"{src} is not on disk"


async def test_worker_is_served_from_the_root(client: AsyncClient):
    response = await client.get("/sw.js")

    assert response.status_code == 200
    assert "javascript" in response.headers["content-type"]
    # A worker fetched from /static/js/ would be scoped to /static/ and could
    # never see a navigation, which is the one thing it exists for.
    assert response.headers.get("service-worker-allowed") == "/"
    # This file is the only thing that can retire a stale cache, so a browser
    # holding an old copy of it holds every old asset with it.
    assert response.headers["cache-control"] == "no-cache"


def test_worker_precaches_the_offline_page_it_falls_back_to():
    source = (STATIC / "js" / "sw.js").read_text(encoding="utf-8")
    assert (STATIC / "offline.html").is_file()
    assert "/static/offline.html" in source


def test_worker_leaves_html_and_writes_alone():
    """The two rules a later edit is most likely to break.

    Caching an SSR page here means serving confidently wrong numbers -- every
    ring, streak and counter in this app is read live off ``CheckIns`` at
    request time -- and intercepting a write puts a worker between a check-in
    and the ``(enrollment_id, occurrence_key)`` idempotency it depends on.
    """
    source = (STATIC / "js" / "sw.js").read_text(encoding="utf-8")

    # Navigations are answered from the network, and the cache is consulted
    # only when fetch *rejects*: never on a status code, so the app's 303/401
    # split reaches the page exactly as the server wrote it.
    assert 'request.method !== "GET"' in source
    assert 'request.mode === "navigate"' in source

    # The navigation path writes nothing: the only cache it touches is the
    # read of the offline page it falls back to.
    page_path = source.split("async function pageFirst")[1].split("\n}")[0]
    assert "cache.put" not in page_path


async def test_settings_offers_the_install_but_renders_it_hidden(
    client: AsyncClient, db: AsyncSession
):
    """The server cannot know whether this browser will offer an install, so
    the section ships hidden and ``paintInstall()`` un-hides what applies --
    which is this page's own rule that a row doing nothing is worse than none.
    """
    await sign_in(client, db)
    body = (await client.get("/views/settings/")).text

    assert "data-install-group hidden" in body
    assert "data-install-row hidden" in body
    assert "data-install-ios hidden" in body
    assert "data-install-action" in body
