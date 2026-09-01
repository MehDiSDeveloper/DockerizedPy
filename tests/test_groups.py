"""Groups: the three role axes stay apart, and the owner cannot vanish.

What is pinned here is the part of the feature that is *access control*
rather than behaviour:

* **the third axis does not merge with the other two.** No app-wide role
  grants a single ``GROUP_*`` permission and no group role grants a
  ``CHALLENGE_*`` one. The assertion is parametrized over both global roles
  for the reason ``test_user_roles.py`` parametrizes its own: a future grant
  must fail the test rather than quietly cross the line.
* **the gate is a composed clause**, so a group somebody is not in answers
  **404** through both front doors, and the id cannot be probed.
* **admin sits strictly below owner.** Handing out `admin`, taking it back,
  transferring and deleting are the owner's alone.
* **the owner cannot leave without handing the group over**, because a group
  with no owner has nobody who can invite, approve or delete it, and no
  route anywhere could put one back.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.auth import create_session_cookie
from app.models.group import Group, GroupMembership, GroupRole
from app.models.user import UserRole
from app.permissions import GROUP_GRANTS, Perm, can, group_role
from tests.group_helpers import (
    add_member,
    make_challenge,
    make_group,
    make_user,
    sign_in,
)

# ---------------------------------------------------------------------------
# The axes
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("global_role", [UserRole.MEMBER.value, UserRole.ADMIN.value])
def test_no_global_role_grants_a_group_permission(global_role):
    """An operator does not become the administrator of somebody's company.

    Checked against the grant map rather than through a route, so the line is
    pinned at the one place policy lives -- adding a ``GROUP_*`` name to
    ``GLOBAL_GRANTS`` fails here immediately, wherever the routes happen to
    check it.
    """
    from app.permissions import GLOBAL_GRANTS

    group_perms = {
        value
        for name, value in vars(Perm).items()
        if name.startswith("GROUP_") and isinstance(value, str)
    }
    assert group_perms
    assert not (GLOBAL_GRANTS[global_role] & group_perms)


def test_no_group_role_grants_a_challenge_permission():
    """The other direction: running a group is not authoring its challenges.

    A group administrator may decide *who is in* a challenge; changing what
    it says stays with its owner, exactly as it does for an app-wide
    moderator.
    """
    challenge_perms = {Perm.CHALLENGE_EDIT, Perm.CHALLENGE_DELETE}
    for granted in GROUP_GRANTS.values():
        assert not (granted & challenge_perms)


async def test_group_role_resolves_owner_from_the_column(db):
    """``Group.owner_id`` wins over the membership row, like ``challenge_role``."""
    owner = await make_user(db, "Owner")
    group = await make_group(db, owner)
    # A row that says otherwise must not be able to demote the owner.
    membership = (
        await db.execute(
            select(GroupMembership).where(GroupMembership.user_id == owner.id)
        )
    ).scalar_one()
    membership.role = GroupRole.MEMBER.value
    assert group_role(owner.id, group=group, membership=membership) == "owner"


async def test_a_membership_for_somebody_else_grants_nothing(db):
    other = await make_user(db, "Other")
    owner = await make_user(db, "Owner")
    group = await make_group(db, owner)
    admin_membership = (
        await db.execute(
            select(GroupMembership).where(GroupMembership.user_id == owner.id)
        )
    ).scalar_one()
    assert group_role(other.id, group=group, membership=admin_membership) is None
    assert not can(
        other, Perm.GROUP_EDIT, group=group, membership=admin_membership
    )


# ---------------------------------------------------------------------------
# The gate
# ---------------------------------------------------------------------------


async def test_a_non_member_gets_404_from_both_front_doors(client, db):
    owner = await make_user(db, "Owner")
    outsider = await make_user(db, "Outsider")
    group = await make_group(db, owner)

    sign_in(client, outsider)
    assert (await client.get(f"/groups/{group.id}")).status_code == 404
    assert (await client.get(f"/views/groups/{group.id}")).status_code == 404
    assert (await client.get(f"/groups/{group.id}/members")).status_code == 404


async def test_a_member_reads_the_group(client, db):
    owner = await make_user(db, "Owner")
    member = await make_user(db, "Member")
    group = await make_group(db, owner)
    await add_member(db, group, member)

    sign_in(client, member)
    res = await client.get(f"/groups/{group.id}")
    assert res.status_code == 200
    body = res.json()
    assert body["member_count"] == 2
    assert body["my_role"] == "member"


async def test_my_groups_lists_only_mine(client, db):
    owner = await make_user(db, "Owner")
    stranger = await make_user(db, "Stranger")
    await make_group(db, owner, name="گروه من")
    await make_group(db, stranger, name="گروه دیگری")

    sign_in(client, owner)
    names = [g["name"] for g in (await client.get("/groups/")).json()]
    assert names == ["گروه من"]


async def test_an_app_admin_is_not_a_group_admin(client, db):
    """The operator's own axis buys nothing here -- 404, like anyone else."""
    owner = await make_user(db, "Owner")
    operator = await make_user(db, "Operator", role=UserRole.ADMIN.value)
    group = await make_group(db, owner)

    sign_in(client, operator)
    assert (await client.get(f"/groups/{group.id}")).status_code == 404
    assert (
        await client.patch(f"/groups/{group.id}", json={"name": "Renamed"})
    ).status_code == 404


