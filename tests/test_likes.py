"""Likes: the four ways a polymorphic reaction table goes wrong.

* **the subject gate** — the table has no FK to its subject, so nothing in
  the database stops a body naming a challenge the caller cannot see. That is
  held by `SUBJECT_RESOLVERS` composing the subject's *own* visibility
  filter, and the answer is 404: a like must not become a way to learn that a
  private challenge exists.
* **idempotency** — the route takes the state the member wants, not "flip
  it", so a retry cannot land on the opposite answer and a double tap cannot
  inflate the count.
* **the acting user** — the session, never the body. There is no
  client-supplied `user_id` anywhere in the reactions router.
* **the count is live** — read off `Reactions`, so unliking takes it back
  down; nothing anywhere stores it.
"""

from __future__ import annotations

from datetime import date

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import create_session_cookie, hash_password
from app.models.challenge import Challenge, ChallengeCategory
from app.models.enrollment import ChallengeRole, Enrollment
from app.models.reaction import Reaction
from app.models.user import User


def likes_path(subject_id: int, subject_type: str = "challenge") -> str:
    return f"/reactions/{subject_type}/{subject_id}/likes"


async def make_user(db: AsyncSession, name: str) -> User:
    user = User(
        name=name,
        email=f"{name}@example.com",
        password_hash=hash_password("password123"),
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


def sign_in(client: AsyncClient, user: User) -> None:
    client.cookies.set("session", create_session_cookie(user.id))


async def make_challenge(
    db: AsyncSession, owner: User, *, visibility: str = "public"
) -> Challenge:
    challenge = Challenge(
        title="چالش تست",
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


async def test_like_then_unlike_moves_the_count(
    client: AsyncClient, db: AsyncSession
) -> None:
    owner = await make_user(db, "owner")
    liker = await make_user(db, "liker")
    challenge = await make_challenge(db, owner)
    sign_in(client, liker)

    res = await client.put(likes_path(challenge.id), json={"liked": True})
    assert res.status_code == 200
    assert res.json() == {"count": 1, "liked": True}

    res = await client.put(likes_path(challenge.id), json={"liked": False})
    assert res.json() == {"count": 0, "liked": False}

    rows = await db.execute(select(func.count(Reaction.id)))
    assert rows.scalar_one() == 0


async def test_liking_twice_is_one_row(client: AsyncClient, db: AsyncSession) -> None:
    """The state is idempotent in both directions -- a retry is not a second
    like, and un-liking something never liked is not an error."""
    owner = await make_user(db, "owner")
    challenge = await make_challenge(db, owner)
    sign_in(client, owner)

    for _ in range(3):
        res = await client.put(likes_path(challenge.id), json={"liked": True})
        assert res.json() == {"count": 1, "liked": True}

    res = await client.put(likes_path(challenge.id), json={"liked": False})
    assert res.json() == {"count": 0, "liked": False}
    res = await client.put(likes_path(challenge.id), json={"liked": False})
    assert res.json() == {"count": 0, "liked": False}


async def test_count_is_shared_but_liked_is_mine(
    client: AsyncClient, db: AsyncSession
) -> None:
    owner = await make_user(db, "owner")
    other = await make_user(db, "other")
    challenge = await make_challenge(db, owner)

    sign_in(client, owner)
    await client.put(likes_path(challenge.id), json={"liked": True})

    sign_in(client, other)
    res = await client.get(likes_path(challenge.id))
    assert res.json() == {"count": 1, "liked": False}


async def test_invisible_challenge_is_404_not_403(
    client: AsyncClient, db: AsyncSession
) -> None:
    """A like cannot become an existence oracle: the subject resolver composes
    `challenge_visibility_filter`, so a private challenge is simply absent."""
    owner = await make_user(db, "owner")
    stranger = await make_user(db, "stranger")
    challenge = await make_challenge(db, owner, visibility="private")
    sign_in(client, stranger)

    assert (await client.get(likes_path(challenge.id))).status_code == 404
    res = await client.put(likes_path(challenge.id), json={"liked": True})
    assert res.status_code == 404

    rows = await db.execute(select(func.count(Reaction.id)))
    assert rows.scalar_one() == 0


async def test_unknown_subject_kind_is_404(
    client: AsyncClient, db: AsyncSession
) -> None:
    """`subject_type` is a path value, so an unknown one must fail closed
    rather than fall through to some default subject."""
    user = await make_user(db, "someone")
    sign_in(client, user)
    res = await client.get(likes_path(1, subject_type="user"))
    assert res.status_code == 404


async def test_signed_out_may_read_but_not_write(
    client: AsyncClient, db: AsyncSession
) -> None:
    """Discovery stays open (CLAUDE.md), so a visitor sees the count; writing
    is a real 401, which is what `apiFetch` turns into the login redirect."""
    owner = await make_user(db, "owner")
    challenge = await make_challenge(db, owner)
    sign_in(client, owner)
    await client.put(likes_path(challenge.id), json={"liked": True})

    client.cookies.clear()
    res = await client.get(likes_path(challenge.id))
    assert res.json() == {"count": 1, "liked": False}
    res = await client.put(likes_path(challenge.id), json={"liked": True})
    assert res.status_code == 401


async def test_heart_is_rendered_on_the_pages(
    client: AsyncClient, db: AsyncSession
) -> None:
    """Server-rendered so the count is right before any JS runs -- and so the
    infinite-scroll fragment's cards carry it too."""
    owner = await make_user(db, "owner")
    challenge = await make_challenge(db, owner)
    sign_in(client, owner)
    await client.put(likes_path(challenge.id), json={"liked": True})

    for path in (
        "/views/challenges/",
        "/views/challenges/fragment",
        f"/views/challenges/{challenge.id}",
    ):
        res = await client.get(path)
        assert res.status_code == 200, path
        assert 'data-like-subject="challenge"' in res.text, path
        assert 'data-liked="true"' in res.text, path
