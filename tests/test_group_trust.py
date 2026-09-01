"""The third door in, and the second approval.

Three things arrived together and this file pins the seam between them:

* **Adding somebody directly** (``POST /groups/{id}/members``). Named by the
  credential they sign in with, never by a user id and never by a name
  search -- a search here would be a way for any group administrator to walk
  the whole membership, which is what ``profile_visibility_filter`` exists to
  prevent.
* **The public link** -- an ordinary invite with ``requires_approval`` set,
  which reaches the group's landing page and *cannot* admit anybody: the
  only thing it offers is a join request.
* **«محرمیت»**, the second approval. Being in the group and being swept into
  what the group asks of everyone are two answers given at two moments, and
  the whole of the second lives in ``GroupMembership.is_trusted``. What is
  pinned is that the split is real in both directions: an unapproved member
  picks up **nothing** from an «همه اعضا» challenge, and is still enrolled
  the moment an administrator names them in one.

The retroactive half matters as much as the gate: approving somebody a week
after they joined must leave them with exactly what somebody approved on the
day has, because otherwise the queue quietly costs people the challenges they
were waiting for.
"""

from __future__ import annotations

from sqlalchemy import func, select

from app.groups import new_invite_code
from app.models.challenge import GroupAudience
from app.models.enrollment import Enrollment
from app.models.group import GroupInvite, GroupMembership, GroupRole
from app.models.notification import Notification, NotificationKind
from tests.group_helpers import (
    add_member,
    make_challenge,
    make_group,
    make_user,
    sign_in,
)


async def _enrolled(db, challenge, user) -> bool:
    return (
        await db.execute(
            select(Enrollment.id).where(
                Enrollment.challenge_id == challenge.id,
                Enrollment.user_id == user.id,
            )
        )
    ).scalar_one_or_none() is not None


async def _membership(db, group, user) -> GroupMembership | None:
    return (
        await db.execute(
            select(GroupMembership).where(
                GroupMembership.group_id == group.id,
                GroupMembership.user_id == user.id,
            )
        )
    ).scalar_one_or_none()


async def _with_mobile(db, user, mobile: str):
    user.mobile = mobile
    await db.commit()
    return user


# ---------------------------------------------------------------------------
# Adding somebody directly
# ---------------------------------------------------------------------------


async def test_admin_adds_a_member_by_mobile(client, db):
    owner = await make_user(db, "Owner")
    newcomer = await _with_mobile(db, await make_user(db, "New"), "+989121234567")
    group = await make_group(db, owner)

    sign_in(client, owner)
    # Typed the way a human types it: a different string, the same account --
    # `normalize_mobile` is what makes those one number rather than four.
    res = await client.post(
        f"/groups/{group.id}/members", json={"identifier": "0912 123 4567"}
    )
    assert res.status_code == 201
    assert res.json()["user_id"] == newcomer.id
    assert await _membership(db, group, newcomer) is not None


async def test_added_member_is_told_and_it_is_its_own_kind(client, db):
    """Not ``GROUP_JOIN_APPROVED``: nothing was asked for here."""
    owner = await make_user(db, "Owner")
    newcomer = await _with_mobile(db, await make_user(db, "New"), "+989121234500")
    group = await make_group(db, owner)

    sign_in(client, owner)
    await client.post(
        f"/groups/{group.id}/members", json={"identifier": "09121234500"}
    )
    kinds = (
        (
            await db.execute(
                select(Notification.kind).where(Notification.user_id == newcomer.id)
            )
        )
        .scalars()
        .all()
    )
    assert NotificationKind.GROUP_MEMBER_ADDED.value in kinds
    assert NotificationKind.GROUP_JOIN_APPROVED.value not in kinds


async def test_adding_an_unknown_number_is_a_404_and_writes_nothing(client, db):
    owner = await make_user(db, "Owner")
    group = await make_group(db, owner)
    sign_in(client, owner)
    res = await client.post(
        f"/groups/{group.id}/members", json={"identifier": "09129999999"}
    )
    assert res.status_code == 404
    count = (
        await db.execute(
            select(func.count())
            .select_from(GroupMembership)
            .where(GroupMembership.group_id == group.id)
        )
    ).scalar_one()
    assert count == 1


