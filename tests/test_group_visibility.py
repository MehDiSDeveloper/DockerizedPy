"""A group's challenges exist for its members and for nobody else.

The mechanism is one extra conjunct on the two challenge visibility filters
(``group_scope_filter``), so what this file pins is that the conjunct reaches
**every** door: the JSON list and detail, the SSR list and its `/fragment`,
search, the status filter and the group page itself. A gate honoured by the
surfaces that remembered it is not a gate, and the fragment in particular is
the one that would let a group's challenges leak back in on scroll.

The second half is the consequence of it being a *conjunct* rather than a
replacement: inside a group the app's ordinary three-way rule still runs, so
`public` means public **to the group** and `private` means only the people
actually in the challenge. That is one rule, scoped -- not a second rule to
keep in agreement with the first.
"""

from __future__ import annotations

from datetime import date

from sqlalchemy import select

from app.models.challenge import Challenge, GroupAudience, ParticipationMode
from app.models.enrollment import Enrollment
from tests.group_helpers import (
    add_member,
    make_challenge,
    make_group,
    make_user,
    sign_in,
)


async def visible_titles(client) -> list[str]:
    return [c["title"] for c in (await client.get("/challenges/")).json()]


# ---------------------------------------------------------------------------
# Non-members
# ---------------------------------------------------------------------------


async def test_a_group_challenge_is_invisible_to_everyone_outside(client, db):
    owner = await make_user(db, "Owner")
    outsider = await make_user(db, "Outsider")
    group = await make_group(db, owner)
    await make_challenge(db, owner, title="چالش عمومی")
    group_challenge = await make_challenge(
        db, owner, group=group, title="چالش شرکتی"
    )

    sign_in(client, outsider)
    assert await visible_titles(client) == ["چالش عمومی"]
    assert (
        await client.get(f"/challenges/{group_challenge.id}")
    ).status_code == 404
    assert (
        await client.get(f"/views/challenges/{group_challenge.id}")
    ).status_code == 404


async def test_the_ssr_list_and_its_fragment_agree(client, db):
    """The fragment is the one that would leak on scroll -- it is the same
    query, and this is what says so."""
    owner = await make_user(db, "Owner")
    outsider = await make_user(db, "Outsider")
    group = await make_group(db, owner)
    await make_challenge(db, owner, group=group, title="چالش شرکتی")

    sign_in(client, outsider)
    page = await client.get("/views/challenges/")
    assert "چالش شرکتی" not in page.text
    fragment = await client.get("/views/challenges/fragment?offset=0&limit=50")
    assert "چالش شرکتی" not in fragment.text


async def test_search_and_the_status_filter_do_not_reopen_it(client, db):
    owner = await make_user(db, "Owner")
    outsider = await make_user(db, "Outsider")
    group = await make_group(db, owner)
    await make_challenge(db, owner, group=group, title="چالش شرکتی")

    sign_in(client, outsider)
    assert (await client.get("/challenges/?q=شرکتی")).json() == []
    assert (await client.get("/challenges/?status=active")).json() == []


async def test_an_anonymous_visitor_sees_no_group_challenge(client, db):
    owner = await make_user(db, "Owner")
    group = await make_group(db, owner)
    challenge = await make_challenge(db, owner, group=group, title="چالش شرکتی")
    await make_challenge(db, owner, title="چالش عمومی")

    client.cookies.clear()
    assert await visible_titles(client) == ["چالش عمومی"]
    assert (await client.get(f"/challenges/{challenge.id}")).status_code == 404


# ---------------------------------------------------------------------------
# Members
# ---------------------------------------------------------------------------


async def test_a_group_member_sees_it_in_the_ordinary_list(client, db):
    """«درست مثل بقیه‌ی چالش‌ها» -- the same list, not a separate one."""
    owner = await make_user(db, "Owner")
    member = await make_user(db, "Member")
    group = await make_group(db, owner)
    await add_member(db, group, member)
    challenge = await make_challenge(db, owner, group=group, title="چالش شرکتی")

    sign_in(client, member)
    assert "چالش شرکتی" in await visible_titles(client)
    assert (await client.get(f"/challenges/{challenge.id}")).status_code == 200
    page = await client.get("/views/challenges/")
    assert "چالش شرکتی" in page.text


async def test_visibility_still_applies_inside_the_group(client, db):
    """Public means public *to the group*; private means its participants.

    The gate is a conjunct, so the existing rule runs untouched inside it --
    which is what keeps this one rule rather than two.
    """
    owner = await make_user(db, "Owner")
    member = await make_user(db, "Member")
    group = await make_group(db, owner)
    await add_member(db, group, member)
    await make_challenge(db, owner, group=group, title="برای همهٔ گروه")
    await make_challenge(
        db, owner, group=group, title="فقط شرکت‌کننده‌ها", visibility="private"
    )

    sign_in(client, member)
    titles = await visible_titles(client)
    assert "برای همهٔ گروه" in titles
    assert "فقط شرکت‌کننده‌ها" not in titles

    # ...and the owner, who is enrolled, does see the private one.
    sign_in(client, owner)
    assert "فقط شرکت‌کننده‌ها" in await visible_titles(client)


