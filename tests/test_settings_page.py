"""Settings live on one page, and the profile keeps none of them.

App preferences used to sit in the profile's menu list, which made the
profile answer two unrelated questions -- who am I, and how should the app
behave -- and left every future preference with no obvious home. These pin
the split so a new setting cannot quietly land back on the profile:

* ``/views/settings/`` renders the theme control (the only preference that
  actually does something today),
* the profile links there and no longer carries a theme control of its own.

Whether the page needs a session is ``test_page_authorization.py``'s job --
``/views/settings/`` is in its ``PROTECTED_PAGES`` list.
"""

from __future__ import annotations

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import create_session_cookie, hash_password
from app.models.user import User


async def make_user(db: AsyncSession) -> User:
    user = User(name="Settings User", password_hash=hash_password("password123"))
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


async def sign_in(client: AsyncClient, db: AsyncSession) -> User:
    user = await make_user(db)
    client.cookies.set("session", create_session_cookie(user.id))
    return user


async def test_settings_page_carries_the_theme_control(
    client: AsyncClient, db: AsyncSession
):
    await sign_in(client, db)
    body = (await client.get("/views/settings/")).text

    assert "data-theme-choice" in body
    # All three states are on screen; the stored one is painted by app.js,
    # which is the only side that can read localStorage.
    for mode in ("system", "light", "dark"):
        assert f'data-theme-option="{mode}"' in body


async def test_profile_hands_settings_off_instead_of_holding_them(
    client: AsyncClient, db: AsyncSession
):
    user = await sign_in(client, db)
    body = (await client.get(f"/views/users/{user.id}")).text

    assert 'href="/views/settings/"' in body
    assert "data-theme-choice" not in body
    assert "data-theme-toggle" not in body


async def test_settings_page_links_back_to_the_profile(
    client: AsyncClient, db: AsyncSession
):
    """The pair has to be walkable both ways -- settings is reached from the
    profile, and the profile is the bottom-nav tab it belongs under."""
    user = await sign_in(client, db)
    body = (await client.get("/views/settings/")).text
    assert f'href="/views/users/{user.id}"' in body
