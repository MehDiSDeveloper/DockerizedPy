"""Group notifications: what is raised, what is not, and what is not said.

The bar this feed is held to is "a place a member could not otherwise find
out", and the group events are all of that shape: somebody else approves you,
promotes you, hands you the group, or puts you in a challenge. Joining by
invite link raises nothing at all -- the joiner did it themselves and is
looking at the result.

Everything here still goes through the one door, so the four invariants hold
without any group call site restating them: no self-notification, one
transaction, the mute list, and the anonymising. The two assignment kinds are
separate rather than one kind with an adjective, which is what makes them
separately mutable and what keeps the row storing an event rather than a
sentence.
"""

from __future__ import annotations

from sqlalchemy import select

from app.models.challenge import GroupAudience, ParticipationMode
from app.models.group import GroupInvite, GroupRole
from app.models.notification import Notification, NotificationKind
from app.notifications import (
    NOTIFICATION_META,
    notification_settings,
    notification_text,
)
from tests.group_helpers import (
    add_member,
    make_group,
    make_user,
    sign_in,
)


async def kinds_for(db, user_id: int) -> list[str]:
    return list(
        (
            await db.execute(
                select(Notification.kind).where(Notification.user_id == user_id)
            )
        )
        .scalars()
        .all()
    )


async def make_invite_code(db, group, owner) -> str:
    from app.groups import new_invite_code

    invite = GroupInvite(
        group_id=group.id, code=new_invite_code(), created_by_user_id=owner.id
    )
    db.add(invite)
    await db.commit()
    return invite.code


# ---------------------------------------------------------------------------
# The map
# ---------------------------------------------------------------------------


def test_every_group_kind_has_wording_and_a_switch():
    """A kind cannot ship without a switch, and the switch cannot describe
    something other than the notification it governs -- both come from the
    same map."""
    group_kinds = [k.value for k in NotificationKind if k.value.startswith("group_")]
    assert group_kinds
    for kind in group_kinds:
        meta = NOTIFICATION_META[kind]
        assert meta["title"] and meta["text"] and meta["icon"] and meta["hint"]


def test_the_two_assignment_kinds_are_separate():
    """One kind with a stored adjective would be a kind that cannot be
    reworded, filtered, counted -- or silenced on its own."""
    optional = NOTIFICATION_META[NotificationKind.GROUP_CHALLENGE_ASSIGNED.value]
    required = NOTIFICATION_META[NotificationKind.GROUP_CHALLENGE_REQUIRED.value]
    assert optional["text"] != required["text"]
    assert "اختیاری" in optional["text"]
    assert "خارج" in required["text"]


async def test_the_sentence_names_the_group_from_the_row(db):
    """No `title`/`body` column: the words are derived, so rewording one is a
    code change rather than a migration over every row ever written."""
    owner = await make_user(db, "Owner")
    actor = await make_user(db, "Somebody")
    group = await make_group(db, owner, name="شرکت آبی")
    notification = Notification(
        user_id=owner.id,
        kind=NotificationKind.GROUP_JOIN_REQUESTED.value,
        actor_user_id=actor.id,
        group_id=group.id,
    )
    db.add(notification)
    await db.commit()

    loaded = (
        await db.execute(
            select(Notification).where(Notification.id == notification.id)
        )
    ).scalar_one()
    await db.refresh(loaded, ["actor", "group", "challenge"])
    text = notification_text(loaded)
    assert "شرکت آبی" in text
    assert "Somebody" in text


# ---------------------------------------------------------------------------
# What is raised
# ---------------------------------------------------------------------------


async def test_being_put_into_a_challenge_says_which_kind_it_is(client, db):
    owner = await make_user(db, "Owner")
    employee = await make_user(db, "Employee")
    group = await make_group(db, owner)
    await add_member(db, group, employee)

    sign_in(client, owner)
    await client.post(
        "/challenges/",
        json={
            "title": "چالش اجباری",
            "cadence": {"kind": "once"},
            "group_id": group.id,
            "group_audience": GroupAudience.ALL.value,
            "participation_mode": ParticipationMode.MANDATORY.value,
        },
    )
    assert NotificationKind.GROUP_CHALLENGE_REQUIRED.value in await kinds_for(
        db, employee.id
    )

    await client.post(
        "/challenges/",
        json={
            "title": "چالش اختیاری",
            "cadence": {"kind": "once"},
            "group_id": group.id,
            "group_audience": GroupAudience.ALL.value,
            "participation_mode": ParticipationMode.OPTIONAL.value,
        },
    )
    assert NotificationKind.GROUP_CHALLENGE_ASSIGNED.value in await kinds_for(
        db, employee.id
    )


