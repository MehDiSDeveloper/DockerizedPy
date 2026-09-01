"""Group challenges: who gets put in, who may leave, and who may change it.

Four behaviours, and each is a place the feature quietly stops working:

* **the audience is enforced server-side.** «همه» is read off the group's
  membership rather than off the request, and «افراد منتخب» is intersected
  with it -- so a body naming somebody outside the group cannot enrol a
  stranger into a challenge they cannot even see.
* **«همه» is a standing answer**, re-read whenever somebody joins the group,
  through every door that creates a membership. A snapshot would mean the
  challenge silently stops being "for everyone" the first time a new person
  arrives.
* **mandatory binds to the membership, not to the person.** Refused while
  you are in the group, allowed the moment you are not, and the enrollment
  and everything logged against it survive either way.
* **adding and removing afterwards is the group administrator's**, and it is
  the only thing about a challenge their group role buys them.
"""

from __future__ import annotations

from datetime import date

from sqlalchemy import func, select

from app.models.challenge import Challenge, GroupAudience, ParticipationMode
from app.models.enrollment import Enrollment
from app.models.group import GroupRole
from app.models.stats import ChallengeStats
from tests.group_helpers import (
    add_member,
    make_challenge,
    make_group,
    make_user,
    sign_in,
)


async def enrolled_ids(db, challenge_id: int) -> set[int]:
    return set(
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


async def make_invite_code(db, group, owner) -> str:
    from app.groups import new_invite_code
    from app.models.group import GroupInvite

    invite = GroupInvite(
        group_id=group.id, code=new_invite_code(), created_by_user_id=owner.id
    )
    db.add(invite)
    await db.commit()
    return invite.code


# ---------------------------------------------------------------------------
# The audience
# ---------------------------------------------------------------------------


async def test_audience_all_enrols_the_whole_group(client, db):
    owner = await make_user(db, "Owner")
    a = await make_user(db, "Member A")
    b = await make_user(db, "Member B")
    group = await make_group(db, owner)
    await add_member(db, group, a)
    await add_member(db, group, b)

    sign_in(client, owner)
    res = await client.post(
        "/challenges/",
        json={
            "title": "چالش سلامت",
            "cadence": {"kind": "once"},
            "group_id": group.id,
            "group_audience": GroupAudience.ALL.value,
        },
    )
    assert res.status_code == 201
    assert await enrolled_ids(db, res.json()["id"]) == {owner.id, a.id, b.id}


async def test_audience_selected_enrols_only_the_named(client, db):
    owner = await make_user(db, "Owner")
    a = await make_user(db, "Member A")
    b = await make_user(db, "Member B")
    group = await make_group(db, owner)
    await add_member(db, group, a)
    await add_member(db, group, b)

    sign_in(client, owner)
    res = await client.post(
        "/challenges/",
        json={
            "title": "چالش دونفره",
            "cadence": {"kind": "once"},
            "group_id": group.id,
            "group_audience": GroupAudience.SELECTED.value,
            "member_ids": [a.id],
        },
    )
    assert await enrolled_ids(db, res.json()["id"]) == {owner.id, a.id}


async def test_one_person_is_selected_with_one_id(client, db):
    """«یک نفر خاص» is not a third audience -- it is `selected` with one box
    ticked, which is why there is no third branch anywhere for it."""
    owner = await make_user(db, "Owner")
    target = await make_user(db, "Target")
    other = await make_user(db, "Other")
    group = await make_group(db, owner)
    await add_member(db, group, target)
    await add_member(db, group, other)

    sign_in(client, owner)
    res = await client.post(
        "/challenges/",
        json={
            "title": "چالش شخصی",
            "cadence": {"kind": "once"},
            "group_id": group.id,
            "member_ids": [target.id],
        },
    )
    assert await enrolled_ids(db, res.json()["id"]) == {owner.id, target.id}


async def test_a_stranger_cannot_be_named_into_a_group_challenge(client, db):
    """Dropped rather than refused: a stranger must never end up enrolled in
    something they cannot see, and it must not be possible to *test* whether
    an id is in the group by watching which bodies are rejected."""
    owner = await make_user(db, "Owner")
    member = await make_user(db, "Member")
    outsider = await make_user(db, "Outsider")
    group = await make_group(db, owner)
    await add_member(db, group, member)

    sign_in(client, owner)
    res = await client.post(
        "/challenges/",
        json={
            "title": "چالش گروهی",
            "cadence": {"kind": "once"},
            "group_id": group.id,
            "member_ids": [member.id, outsider.id],
        },
    )
    assert res.status_code == 201
    assert await enrolled_ids(db, res.json()["id"]) == {owner.id, member.id}


async def test_the_participant_count_matches_the_rows(client, db):
    owner = await make_user(db, "Owner")
    a = await make_user(db, "Member A")
    group = await make_group(db, owner)
    await add_member(db, group, a)

    sign_in(client, owner)
    challenge_id = (
        await client.post(
            "/challenges/",
            json={
                "title": "چالش گروهی",
                "cadence": {"kind": "once"},
                "group_id": group.id,
                "group_audience": GroupAudience.ALL.value,
            },
        )
    ).json()["id"]

    stats = (
        await db.execute(
            select(ChallengeStats).where(ChallengeStats.challenge_id == challenge_id)
        )
    ).scalar_one()
    assert stats.participant_count == 2


# ---------------------------------------------------------------------------
# The standing audience
# ---------------------------------------------------------------------------


async def test_a_new_arrival_picks_up_the_standing_challenges(client, db):
    """Through the invite door -- **immediately**, no second approval.

    Being in the group is the whole answer now: an arrival picks up every
    ``all``-audience challenge the moment they join, and nothing outside
    ``selected``'s own membership check.
    """
    owner = await make_user(db, "Owner")
    newcomer = await make_user(db, "Newcomer")
    group = await make_group(db, owner)
    everyone = await make_challenge(
        db, owner, group=group, title="برای همه", audience=GroupAudience.ALL.value
    )
    only_some = await make_challenge(
        db, owner, group=group, title="فقط بعضی‌ها",
        audience=GroupAudience.SELECTED.value,
    )
    code = await make_invite_code(db, group, owner)

    sign_in(client, newcomer)
    assert (await client.post(f"/invites/{code}/accept")).status_code == 201

    assert newcomer.id in await enrolled_ids(db, everyone.id)
    assert newcomer.id not in await enrolled_ids(db, only_some.id)


async def test_an_approved_request_picks_them_up_too(client, db):
    """Both doors, because a member who arrived by one and missed what
    everybody else has is exactly the drift `apply_standing_audience` exists
    to prevent."""
    owner = await make_user(db, "Owner")
    hopeful = await make_user(db, "Hopeful")
    group = await make_group(db, owner)
    everyone = await make_challenge(
        db, owner, group=group, title="برای همه", audience=GroupAudience.ALL.value
    )
    code = await make_invite_code(db, group, owner)

    sign_in(client, hopeful)
    request_id = (await client.post(f"/invites/{code}/request")).json()["id"]
    sign_in(client, owner)
    await client.post(f"/groups/{group.id}/requests/{request_id}/approve")

    assert hopeful.id in await enrolled_ids(db, everyone.id)


async def test_an_archived_standing_challenge_is_skipped(client, db):
    """It produces no occurrences, so enrolling somebody in one gives them a
    row that can never be acted on and a notification about something over."""
    owner = await make_user(db, "Owner")
    newcomer = await make_user(db, "Newcomer")
    group = await make_group(db, owner)
    old = await make_challenge(
        db, owner, group=group, title="تمام‌شده", audience=GroupAudience.ALL.value
    )
    old.lifecycle_status = "archived"
    await db.commit()
    code = await make_invite_code(db, group, owner)

    sign_in(client, newcomer)
    await client.post(f"/invites/{code}/accept")
    assert newcomer.id not in await enrolled_ids(db, old.id)


# ---------------------------------------------------------------------------
# Mandatory participation
# ---------------------------------------------------------------------------


async def test_a_mandatory_challenge_cannot_be_left(client, db):
    owner = await make_user(db, "Owner")
    member = await make_user(db, "Member")
    group = await make_group(db, owner)
    await add_member(db, group, member)
    challenge = await make_challenge(
        db,
        owner,
        group=group,
        audience=GroupAudience.ALL.value,
        participation=ParticipationMode.MANDATORY.value,
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
    assert (await client.delete(f"/enrollments/{challenge.id}")).status_code == 403


async def test_an_optional_group_challenge_can_be_left(client, db):
    owner = await make_user(db, "Owner")
    member = await make_user(db, "Member")
    group = await make_group(db, owner)
    await add_member(db, group, member)
    challenge = await make_challenge(
        db, owner, group=group, participation=ParticipationMode.OPTIONAL.value
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
    assert (await client.delete(f"/enrollments/{challenge.id}")).status_code == 204


async def test_a_personal_challenge_is_unaffected(client, db):
    """`participation_mode` is stored on every challenge and consulted on
    none that has no group -- so nothing changes outside a group."""
    owner = await make_user(db, "Owner")
    joiner = await make_user(db, "Joiner")
    challenge = await make_challenge(db, owner)
    challenge.participation_mode = ParticipationMode.MANDATORY.value
    await db.commit()

    sign_in(client, joiner)
    assert (await client.post(f"/enrollments/{challenge.id}")).status_code == 201
    assert (await client.delete(f"/enrollments/{challenge.id}")).status_code == 204


# ---------------------------------------------------------------------------
# Changing the roster afterwards
# ---------------------------------------------------------------------------


async def test_an_administrator_adds_and_removes_participants(client, db):
    owner = await make_user(db, "Owner")
    admin = await make_user(db, "Admin")
    member = await make_user(db, "Member")
    group = await make_group(db, owner)
    await add_member(db, group, admin, role=GroupRole.ADMIN.value)
    await add_member(db, group, member)
    challenge = await make_challenge(db, owner, group=group)

    sign_in(client, admin)
    res = await client.post(
        f"/groups/{group.id}/challenges/{challenge.id}/participants",
        json={"user_ids": [member.id]},
    )
    assert res.status_code == 204
    assert member.id in await enrolled_ids(db, challenge.id)

    res = await client.delete(
        f"/groups/{group.id}/challenges/{challenge.id}/participants/{member.id}"
    )
    assert res.status_code == 204
    assert member.id not in await enrolled_ids(db, challenge.id)


async def test_adding_twice_is_the_same_request(client, db):
    owner = await make_user(db, "Owner")
    member = await make_user(db, "Member")
    group = await make_group(db, owner)
    await add_member(db, group, member)
    challenge = await make_challenge(db, owner, group=group)

    sign_in(client, owner)
    for _ in range(2):
        assert (
            await client.post(
                f"/groups/{group.id}/challenges/{challenge.id}/participants",
                json={"user_ids": [member.id]},
            )
        ).status_code == 204

    count = (
        await db.execute(
            select(func.count())
            .select_from(Enrollment)
            .where(
                Enrollment.challenge_id == challenge.id,
                Enrollment.user_id == member.id,
            )
        )
    ).scalar_one()
    assert count == 1


async def test_a_plain_member_cannot_change_the_roster(client, db):
    owner = await make_user(db, "Owner")
    member = await make_user(db, "Member")
    other = await make_user(db, "Other")
    group = await make_group(db, owner)
    await add_member(db, group, member)
    await add_member(db, group, other)
    challenge = await make_challenge(db, owner, group=group)

    sign_in(client, member)
    assert (
        await client.post(
            f"/groups/{group.id}/challenges/{challenge.id}/participants",
            json={"user_ids": [other.id]},
        )
    ).status_code == 403


async def test_the_challenge_owner_cannot_be_removed(client, db):
    """Their enrolment is what carries ``ChallengeRole.OWNER`` -- removing it
    would strip the author of the thing they wrote."""
    owner = await make_user(db, "Owner")
    admin = await make_user(db, "Admin")
    group = await make_group(db, owner)
    await add_member(db, group, admin, role=GroupRole.ADMIN.value)
    challenge = await make_challenge(db, owner, group=group)

    sign_in(client, admin)
    res = await client.delete(
        f"/groups/{group.id}/challenges/{challenge.id}/participants/{owner.id}"
    )
    assert res.status_code == 409


async def test_a_group_admin_still_cannot_edit_the_challenge(client, db):
    """The line: deciding *who is in* is running the group; changing what the
    challenge says is authorship, and stays with its owner."""
    owner = await make_user(db, "Owner")
    admin = await make_user(db, "Admin")
    group = await make_group(db, owner)
    await add_member(db, group, admin, role=GroupRole.ADMIN.value)
    challenge = await make_challenge(db, owner, group=group)

    sign_in(client, admin)
    challenge_id = challenge.id
    res = await client.patch(
        f"/challenges/{challenge_id}", json={"title": "عنوان تازه"}
    )
    assert res.status_code == 403

    db.expire_all()
    fresh = (
        await db.execute(select(Challenge).where(Challenge.id == challenge_id))
    ).scalar_one()
    assert fresh.title == "چالش"


async def test_the_participants_screen_is_administrator_only(client, db):
    owner = await make_user(db, "Owner")
    member = await make_user(db, "Member")
    group = await make_group(db, owner)
    await add_member(db, group, member)
    challenge = await make_challenge(db, owner, group=group)
    url = f"/views/groups/{group.id}/challenges/{challenge.id}/participants"

    sign_in(client, member)
    assert (await client.get(url)).status_code == 404

    sign_in(client, owner)
    assert (await client.get(url)).status_code == 200