async def test_adding_somebody_already_in_is_a_409(client, db):
    owner = await make_user(db, "Owner")
    member = await _with_mobile(db, await make_user(db, "Member"), "+989121230001")
    group = await make_group(db, owner)
    await add_member(db, group, member)

    sign_in(client, owner)
    res = await client.post(
        f"/groups/{group.id}/members", json={"identifier": "09121230001"}
    )
    assert res.status_code == 409


async def test_a_plain_member_cannot_add_anybody(client, db):
    """403 and not 404: they can see the group, so refusing leaks nothing."""
    owner = await make_user(db, "Owner")
    member = await make_user(db, "Member")
    outsider = await _with_mobile(db, await make_user(db, "Outsider"), "+989121230002")
    group = await make_group(db, owner)
    await add_member(db, group, member)

    sign_in(client, member)
    res = await client.post(
        f"/groups/{group.id}/members", json={"identifier": "09121230002"}
    )
    assert res.status_code == 403
    assert await _membership(db, group, outsider) is None


async def test_a_new_member_arrives_waiting_for_the_second_approval(client, db):
    owner = await make_user(db, "Owner")
    newcomer = await _with_mobile(db, await make_user(db, "New"), "+989121230003")
    group = await make_group(db, owner)

    sign_in(client, owner)
    res = await client.post(
        f"/groups/{group.id}/members", json={"identifier": "09121230003"}
    )
    assert res.json()["is_trusted"] is False
    membership = await _membership(db, group, newcomer)
    assert membership.is_trusted is False


async def test_both_answers_can_be_given_at_once(client, db):
    owner = await make_user(db, "Owner")
    await _with_mobile(db, await make_user(db, "New"), "+989121230004")
    group = await make_group(db, owner)
    challenge = await make_challenge(
        db, owner, group=group, audience=GroupAudience.ALL.value
    )

    sign_in(client, owner)
    res = await client.post(
        f"/groups/{group.id}/members",
        json={"identifier": "09121230004", "trusted": True},
    )
    assert res.status_code == 201
    assert res.json()["is_trusted"] is True
    user_id = res.json()["user_id"]
    assert (
        await db.execute(
            select(Enrollment.id).where(
                Enrollment.challenge_id == challenge.id,
                Enrollment.user_id == user_id,
            )
        )
    ).scalar_one_or_none() is not None


# ---------------------------------------------------------------------------
# The second approval
# ---------------------------------------------------------------------------


async def test_an_unapproved_member_picks_up_no_standing_challenge(client, db):
    """The gate, in the direction that matters: «همه اعضا» does not mean them.

    Driven through the invite link rather than by calling
    ``apply_standing_audience`` directly, because the rule has to hold at the
    door somebody actually walks through.
    """
    owner = await make_user(db, "Owner")
    joiner = await make_user(db, "Joiner")
    group = await make_group(db, owner)
    challenge = await make_challenge(
        db, owner, group=group, audience=GroupAudience.ALL.value
    )
    invite = GroupInvite(
        group_id=group.id, code=new_invite_code(), uses=0, created_by_user_id=owner.id
    )
    db.add(invite)
    await db.commit()

    sign_in(client, joiner)
    res = await client.post(f"/invites/{invite.code}/accept")
    assert res.status_code == 201
    assert await _membership(db, group, joiner) is not None
    assert not await _enrolled(db, challenge, joiner)


async def test_naming_somebody_enrols_them_even_while_they_wait(client, db):
    """Picking a person by hand *is* the approval this feature asks for."""
    owner = await make_user(db, "Owner")
    member = await make_user(db, "Member")
    group = await make_group(db, owner)
    await add_member(db, group, member, trusted=False)
    challenge = await make_challenge(
        db, owner, group=group, audience=GroupAudience.SELECTED.value
    )

    sign_in(client, owner)
    res = await client.post(
        f"/groups/{group.id}/challenges/{challenge.id}/participants",
        json={"user_ids": [member.id]},
    )
    assert res.status_code == 204
    assert await _enrolled(db, challenge, member)


