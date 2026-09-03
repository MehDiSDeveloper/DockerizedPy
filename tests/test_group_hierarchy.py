"""Nested groups: what the tree changes, and what it deliberately does not.

A subgroup is a group -- its own roster, its own links, its own challenges --
and ``Group.parent_id`` adds exactly two rules, both of which live in
``group_ids_for``:

* **membership reaches upward**, so a department's people are the company's
  people: they see its challenges and its «همه» audience reaches them.
* **authority reaches downward, and only authority**, capped at ``admin``:
  whoever runs a group runs what is inside it, but does not become the owner
  of a room somebody else opened -- and a plain member of the parent gets no
  reach into a sub-team at all.

Everything else is asserted *not* to have changed, because that is the whole
claim the design makes.
"""

from __future__ import annotations

from sqlalchemy import select

from app.groups import MAX_GROUP_DEPTH
from app.models.challenge import GroupAudience
from app.models.enrollment import Enrollment
from app.models.group import GroupRole
from tests.group_helpers import (
    add_member,
    make_challenge,
    make_group,
    make_user,
    sign_in,
)


async def _create_subgroup(client, parent_id: int, name: str = "واحد فروش"):
    return await client.post(
        "/groups/", json={"name": name, "kind": "team", "parent_id": parent_id}
    )


async def _enrolled_ids(db, challenge_id: int) -> set[int]:
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


# ---------------------------------------------------------------------------
# Who may open one
# ---------------------------------------------------------------------------


async def test_an_administrator_opens_a_subgroup_and_owns_it(client, db):
    owner = await make_user(db, "Owner")
    admin = await make_user(db, "Admin")
    parent = await make_group(db, owner)
    await add_member(db, parent, admin, role=GroupRole.ADMIN.value)
    sign_in(client, admin)

    res = await _create_subgroup(client, parent.id)
    assert res.status_code == 201
    body = res.json()
    assert body["parent_id"] == parent.id
    assert body["parent_name"] == parent.name
    # The creator owns what they opened -- a subgroup is created the one way
    # groups are created.
    assert body["owner_id"] == admin.id
    assert body["my_role"] == GroupRole.OWNER.value


async def test_a_plain_member_may_not_open_a_subgroup(client, db):
    owner = await make_user(db, "Owner")
    member = await make_user(db, "Member")
    parent = await make_group(db, owner)
    await add_member(db, parent, member)
    sign_in(client, member)

    assert (await _create_subgroup(client, parent.id)).status_code == 403


async def test_an_outsider_cannot_learn_a_group_id_by_posting_to_it(client, db):
    owner = await make_user(db, "Owner")
    stranger = await make_user(db, "Stranger")
    parent = await make_group(db, owner)
    sign_in(client, stranger)

    # 404, not 403: the group visibility rule, same as everywhere else.
    assert (await _create_subgroup(client, parent.id)).status_code == 404


async def test_nesting_is_capped(client, db):
    owner = await make_user(db, "Owner")
    sign_in(client, owner)
    group = await make_group(db, owner)
    deepest = group.id
    for level in range(MAX_GROUP_DEPTH - 1):
        res = await _create_subgroup(client, deepest, name=f"سطح {level}")
        assert res.status_code == 201
        deepest = res.json()["id"]

    refused = await _create_subgroup(client, deepest, name="یکی زیادی")
    assert refused.status_code == 409


# ---------------------------------------------------------------------------
# Membership upward
# ---------------------------------------------------------------------------


async def test_a_subgroup_member_reaches_the_parent(client, db):
    owner = await make_user(db, "Owner")
    worker = await make_user(db, "Worker")
    parent = await make_group(db, owner)
    child = await make_group(db, owner, name="واحد فروش", parent=parent)
    await add_member(db, child, worker)
    sign_in(client, worker)

    # The company is reachable, and its challenges are visible...
    assert (await client.get(f"/groups/{parent.id}")).status_code == 200
    challenge = await make_challenge(db, owner, group=parent, title="چالش شرکت")
    listed = await client.get("/challenges/")
    assert challenge.id in [c["id"] for c in listed.json()]
    # ...but nothing about the company promotes them there.
    assert (await client.get(f"/groups/{parent.id}")).json()["my_role"] == (
        GroupRole.MEMBER.value
    )


async def test_a_parent_member_does_not_walk_into_a_sub_team(client, db):
    owner = await make_user(db, "Owner")
    member = await make_user(db, "Member")
    parent = await make_group(db, owner)
    await add_member(db, parent, member)
    child = await make_group(db, owner, name="واحد فروش", parent=parent)
    sign_in(client, member)

    # Downward reach is authority's, not membership's: a plain member of the
    # company is not in the sales team's room.
    assert (await client.get(f"/groups/{child.id}")).status_code == 404
    hidden = await make_challenge(db, owner, group=child, title="چالش واحد")
    listed = [c["id"] for c in (await client.get("/challenges/")).json()]
    assert hidden.id not in listed


# ---------------------------------------------------------------------------
# Authority downward, and where it stops
# ---------------------------------------------------------------------------


