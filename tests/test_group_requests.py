"""The membership request queue, folded into the manage page.

Two things are worth pinning, and each is a decision rather than a mechanism:

* **The gate is the manage page's.** A member who is not an administrator
  gets a **404** on ``/manage`` -- a screen whose whole content is other
  people's requests has no business confirming to them that it exists.
* **Pending is a queue, oldest first** -- the longest wait is the answer
  that is owed, and this is the one list in the app that deliberately does
  not read newest-first.

The JSON twin (``GET /groups/{id}/requests``) still answers all three states
for an API consumer; only the SSR screen and its ``?status=`` filter were
folded away.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.models.group import GroupJoinRequest, GroupRole, JoinRequestStatus
from tests.group_helpers import add_member, make_group, make_user, sign_in


async def make_request(
    db,
    group,
    user,
    *,
    status: str = JoinRequestStatus.PENDING.value,
    created_at: datetime | None = None,
    decided_at: datetime | None = None,
    decided_by=None,
) -> GroupJoinRequest:
    row = GroupJoinRequest(
        group_id=group.id,
        user_id=user.id,
        status=status,
        decided_at=decided_at,
        decided_by_user_id=decided_by.id if decided_by else None,
        last_modifier_user_id=user.id,
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    if created_at is not None:
        row.created_at = created_at
        await db.commit()
        await db.refresh(row)
    return row


# ---------------------------------------------------------------------------
# The gate
# ---------------------------------------------------------------------------


async def test_manage_page_is_404_for_a_member_and_open_to_an_administrator(
    client, db
):
    owner = await make_user(db, "Owner")
    group = await make_group(db, owner)
    plain = await make_user(db, "Plain")
    await add_member(db, group, plain)

    sign_in(client, plain)
    assert (await client.get(f"/views/groups/{group.id}/manage")).status_code == 404

    sign_in(client, owner)
    assert (await client.get(f"/views/groups/{group.id}/manage")).status_code == 200


async def test_manage_page_is_404_for_an_outsider(client, db):
    """The group itself is a 404 to anybody not in it, and this page inherits
    that from ``load_group`` rather than checking anything of its own."""
    owner = await make_user(db, "Owner")
    group = await make_group(db, owner)
    outsider = await make_user(db, "Outsider")

    sign_in(client, outsider)
    assert (await client.get(f"/views/groups/{group.id}/manage")).status_code == 404


# ---------------------------------------------------------------------------
# What the manage page shows
# ---------------------------------------------------------------------------


async def test_manage_page_names_a_pending_requester(client, db):
    owner = await make_user(db, "Owner")
    group = await make_group(db, owner)
    waiting = await make_user(db, "Waiting")
    await make_request(db, group, waiting)

    sign_in(client, owner)
    body = (await client.get(f"/views/groups/{group.id}/manage")).text
    assert "Waiting" in body


async def test_pending_is_oldest_first(client, db):
    owner = await make_user(db, "Owner")
    group = await make_group(db, owner)
    now = datetime.now(UTC)

    early = await make_user(db, "Early")
    late = await make_user(db, "Late")
    await make_request(db, group, early, created_at=now - timedelta(days=3))
    await make_request(db, group, late, created_at=now - timedelta(days=1))

    sign_in(client, owner)
    body = (await client.get(f"/views/groups/{group.id}/manage")).text
    # A queue, not a feed: the longest wait is the answer that is owed.
    assert body.index("Early") < body.index("Late")


# ---------------------------------------------------------------------------
# The JSON twin
# ---------------------------------------------------------------------------


async def test_json_list_takes_the_same_three_states(client, db):
    owner = await make_user(db, "Owner")
    group = await make_group(db, owner)
    asker = await make_user(db, "Asker")
    await make_request(
        db,
        group,
        asker,
        status=JoinRequestStatus.APPROVED.value,
        decided_at=datetime.now(UTC),
        decided_by=owner,
    )

    sign_in(client, owner)
    assert (await client.get(f"/groups/{group.id}/requests")).json() == []
    rows = (await client.get(f"/groups/{group.id}/requests?status=approved")).json()
    assert [r["user_id"] for r in rows] == [asker.id]
    assert rows[0]["decided_by_name"] == "Owner"
    assert (
        await client.get(f"/groups/{group.id}/requests?status=nope")
    ).status_code == 422


async def test_json_list_is_403_for_a_plain_member(client, db):
    """A fixed path reached by a fetch: 403, not 404 -- everybody in a group
    can already see who administers it."""
    owner = await make_user(db, "Owner")
    group = await make_group(db, owner)
    plain = await make_user(db, "Plain")
    await add_member(db, group, plain, role=GroupRole.MEMBER.value)

    sign_in(client, plain)
    assert (await client.get(f"/groups/{group.id}/requests")).status_code == 403
