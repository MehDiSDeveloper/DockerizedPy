"""Leaving: the two exits, and the line between walking out and being removed.

Both new doors give up enrollments in bulk, so what is pinned here is the
part that decides *whose* rows may go and *which*:

* **the bulk unenrol keeps the obligations.** Asked from inside the group,
  ``still_in_group`` is true, so a mandatory challenge stays and is named
  back to the member rather than silently skipped.
* **leaving the group releases everything**, mandatory included: the
  obligation belonged to the membership that is ending. That is precisely
  what the confirmation on the group page promises, and the promise and the
  write are one function.
* **being removed is not the same act.** An administrator's removal leaves
  the enrollments alone -- destroying what another person logged is not a
  power running a group buys -- which is the rule ``app/groups.py`` has had
  from the start, now stated against the one route that could break it.
* **it is your own rows and nobody else's.** There is no subject in the
  path, so there is nothing to point at somebody else.
* **the counters come down** and **the owners are told**, because a bulk
  leave is the same act ``DELETE /enrollments/{id}`` performs, repeated --
  not a shortcut around it.
"""

from __future__ import annotations

from datetime import date

from sqlalchemy import select

from app.models.challenge import GroupAudience, ParticipationMode, Visibility
from app.models.enrollment import Enrollment
from app.models.group import GroupMembership, GroupRole
from app.models.notification import Notification, NotificationKind
from app.models.stats import ChallengeStats
from tests.group_helpers import (
    add_member,
    make_challenge,
    make_group,
    make_user,
    sign_in,
)


async def _enrol(db, challenge, user) -> Enrollment:
    enrollment = Enrollment(
        challenge_id=challenge.id,
        user_id=user.id,
        start_date=date(2026, 1, 1),
    )
    db.add(enrollment)
    stats = (
        await db.execute(
            select(ChallengeStats).where(
                ChallengeStats.challenge_id == challenge.id
            )
        )
    ).scalar_one()
    stats.participant_count += 1
    await db.commit()
    return enrollment


async def my_challenge_ids(db, user_id: int) -> set[int]:
    return set(
        (
            await db.execute(
                select(Enrollment.challenge_id).where(Enrollment.user_id == user_id)
            )
        )
        .scalars()
        .all()
    )


# ---------------------------------------------------------------------------
# The bulk unenrol
# ---------------------------------------------------------------------------


async def test_bulk_leave_gives_up_every_optional_challenge(client, db):
    owner = await make_user(db, "Owner")
    member = await make_user(db, "Member")
    group = await make_group(db, owner)
    await add_member(db, group, member)
    first = await make_challenge(db, owner, group=group, title="اول")
    second = await make_challenge(db, owner, group=group, title="دوم")
    await _enrol(db, first, member)
    await _enrol(db, second, member)

    sign_in(client, member)
    res = await client.delete(f"/groups/{group.id}/enrollments")
    assert res.status_code == 200
    assert sorted(res.json()["left"]) == ["اول", "دوم"]
    assert res.json()["kept"] == []
    assert await my_challenge_ids(db, member.id) == set()


async def test_bulk_leave_keeps_a_mandatory_challenge_and_names_it(client, db):
    """Named, not silently skipped: a member who presses «همه» and finds one
    still there has to be told which, and why."""
    owner = await make_user(db, "Owner")
    member = await make_user(db, "Member")
    group = await make_group(db, owner)
    await add_member(db, group, member)
    optional = await make_challenge(db, owner, group=group, title="اختیاری")
    required = await make_challenge(
        db,
        owner,
        group=group,
        title="اجباری",
        participation=ParticipationMode.MANDATORY.value,
    )
    await _enrol(db, optional, member)
    await _enrol(db, required, member)

    sign_in(client, member)
    res = await client.delete(f"/groups/{group.id}/enrollments")
    assert res.status_code == 200
    assert res.json()["left"] == ["اختیاری"]
    assert res.json()["kept"] == ["اجباری"]
    assert await my_challenge_ids(db, member.id) == {required.id}


