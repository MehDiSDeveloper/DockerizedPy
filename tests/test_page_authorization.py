"""Which SSR pages need a session, and what an anonymous visitor gets instead.

The rule these pin down: a *page* answers an unauthenticated request with a 303
to the login form carrying a ``?next=`` back, never the API's 401 -- a browser
navigation has nowhere to display ``{"detail": "Not authenticated"}``. The
``/fragment`` routes are the deliberate exception and must keep 401ing, because
``createInfiniteScroller()`` in ``app.js`` reads that status to redirect itself;
a redirect would be followed by fetch() and appended as if it were cards.

This file stops at "is there a session". Whether that session may see *this
particular row* is the next layer up, pinned by
``test_object_authorization.py`` -- which is why ``/views/users/1`` appears
below only as the first user viewing their own profile.
"""

from __future__ import annotations

from urllib.parse import parse_qs, urlparse

import pytest
from httpx import AsyncClient
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import create_session_cookie, hash_password
from app.models.user import User

# Every page that requires a signed-in user, and nothing that does not.
PROTECTED_PAGES = [
    "/views/today/",
    "/views/home/",
    "/views/challenges/create",
    "/views/settings/",
    "/views/users/1",
]

# Infinite-scroll endpoints: same requirement, different failure mode.
PROTECTED_FRAGMENTS = [
    "/views/today/fragment",
    "/views/home/fragment",
]


async def make_user(db: AsyncSession, name: str = "Page Auth User") -> User:
    user = User(name=name, password_hash=hash_password("password123"))
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


def authenticate(client: AsyncClient, user_id: int) -> None:
    client.cookies.set("session", create_session_cookie(user_id))


def login_next(location: str) -> str:
    """The ``?next=`` a login redirect is carrying."""
    parsed = urlparse(location)
    assert parsed.path == "/views/auth/"
    return parse_qs(parsed.query)["next"][0]


@pytest.mark.parametrize("path", PROTECTED_PAGES)
async def test_anonymous_page_redirects_to_login(client: AsyncClient, path: str):
    response = await client.get(path)
    assert response.status_code == 303
    assert login_next(response.headers["location"]) == path


@pytest.mark.parametrize("path", PROTECTED_FRAGMENTS)
async def test_anonymous_fragment_gets_401_not_a_redirect(
    client: AsyncClient, path: str
):
    response = await client.get(path)
    assert response.status_code == 401


async def test_login_redirect_preserves_the_query_string(client: AsyncClient):
    """A deep link has to survive the round trip through the login form."""
    response = await client.get("/views/home/?status=completed")
    assert login_next(response.headers["location"]) == "/views/home/?status=completed"


@pytest.mark.parametrize("path", PROTECTED_PAGES)
async def test_signed_in_user_reaches_every_protected_page(
    client: AsyncClient, db: AsyncSession, path: str
):
    user = await make_user(db)
    assert user.id == 1, "/views/users/1 in PROTECTED_PAGES assumes the first user"
    authenticate(client, user.id)
    response = await client.get(path)
    assert response.status_code == 200


@pytest.mark.parametrize("path", ["/views/challenges/", "/views/challenges/fragment"])
async def test_discovery_stays_open_to_anonymous_visitors(
    client: AsyncClient, path: str
):
    """Browsing public challenges is not gated -- that is what the visibility
    filter is for. Locking it would make the app unlinkable from outside."""
    response = await client.get(path)
    assert response.status_code == 200


async def test_stale_cookie_is_cleared_instead_of_looping(
    client: AsyncClient, db: AsyncSession
):
    """A signed cookie outlives its account: sessions are stateless and cannot
    be revoked, so the only defence is that pages load the row and fail closed.
    Clearing it on the way out is what stops the login page -- which redirects
    signed-in visitors away -- from bouncing them straight back here."""
    user = await make_user(db)
    authenticate(client, user.id)
    await db.execute(delete(User).where(User.id == user.id))
    await db.commit()

    response = await client.get("/views/home/")
    assert response.status_code == 303
    assert urlparse(response.headers["location"]).path == "/views/auth/"
    # Asserted on the header rather than the client jar: the fixture seeds the
    # cookie domainless, so httpx files the expiry under the response host and
    # keeps the original alongside it. A real browser matches them.
    set_cookie = response.headers["set-cookie"]
    assert set_cookie.startswith(('session=""', "session=;"))
    assert "Max-Age=0" in set_cookie or "1970" in set_cookie


async def test_login_page_bounces_an_already_signed_in_user(
    client: AsyncClient, db: AsyncSession
):
    user = await make_user(db)
    authenticate(client, user.id)
    response = await client.get("/views/auth/?next=/views/today/")
    assert response.status_code == 303
    assert response.headers["location"] == "/views/today/"


async def test_login_page_still_renders_for_anonymous(client: AsyncClient):
    response = await client.get("/views/auth/")
    assert response.status_code == 200


async def test_login_page_refuses_an_offsite_next(client: AsyncClient, db: AsyncSession):
    """The open-redirect clamp has to hold on the bounce path too, not just in
    the form's ``window.location.href``."""
    user = await make_user(db)
    authenticate(client, user.id)
    response = await client.get("/views/auth/?next=https://evil.example/")
    assert response.status_code == 303
    assert response.headers["location"] == "/views/home/"
