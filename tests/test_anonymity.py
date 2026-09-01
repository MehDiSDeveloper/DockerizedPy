"""Anonymous participation: the policy decides, and the answer is stored once.

Three things are pinned here, and they are the three ways this feature goes
wrong:

* **the resolution** -- ``resolve_anonymity`` is the only thing that turns a
  challenge's ``identity_mode`` plus a joiner's request into
  ``Enrollment.is_anonymous``. A body asking to hide inside a challenge that
  names everyone must be answered with a named enrolment, or the mode is a
  suggestion rather than a rule.
* **the lock** -- the mode is frozen once anyone but the owner has joined.
  Without it, an owner can flip a challenge to `named` and unmask people who
  joined on the opposite promise, which is precisely what storing the
  resolved answer (instead of re-deriving it) exists to prevent.
* **the leak** -- an anonymous member's name must not reach the owner's
  notification feed. It is dropped at the write, because ``ENROLLMENT_LEFT``
  outlives the enrollment row that carried the choice.
"""

from __future__ import annotations

from datetime import date

from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import create_session_cookie, hash_password
from app.identity import resolve_anonymity
from app.models.challenge import Challenge, ChallengeCategory, IdentityMode
from app.models.enrollment import ChallengeRole, Enrollment
from app.models.notification import Notification, NotificationKind
from app.models.user import User