async def test_bulk_leave_touches_only_this_group(client, db):
    """A personal challenge and another group's are not this group's business."""
    owner = await make_user(db, "Owner")
    member = await make_user(db, "Member")
    group = await make_group(db, owner)
    other = await make_group(db, owner, name="گروه دیگر")
    await add_member(db, group, member)
    await add_member(db, other, member)
    here = await make_challenge(db, owner, group=group)
    elsewhere = await make_challenge(db, owner, group=other)
    personal = await make_challenge(db, owner)
    for challenge in (here, elsewhere, personal):
        await _enrol(db, challenge, member)

    sign_in(client, member)
    assert (await client.delete(f"/groups/{group.id}/enrollments")).status_code == 200
    assert await my_challenge_ids(db, member.id) == {elsewhere.id, personal.id}


async def test_bulk_leave_is_only_ever_your_own_rows(client, db):
    """There is no subject in the path, so an administrator running it gives
    up *their* enrollments -- everybody else's are untouched."""
    owner = await make_user(db, "Owner")
    member = await make_user(db, "Member")
    group = await make_group(db, owner)
    await add_member(db, group, member, role=GroupRole.ADMIN.value)
    challenge = await make_challenge(db, owner, group=group)
    await _enrol(db, challenge, member)
    victim = await make_user(db, "Victim")
    await add_member(db, group, victim)
    await _enrol(db, challenge, victim)

    sign_in(client, member)
    assert (await client.delete(f"/groups/{group.id}/enrollments")).status_code == 200
    assert await my_challenge_ids(db, member.id) == set()
    assert await my_challenge_ids(db, victim.id) == {challenge.id}


async def test_bulk_leave_404s_a_stranger(client, db):
    """The group's own gate, unchanged: a group you are not in is not there."""
    owner = await make_user(db, "Owner")
    outsider = await make_user(db, "Outsider")
    group = await make_group(db, owner)

    sign_in(client, outsider)
    assert (await client.delete(f"/groups/{group.id}/enrollments")).status_code == 404


async def test_bulk_leave_with_nothing_to_leave_is_a_200(client, db):
    owner = await make_user(db, "Owner")
    member = await make_user(db, "Member")
    group = await make_group(db, owner)
    await add_member(db, group, member)

    sign_in(client, member)
    res = await client.delete(f"/groups/{group.id}/enrollments")
    assert res.status_code == 200
    assert res.json() == {"left": [], "kept": [], "left_group": False}


async def test_bulk_leave_brings_the_counter_down(client, db):
    """The same act `unenroll` performs, so the same counter moves."""
    owner = await make_user(db, "Owner")
    member = await make_user(db, "Member")
    group = await make_group(db, owner)
    await add_member(db, group, member)
    challenge = await make_challenge(db, owner, group=group)
    await _enrol(db, challenge, member)

    sign_in(client, member)
    await client.delete(f"/groups/{group.id}/enrollments")
    stats = (
        await db.execute(
            select(ChallengeStats).where(
                ChallengeStats.challenge_id == challenge.id
            )
        )
    ).scalar_one()
    await db.refresh(stats)
    assert stats.participant_count == 1  # the owner's own


async def test_bulk_leave_still_tells_each_owner(client, db):
    """Twenty departures, not one: the owners are different people, and a
    bulk action that stopped notifying them would be a way to slip out."""
    owner = await make_user(db, "Owner")
    member = await make_user(db, "Member")
    group = await make_group(db, owner)
    await add_member(db, group, member)
    challenge = await make_challenge(
        db, owner, group=group, visibility=Visibility.PRIVATE.value
    )
    await _enrol(db, challenge, member)

    sign_in(client, member)
    await client.delete(f"/groups/{group.id}/enrollments")
    kinds = (
        (
            await db.execute(
                select(Notification.kind).where(Notification.user_id == owner.id)
            )
        )
        .scalars()
        .all()
    )
    assert NotificationKind.ENROLLMENT_LEFT.value in kinds


# ---------------------------------------------------------------------------
# Leaving the group
# ---------------------------------------------------------------------------


