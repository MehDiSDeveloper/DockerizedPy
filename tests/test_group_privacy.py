"""Privacy inside a group: anonymity holds, and administering is not access.

The requirement this file exists for is the corporate one: an employee must
not have their number appear beside their name because somebody else put them
in a challenge. Three rules carry it, and each is a separate way it could
fail:

* **somebody who was never asked is not named.** A group administrator writes
  the enrolment, so ``resolve_assigned_anonymity`` -- not
  ``resolve_anonymity`` -- decides, and for ``member_choice`` its answer is
  the *private* one. The member changes it through a door of its own, which
  runs the answer back through the challenge's own policy.
* **no new surface leaks a name.** The leaderboard and the participant
  avatars go through ``participant_display`` / ``participant_avatar``
  regardless of whether the challenge belongs to a group, and the group's own
  screens add no fourth surface that prints a name beside a number.
* **running a group is not reach over its members.** ``GROUP_MANAGE_MEMBERS``
  buys deciding who is in a challenge and nothing about anybody's account:
  ``profile_visibility_filter`` is untouched, so a group administrator gets
  the same 404 on a member's profile that any other member does.
"""

from __future__ import annotations

from datetime import date

from sqlalchemy import select

from app.identity import ANONYMOUS_NAME, resolve_assigned_anonymity
from app.models.challenge import GroupAudience, IdentityMode
from app.models.enrollment import Enrollment
from app.models.group import GroupRole
from tests.group_helpers import (
    add_member,
    make_challenge,
    make_group,
    make_user,
    sign_in,
)


async def enrollment_of(db, challenge_id: int, user_id: int) -> Enrollment:
    return (
        await db.execute(
            select(Enrollment).where(
                Enrollment.challenge_id == challenge_id,
                Enrollment.user_id == user_id,
            )
        )
    ).scalar_one()


# ---------------------------------------------------------------------------
# The un-asked default
# ---------------------------------------------------------------------------


def test_assignment_defaults_member_choice_to_anonymous():
    """The one case where the two resolutions differ, and why.

    "Was not asked" and "answered no" are not the same answer, and this is
    the whole difference between the two functions.
    """
    assert resolve_assigned_anonymity(IdentityMode.MEMBER_CHOICE.value) is True
    # The other two answer for everyone; an assignment does not make the
    # challenge a different challenge.
    assert resolve_assigned_anonymity(IdentityMode.NAMED.value) is False
    assert resolve_assigned_anonymity(IdentityMode.ANONYMOUS.value) is True


async def test_an_assigned_member_is_stored_anonymous(client, db):
    owner = await make_user(db, "Owner")
    employee = await make_user(db, "Employee")
    group = await make_group(db, owner)
    await add_member(db, group, employee)

    sign_in(client, owner)
    challenge_id = (
        await client.post(
            "/challenges/",
            json={
                "title": "چالش سلامت سازمانی",
                "cadence": {"kind": "once"},
                "group_id": group.id,
                "group_audience": GroupAudience.ALL.value,
                "identity_mode": IdentityMode.MEMBER_CHOICE.value,
            },
        )
    ).json()["id"]

    assert (await enrollment_of(db, challenge_id, employee.id)).is_anonymous is True
    # The creator is never anonymous -- authorship is not participation, and
    # challenge-detail names them «سازنده» in every mode.
    assert (await enrollment_of(db, challenge_id, owner.id)).is_anonymous is False


async def test_a_named_group_challenge_still_names_everyone(client, db):
    """The policy decides. An assignment is not a way to make people
    anonymous in a challenge that names them."""
    owner = await make_user(db, "Owner")
    employee = await make_user(db, "Employee")
    group = await make_group(db, owner)
    await add_member(db, group, employee)

    sign_in(client, owner)
    challenge_id = (
        await client.post(
            "/challenges/",
            json={
                "title": "چالش با نام",
                "cadence": {"kind": "once"},
                "group_id": group.id,
                "group_audience": GroupAudience.ALL.value,
                "identity_mode": IdentityMode.NAMED.value,
            },
        )
    ).json()["id"]
    assert (await enrollment_of(db, challenge_id, employee.id)).is_anonymous is False


# ---------------------------------------------------------------------------
# Changing your mind
# ---------------------------------------------------------------------------


async def test_the_member_may_change_their_own_answer(client, db):
    owner = await make_user(db, "Owner")
    employee = await make_user(db, "Employee")
    group = await make_group(db, owner)
    await add_member(db, group, employee)
    challenge = await make_challenge(
        db,
        owner,
        group=group,
        identity_mode=IdentityMode.MEMBER_CHOICE.value,
    )
    db.add(
        Enrollment(
            challenge_id=challenge.id,
            user_id=employee.id,
            start_date=date(2026, 1, 1),
            is_anonymous=True,
        )
    )
    await db.commit()

    sign_in(client, employee)
    res = await client.patch(
        f"/enrollments/{challenge.id}/anonymity", json={"is_anonymous": False}
    )
    assert res.status_code == 200
    assert res.json()["is_anonymous"] is False
    assert (await enrollment_of(db, challenge.id, employee.id)).is_anonymous is False