async def test_a_new_all_challenge_skips_the_unapproved(client, db):
    """The other half of the same rule, at creation time."""
    owner = await make_user(db, "Owner")
    waiting = await make_user(db, "Waiting")
    settled = await make_user(db, "Settled")
    group = await make_group(db, owner)
    await add_member(db, group, waiting, trusted=False)
    await add_member(db, group, settled)

    sign_in(client, owner)
    res = await client.post(
        "/challenges/",
        json={
            "title": "پیاده‌روی شرکت",
            "category": "سلامت جسمانی",
            "cadence": {"kind": "once"},
            "group_id": group.id,
            "group_audience": GroupAudience.ALL.value,
        },
    )
    assert res.status_code == 201
    challenge_id = res.json()["id"]
    enrolled = set(
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
    assert settled.id in enrolled
    assert waiting.id not in enrolled


async def test_approving_is_retroactive(client, db):
    """Approved a week later, and they end up where the others already are."""
    owner = await make_user(db, "Owner")
    member = await make_user(db, "Member")
    group = await make_group(db, owner)
    await add_member(db, group, member, trusted=False)
    challenge = await make_challenge(
        db, owner, group=group, audience=GroupAudience.ALL.value
    )
    assert not await _enrolled(db, challenge, member)

    sign_in(client, owner)
    res = await client.post(f"/groups/{group.id}/members/{member.id}/trust")
    assert res.status_code == 200
    assert res.json()["is_trusted"] is True
    assert await _enrolled(db, challenge, member)
    kinds = (
        (
            await db.execute(
                select(Notification.kind).where(Notification.user_id == member.id)
            )
        )
        .scalars()
        .all()
    )
    assert NotificationKind.GROUP_MEMBER_TRUSTED.value in kinds


async def test_approving_twice_is_the_same_request(client, db):
    owner = await make_user(db, "Owner")
    member = await make_user(db, "Member")
    group = await make_group(db, owner)
    await add_member(db, group, member, trusted=False)
    challenge = await make_challenge(
        db, owner, group=group, audience=GroupAudience.ALL.value
    )

    sign_in(client, owner)
    await client.post(f"/groups/{group.id}/members/{member.id}/trust")
    await client.post(f"/groups/{group.id}/members/{member.id}/trust")
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


async def test_taking_the_approval_back_touches_no_enrollment(client, db):
    """The same call ``remove_member`` makes: what they logged is theirs."""
    owner = await make_user(db, "Owner")
    member = await make_user(db, "Member")
    group = await make_group(db, owner)
    await add_member(db, group, member, trusted=False)
    challenge = await make_challenge(
        db, owner, group=group, audience=GroupAudience.ALL.value
    )
    sign_in(client, owner)
    await client.post(f"/groups/{group.id}/members/{member.id}/trust")
    assert await _enrolled(db, challenge, member)

    res = await client.request(
        "DELETE", f"/groups/{group.id}/members/{member.id}/trust"
    )
    assert res.status_code == 200
    assert res.json()["is_trusted"] is False
    assert await _enrolled(db, challenge, member)


async def test_the_owner_is_always_approved(client, db):
    owner = await make_user(db, "Owner")
    group = await make_group(db, owner)
    sign_in(client, owner)
    res = await client.request("DELETE", f"/groups/{group.id}/members/{owner.id}/trust")
    assert res.status_code == 400


async def test_promoting_to_admin_carries_the_approval(client, db):
    """An administrator waiting to be approved is a contradiction."""
    owner = await make_user(db, "Owner")
    member = await make_user(db, "Member")
    group = await make_group(db, owner)
    await add_member(db, group, member, trusted=False)

    sign_in(client, owner)
    res = await client.patch(
        f"/groups/{group.id}/members/{member.id}", json={"role": GroupRole.ADMIN.value}
    )
    assert res.status_code == 200
    assert res.json()["is_trusted"] is True


async def test_a_plain_member_cannot_approve(client, db):
    owner = await make_user(db, "Owner")
    member = await make_user(db, "Member")
    other = await make_user(db, "Other")
    group = await make_group(db, owner)
    await add_member(db, group, member)
    await add_member(db, group, other, trusted=False)

    sign_in(client, member)
    res = await client.post(f"/groups/{group.id}/members/{other.id}/trust")
    assert res.status_code == 403
    assert (await _membership(db, group, other)).is_trusted is False


# ---------------------------------------------------------------------------
# The public link
# ---------------------------------------------------------------------------


async def test_the_public_link_is_get_or_create(client, db):
    """Standing means standing: asking twice answers the same code twice."""
    owner = await make_user(db, "Owner")
    group = await make_group(db, owner)
    sign_in(client, owner)
    first = await client.post(f"/groups/{group.id}/invites/public")
    second = await client.post(f"/groups/{group.id}/invites/public")
    assert first.status_code == 200
    assert first.json()["code"] == second.json()["code"]
    assert first.json()["requires_approval"] is True
    assert first.json()["max_uses"] is None
    count = (
        await db.execute(
            select(func.count())
            .select_from(GroupInvite)
            .where(GroupInvite.group_id == group.id)
        )
    ).scalar_one()
    assert count == 1


async def test_the_public_link_admits_nobody(client, db):
    """It is a door that asks. Accepting it is refused, not honoured."""
    owner = await make_user(db, "Owner")
    outsider = await make_user(db, "Outsider")
    group = await make_group(db, owner)
    sign_in(client, owner)
    code = (await client.post(f"/groups/{group.id}/invites/public")).json()["code"]

    sign_in(client, outsider)
    res = await client.post(f"/invites/{code}/accept")
    assert res.status_code == 409
    assert await _membership(db, group, outsider) is None


async def test_the_public_link_opens_a_request(client, db):
    owner = await make_user(db, "Owner")
    outsider = await make_user(db, "Outsider")
    group = await make_group(db, owner)
    sign_in(client, owner)
    code = (await client.post(f"/groups/{group.id}/invites/public")).json()["code"]

    sign_in(client, outsider)
    preview = await client.get(f"/invites/{code}")
    assert preview.json()["requires_approval"] is True
    res = await client.post(f"/invites/{code}/request")
    assert res.status_code == 201
    # And the page says so rather than offering a button that answers 409.
    page = await client.get(f"/views/invites/{code}")
    assert "درخواست عضویت تو ثبت شده" in page.text


async def test_an_approved_request_still_waits_for_the_second_approval(client, db):
    """The two doors and the two approvals are independent.

    Somebody let into the group is *in* -- and that is all: the group's
    standing challenges are the second decision, so a member approved at the
    door still shows up in the approval queue.
    """
    owner = await make_user(db, "Owner")
    outsider = await make_user(db, "Outsider")
    group = await make_group(db, owner)
    challenge = await make_challenge(
        db, owner, group=group, audience=GroupAudience.ALL.value
    )
    sign_in(client, owner)
    code = (await client.post(f"/groups/{group.id}/invites/public")).json()["code"]
    sign_in(client, outsider)
    request_id = (await client.post(f"/invites/{code}/request")).json()["id"]

    sign_in(client, owner)
    res = await client.post(f"/groups/{group.id}/requests/{request_id}/approve")
    assert res.status_code == 200
    membership = await _membership(db, group, outsider)
    assert membership is not None
    assert membership.is_trusted is False
    assert not await _enrolled(db, challenge, outsider)


# ---------------------------------------------------------------------------
# The screens
# ---------------------------------------------------------------------------


async def test_the_manage_page_lists_the_queue_and_404s_a_member(client, db):
    owner = await make_user(db, "Owner")
    member = await make_user(db, "Member")
    waiting = await make_user(db, "Waiting")
    group = await make_group(db, owner)
    await add_member(db, group, member)
    await add_member(db, group, waiting, trusted=False)

    sign_in(client, owner)
    page = await client.get(f"/views/groups/{group.id}/manage")
    assert page.status_code == 200
    assert "Waiting" in page.text
    assert 'data-trust' in page.text

    sign_in(client, member)
    assert (await client.get(f"/views/groups/{group.id}/manage")).status_code == 404