async def test_leaving_the_group_releases_every_challenge(client, db):
    """Including the mandatory one: the obligation belonged to the membership
    that is ending, which is exactly what the confirmation promises."""
    owner = await make_user(db, "Owner")
    member = await make_user(db, "Member")
    group = await make_group(db, owner)
    await add_member(db, group, member)
    optional = await make_challenge(db, owner, group=group, title="اختیاری")
    required = await make_challenge(
        db,
        owner,
        group=group,
        title="اجباری",
        audience=GroupAudience.ALL.value,
        participation=ParticipationMode.MANDATORY.value,
    )
    await _enrol(db, optional, member)
    await _enrol(db, required, member)

    sign_in(client, member)
    res = await client.delete(f"/groups/{group.id}/members/{member.id}")
    assert res.status_code == 204
    assert await my_challenge_ids(db, member.id) == set()
    gone = (
        await db.execute(
            select(GroupMembership).where(
                GroupMembership.group_id == group.id,
                GroupMembership.user_id == member.id,
            )
        )
    ).scalar_one_or_none()
    assert gone is None


async def test_leaving_the_group_leaves_other_challenges_alone(client, db):
    owner = await make_user(db, "Owner")
    member = await make_user(db, "Member")
    group = await make_group(db, owner)
    await add_member(db, group, member)
    personal = await make_challenge(db, owner)
    await _enrol(db, personal, member)

    sign_in(client, member)
    assert (
        await client.delete(f"/groups/{group.id}/members/{member.id}")
    ).status_code == 204
    assert await my_challenge_ids(db, member.id) == {personal.id}


async def test_being_removed_keeps_the_enrollments(client, db):
    """The other half of the split, and the older rule: destroying what
    another person logged is not a power running a group buys."""
    owner = await make_user(db, "Owner")
    member = await make_user(db, "Member")
    group = await make_group(db, owner)
    await add_member(db, group, member)
    challenge = await make_challenge(db, owner, group=group)
    await _enrol(db, challenge, member)

    sign_in(client, owner)
    assert (
        await client.delete(f"/groups/{group.id}/members/{member.id}")
    ).status_code == 204
    assert await my_challenge_ids(db, member.id) == {challenge.id}


async def test_being_removed_makes_a_mandatory_challenge_leavable(client, db):
    """Unchanged, and it is what makes the removal humane: they were given
    the choice rather than had it made for them."""
    owner = await make_user(db, "Owner")
    member = await make_user(db, "Member")
    group = await make_group(db, owner)
    await add_member(db, group, member)
    challenge = await make_challenge(
        db,
        owner,
        group=group,
        participation=ParticipationMode.MANDATORY.value,
    )
    await _enrol(db, challenge, member)

    sign_in(client, owner)
    await client.delete(f"/groups/{group.id}/members/{member.id}")
    sign_in(client, member)
    assert (await client.delete(f"/enrollments/{challenge.id}")).status_code == 204


async def test_the_owner_still_cannot_leave(client, db):
    """The bulk release runs after the owner rule, not instead of it."""
    owner = await make_user(db, "Owner")
    member = await make_user(db, "Member")
    group = await make_group(db, owner)
    await add_member(db, group, member)
    challenge = await make_challenge(db, owner, group=group)

    sign_in(client, owner)
    assert (
        await client.delete(f"/groups/{group.id}/members/{owner.id}")
    ).status_code == 409
    assert await my_challenge_ids(db, owner.id) == {challenge.id}


# ---------------------------------------------------------------------------
# The page
# ---------------------------------------------------------------------------


async def test_the_about_panel_offers_the_two_exits(client, db):
    """And says what the second one costs -- the sentence is the feature."""
    owner = await make_user(db, "Owner")
    member = await make_user(db, "Member")
    group = await make_group(db, owner)
    await add_member(db, group, member)
    challenge = await make_challenge(db, owner, group=group)
    await _enrol(db, challenge, member)

    sign_in(client, member)
    body = (await client.get(f"/views/groups/{group.id}")).text
    assert 'id="grLeaveChallenges"' in body
    assert 'id="grLeaveGroup"' in body
    assert "ترک می‌شود" in body


async def test_the_owner_is_offered_the_transfer_instead_of_a_leave(client, db):
    """A control that would answer 409 is a control that lies, so it is not
    drawn -- the same call the login screen's resend countdown makes."""
    owner = await make_user(db, "Owner")
    group = await make_group(db, owner)

    sign_in(client, owner)
    body = (await client.get(f"/views/groups/{group.id}")).text
    # The row, not the handler: the script is emitted on every group page and
    # guards itself, so what must be absent is the control.
    assert 'id="grLeaveGroup"' not in body
    assert f"/views/groups/{group.id}/manage" in body