async def make_user(db: AsyncSession, name: str) -> User:
    user = User(
        name=name,
        email=f"{name.lower().replace(' ', '.')}@example.com",
        password_hash=hash_password("password123"),
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
    identity_mode: str = IdentityMode.NAMED.value,
    visibility: str = "public",
) -> Challenge:
    challenge = Challenge(
        title="A challenge",
        owner_id=owner.id,
        category=ChallengeCategory.OTHER,
        cadence_kind="once",
        cadence={"kind": "once"},
        visibility=visibility,
        lifecycle_status="active",
        identity_mode=identity_mode,
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


async def enrollment_of(
    db: AsyncSession, challenge: Challenge, user: User
) -> Enrollment | None:
    return (
        await db.execute(
            select(Enrollment).where(
                Enrollment.challenge_id == challenge.id,
                Enrollment.user_id == user.id,
            )
        )
    ).scalar_one_or_none()


# --- the resolution ---------------------------------------------------------


def test_only_member_choice_consults_the_joiner():
    """The two absolute modes answer for everyone; a request is not a vote."""
    assert resolve_anonymity(IdentityMode.NAMED.value, True) is False
    assert resolve_anonymity(IdentityMode.ANONYMOUS.value, False) is True
    assert resolve_anonymity(IdentityMode.MEMBER_CHOICE.value, True) is True
    assert resolve_anonymity(IdentityMode.MEMBER_CHOICE.value, False) is False


def test_an_unknown_mode_falls_back_to_the_column_default():
    """A row from a newer app, or one hand-edited, must still be joinable."""
    assert resolve_anonymity("something_else", True) is False
    assert resolve_anonymity(None, True) is False


async def test_named_challenge_ignores_a_request_to_hide(
    client: AsyncClient, db: AsyncSession
):
    owner = await make_user(db, "Named Owner")
    joiner = await make_user(db, "Named Joiner")
    challenge = await make_challenge(db, owner)

    sign_in(client, joiner)
    res = await client.post(f"/enrollments/{challenge.id}", json={"is_anonymous": True})
    assert res.status_code == 201
    # Not a 422: the request is simply not the question this challenge asks.
    assert res.json()["is_anonymous"] is False
    assert (await enrollment_of(db, challenge, joiner)).is_anonymous is False


async def test_anonymous_challenge_hides_a_joiner_who_did_not_ask(
    client: AsyncClient, db: AsyncSession
):
    owner = await make_user(db, "Hidden Owner")
    joiner = await make_user(db, "Hidden Joiner")
    challenge = await make_challenge(
        db, owner, identity_mode=IdentityMode.ANONYMOUS.value
    )

    sign_in(client, joiner)
    res = await client.post(f"/enrollments/{challenge.id}", json={})
    assert res.status_code == 201
    assert res.json()["is_anonymous"] is True


async def test_member_choice_takes_the_answer_both_ways(
    client: AsyncClient, db: AsyncSession
):
    owner = await make_user(db, "Choice Owner")
    hider = await make_user(db, "Choice Hider")
    shower = await make_user(db, "Choice Shower")
    challenge = await make_challenge(
        db, owner, identity_mode=IdentityMode.MEMBER_CHOICE.value
    )

    sign_in(client, hider)
    hidden = await client.post(
        f"/enrollments/{challenge.id}", json={"is_anonymous": True}
    )
    assert hidden.json()["is_anonymous"] is True

    sign_in(client, shower)
    shown = await client.post(
        f"/enrollments/{challenge.id}", json={"is_anonymous": False}
    )
    assert shown.json()["is_anonymous"] is False


async def test_the_creator_is_never_anonymous(client: AsyncClient, db: AsyncSession):
    """Anonymity is about participation; authorship is a separate thing, and
    challenge-detail names the creator in every mode."""
    owner = await make_user(db, "Creator")
    sign_in(client, owner)
    res = await client.post(
        "/challenges/",
        json={
            "title": "Fully hidden",
            "category": ChallengeCategory.OTHER.value,
            "identity_mode": IdentityMode.ANONYMOUS.value,
            "cadence": {"kind": "once"},
        },
    )
    assert res.status_code == 201
    challenge_id = res.json()["id"]
    enrollment = (
        await db.execute(
            select(Enrollment).where(Enrollment.challenge_id == challenge_id)
        )
    ).scalar_one()
    assert enrollment.role == ChallengeRole.OWNER.value
    assert enrollment.is_anonymous is False


async def test_the_creator_is_never_anonymous_via_the_ssr_wizard(
    client: AsyncClient, db: AsyncSession
):
    """The two create routes share one writer (`create_challenge_record`) --
    before that, the SSR half wrote the creator's enrolment without
    `is_anonymous=False`, so an `anonymous` challenge made through the wizard
    could hide its own author."""
    owner = await make_user(db, "SSR Creator")
    sign_in(client, owner)
    res = await client.post(
        "/views/challenges/create",
        json={
            "title": "Fully hidden via SSR",
            "category": ChallengeCategory.OTHER.value,
            "identity_mode": IdentityMode.ANONYMOUS.value,
            "cadence": {"kind": "once"},
        },
    )
    assert res.status_code == 201
    challenge_id = res.json()["id"]
    enrollment = (
        await db.execute(
            select(Enrollment).where(Enrollment.challenge_id == challenge_id)
        )
    ).scalar_one()
    assert enrollment.role == ChallengeRole.OWNER.value
    assert enrollment.is_anonymous is False


# --- the lock ---------------------------------------------------------------


async def test_the_mode_is_editable_while_the_owner_is_alone(
    client: AsyncClient, db: AsyncSession
):
    owner = await make_user(db, "Lonely Owner")
    challenge = await make_challenge(db, owner)

    sign_in(client, owner)
    res = await client.patch(
        f"/challenges/{challenge.id}",
        json={"identity_mode": IdentityMode.ANONYMOUS.value},
    )
    assert res.status_code == 200
    assert res.json()["identity_mode"] == IdentityMode.ANONYMOUS.value


async def test_the_mode_locks_once_somebody_else_joins(
    client: AsyncClient, db: AsyncSession
):
    """The whole point of storing the resolved answer: nobody's promise gets
    rewritten under them."""
    owner = await make_user(db, "Locked Owner")
    joiner = await make_user(db, "Locked Joiner")
    challenge = await make_challenge(
        db, owner, identity_mode=IdentityMode.ANONYMOUS.value
    )

    sign_in(client, joiner)
    joined = await client.post(f"/enrollments/{challenge.id}", json={})
    assert joined.status_code == 201

    sign_in(client, owner)
    res = await client.patch(
        f"/challenges/{challenge.id}",
        json={"identity_mode": IdentityMode.NAMED.value},
    )
    assert res.status_code == 409

    await db.refresh(challenge)
    assert challenge.identity_mode == IdentityMode.ANONYMOUS.value
    assert (await enrollment_of(db, challenge, joiner)).is_anonymous is True


async def test_the_enrollment_patch_is_not_a_way_around_the_resolution(
    client: AsyncClient, db: AsyncSession
):
    """`EnrollmentUpdate` deliberately does not carry the flag -- the handler
    writes whatever that shape holds straight onto the row."""
    owner = await make_user(db, "Patch Owner")
    joiner = await make_user(db, "Patch Joiner")
    challenge = await make_challenge(
        db, owner, identity_mode=IdentityMode.ANONYMOUS.value
    )

    sign_in(client, joiner)
    await client.post(f"/enrollments/{challenge.id}", json={})
    res = await client.patch(
        f"/enrollments/{challenge.id}", json={"is_anonymous": False}
    )
    assert res.status_code == 200
    assert res.json()["is_anonymous"] is True


# --- the leak ---------------------------------------------------------------


async def notifications_for(db: AsyncSession, user: User) -> list[Notification]:
    rows = await db.execute(
        select(Notification).where(Notification.user_id == user.id)
    )
    return list(rows.scalars().all())


async def test_an_anonymous_join_reaches_the_owner_without_a_name(
    client: AsyncClient, db: AsyncSession
):
    """Membership notifications only fire for a non-public challenge, so the
    leak this closes is exactly the one that can happen."""
    owner = await make_user(db, "Feed Owner")
    joiner = await make_user(db, "Feed Joiner")
    challenge = await make_challenge(
        db,
        owner,
        identity_mode=IdentityMode.ANONYMOUS.value,
        visibility="unlisted",
    )

    sign_in(client, joiner)
    joined = await client.post(f"/enrollments/{challenge.id}", json={})
    assert joined.status_code == 201

    rows = await notifications_for(db, owner)
    assert [r.kind for r in rows] == [NotificationKind.ENROLLMENT_JOINED.value]
    # The event is still recorded; only the actor is dropped, and at the
    # write rather than at render time.
    assert rows[0].actor_user_id is None
    assert rows[0].challenge_id == challenge.id


async def test_an_anonymous_departure_is_nameless_too(
    client: AsyncClient, db: AsyncSession
):
    """The case that forces the decision to be made at write time: the
    enrollment carrying the choice is gone by the time the feed renders."""
    owner = await make_user(db, "Exit Owner")
    joiner = await make_user(db, "Exit Joiner")
    challenge = await make_challenge(
        db,
        owner,
        identity_mode=IdentityMode.ANONYMOUS.value,
        visibility="unlisted",
    )

    sign_in(client, joiner)
    await client.post(f"/enrollments/{challenge.id}", json={})
    left_res = await client.delete(f"/enrollments/{challenge.id}")
    assert left_res.status_code == 204

    left = [
        r
        for r in await notifications_for(db, owner)
        if r.kind == NotificationKind.ENROLLMENT_LEFT.value
    ]
    assert len(left) == 1
    assert left[0].actor_user_id is None


async def test_a_named_join_still_names_the_joiner(
    client: AsyncClient, db: AsyncSession
):
    """The anonymising must be conditional, or the feed stops being useful."""
    owner = await make_user(db, "Open Owner")
    joiner = await make_user(db, "Open Joiner")
    challenge = await make_challenge(db, owner, visibility="unlisted")

    sign_in(client, joiner)
    await client.post(f"/enrollments/{challenge.id}", json={})

    rows = await notifications_for(db, owner)
    assert [r.actor_user_id for r in rows] == [joiner.id]
