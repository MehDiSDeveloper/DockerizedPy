"""Invite links: the capacity is real, and a dead link is not a dead end.

The capacity is the part worth a test of its own. It is enforced by a
conditional ``UPDATE ... WHERE uses < max_uses`` and a check of ``rowcount``
(``accept_invite``), never by reading ``uses``, deciding, and writing back --
which is exactly the race that lets a one-seat link admit two people. The
concurrency test below drives the real app through ``asyncio.gather`` on a
file-backed SQLite (see ``conftest.py`` on why the engine is a file), so it
exercises two genuine transactions rather than two sequential calls.

The other half is the join *request*: the door for somebody whose link
expired or filled up. It is keyed on the invite code and not on a group id,
so it cannot be used to walk the sequential group ids and learn which groups
exist.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select

from app.groups import (
    INVITE_ACTIVE,
    INVITE_EXPIRED,
    INVITE_FULL,
    INVITE_REVOKED,
    invite_state,
)
from app.models.group import (
    GroupInvite,
    GroupJoinRequest,
    GroupMembership,
    GroupRole,
    JoinRequestStatus,
)
from app.models.notification import Notification, NotificationKind
from tests.group_helpers import add_member, make_group, make_user, sign_in


async def make_invite(db, group, creator, **kwargs) -> GroupInvite:
    from app.groups import new_invite_code

    invite = GroupInvite(
        group_id=group.id,
        code=kwargs.pop("code", None) or new_invite_code(),
        uses=kwargs.pop("uses", 0),
        created_by_user_id=creator.id,
        **kwargs,
    )
    db.add(invite)
    await db.commit()
    await db.refresh(invite)
    return invite


# ---------------------------------------------------------------------------
# The derived state
# ---------------------------------------------------------------------------


async def test_invite_state_is_derived_from_the_row(db):
    """Never stored: a stored status would need a job to rewrite it the
    minute a link expired, and a state that disagrees with whether the link
    works is worse than none."""
    owner = await make_user(db, "Owner")
    group = await make_group(db, owner)

    live = await make_invite(db, group, owner, max_uses=5, uses=2)
    assert invite_state(live) == INVITE_ACTIVE

    full = await make_invite(db, group, owner, max_uses=2, uses=2)
    assert invite_state(full) == INVITE_FULL

    past = await make_invite(
        db, group, owner, expires_at=datetime.now(UTC) - timedelta(days=1)
    )
    assert invite_state(past) == INVITE_EXPIRED

    # Revoked wins over expired: it is the administrator's own act, which is
    # the more useful of the two answers.
    dead = await make_invite(
        db,
        group,
        owner,
        expires_at=datetime.now(UTC) - timedelta(days=1),
        revoked_at=datetime.now(UTC),
    )
    assert invite_state(dead) == INVITE_REVOKED


async def test_unlimited_is_a_real_choice(db):
    owner = await make_user(db, "Owner")
    group = await make_group(db, owner)
    invite = await make_invite(db, group, owner, max_uses=None, uses=999)
    assert invite_state(invite) == INVITE_ACTIVE


# ---------------------------------------------------------------------------
# Capacity
# ---------------------------------------------------------------------------


async def test_a_one_seat_link_admits_one_person(client, db):
    owner = await make_user(db, "Owner")
    first = await make_user(db, "First")
    second = await make_user(db, "Second")
    group = await make_group(db, owner)
    invite = await make_invite(db, group, owner, max_uses=1)

    sign_in(client, first)
    assert (await client.post(f"/invites/{invite.code}/accept")).status_code == 201

    sign_in(client, second)
    res = await client.post(f"/invites/{invite.code}/accept")
    assert res.status_code == 409

    count = (
        await db.execute(
            select(func.count())
            .select_from(GroupMembership)
            .where(GroupMembership.group_id == group.id)
        )
    ).scalar_one()
    assert count == 2  # the owner and the first joiner, and nobody else


async def test_capacity_holds_under_a_concurrent_race(client, db):
    """Two people tapping a one-seat link at the same instant.

    This is the test the conditional UPDATE exists for: a read-then-write
    implementation passes the sequential test above and fails this one.
    """
    owner = await make_user(db, "Owner")
    group = await make_group(db, owner)
    invite = await make_invite(db, group, owner, max_uses=1)
    racers = [await make_user(db, f"Racer {i}") for i in range(4)]

    from app.auth import create_session_cookie

    async def attempt(user):
        return await client.post(
            f"/invites/{invite.code}/accept",
            cookies={"session": create_session_cookie(user.id)},
        )

    results = await asyncio.gather(*(attempt(u) for u in racers))
    accepted = [r for r in results if r.status_code == 201]
    assert len(accepted) == 1

    group_id, code = group.id, invite.code
    members = (
        await db.execute(
            select(func.count())
            .select_from(GroupMembership)
            .where(GroupMembership.group_id == group_id)
        )
    ).scalar_one()
    assert members == 2

    db.expire_all()
    fresh = (
        await db.execute(select(GroupInvite).where(GroupInvite.code == code))
    ).scalar_one()
    assert fresh.uses == 1


async def test_a_revoked_link_lets_nobody_in(client, db):
    owner = await make_user(db, "Owner")
    joiner = await make_user(db, "Joiner")
    group = await make_group(db, owner)
    invite = await make_invite(db, group, owner)

    sign_in(client, owner)
    assert (
        await client.delete(f"/groups/{group.id}/invites/{invite.id}")
    ).status_code == 204

    sign_in(client, joiner)
    assert (await client.post(f"/invites/{invite.code}/accept")).status_code == 409


async def test_an_expired_link_lets_nobody_in(client, db):
    owner = await make_user(db, "Owner")
    joiner = await make_user(db, "Joiner")
    group = await make_group(db, owner)
    invite = await make_invite(
        db, group, owner, expires_at=datetime.now(UTC) - timedelta(minutes=1)
    )

    sign_in(client, joiner)
    assert (await client.post(f"/invites/{invite.code}/accept")).status_code == 409


async def test_re_using_your_own_link_spends_no_seat(client, db):
    """Somebody re-opening the link they joined with has not joined twice."""
    owner = await make_user(db, "Owner")
    joiner = await make_user(db, "Joiner")
    group = await make_group(db, owner)
    invite = await make_invite(db, group, owner, max_uses=2)

    sign_in(client, joiner)
    assert (await client.post(f"/invites/{invite.code}/accept")).status_code == 201
    assert (await client.post(f"/invites/{invite.code}/accept")).status_code == 201

    code = invite.code
    db.expire_all()
    fresh = (
        await db.execute(select(GroupInvite).where(GroupInvite.code == code))
    ).scalar_one()
    assert fresh.uses == 1


# ---------------------------------------------------------------------------
# Who may make and see links
# ---------------------------------------------------------------------------


async def test_only_managers_create_and_list_links(client, db):
    owner = await make_user(db, "Owner")
    admin = await make_user(db, "Admin")
    member = await make_user(db, "Member")
    group = await make_group(db, owner)
    await add_member(db, group, admin, role=GroupRole.ADMIN.value)
    await add_member(db, group, member)

    sign_in(client, member)
    assert (
        await client.post(f"/groups/{group.id}/invites", json={"max_uses": 5})
    ).status_code == 403
    assert (await client.get(f"/groups/{group.id}/invites")).status_code == 403

    sign_in(client, admin)
    res = await client.post(f"/groups/{group.id}/invites", json={"max_uses": 5})
    assert res.status_code == 201
    body = res.json()
    assert body["state"] == INVITE_ACTIVE
    assert body["uses"] == 0
    assert len(body["code"]) >= 20  # unguessable, not typed


async def test_the_preview_shows_the_group_and_nothing_else(client, db):
    """Whoever holds the code is not a member yet, so an unused invite must
    not become a way to read the group."""
    owner = await make_user(db, "Owner")
    outsider = await make_user(db, "Outsider")
    group = await make_group(db, owner)
    invite = await make_invite(db, group, owner)

    sign_in(client, outsider)
    body = (await client.get(f"/invites/{invite.code}")).json()
    assert set(body) == {
        "group_id",
        "name",
        "description",
        "kind",
        "emblem",
        "member_count",
        "state",
        # Whether this link admits or only lets somebody ask -- a fact about
        # the link, not about the group, which is why it may be here.
        "requires_approval",
        "already_member",
        "request_pending",
    }


# ---------------------------------------------------------------------------
# Join requests
# ---------------------------------------------------------------------------


async def test_a_full_link_still_offers_a_request(client, db):
    owner = await make_user(db, "Owner")
    hopeful = await make_user(db, "Hopeful")
    group = await make_group(db, owner)
    invite = await make_invite(db, group, owner, max_uses=1, uses=1)

    sign_in(client, hopeful)
    assert (await client.post(f"/invites/{invite.code}/accept")).status_code == 409

    res = await client.post(f"/invites/{invite.code}/request")
    assert res.status_code == 201
    assert res.json()["status"] == JoinRequestStatus.PENDING.value

    # The administrators are told -- it is the one thing on the manage screen
    # with somebody waiting on the other end.
    kinds = (
        await db.execute(
            select(Notification.kind).where(Notification.user_id == owner.id)
        )
    ).scalars().all()
    assert NotificationKind.GROUP_JOIN_REQUESTED.value in kinds


async def test_asking_twice_is_the_same_request(client, db):
    owner = await make_user(db, "Owner")
    hopeful = await make_user(db, "Hopeful")
    group = await make_group(db, owner)
    invite = await make_invite(db, group, owner, max_uses=1, uses=1)

    sign_in(client, hopeful)
    first = await client.post(f"/invites/{invite.code}/request")
    second = await client.post(f"/invites/{invite.code}/request")
    assert first.json()["id"] == second.json()["id"]

    count = (
        await db.execute(
            select(func.count())
            .select_from(GroupJoinRequest)
            .where(GroupJoinRequest.group_id == group.id)
        )
    ).scalar_one()
    assert count == 1


async def test_approving_admits_and_tells_the_asker(client, db):
    owner = await make_user(db, "Owner")
    hopeful = await make_user(db, "Hopeful")
    group = await make_group(db, owner)
    invite = await make_invite(db, group, owner, max_uses=1, uses=1)

    sign_in(client, hopeful)
    request_id = (await client.post(f"/invites/{invite.code}/request")).json()["id"]

    sign_in(client, owner)
    res = await client.post(f"/groups/{group.id}/requests/{request_id}/approve")
    assert res.status_code == 200
    assert res.json()["status"] == JoinRequestStatus.APPROVED.value

    joined = (
        await db.execute(
            select(GroupMembership).where(
                GroupMembership.group_id == group.id,
                GroupMembership.user_id == hopeful.id,
            )
        )
    ).scalar_one_or_none()
    assert joined is not None

    kinds = (
        await db.execute(
            select(Notification.kind).where(Notification.user_id == hopeful.id)
        )
    ).scalars().all()
    assert NotificationKind.GROUP_JOIN_APPROVED.value in kinds


async def test_rejecting_keeps_the_record_and_admits_nobody(client, db):
    owner = await make_user(db, "Owner")
    hopeful = await make_user(db, "Hopeful")
    group = await make_group(db, owner)
    invite = await make_invite(db, group, owner, max_uses=1, uses=1)

    sign_in(client, hopeful)
    request_id = (await client.post(f"/invites/{invite.code}/request")).json()["id"]

    sign_in(client, owner)
    res = await client.post(f"/groups/{group.id}/requests/{request_id}/reject")
    assert res.json()["status"] == JoinRequestStatus.REJECTED.value

    # Deciding twice is refused rather than overwriting: two administrators
    # tapping the same row must not silently disagree.
    again = await client.post(f"/groups/{group.id}/requests/{request_id}/approve")
    assert again.status_code == 409

    members = (
        await db.execute(
            select(func.count())
            .select_from(GroupMembership)
            .where(GroupMembership.group_id == group.id)
        )
    ).scalar_one()
    assert members == 1


async def test_a_plain_member_cannot_see_or_decide_requests(client, db):
    owner = await make_user(db, "Owner")
    member = await make_user(db, "Member")
    hopeful = await make_user(db, "Hopeful")
    group = await make_group(db, owner)
    await add_member(db, group, member)
    invite = await make_invite(db, group, owner, max_uses=1, uses=1)

    sign_in(client, hopeful)
    request_id = (await client.post(f"/invites/{invite.code}/request")).json()["id"]

    sign_in(client, member)
    assert (await client.get(f"/groups/{group.id}/requests")).status_code == 403
    assert (
        await client.post(f"/groups/{group.id}/requests/{request_id}/approve")
    ).status_code == 403
