"""The moderation roster: every challenge, and the reach that comes with it.

Three things are pinned here, and they are the three ways this feature can
go wrong:

* **the gate** -- the page answers a member 404 and the fragment answers a
  member 403, the same split the member roster uses and for the same reasons;
* **the reach** -- an admin's list includes a private challenge they neither
  own nor are enrolled in, which is the whole point of the screen and the one
  place `scope_all` is allowed to skip the visibility filter;
* **the line** -- moderation writes `lifecycle_status` and `visibility` and
  nothing else. An admin who could also rename a challenge would be
  indistinguishable from its owner, which is exactly the axis merge
  `test_user_roles.py` exists to prevent.

The delete rule is deliberately the *same* for a moderator as for an owner:
once anyone else has joined, deleting would destroy their logged history, so
it is refused and archiving is the way out.
"""

from __future__ import annotations

from datetime import date

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import create_session_cookie, hash_password
from app.models.challenge import Challenge, ChallengeCategory
from app.models.enrollment import ChallengeRole, Enrollment
from app.models.user import User, UserRole

PAGE_PATH = "/views/admin/challenges"
FRAGMENT_PATH = "/views/admin/challenges/fragment"


async def make_user(db: AsyncSession, name: str, role: str = UserRole.MEMBER.value):
    user = User(
        name=name,
        email=f"{name.lower().replace(' ', '.')}@example.com",
        password_hash=hash_password("password123"),
        role=role,
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


def sign_in(client: AsyncClient, user: User) -> None:
    client.cookies.set("session", create_session_cookie(user.id))


async def make_challenge(
    db: AsyncSession,
    owner: User,
    *,
    title: str = "A challenge",
    visibility: str = "public",
) -> Challenge:
    challenge = Challenge(
        title=title,
        owner_id=owner.id,
        category=ChallengeCategory.OTHER,
        cadence_kind="once",
        cadence={"kind": "once"},
        visibility=visibility,
        lifecycle_status="active",
    )
    db.add(challenge)
    await db.flush()
    db.add(
        Enrollment(
            challenge_id=challenge.id,
            user_id=owner.id,
            start_date=date(2026, 1, 1),
            role=ChallengeRole.OWNER.value,
        )
    )
    await db.commit()
    await db.refresh(challenge)
    return challenge


async def join(db: AsyncSession, challenge: Challenge, user: User) -> None:
    db.add(
        Enrollment(
            challenge_id=challenge.id,
            user_id=user.id,
            start_date=date(2026, 1, 1),
            role=ChallengeRole.PARTICIPANT.value,
        )
    )
    await db.commit()


# --- the gate ---------------------------------------------------------------


async def test_anonymous_is_offered_a_login(client: AsyncClient):
    """Authentication fails first, as a 303 -- a signed-out visitor is sent to
    log in rather than told the page is missing."""
    response = await client.get(PAGE_PATH)
    assert response.status_code == 303
    assert response.headers["location"].startswith("/views/auth/?next=")


async def test_member_gets_a_404_from_the_page(client: AsyncClient, db: AsyncSession):
    sign_in(client, await make_user(db, "Plain Member"))
    assert (await client.get(PAGE_PATH)).status_code == 404


async def test_member_gets_a_403_from_the_fragment(
    client: AsyncClient, db: AsyncSession
):
    """The fragment is a `fetch()` target, so it takes `get_admin_user`: a 401
    for an expired session is what `createInfiniteScroller()` redirects on,
    and a member gets the plain 403 that route answers."""
    sign_in(client, await make_user(db, "Fragment Member"))
    assert (await client.get(FRAGMENT_PATH)).status_code == 403


async def test_admin_opens_the_page(client: AsyncClient, db: AsyncSession):
    admin = await make_user(db, "Panel Admin", role=UserRole.ADMIN.value)
    await make_challenge(db, admin, title="Listed challenge")
    sign_in(client, admin)

    response = await client.get(PAGE_PATH)
    assert response.status_code == 200
    assert "Listed challenge" in response.text


# --- the reach --------------------------------------------------------------


async def test_the_roster_shows_a_private_challenge_the_admin_cannot_reach(
    client: AsyncClient, db: AsyncSession
):
    """`scope_all=True` is the difference between this list and the public
    one. Without it the panel would show an admin only what any member sees,
    and the challenge most likely to need moderating is the hidden one."""
    owner = await make_user(db, "Secretive Owner")
    admin = await make_user(db, "Reaching Admin", role=UserRole.ADMIN.value)
    await make_challenge(db, owner, title="Hidden challenge", visibility="private")
    sign_in(client, admin)

    assert "Hidden challenge" in (await client.get(PAGE_PATH)).text
    assert "Hidden challenge" in (await client.get(FRAGMENT_PATH)).text


async def test_a_private_challenge_stays_404_for_a_member(
    client: AsyncClient, db: AsyncSession
):
    """The reach belongs to the permission, not to the route: the same
    mutation a moderator is allowed still answers a member 404, because it
    selects through the visibility filter for anyone without it."""
    owner = await make_user(db, "Private Owner")
    member = await make_user(db, "Nosy Member")
    challenge = await make_challenge(db, owner, visibility="private")
    sign_in(client, member)

    assert (
        await client.patch(
            f"/challenges/{challenge.id}", json={"lifecycle_status": "archived"}
        )
    ).status_code == 404


# --- the line: moderation is not authorship ---------------------------------


async def test_admin_archives_someone_elses_challenge(
    client: AsyncClient, db: AsyncSession
):
    owner = await make_user(db, "Archived Owner")
    admin = await make_user(db, "Archiving Admin", role=UserRole.ADMIN.value)
    challenge = await make_challenge(db, owner, visibility="private")
    sign_in(client, admin)

    response = await client.patch(
        f"/challenges/{challenge.id}", json={"lifecycle_status": "archived"}
    )
    assert response.status_code == 200
    assert response.json()["lifecycle_status"] == "archived"


async def test_admin_cannot_rename_while_archiving(
    client: AsyncClient, db: AsyncSession
):
    """A body that mixes a moderated field with an authored one is refused
    whole. Half-applying it would let any moderation request smuggle an edit
    through, which is the axis line stated as a request shape.
    """
    owner = await make_user(db, "Untouched Owner")
    admin = await make_user(db, "Overreaching Admin", role=UserRole.ADMIN.value)
    challenge = await make_challenge(db, owner)
    sign_in(client, admin)

    response = await client.patch(
        f"/challenges/{challenge.id}",
        json={"lifecycle_status": "archived", "title": "Hijacked"},
    )
    assert response.status_code == 403
    await db.refresh(challenge)
    assert challenge.title == "A challenge"
    assert challenge.lifecycle_status == "active"


async def test_member_cannot_moderate(client: AsyncClient, db: AsyncSession):
    owner = await make_user(db, "Owner Of Mine")
    member = await make_user(db, "Other Member")
    challenge = await make_challenge(db, owner)
    sign_in(client, member)

    assert (
        await client.patch(
            f"/challenges/{challenge.id}", json={"lifecycle_status": "archived"}
        )
    ).status_code == 403


# --- removal ----------------------------------------------------------------


async def test_admin_deletes_a_challenge_nobody_joined(
    client: AsyncClient, db: AsyncSession
):
    owner = await make_user(db, "Deleted Owner")
    admin = await make_user(db, "Deleting Admin", role=UserRole.ADMIN.value)
    challenge = await make_challenge(db, owner)
    sign_in(client, admin)

    assert (await client.delete(f"/challenges/{challenge.id}")).status_code == 204


async def test_deletion_is_refused_once_others_joined_even_for_an_admin(
    client: AsyncClient, db: AsyncSession
):
    """Moderation does not buy a way around the history rule: the cascade
    would destroy what the participants logged, so the answer is the same 409
    the owner gets, and archiving is the exit."""
    owner = await make_user(db, "Busy Owner")
    joiner = await make_user(db, "A Joiner")
    admin = await make_user(db, "Blocked Admin", role=UserRole.ADMIN.value)
    challenge = await make_challenge(db, owner)
    await join(db, challenge, joiner)
    sign_in(client, admin)

    assert (await client.delete(f"/challenges/{challenge.id}")).status_code == 409


@pytest.mark.parametrize("role", [UserRole.MEMBER.value, UserRole.ADMIN.value])
async def test_the_panel_is_linked_only_for_an_admin(
    client: AsyncClient, db: AsyncSession, role: str
):
    """The affordance and the gate agree without either trusting the other --
    the same assertion `test_admin_page.py` makes about the settings row."""
    user = await make_user(db, f"Linked {role}", role=role)
    sign_in(client, user)

    response = await client.get("/views/admin/")
    if role == UserRole.ADMIN.value:
        assert PAGE_PATH in response.text
    else:
        assert response.status_code == 404