async def test_an_enrollment_survives_leaving_the_group(client, db):
    """Somebody removed from the group keeps what they were already in.

    They still have logged history there, the app never destroys that, and a
    member who could neither see nor leave a challenge they are still
    enrolled in would be stuck. It leaks nothing: they were in the room.
    """
    owner = await make_user(db, "Owner")
    member = await make_user(db, "Member")
    group = await make_group(db, owner)
    await add_member(db, group, member)
    challenge = await make_challenge(
        db,
        owner,
        group=group,
        title="چالش شرکتی",
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

    sign_in(client, owner)
    assert (
        await client.delete(f"/groups/{group.id}/members/{member.id}")
    ).status_code == 204

    sign_in(client, member)
    assert (await client.get(f"/challenges/{challenge.id}")).status_code == 200
    # And they may now leave it, even though it was mandatory -- the
    # obligation belonged to the membership.
    assert (await client.delete(f"/enrollments/{challenge.id}")).status_code == 204


async def test_the_group_page_narrows_without_widening(client, db):
    """`group_id` is applied *on top of* the visibility clause, so asking for
    a group you are not in returns nothing rather than its challenges."""
    owner = await make_user(db, "Owner")
    outsider = await make_user(db, "Outsider")
    group = await make_group(db, owner)
    await make_challenge(db, owner, group=group, title="چالش شرکتی")

    sign_in(client, outsider)
    assert (
        await client.get(f"/views/groups/{group.id}/challenges/fragment")
    ).status_code == 404


async def test_the_operator_still_reaches_it_for_moderation(client, db):
    """`scope_all=True` is unchanged: an operator sees every challenge on the
    moderation roster, group ones included. Moderating is not owning -- the
    same line ``test_groups.py`` pins from the other side."""
    from app.models.user import UserRole

    owner = await make_user(db, "Owner")
    operator = await make_user(db, "Operator", role=UserRole.ADMIN.value)
    group = await make_group(db, owner)
    await make_challenge(db, owner, group=group, title="چالش شرکتی")

    sign_in(client, operator)
    page = await client.get("/views/admin/challenges")
    assert page.status_code == 200
    assert "چالش شرکتی" in page.text


async def test_group_id_cannot_be_changed_after_creation(client, db):
    """Moving a challenge between groups would retroactively change who has
    been able to see it -- the same objection that locks `identity_mode`.
    `ChallengeUpdate` does not carry the field, so the body is ignored."""
    owner = await make_user(db, "Owner")
    other_owner = await make_user(db, "Other")
    group = await make_group(db, owner)
    other_group = await make_group(db, other_owner, name="گروه دیگر")
    challenge = await make_challenge(db, owner, group=group)

    sign_in(client, owner)
    challenge_id, group_id = challenge.id, group.id
    res = await client.patch(
        f"/challenges/{challenge_id}", json={"group_id": other_group.id}
    )
    assert res.status_code == 200
    db.expire_all()
    fresh = (
        await db.execute(select(Challenge).where(Challenge.id == challenge_id))
    ).scalar_one()
    assert fresh.group_id == group_id


async def test_creating_under_a_group_needs_the_permission(client, db):
    """404 for a group you are not in -- the id must not be probeable -- and
    403 for a member of it who may not create there."""
    owner = await make_user(db, "Owner")
    member = await make_user(db, "Member")
    outsider = await make_user(db, "Outsider")
    group = await make_group(db, owner)
    await add_member(db, group, member)

    body = {
        "title": "چالش تازه",
        "cadence": {"kind": "once"},
        "group_id": group.id,
        "group_audience": GroupAudience.ALL.value,
        "participation_mode": ParticipationMode.OPTIONAL.value,
    }

    sign_in(client, outsider)
    assert (await client.post("/challenges/", json=body)).status_code == 404

    sign_in(client, member)
    assert (await client.post("/challenges/", json=body)).status_code == 403

    sign_in(client, owner)
    assert (await client.post("/challenges/", json=body)).status_code == 201


async def test_the_ssr_create_path_checks_the_same_thing(client, db):
    """The duplicated create logic CLAUDE.md warns about: both halves call
    the same two helpers, so a permission cannot go missing from one."""
    owner = await make_user(db, "Owner")
    member = await make_user(db, "Member")
    group = await make_group(db, owner)
    await add_member(db, group, member)

    body = {
        "title": "چالش تازه",
        "cadence": {"kind": "once"},
        "group_id": group.id,
    }
    sign_in(client, member)
    assert (
        await client.post("/views/challenges/create", json=body)
    ).status_code == 403