async def test_a_parent_administrator_administers_the_subgroup(client, db):
    owner = await make_user(db, "Owner")
    admin = await make_user(db, "Admin")
    parent = await make_group(db, owner)
    await add_member(db, parent, admin, role=GroupRole.ADMIN.value)
    child = await make_group(db, owner, name="واحد فروش", parent=parent)
    sign_in(client, admin)

    res = await client.get(f"/groups/{child.id}")
    assert res.status_code == 200
    assert res.json()["my_role"] == GroupRole.ADMIN.value
    created = await client.post(
        "/challenges/",
        json={
            "title": "چالش واحد",
            "category": "سلامت جسمانی",
            "cadence": {"kind": "once"},
            "group_id": child.id,
        },
    )
    assert created.status_code == 201


async def test_inherited_authority_stops_at_admin(client, db):
    """The owner of the company does not own a room somebody else opened.

    Deleting and transferring are the owner's alone, and inheriting them down
    the tree would make «مالک» mean two different things on one screen.
    """
    owner = await make_user(db, "Owner")
    lead = await make_user(db, "Lead")
    parent = await make_group(db, owner)
    await add_member(db, parent, lead, role=GroupRole.ADMIN.value)
    child = await make_group(db, lead, name="واحد فروش", parent=parent)
    sign_in(client, owner)

    assert (await client.get(f"/groups/{child.id}")).json()["my_role"] == (
        GroupRole.ADMIN.value
    )
    assert (await client.delete(f"/groups/{child.id}")).status_code == 403
    transfer = await client.post(
        f"/groups/{child.id}/transfer", json={"new_owner_user_id": owner.id}
    )
    assert transfer.status_code == 403


async def test_a_group_with_subgroups_is_not_deleted(client, db):
    owner = await make_user(db, "Owner")
    parent = await make_group(db, owner)
    await make_group(db, owner, name="واحد فروش", parent=parent)
    sign_in(client, owner)

    assert (await client.delete(f"/groups/{parent.id}")).status_code == 409


# ---------------------------------------------------------------------------
# «همه» means everyone the group covers
# ---------------------------------------------------------------------------


async def test_the_standing_audience_reaches_the_subtree(client, db):
    owner = await make_user(db, "Owner")
    worker = await make_user(db, "Worker")
    parent = await make_group(db, owner)
    child = await make_group(db, owner, name="واحد فروش", parent=parent)
    await add_member(db, child, worker)
    sign_in(client, owner)

    res = await client.post(
        "/challenges/",
        json={
            "title": "چالش شرکت",
            "category": "سلامت جسمانی",
            "cadence": {"kind": "once"},
            "group_id": parent.id,
            "group_audience": GroupAudience.ALL.value,
        },
    )
    assert res.status_code == 201
    assert worker.id in await _enrolled_ids(db, res.json()["id"])


async def test_joining_a_subgroup_picks_up_the_parents_standing_challenges(
    client, db
):
    from app.groups import new_invite_code
    from app.models.group import GroupInvite

    owner = await make_user(db, "Owner")
    joiner = await make_user(db, "Joiner")
    parent = await make_group(db, owner)
    child = await make_group(db, owner, name="واحد فروش", parent=parent)
    standing = await make_challenge(
        db,
        owner,
        group=parent,
        title="چالش شرکت",
        audience=GroupAudience.ALL.value,
    )
    invite = GroupInvite(
        group_id=child.id, code=new_invite_code(), created_by_user_id=owner.id
    )
    db.add(invite)
    await db.commit()

    sign_in(client, joiner)
    accepted = await client.post(f"/invites/{invite.code}/accept")
    assert accepted.status_code in (200, 201)
    assert joiner.id in await _enrolled_ids(db, standing.id)


# ---------------------------------------------------------------------------
# Creating a challenge in a group stays the administrators'
# ---------------------------------------------------------------------------


async def test_a_plain_member_cannot_put_a_challenge_in_the_group(client, db):
    owner = await make_user(db, "Owner")
    member = await make_user(db, "Member")
    group = await make_group(db, owner)
    await add_member(db, group, member)
    sign_in(client, member)

    for url in ("/challenges/", "/views/challenges/create"):
        res = await client.post(
            url,
            json={
                "title": "چالش من",
                "category": "سلامت جسمانی",
                "cadence": {"kind": "once"},
                "group_id": group.id,
            },
        )
        assert res.status_code == 403, url


# ---------------------------------------------------------------------------
# The screens
# ---------------------------------------------------------------------------


async def test_the_group_page_shows_its_children_and_its_parent(client, db):
    """One render check, because the subgroup tab is conditional markup.

    A tab that renders only when there is something in it is exactly the kind
    of thing that can stop rendering without anything failing.
    """
    owner = await make_user(db, "Owner")
    parent = await make_group(db, owner)
    child = await make_group(db, owner, name="واحد فروش", parent=parent)
    sign_in(client, owner)

    on_parent = await client.get(f"/views/groups/{parent.id}")
    assert on_parent.status_code == 200
    assert 'data-panel="subgroups"' in on_parent.text
    assert "واحد فروش" in on_parent.text

    on_child = await client.get(f"/views/groups/{child.id}")
    assert on_child.status_code == 200
    assert f"زیرگروهی از {parent.name}" in on_child.text
    # The manage screen offers the way to make another one.
    manage = await client.get(f"/views/groups/{child.id}/manage")
    assert manage.status_code == 200
    assert 'id="grNewSubgroup"' in manage.text