async def test_the_door_does_not_bypass_the_challenge_policy(client, db):
    """409, not a silent no-op: unlike a join -- where "hide me" is simply
    not the question that challenge asks -- this request has no other
    purpose, and answering 200-with-nothing-changed would leave the member
    believing something the row does not say."""
    owner = await make_user(db, "Owner")
    member = await make_user(db, "Member")
    challenge = await make_challenge(
        db, owner, identity_mode=IdentityMode.NAMED.value
    )
    db.add(
        Enrollment(
            challenge_id=challenge.id,
            user_id=member.id,
            start_date=date(2026, 1, 1),
        )
    )
    await db.commit()

    sign_in(client, member)
    res = await client.patch(
        f"/enrollments/{challenge.id}/anonymity", json={"is_anonymous": True}
    )
    assert res.status_code == 409
    assert (await enrollment_of(db, challenge.id, member.id)).is_anonymous is False


async def test_nobody_can_change_somebody_elses_answer(client, db):
    """There is no client-supplied `user_id` anywhere in that module -- the
    route is keyed on the session user, like every other enrollment route."""
    owner = await make_user(db, "Owner")
    member = await make_user(db, "Member")
    group = await make_group(db, owner)
    await add_member(db, group, member)
    challenge = await make_challenge(
        db, owner, group=group, identity_mode=IdentityMode.MEMBER_CHOICE.value
    )
    db.add(
        Enrollment(
            challenge_id=challenge.id,
            user_id=member.id,
            start_date=date(2026, 1, 1),
            is_anonymous=True,
        )
    )
    await db.commit()

    # The group's owner PATCHes the same URL: it can only ever reach *their
    # own* enrolment, which is the owner's -- the member's row is untouched.
    sign_in(client, owner)
    await client.patch(
        f"/enrollments/{challenge.id}/anonymity", json={"is_anonymous": False}
    )
    assert (await enrollment_of(db, challenge.id, member.id)).is_anonymous is True


# ---------------------------------------------------------------------------
# The surfaces
# ---------------------------------------------------------------------------


async def test_the_leaderboard_hides_an_anonymous_group_participant(client, db):
    """No exception for group challenges: an anonymity promise honoured only
    by the surfaces that remembered it is not a promise."""
    owner = await make_user(db, "Owner")
    hidden = await make_user(db, "Hidden Employee")
    watcher = await make_user(db, "Watcher")
    group = await make_group(db, owner)
    await add_member(db, group, hidden)
    await add_member(db, group, watcher)
    challenge = await make_challenge(
        db, owner, group=group, identity_mode=IdentityMode.MEMBER_CHOICE.value
    )
    for user, anon in ((hidden, True), (watcher, False)):
        db.add(
            Enrollment(
                challenge_id=challenge.id,
                user_id=user.id,
                start_date=date(2026, 1, 1),
                is_anonymous=anon,
            )
        )
    await db.commit()

    sign_in(client, watcher)
    page = await client.get(f"/views/challenges/{challenge.id}/leaderboard")
    assert page.status_code == 200
    assert "Hidden Employee" not in page.text
    # Still ranked -- dropping the row would let everyone derive who is
    # missing. Hiding the name is the promise, not withholding the score.
    assert ANONYMOUS_NAME in page.text


async def test_a_group_admin_gets_no_reach_into_a_profile(client, db):
    """``profile_visibility_filter`` is untouched by this feature.

    A group administrator sees a roster of names and pictures, which is what
    a roster is; an account is not theirs to open, and the 404 is the same
    one any other member gets.
    """
    owner = await make_user(db, "Owner")
    member = await make_user(db, "Member")
    group = await make_group(db, owner)
    await add_member(db, group, member)

    sign_in(client, owner)
    assert (await client.get(f"/users/{member.id}")).status_code == 404
    assert (await client.get(f"/views/users/{member.id}")).status_code == 404
    # ...and the roster gives them no link to one either.
    roster = await client.get(f"/views/groups/{group.id}/members")
    assert roster.status_code == 200
    assert f"/views/users/{member.id}" not in roster.text


async def test_the_group_roster_carries_only_name_picture_and_role(client, db):
    """The shape is the gate: an administrator must not learn a member's
    email or phone because they administer the group."""
    owner = await make_user(db, "Owner")
    member = await make_user(db, "Member")
    group = await make_group(db, owner)
    await add_member(db, group, member, role=GroupRole.ADMIN.value)

    sign_in(client, owner)
    rows = (await client.get(f"/groups/{group.id}/members")).json()
    assert rows
    for row in rows:
        # The line this test guards is the one between a roster and a file
        # on somebody, and it has not moved.
        assert set(row) == {
            "user_id",
            "name",
            "avatar",
            "role",
            "joined_at",
        }


async def test_the_participants_screen_shows_no_scores(client, db):
    """Deciding who is in a challenge is running the group; a file on
    anybody is not part of it."""
    owner = await make_user(db, "Owner")
    member = await make_user(db, "Member")
    group = await make_group(db, owner)
    await add_member(db, group, member)
    challenge = await make_challenge(db, owner, group=group)

    sign_in(client, owner)
    page = await client.get(
        f"/views/groups/{group.id}/challenges/{challenge.id}/participants"
    )
    assert page.status_code == 200
    for forbidden in ("پیاپی", "رتبه", "درصد پیشرفت"):
        assert forbidden not in page.text
