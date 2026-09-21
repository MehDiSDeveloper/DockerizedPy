"""Staffing an act: who may ask, who may answer, and what is never deleted.

The role axis, pinned at the three places it goes wrong:

* **who may ask.** `CHALLENGE_MANAGE_REFEREES` is the owner's and nobody
  else's -- and it is deliberately **not** an admin grant, the same line
  `CHALLENGE_EDIT` draws: staffing an act is authorship, not moderation.
* **asked is not agreed.** An invited referee holds no power at all, which
  is what keeps somebody from being made accountable for another person's
  promise without ever saying yes.
* **nothing is deleted.** Declining and being removed are states, because
  the row is the only record that somebody was asked -- and re-inviting them
  must reuse that one row rather than starting a second history.
"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import create_session_cookie, hash_password
from app.models.act import ChallengeReferee, RefereeState
from app.models.challenge import Challenge, ChallengeCategory, ReviewMode
from app.models.enrollment import ChallengeRole, Enrollment
from app.models.notification import Notification, NotificationKind
from app.models.stats import ChallengeStats
from app.models.user import User, UserRole
from app.referees import is_pact


async def make_user(
    db: AsyncSession, name: str, *, mobile: str | None = None, role: str | None = None
) -> User:
    user = User(
        name=name,
        email=f"{name.lower()}@example.com",
        password_hash=hash_password("password123"),
        mobile=mobile,
        role=role or UserRole.MEMBER.value,
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


def sign_in(client: AsyncClient, user: User) -> None:
    client.cookies.set("session", create_session_cookie(user.id))


def local_today():
    """Today in the enrollment timezone these tests all use.

    Not `date.today()`: an act's occurrences are derived in each
    enrollment's *own* zone (CLAUDE.md, the single most important edge
    case), so a start date has to be set in the same zone the engine reads.
    """
    return datetime.now(ZoneInfo("Asia/Tehran")).date()


async def make_act(db: AsyncSession, owner: User, **kw) -> Challenge:
    challenge = Challenge(
        title="An act",
        owner_id=owner.id,
        category=ChallengeCategory.OTHER,
        cadence_kind="once",
        cadence={"kind": "once"},
        visibility="public",
        lifecycle_status="active",
        review_mode=ReviewMode.REFEREE.value,
        **kw,
    )
    db.add(challenge)
    await db.flush()
    db.add(
        Enrollment(
            challenge_id=challenge.id,
            user_id=owner.id,
            timezone="Asia/Tehran",
            start_date=local_today(),
            role=ChallengeRole.OWNER.value,
        )
    )
    db.add(ChallengeStats(challenge_id=challenge.id, participant_count=1))
    await db.commit()
    await db.refresh(challenge)
    return challenge


# --- who may ask ---------------------------------------------------------


async def test_the_owner_invites_by_credential(client: AsyncClient, db: AsyncSession):
    """By mobile or exact email -- never by id and never by a name search,
    which would hand every act's owner a way to walk the membership."""
    owner = await make_user(db, "Owner")
    watcher = await make_user(db, "Watcher", mobile="+989121234567")
    challenge = await make_act(db, owner)
    sign_in(client, owner)

    # The number as an Iranian would type it -- `normalize_mobile` is what
    # makes 09… and +989… one account.
    res = await client.post(
        f"/challenges/{challenge.id}/referees",
        json={"identifier": "09121234567"},
    )
    assert res.status_code == 201
    assert res.json()["user_id"] == watcher.id
    assert res.json()["state"] == RefereeState.INVITED.value

    # And by email.
    third = await make_user(db, "Third")
    assert (
        await client.post(
            f"/challenges/{challenge.id}/referees",
            json={"identifier": "THIRD@example.com"},
        )
    ).status_code == 201
    assert third.id in {
        r["user_id"]
        for r in (await client.get(f"/challenges/{challenge.id}/referees")).json()
    }

    kinds = (
        (
            await db.execute(
                select(Notification.kind).where(Notification.user_id == watcher.id)
            )
        )
        .scalars()
        .all()
    )
    assert NotificationKind.REFEREE_INVITED.value in kinds


