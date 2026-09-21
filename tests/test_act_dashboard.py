"""Assignment: an act handed to a group, and the owner's read of the cohort.

The distribution half is the group machinery that already existed -- this
pins that it still works once an act carries proof and review, and that the
dashboard on top of it answers the one question it exists for: «چه کسی
انجام داده، چه کسی عقب است».

Three things are load-bearing here:

* **each member gets an independent run.** One enrollment, one set of
  check-ins, one streak -- assignment is distribution, not sharing.
* **the dashboard is the owner's and nobody else's**, and a member asking
  for it gets a **404**, not a 403: it is a screen they have no business
  learning exists.
* **it widens nothing.** Rows go through `participant_display`, so an
  anonymous participant keeps their figures and loses their name here too.
"""

from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import create_session_cookie, hash_password
from app.identity import ANONYMOUS_NAME
from app.models.challenge import IdentityMode, ReviewMode
from app.models.enrollment import Enrollment
from app.models.group import Group, GroupKind, GroupMembership, GroupRole
from app.models.user import User


async def make_user(db: AsyncSession, name: str) -> User:
    user = User(
        name=name,
        email=f"{name.lower()}@example.com",
        password_hash=hash_password("password123"),
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


def sign_in(client: AsyncClient, user: User) -> None:
    client.cookies.set("session", create_session_cookie(user.id))


async def make_group(db: AsyncSession, owner: User, *members: User) -> Group:
    group = Group(name="یک سازمان", kind=GroupKind.COMPANY.value, owner_id=owner.id)
    db.add(group)
    await db.flush()
    db.add(
        GroupMembership(
            group_id=group.id, user_id=owner.id, role=GroupRole.OWNER.value
        )
    )
    for m in members:
        db.add(
            GroupMembership(
                group_id=group.id, user_id=m.id, role=GroupRole.MEMBER.value
            )
        )
    await db.commit()
    await db.refresh(group)
    return group


def local_today() -> date:
    """Today in the enrollment timezone these tests all use.

    Not `date.today()`: an act's occurrences are derived in each
    enrollment's *own* zone (CLAUDE.md, the single most important edge
    case), so a test asking "is today's occurrence due" has to ask in the
    same zone the engine will.
    """
    return datetime.now(ZoneInfo("Asia/Tehran")).date()


def today_key() -> str:
    return f"D{local_today().isoformat()}"


DAILY = {"kind": "recurring_days", "mode": "every_n_days", "n": 1}


async def test_an_act_handed_to_a_group_gives_everybody_their_own_run(
    client: AsyncClient, db: AsyncSession
):
    """«همه اعضا» is a *standing* audience -- read again whenever somebody
    joins -- and each person gets an enrollment of their own."""
    owner = await make_user(db, "Owner")
    a = await make_user(db, "Ali")
    b = await make_user(db, "Bita")
    group = await make_group(db, owner, a, b)

    sign_in(client, owner)
    res = await client.post(
        "/challenges/",
        json={
            "title": "هر روز ده هزار قدم",
            "cadence": DAILY,
            "group_id": group.id,
            "group_audience": "all",
            "identity_mode": IdentityMode.NAMED.value,
            "review_mode": ReviewMode.REFEREE.value,
        },
    )
    assert res.status_code == 201
    challenge_id = res.json()["id"]

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
    assert doers == {owner.id, a.id, b.id}

    # Each run is independent: one member reporting moves nobody else.
    sign_in(client, a)
    assert (
        await client.post(
            "/checkins/",
            json={
                "challenge_id": challenge_id,
                "occurrence_key": today_key(),
                "state": "completed",
            },
        )
    ).status_code == 201

    sign_in(client, b)
    assert len((await client.get("/today/")).json()) == 1


async def test_the_dashboard_says_who_is_behind(
    client: AsyncClient, db: AsyncSession
):
    owner = await make_user(db, "Owner")
    keen = await make_user(db, "Keen")
    behind = await make_user(db, "Behind")
    group = await make_group(db, owner, keen, behind)

    sign_in(client, owner)
    challenge_id = (
        await client.post(
            "/challenges/",
            json={
                "title": "هر روز ده هزار قدم",
                "cadence": DAILY,
                "group_id": group.id,
                "group_audience": "all",
                "identity_mode": IdentityMode.NAMED.value,
            },
        )
    ).json()["id"]

    sign_in(client, keen)
    await client.post(
        "/checkins/",
        json={
            "challenge_id": challenge_id,
            "occurrence_key": today_key(),
            "state": "completed",
        },
    )

    sign_in(client, owner)
    page = await client.get(f"/views/challenges/{challenge_id}/dashboard")
    assert page.status_code == 200
    body = page.text
    assert "پیگیری اعضا" in body
    assert "عقب است" in body
    # The keen one is named and is not flagged; the two who have reported
    # nothing are. The owner counts as one of them -- «همه اعضا» includes
    # whoever set it, and a dashboard that quietly excused its author would
    # be lying about the cohort.
    assert body.count("عقب است") >= 2
    assert "Keen" in body and "Behind" in body

    fragment = await client.get(
        f"/views/challenges/{challenge_id}/dashboard/fragment?offset=0&limit=25"
    )
    assert fragment.status_code == 200
    assert fragment.headers["X-Has-More"] == "false"


async def test_the_dashboard_is_404_to_everybody_but_the_owner(
    client: AsyncClient, db: AsyncSession
):
    """404, not 403 -- the call the manage screen makes. A participant has
    no business learning that an owner's screen exists."""
    owner = await make_user(db, "Owner")
    member = await make_user(db, "Member")
    group = await make_group(db, owner, member)

    sign_in(client, owner)
    challenge_id = (
        await client.post(
            "/challenges/",
            json={
                "title": "هر روز ده هزار قدم",
                "cadence": DAILY,
                "group_id": group.id,
                "group_audience": "all",
            },
        )
    ).json()["id"]

    sign_in(client, member)
    assert (
        await client.get(f"/views/challenges/{challenge_id}/dashboard")
    ).status_code == 404
    # The fragment answers a `fetch()`, so an unauthorised one is still a
    # 404 rather than a redirect -- but a signed-out one is a 401.
    assert (
        await client.get(f"/views/challenges/{challenge_id}/dashboard/fragment")
    ).status_code == 404

    client.cookies.clear()
    assert (
        await client.get(f"/views/challenges/{challenge_id}/dashboard/fragment")
    ).status_code == 401


async def test_the_dashboard_honours_anonymity(
    client: AsyncClient, db: AsyncSession
):
    """It widens nothing. An owner who needs names sets هویت اعضا to «با
    نام» when they create the act -- which is a term the participants can
    read -- rather than getting them through a screen they cannot see."""
    owner = await make_user(db, "Owner")
    hidden = await make_user(db, "Hidden")
    group = await make_group(db, owner, hidden)

    sign_in(client, owner)
    challenge_id = (
        await client.post(
            "/challenges/",
            json={
                "title": "هر روز ده هزار قدم",
                "cadence": DAILY,
                "group_id": group.id,
                "group_audience": "all",
                # Somebody who was never asked is not named
                # (`resolve_assigned_anonymity`).
                "identity_mode": IdentityMode.MEMBER_CHOICE.value,
            },
        )
    ).json()["id"]

    body = (
        await client.get(f"/views/challenges/{challenge_id}/dashboard")
    ).text
    assert "Hidden" not in body
    assert ANONYMOUS_NAME in body
    # The owner's own row is always themselves -- you already know who you
    # are, and a screen that cannot point at «تو» is one nobody can read.
    assert "Owner" in body


async def test_a_pending_report_shows_as_waiting_not_as_done(
    client: AsyncClient, db: AsyncSession
):
    """The one figure the owner acts on differently: a member who reported
    is not behind, and their report has still not counted."""
    owner = await make_user(db, "Owner")
    doer = await make_user(db, "Doer")
    group = await make_group(db, owner, doer)

    sign_in(client, owner)
    challenge_id = (
        await client.post(
            "/challenges/",
            json={
                "title": "هر روز ده هزار قدم",
                "cadence": DAILY,
                "group_id": group.id,
                "group_audience": "all",
                "identity_mode": IdentityMode.NAMED.value,
                "review_mode": ReviewMode.REFEREE.value,
            },
        )
    ).json()["id"]

    sign_in(client, doer)
    await client.post(
        "/checkins/",
        json={
            "challenge_id": challenge_id,
            "occurrence_key": today_key(),
            "state": "completed",
        },
    )

    sign_in(client, owner)
    body = (await client.get(f"/views/challenges/{challenge_id}/dashboard")).text
    # One report waiting, and it is the owner alone who is behind.
    assert "در انتظار تأیید" in body
    assert body.count("عقب است") == 1
