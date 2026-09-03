"""A roadmap's invite link, and the one rule about it that can break silently.

The capacity is the part worth a test of its own, and it is worth it *here*
as well as on the group side because the two now share one implementation
(``app.invites.consume_seat``): a regression in that one statement would let
both subsystems exceed a capacity at once. It is a conditional
``UPDATE ... WHERE uses < max_uses`` plus a ``rowcount`` check, never a read
followed by a write -- which is exactly the race that admits two people to a
one-seat link. The concurrency test drives the real app through
``asyncio.gather`` on a file-backed SQLite (see ``conftest.py`` on why the
engine is a file), so it exercises two genuine transactions rather than two
sequential calls.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select

from app.auth import create_session_cookie
from app.invites import (
    INVITE_ACTIVE,
    INVITE_EXPIRED,
    INVITE_FULL,
    INVITE_REVOKED,
    invite_state,
    new_invite_code,
)
from app.models.roadmap import RoadmapEnrollment, RoadmapInvite
from tests.roadmap_helpers import (
    add_step,
    make_challenge,
    make_roadmap,
    make_user,
    sign_in,
)


async def make_invite(db, roadmap, creator, **kwargs) -> RoadmapInvite:
    invite = RoadmapInvite(
        roadmap_id=roadmap.id,
        code=kwargs.pop("code", None) or new_invite_code(),
        uses=kwargs.pop("uses", 0),
        created_by_user_id=creator.id,
        **kwargs,
    )
    db.add(invite)
    await db.commit()
    await db.refresh(invite)
    return invite


async def test_invite_state_is_derived_from_the_row(db):
    """Never stored -- one function answers for a group's link and a
    roadmap's, so the two cannot come to disagree about what «منقضی» means."""
    owner = await make_user(db, "Owner")
    roadmap = await make_roadmap(db, owner)

    assert invite_state(await make_invite(db, roadmap, owner, max_uses=5, uses=2)) == (
        INVITE_ACTIVE
    )
    assert invite_state(await make_invite(db, roadmap, owner, max_uses=2, uses=2)) == (
        INVITE_FULL
    )
    assert invite_state(
        await make_invite(
            db, roadmap, owner, expires_at=datetime.now(UTC) - timedelta(days=1)
        )
    ) == INVITE_EXPIRED
    assert invite_state(
        await make_invite(db, roadmap, owner, revoked_at=datetime.now(UTC))
    ) == INVITE_REVOKED


async def test_capacity_holds_under_a_concurrent_race(client, db):
    """Four people tapping a one-seat link at the same instant.

    This is the test the conditional UPDATE exists for: a read-then-write
    implementation passes a sequential test and fails this one.
    """
    owner = await make_user(db, "Owner")
    roadmap = await make_roadmap(db, owner)
    challenge = await make_challenge(db, owner)
    await add_step(db, roadmap, challenge)
    invite = await make_invite(db, roadmap, owner, max_uses=1)
    racers = [await make_user(db, f"Racer {i}") for i in range(4)]

    async def attempt(user):
        return await client.post(
            f"/roadmap-invites/{invite.code}/accept",
            cookies={"session": create_session_cookie(user.id)},
        )

    results = await asyncio.gather(*(attempt(u) for u in racers))
    assert len([r for r in results if r.status_code == 201]) == 1

    roadmap_id, code = roadmap.id, invite.code
    walkers = (
        await db.execute(
            select(func.count())
            .select_from(RoadmapEnrollment)
            .where(RoadmapEnrollment.roadmap_id == roadmap_id)
        )
    ).scalar_one()
    assert walkers == 1

    db.expire_all()
    fresh = (
        await db.execute(select(RoadmapInvite).where(RoadmapInvite.code == code))
    ).scalar_one()
    assert fresh.uses == 1


async def test_a_dead_link_lets_nobody_in(client, db):
    owner = await make_user(db, "Owner")
    guest = await make_user(db, "Guest")
    roadmap = await make_roadmap(db, owner)
    revoked = await make_invite(db, roadmap, owner, revoked_at=datetime.now(UTC))

    sign_in(client, guest)
    assert (
        await client.post(f"/roadmap-invites/{revoked.code}/accept")
    ).status_code == 409
    # The landing page still renders and says which of the four states it is
    # in, rather than 404ing on a link somebody was genuinely handed.
    page = await client.get(f"/views/roadmap-invites/{revoked.code}")
    assert page.status_code == 200


async def test_re_opening_your_own_link_does_not_spend_a_seat(client, db):
    """Idempotent, and it matters for the capacity: somebody re-opening the
    link they joined with has not joined twice."""
    owner = await make_user(db, "Owner")
    guest = await make_user(db, "Guest")
    roadmap = await make_roadmap(db, owner)
    challenge = await make_challenge(db, owner)
    await add_step(db, roadmap, challenge)
    invite = await make_invite(db, roadmap, owner, max_uses=2)
    code = invite.code

    sign_in(client, guest)
    assert (await client.post(f"/roadmap-invites/{code}/accept")).status_code == 201
    assert (await client.post(f"/roadmap-invites/{code}/accept")).status_code == 201

    db.expire_all()
    fresh = (
        await db.execute(select(RoadmapInvite).where(RoadmapInvite.code == code))
    ).scalar_one()
    assert fresh.uses == 1


async def test_the_landing_preview_never_names_the_steps(client, db):
    """Whoever holds the code is not walking the course, so
    `roadmap_scope_filter` grants them nothing -- and a preview that listed
    the challenges would make a forwarded link a way to read a private one."""
    owner = await make_user(db, "Owner")
    guest = await make_user(db, "Guest")
    roadmap = await make_roadmap(db, owner, visibility="unlisted")
    secret = await make_challenge(db, owner, title="راز بزرگ", visibility="private")
    await add_step(db, roadmap, secret)
    invite = await make_invite(db, roadmap, owner)

    sign_in(client, guest)
    preview = (await client.get(f"/roadmap-invites/{invite.code}")).json()
    assert preview["step_count"] == 1
    assert "راز بزرگ" not in str(preview)
    page = await client.get(f"/views/roadmap-invites/{invite.code}")
    assert "راز بزرگ" not in page.text