async def test_an_unknown_credential_is_a_plain_404(
    client: AsyncClient, db: AsyncSession
):
    """Honest, and cheap to keep: the oracle costs one whole phone number
    per guess, and the alternative is a screen that silently does nothing."""
    owner = await make_user(db, "Owner")
    challenge = await make_act(db, owner)
    sign_in(client, owner)
    assert (
        await client.post(
            f"/challenges/{challenge.id}/referees",
            json={"identifier": "09129999999"},
        )
    ).status_code == 404


async def test_only_the_owner_may_staff_an_act(client: AsyncClient, db: AsyncSession):
    owner = await make_user(db, "Owner")
    member = await make_user(db, "Member")
    admin = await make_user(db, "Admin", role=UserRole.ADMIN.value)
    watcher = await make_user(db, "Watcher")
    challenge = await make_act(db, owner)

    sign_in(client, member)
    assert (
        await client.post(
            f"/challenges/{challenge.id}/referees",
            json={"identifier": watcher.email},
        )
    ).status_code == 403

    # **And an operator is not the owner either.** The same line
    # `CHALLENGE_EDIT` draws: moderation is not authorship, and staffing an
    # act is authorship.
    sign_in(client, admin)
    assert (
        await client.post(
            f"/challenges/{challenge.id}/referees",
            json={"identifier": watcher.email},
        )
    ).status_code == 403


# --- asked is not agreed -------------------------------------------------


async def test_only_the_invitee_answers_their_own_invitation(
    client: AsyncClient, db: AsyncSession
):
    """`/me`, not `/{user_id}`: the acting member comes from the session, so
    answering somebody else's invitation is unexpressible rather than merely
    refused."""
    owner = await make_user(db, "Owner")
    watcher = await make_user(db, "Watcher")
    stranger = await make_user(db, "Stranger")
    challenge = await make_act(db, owner)
    db.add(
        ChallengeReferee(
            challenge_id=challenge.id,
            user_id=watcher.id,
            state=RefereeState.INVITED.value,
            invited_by_user_id=owner.id,
        )
    )
    await db.commit()

    sign_in(client, stranger)
    assert (
        await client.patch(
            f"/challenges/{challenge.id}/referees/me", json={"accept": True}
        )
    ).status_code == 404

    sign_in(client, watcher)
    res = await client.patch(
        f"/challenges/{challenge.id}/referees/me", json={"accept": True}
    )
    assert res.status_code == 200
    assert res.json()["state"] == RefereeState.ACTIVE.value
    assert res.json()["responded_at"] is not None

    # Answering twice is refused: an active referee steps down by being
    # removed, which is the owner's act and the one the log records.
    assert (
        await client.patch(
            f"/challenges/{challenge.id}/referees/me", json={"accept": False}
        )
    ).status_code == 409

    # The person who asked hears either way.
    kinds = (
        (
            await db.execute(
                select(Notification.kind).where(Notification.user_id == owner.id)
            )
        )
        .scalars()
        .all()
    )
    assert NotificationKind.REFEREE_RESPONDED.value in kinds


# --- nothing is deleted --------------------------------------------------


async def test_removing_is_a_state_and_re_inviting_reuses_the_row(
    client: AsyncClient, db: AsyncSession
):
    owner = await make_user(db, "Owner")
    watcher = await make_user(db, "Watcher")
    challenge = await make_act(db, owner)
    sign_in(client, owner)

    await client.post(
        f"/challenges/{challenge.id}/referees", json={"identifier": watcher.email}
    )
    assert (
        await client.delete(f"/challenges/{challenge.id}/referees/{watcher.id}")
    ).status_code == 204

    row = (
        await db.execute(
            select(ChallengeReferee).where(
                ChallengeReferee.challenge_id == challenge.id,
                ChallengeReferee.user_id == watcher.id,
            )
        )
    ).scalar_one()
    await db.refresh(row)
    assert row.state == RefereeState.REMOVED.value

    # Removed rows leave the default listing, which is what a member reading
    # the act sees.
    assert (await client.get(f"/challenges/{challenge.id}/referees")).json() == []

    # Re-inviting flips the *same* row rather than stacking a second one --
    # the unique constraint says one row per person per act.
    await client.post(
        f"/challenges/{challenge.id}/referees", json={"identifier": watcher.email}
    )
    count = (
        await db.execute(
            select(func.count())
            .select_from(ChallengeReferee)
            .where(
                ChallengeReferee.challenge_id == challenge.id,
                ChallengeReferee.user_id == watcher.id,
            )
        )
    ).scalar_one()
    assert count == 1