# ---------------------------------------------------------------------------
# Admin below owner
# ---------------------------------------------------------------------------


async def test_an_admin_may_not_promote(client, db):
    owner = await make_user(db, "Owner")
    admin = await make_user(db, "Admin")
    member = await make_user(db, "Member")
    group = await make_group(db, owner)
    await add_member(db, group, admin, role=GroupRole.ADMIN.value)
    await add_member(db, group, member)

    sign_in(client, admin)
    res = await client.patch(
        f"/groups/{group.id}/members/{member.id}", json={"role": "admin"}
    )
    assert res.status_code == 403


async def test_an_owner_promotes_and_demotes(client, db):
    owner = await make_user(db, "Owner")
    member = await make_user(db, "Member")
    group = await make_group(db, owner)
    await add_member(db, group, member)

    sign_in(client, owner)
    res = await client.patch(
        f"/groups/{group.id}/members/{member.id}", json={"role": "admin"}
    )
    assert res.status_code == 200
    assert res.json()["role"] == "admin"

    res = await client.patch(
        f"/groups/{group.id}/members/{member.id}", json={"role": "member"}
    )
    assert res.json()["role"] == "member"


async def test_the_role_route_refuses_owner_as_a_target(client, db):
    """A second owner would contradict ``Group.owner_id``; transfer is the door."""
    owner = await make_user(db, "Owner")
    member = await make_user(db, "Member")
    group = await make_group(db, owner)
    await add_member(db, group, member)

    sign_in(client, owner)
    res = await client.patch(
        f"/groups/{group.id}/members/{member.id}", json={"role": "owner"}
    )
    assert res.status_code == 422


async def test_an_admin_removes_a_member_but_not_another_admin(client, db):
    owner = await make_user(db, "Owner")
    admin = await make_user(db, "Admin")
    other_admin = await make_user(db, "Other Admin")
    member = await make_user(db, "Member")
    group = await make_group(db, owner)
    await add_member(db, group, admin, role=GroupRole.ADMIN.value)
    await add_member(db, group, other_admin, role=GroupRole.ADMIN.value)
    await add_member(db, group, member)

    sign_in(client, admin)
    assert (
        await client.delete(f"/groups/{group.id}/members/{other_admin.id}")
    ).status_code == 403
    assert (
        await client.delete(f"/groups/{group.id}/members/{member.id}")
    ).status_code == 204


async def test_a_member_may_leave_but_the_owner_may_not(client, db):
    owner = await make_user(db, "Owner")
    member = await make_user(db, "Member")
    group = await make_group(db, owner)
    await add_member(db, group, member)

    sign_in(client, member)
    assert (
        await client.delete(f"/groups/{group.id}/members/{member.id}")
    ).status_code == 204

    sign_in(client, owner)
    res = await client.delete(f"/groups/{group.id}/members/{owner.id}")
    assert res.status_code == 409


async def test_transfer_moves_both_records(client, db):
    """The column and the two rows, in one transaction -- a half-applied
    transfer would leave ``group_role`` still answering "owner" for the
    previous owner while the roster claimed otherwise."""
    owner = await make_user(db, "Owner")
    heir = await make_user(db, "Heir")
    group = await make_group(db, owner)
    await add_member(db, group, heir)

    sign_in(client, owner)
    res = await client.post(
        f"/groups/{group.id}/transfer", json={"new_owner_user_id": heir.id}
    )
    assert res.status_code == 200

    # Ids read before expiring: an expired attribute access outside the
    # async context is a MissingGreenlet, not a stale read.
    group_id, owner_id, heir_id = group.id, owner.id, heir.id
    db.expire_all()
    fresh = (
        await db.execute(select(Group).where(Group.id == group_id))
    ).scalar_one()
    assert fresh.owner_id == heir_id
    roles = dict(
        (
            await db.execute(
                select(GroupMembership.user_id, GroupMembership.role).where(
                    GroupMembership.group_id == group_id
                )
            )
        ).all()
    )
    assert roles[heir_id] == "owner"
    # The outgoing owner keeps admin: they were running the place a moment
    # ago, and dropping them to the bottom is a demotion nobody asked for.
    assert roles[owner_id] == "admin"

    # And now the previous owner *can* leave. The cookie is set from the id
    # captured above rather than from the (now expired) row.
    client.cookies.set("session", create_session_cookie(owner_id))
    assert (
        await client.delete(f"/groups/{group_id}/members/{owner_id}")
    ).status_code == 204