async def test_the_creator_is_not_told_about_their_own_challenge(client, db):
    """Invariant one, for free: `notify` drops a notification whose recipient
    is its actor, so no group call site has to remember it."""
    owner = await make_user(db, "Owner")
    group = await make_group(db, owner)

    sign_in(client, owner)
    await client.post(
        "/challenges/",
        json={
            "title": "چالش من",
            "cadence": {"kind": "once"},
            "group_id": group.id,
            "group_audience": GroupAudience.ALL.value,
        },
    )
    assert await kinds_for(db, owner.id) == []


async def test_a_role_change_and_a_transfer_reach_the_member(client, db):
    owner = await make_user(db, "Owner")
    member = await make_user(db, "Member")
    group = await make_group(db, owner)
    await add_member(db, group, member)

    sign_in(client, owner)
    await client.patch(
        f"/groups/{group.id}/members/{member.id}", json={"role": "admin"}
    )
    assert NotificationKind.GROUP_ROLE_CHANGED.value in await kinds_for(db, member.id)

    await client.post(
        f"/groups/{group.id}/transfer", json={"new_owner_user_id": member.id}
    )
    assert NotificationKind.GROUP_OWNERSHIP_TRANSFERRED.value in await kinds_for(
        db, member.id
    )


async def test_a_role_change_that_changes_nothing_says_nothing(client, db):
    owner = await make_user(db, "Owner")
    admin = await make_user(db, "Admin")
    group = await make_group(db, owner)
    await add_member(db, group, admin, role=GroupRole.ADMIN.value)

    sign_in(client, owner)
    await client.patch(
        f"/groups/{group.id}/members/{admin.id}", json={"role": "admin"}
    )
    assert await kinds_for(db, admin.id) == []


async def test_joining_by_link_raises_nothing(client, db):
    """The joiner did it themselves and is looking at the result; the
    administrators handed the link out. Neither learns anything from a
    notification, and a feed of them buries the ones that matter."""
    owner = await make_user(db, "Owner")
    joiner = await make_user(db, "Joiner")
    group = await make_group(db, owner)
    code = await make_invite_code(db, group, owner)

    sign_in(client, joiner)
    await client.post(f"/invites/{code}/accept")
    assert await kinds_for(db, owner.id) == []
    assert await kinds_for(db, joiner.id) == []


# ---------------------------------------------------------------------------
# The mute list
# ---------------------------------------------------------------------------


async def test_a_muted_group_kind_stops_arriving(client, db):
    """The check lives in the one door in, so a group call site cannot forget
    it -- which is the whole reason `notify` is where it is."""
    owner = await make_user(db, "Owner")
    employee = await make_user(db, "Employee")
    group = await make_group(db, owner)
    await add_member(db, group, employee)

    sign_in(client, employee)
    res = await client.put(
        "/notifications/prefs",
        json={
            "kind": NotificationKind.GROUP_CHALLENGE_ASSIGNED.value,
            "enabled": False,
        },
    )
    assert res.status_code == 200

    sign_in(client, owner)
    await client.post(
        "/challenges/",
        json={
            "title": "چالش اختیاری",
            "cadence": {"kind": "once"},
            "group_id": group.id,
            "group_audience": GroupAudience.ALL.value,
        },
    )
    assert await kinds_for(db, employee.id) == []

    # The mandatory kind is a different switch and still arrives -- which is
    # the pair anybody would actually want.
    await client.post(
        "/challenges/",
        json={
            "title": "چالش اجباری",
            "cadence": {"kind": "once"},
            "group_id": group.id,
            "group_audience": GroupAudience.ALL.value,
            "participation_mode": ParticipationMode.MANDATORY.value,
        },
    )
    assert await kinds_for(db, employee.id) == [
        NotificationKind.GROUP_CHALLENGE_REQUIRED.value
    ]


async def test_the_settings_screen_grows_a_switch_per_group_kind(db):
    user = await make_user(db, "Member")
    switches = {row["kind"] for row in notification_settings(user)}
    for kind in NotificationKind:
        assert kind.value in switches


async def test_the_notification_page_renders_a_group_row(client, db):
    """The feed's renderers reach the group relationship -- a missing
    `selectinload` here would be a MissingGreenlet at render time, not a
    query error (CLAUDE.md)."""
    owner = await make_user(db, "Owner")
    actor = await make_user(db, "Somebody")
    group = await make_group(db, owner, name="شرکت آبی")
    db.add(
        Notification(
            user_id=owner.id,
            kind=NotificationKind.GROUP_JOIN_REQUESTED.value,
            actor_user_id=actor.id,
            group_id=group.id,
        )
    )
    await db.commit()

    sign_in(client, owner)
    page = await client.get("/views/notifications/")
    assert page.status_code == 200
    assert "شرکت آبی" in page.text
