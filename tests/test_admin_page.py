"""The admin panel is one page, reachable only by an admin, and only from
the settings screen.

Two things are pinned here, and both are about what a *member* sees:

* the panel answers a signed-in member 404 -- the same answer an unknown
  path gives, so its existence is not confirmed to someone who guesses the
  URL. (Authentication still fails first as a 303 to login; that split lives
  in ``test_page_authorization.py``.)
* the settings row that links to it is rendered for an admin and for nobody
  else, so the gate and the affordance agree without either trusting the
  other.
"""

from __future__ import annotations

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import create_session_cookie, hash_password
from app.models.user import User, UserRole

ADMIN_PATH = "/views/admin/"
SETTINGS_PATH = "/views/settings/"
FRAGMENT_PATH = "/views/admin/fragment"


async def sign_in(
    client: AsyncClient, db: AsyncSession, role: str = UserRole.MEMBER.value
) -> User:
    user = User(
        name=f"{role.title()} User",
        email=f"{role}@example.com",
        password_hash=hash_password("password123"),
        role=role,
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    client.cookies.set("session", create_session_cookie(user.id))
    return user


async def test_anonymous_is_offered_a_login_not_a_404(client: AsyncClient):
    """Authentication fails first, so a signed-out visitor is not told the
    page is missing -- they are sent to log in, and only then judged.

    Not in ``test_page_authorization.py``'s ``PROTECTED_PAGES``: that list is
    also asserted to answer 200 once signed in, which is exactly what this
    page must *not* do for a member.
    """
    response = await client.get(ADMIN_PATH)
    assert response.status_code == 303
    assert response.headers["location"] == "/views/auth/?next=%2Fviews%2Fadmin%2F"


async def test_member_gets_a_404_from_the_panel(client: AsyncClient, db: AsyncSession):
    await sign_in(client, db)
    assert (await client.get(ADMIN_PATH)).status_code == 404


async def test_admin_sees_the_roster(client: AsyncClient, db: AsyncSession):
    admin = await sign_in(client, db, role=UserRole.ADMIN.value)

    response = await client.get(ADMIN_PATH)
    assert response.status_code == 200
    # The panel lists the member and the one field it administers.
    assert admin.name in response.text
    assert f'data-member-id="{admin.id}"' in response.text


async def test_only_an_admin_is_offered_the_panel(
    client: AsyncClient, db: AsyncSession
):
    await sign_in(client, db)
    assert ADMIN_PATH not in (await client.get(SETTINGS_PATH)).text

    client.cookies.clear()
    await sign_in(client, db, role=UserRole.ADMIN.value)
    assert ADMIN_PATH in (await client.get(SETTINGS_PATH)).text


async def add_member(db: AsyncSession, name: str, email: str, role: str) -> User:
    user = User(
        name=name,
        email=email,
        password_hash=hash_password("password123"),
        role=role,
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


async def test_the_profile_offers_the_panel_to_an_admin_only(
    client: AsyncClient, db: AsyncSession
):
    """The second entry point, pinned like the settings one.

    The row is rendered off ``viewer_is_admin`` -- the person *looking* --
    so a member's own profile must not carry it even though the page and the
    row both belong to them.
    """
    member = await sign_in(client, db)
    assert ADMIN_PATH not in (await client.get(f"/views/users/{member.id}")).text

    client.cookies.clear()
    admin = await sign_in(client, db, role=UserRole.ADMIN.value)
    assert ADMIN_PATH in (await client.get(f"/views/users/{admin.id}")).text


async def test_search_and_role_filter_narrow_the_roster(
    client: AsyncClient, db: AsyncSession
):
    """Search spans name *and* email, and the role filter is a separate cut.

    Both run through ``fetch_member_page`` in ``routers/user.py``, so this
    also pins that the page and the JSON ``GET /users/`` cannot disagree
    about what a term matches.
    """
    admin = await sign_in(client, db, role=UserRole.ADMIN.value)
    zahra = await add_member(db, "Zahra", "zahra@example.com", UserRole.MEMBER.value)
    reza = await add_member(db, "Reza", "reza@example.com", UserRole.ADMIN.value)

    by_name = await client.get(ADMIN_PATH, params={"q": "Zahra"})
    assert f'data-member-id="{zahra.id}"' in by_name.text
    assert f'data-member-id="{reza.id}"' not in by_name.text

    by_email = await client.get(ADMIN_PATH, params={"q": "reza@example"})
    assert f'data-member-id="{reza.id}"' in by_email.text
    assert f'data-member-id="{zahra.id}"' not in by_email.text

    admins_only = await client.get(ADMIN_PATH, params={"role": "admin"})
    assert f'data-member-id="{admin.id}"' in admins_only.text
    assert f'data-member-id="{reza.id}"' in admins_only.text
    assert f'data-member-id="{zahra.id}"' not in admins_only.text

    # The API answers the same question with the same rule.
    api = await client.get("/users/", params={"role": "admin"})
    assert {row["id"] for row in api.json()} == {admin.id, reza.id}


async def test_a_like_wildcard_in_the_search_term_is_literal(
    client: AsyncClient, db: AsyncSession
):
    """``%`` is escaped, so it finds the member called ``%`` -- not everyone.

    Without the escape this is the one search term that silently ignores the
    filter, which reads as "search is broken" rather than as a bug.
    """
    await sign_in(client, db, role=UserRole.ADMIN.value)
    await add_member(db, "Plain", "plain@example.com", UserRole.MEMBER.value)

    response = await client.get(ADMIN_PATH, params={"q": "%"})
    assert 'data-member-id=' not in response.text


async def test_the_fragment_pages_the_roster_and_reports_more(
    client: AsyncClient, db: AsyncSession
):
    """Lazy loading: one page at a time, with ``X-Has-More`` driving the
    scroller, exactly like the challenge list's fragment."""
    await sign_in(client, db, role=UserRole.ADMIN.value)
    for i in range(4):
        await add_member(db, f"Member {i}", f"m{i}@example.com", UserRole.MEMBER.value)

    first = await client.get(FRAGMENT_PATH, params={"offset": 0, "limit": 2})
    assert first.status_code == 200
    assert first.headers["X-Has-More"] == "true"
    assert first.text.count("data-member-id=") == 2

    last = await client.get(FRAGMENT_PATH, params={"offset": 4, "limit": 2})
    assert last.headers["X-Has-More"] == "false"
    assert last.text.count("data-member-id=") == 1

    # The filters apply to the fragment too, or they leak back in on scroll.
    filtered = await client.get(FRAGMENT_PATH, params={"role": "admin"})
    assert filtered.text.count("data-member-id=") == 1


async def test_the_fragment_answers_a_member_403_not_a_redirect(
    client: AsyncClient, db: AsyncSession
):
    """A `fetch()` needs a status it can act on.

    The page dependency's 303 would be followed silently and the login
    page's markup appended as member rows, so this route carries the
    JSON-shaped dependency: 401 signed out (what the scroller redirects on),
    403 for a signed-in member.
    """
    assert (await client.get(FRAGMENT_PATH)).status_code == 401

    await sign_in(client, db)
    assert (await client.get(FRAGMENT_PATH)).status_code == 403
