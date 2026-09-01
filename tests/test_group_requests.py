"""The membership request queue on a screen of its own.

Three things are worth pinning, and each is a decision rather than a
mechanism:

* **The default is the open queue.** Decided requests are history -- what
  somebody comes here to check, not to act on -- so they are one tap away
  and never mixed into the list of people still waiting.
* **The order flips with the state.** Pending is a *queue* (oldest first,
  because the longest wait is the answer that is owed); decided is history
  (newest first off ``decided_at``, which is when the thing the reader is
  looking for actually happened).
* **The gate is the manage page's, not the group's.** A member who is not an
  administrator gets a **404** on the page -- a screen whose whole content is
  other people's requests has no business confirming to them that it exists
  -- while the fragment answers a ``fetch()`` a real 401/403.
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


async def test_page_is_404_for_a_member_and_open_to_an_administrator(client, db):
    owner = await make_user(db, "Owner")
    group = await make_group(db, owner)
    plain = await make_user(db, "Plain")
    await add_member(db, group, plain)

    sign_in(client, plain)
    assert (await client.get(f"/views/groups/{group.id}/requests")).status_code == 404

    sign_in(client, owner)
    assert (await client.get(f"/views/groups/{group.id}/requests")).status_code == 200


async def test_page_is_404_for_an_outsider(client, db):
    """The group itself is a 404 to anybody not in it, and this page inherits
    that from ``load_group`` rather than checking anything of its own."""
    owner = await make_user(db, "Owner")
    group = await make_group(db, owner)
    outsider = await make_user(db, "Outsider")

    sign_in(client, outsider)
    assert (await client.get(f"/views/groups/{group.id}/requests")).status_code == 404


async def test_fragment_answers_a_fetch_rather_than_redirecting(client, db):
    """The 303/401 split: a page redirects a signed-out visitor to login, a
    fragment must answer a status the scroller can read."""
    owner = await make_user(db, "Owner")
    group = await make_group(db, owner)
    client.cookies.clear()
    assert (
        await client.get(f"/views/groups/{group.id}/requests/fragment")
    ).status_code == 401


async def test_an_unknown_state_is_refused_not_silently_the_queue(client, db):
    owner = await make_user(db, "Owner")
    group = await make_group(db, owner)
    sign_in(client, owner)
    r = await client.get(f"/views/groups/{group.id}/requests?status=everything")
    assert r.status_code == 422


# ---------------------------------------------------------------------------
# What the page shows
# ---------------------------------------------------------------------------


async def test_the_default_is_the_open_queue_only(client, db):
    owner = await make_user(db, "Owner")
    group = await make_group(db, owner)
    waiting = await make_user(db, "Waiting")
    done = await make_user(db, "Done")
    await make_request(db, group, waiting)
    await make_request(
        db,
        group,
        done,
        status=JoinRequestStatus.APPROVED.value,
        decided_at=datetime.now(UTC),
        decided_by=owner,
    )

    sign_in(client, owner)
    body = (await client.get(f"/views/groups/{group.id}/requests")).text
    assert "Waiting" in body
    assert "Done" not in body

    body = (await client.get(f"/views/groups/{group.id}/requests?status=approved")).text
    assert "Done" in body
    assert "Waiting" not in body


async def test_a_decided_row_names_when_and_by_whom(client, db):
    """The pair is the only thing this table records that the membership row
    cannot say, and it is the whole reason a terminal row is kept."""
    owner = await make_user(db, "Owner")
    group = await make_group(db, owner)
    rejected = await make_user(db, "Rejected")
    await make_request(
        db,
        group,
        rejected,
        status=JoinRequestStatus.REJECTED.value,
        decided_at=datetime.now(UTC),
        decided_by=owner,
    )

    sign_in(client, owner)
    body = (await client.get(f"/views/groups/{group.id}/requests?status=rejected")).text
    assert "Rejected" in body
    assert "Owner" in body


async def test_pending_is_oldest_first_and_history_is_newest_first(client, db):
    owner = await make_user(db, "Owner")
    group = await make_group(db, owner)
    now = datetime.now(UTC)

    early = await make_user(db, "Early")
    late = await make_user(db, "Late")
    await make_request(db, group, early, created_at=now - timedelta(days=3))
    await make_request(db, group, late, created_at=now - timedelta(days=1))

    sign_in(client, owner)
    body = (await client.get(f"/views/groups/{group.id}/requests")).text
    # A queue, not a feed: the longest wait is the answer that is owed.
    assert body.index("Early") < body.index("Late")

    old = await make_user(db, "OldDecision")
    fresh = await make_user(db, "FreshDecision")
    await make_request(
        db,
        group,
        old,
        status=JoinRequestStatus.APPROVED.value,
        decided_at=now - timedelta(days=5),
        decided_by=owner,
    )
    await make_request(
        db,
        group,
        fresh,
        status=JoinRequestStatus.APPROVED.value,
        decided_at=now - timedelta(hours=1),
        decided_by=owner,
    )
    body = (await client.get(f"/views/groups/{group.id}/requests?status=approved")).text
    assert body.index("FreshDecision") < body.index("OldDecision")


async def test_the_fragment_pages_and_declares_more(client, db):
    owner = await make_user(db, "Owner")
    group = await make_group(db, owner)
    for i in range(3):
        await make_request(db, group, await make_user(db, f"Asker{i}"))

    sign_in(client, owner)
    r = await client.get(f"/views/groups/{group.id}/requests/fragment?offset=0&limit=2")
    assert r.status_code == 200
    assert r.headers["X-Has-More"] == "true"
    r = await client.get(f"/views/groups/{group.id}/requests/fragment?offset=2&limit=2")
    assert r.headers["X-Has-More"] == "false"


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