async def test_only_the_owner_deletes_and_challenges_block_it(client, db):
    owner = await make_user(db, "Owner")
    admin = await make_user(db, "Admin")
    group = await make_group(db, owner)
    await add_member(db, group, admin, role=GroupRole.ADMIN.value)

    sign_in(client, admin)
    assert (await client.delete(f"/groups/{group.id}")).status_code == 403

    await make_challenge(db, owner, group=group)
    sign_in(client, owner)
    # 409, not a cascade: deleting the group would take its members' logged
    # history with it, which is the harm the challenge delete guard exists
    # to prevent.
    assert (await client.delete(f"/groups/{group.id}")).status_code == 409


async def test_an_empty_group_deletes(client, db):
    owner = await make_user(db, "Owner")
    group = await make_group(db, owner)
    sign_in(client, owner)
    assert (await client.delete(f"/groups/{group.id}")).status_code == 204
    assert (
        await db.execute(select(Group).where(Group.id == group.id))
    ).scalar_one_or_none() is None


# ---------------------------------------------------------------------------
# The management screen's own gate
# ---------------------------------------------------------------------------


async def test_the_manage_page_404s_a_plain_member(client, db):
    """404 and not 403: a page whose content is other people's pending
    requests has no business confirming to a member that it exists."""
    owner = await make_user(db, "Owner")
    member = await make_user(db, "Member")
    group = await make_group(db, owner)
    await add_member(db, group, member)

    sign_in(client, member)
    assert (await client.get(f"/views/groups/{group.id}/manage")).status_code == 404

    sign_in(client, owner)
    assert (await client.get(f"/views/groups/{group.id}/manage")).status_code == 200


async def test_group_pages_redirect_a_signed_out_visitor(client, db):
    """The 303/401 split: a page offers a login, a fragment answers 401."""
    owner = await make_user(db, "Owner")
    group = await make_group(db, owner)
    client.cookies.clear()

    page = await client.get(f"/views/groups/{group.id}", follow_redirects=False)
    assert page.status_code == 303
    assert "/views/auth/" in page.headers["location"]

    fragment = await client.get(
        f"/views/groups/{group.id}/members/fragment", follow_redirects=False
    )
    assert fragment.status_code == 401


# ---------------------------------------------------------------------------
# The screens render
# ---------------------------------------------------------------------------


async def test_every_group_screen_renders(client, db):
    """A smoke test over the five screens.

    Each views router builds its own ``Jinja2Templates`` and owes the
    registrations for what it renders (`register_group_filters`,
    `register_avatar_filters`, `register_icon_filters`), and a missing one is
    a template error at *render* time, not at import -- so it is exactly the
    kind of break no unit test catches and every reader hits.
    """
    from tests.group_helpers import make_challenge as _make_challenge

    owner = await make_user(db, "Owner")
    group = await make_group(db, owner)
    challenge = await _make_challenge(db, owner, group=group)

    from app.groups import new_invite_code
    from app.models.group import GroupInvite

    invite = GroupInvite(
        group_id=group.id, code=new_invite_code(), created_by_user_id=owner.id
    )
    db.add(invite)
    await db.commit()

    sign_in(client, owner)
    for url in (
        "/views/groups/",
        f"/views/groups/{group.id}",
        f"/views/groups/{group.id}/members",
        f"/views/groups/{group.id}/manage",
        f"/views/groups/{group.id}/challenges/{challenge.id}/participants",
        f"/views/invites/{invite.code}",
        # The wizard, opened from inside the group: the one path that renders
        # its two extra questions.
        f"/views/challenges/create?group={group.id}",
        # ...and the challenge itself, which wears the group chip.
        f"/views/challenges/{challenge.id}",
    ):
        res = await client.get(url)
        assert res.status_code == 200, f"{url} -> {res.status_code}"


async def test_the_wizard_is_unchanged_without_a_group(client, db):
    """The constraint the whole create path was written to: a member making a
    personal challenge is asked nothing new."""
    member = await make_user(db, "Member")
    sign_in(client, member)
    page = await client.get("/views/challenges/create")
    assert page.status_code == 200
    assert "این چالش برای کیست؟" not in page.text
    assert "const IN_GROUP = false;" in page.text