async def test_inviting_twice_is_the_same_request_as_once(
    client: AsyncClient, db: AsyncSession
):
    owner = await make_user(db, "Owner")
    watcher = await make_user(db, "Watcher")
    challenge = await make_act(db, owner)
    sign_in(client, owner)

    first = await client.post(
        f"/challenges/{challenge.id}/referees", json={"identifier": watcher.email}
    )
    second = await client.post(
        f"/challenges/{challenge.id}/referees", json={"identifier": watcher.email}
    )
    assert second.status_code == 201
    assert second.json()["id"] == first.json()["id"]


async def test_removing_somebody_who_is_not_a_referee_is_404(
    client: AsyncClient, db: AsyncSession
):
    """A 403 would confirm the account exists -- the call the group router
    makes for a member id that is not in the group."""
    owner = await make_user(db, "Owner")
    stranger = await make_user(db, "Stranger")
    challenge = await make_act(db, owner)
    sign_in(client, owner)
    assert (
        await client.delete(f"/challenges/{challenge.id}/referees/{stranger.id}")
    ).status_code == 404


# --- a pact is an arrangement, not a kind --------------------------------


async def test_a_pact_is_two_enrollments_and_two_referee_rows(
    client: AsyncClient, db: AsyncSession
):
    """`pact_with` writes both halves in one request, and `is_pact` reads the
    arrangement back -- there is no column anywhere that says «pact»."""
    a = await make_user(db, "Ali")
    b = await make_user(db, "Bita", mobile="+989121110000")
    sign_in(client, a)

    res = await client.post(
        "/challenges/",
        json={
            "title": "هر روز بدویم",
            "cadence": {"kind": "recurring_days", "mode": "every_n_days", "n": 1},
            "pact_with": "09121110000",
        },
    )
    assert res.status_code == 201
    challenge_id = res.json()["id"]
    # Setting `pact_with` implies review: a pact whose reports settle
    # themselves is two people keeping separate diaries.
    assert res.json()["review_mode"] == ReviewMode.REFEREE.value

    doers = set(
        (
            await db.execute(
                select(Enrollment.user_id).where(
                    Enrollment.challenge_id == challenge_id
                )
            )
        )
        .scalars()
        .all()
    )
    assert doers == {a.id, b.id}

    rows = {
        r.user_id: r.state
        for r in (
            await db.execute(
                select(ChallengeReferee).where(
                    ChallengeReferee.challenge_id == challenge_id
                )
            )
        )
        .scalars()
        .all()
    }
    # The one who opened it has already agreed; the other half is asked.
    assert rows[a.id] == RefereeState.ACTIVE.value
    assert rows[b.id] == RefereeState.INVITED.value

    sign_in(client, b)
    await client.patch(
        f"/challenges/{challenge_id}/referees/me", json={"accept": True}
    )

    challenge = (
        await db.execute(select(Challenge).where(Challenge.id == challenge_id))
    ).scalar_one()
    assert await is_pact(db, challenge) is True


async def test_a_pact_with_an_unknown_person_creates_nothing(
    client: AsyncClient, db: AsyncSession
):
    """Resolved before anything is written: an act naming somebody who has
    no account must not exist even briefly."""
    a = await make_user(db, "Ali")
    sign_in(client, a)
    res = await client.post(
        "/challenges/",
        json={
            "title": "هر روز بدویم",
            "cadence": {"kind": "once"},
            "pact_with": "09129999999",
        },
    )
    assert res.status_code == 404
    assert (
        await db.execute(select(func.count()).select_from(Challenge))
    ).scalar_one() == 0


async def test_a_pact_needs_two_people(client: AsyncClient, db: AsyncSession):
    a = await make_user(db, "Ali")
    sign_in(client, a)
    res = await client.post(
        "/challenges/",
        json={
            "title": "با خودم",
            "cadence": {"kind": "once"},
            "pact_with": a.email,
        },
    )
    assert res.status_code == 422
