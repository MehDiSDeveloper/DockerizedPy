"""The derived challenge status ("شروع نشده" / "در حال اجرا" / "تمام شده").

The rule is written twice on purpose -- `challenge_status` renders a card
badge, `status_filter` pages the list in SQL -- so the point of these tests is
less each bucket in isolation than that the two halves never disagree.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.challenge import Challenge
from app.routers.challenge import (
    STATUS_ACTIVE,
    STATUS_FINISHED,
    STATUS_UPCOMING,
    challenge_status,
)
from tests.test_challenge_lifecycle import authenticate, create_challenge, make_user


def _iso(dt: datetime) -> str:
    """The shape pydantic's mode="json" writes into the cadence column."""
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


async def _seed(client: AsyncClient, db: AsyncSession) -> dict[str, int]:
    """One challenge per distinguishable case, keyed by its expected status."""
    owner = await make_user(db, "Owner")
    authenticate(client, owner.id)
    now = datetime.now(UTC)

    async def new(title: str, **cadence_kw) -> int:
        resp = await create_challenge(
            client, title=title, cadence=cadence_kw or {"kind": "once"}
        )
        assert resp.status_code == 201, resp.text
        return resp.json()["id"]

    ids = {
        "no_dates": await new("open ended"),
        "future_due": await new("due later"),
        "past_due": await new("due passed"),
        "archived": await new("archived"),
        "draft": await new("draft"),
        "future_session": await new(
            "session ahead",
            kind="schedule",
            datetimes=[_iso(now + timedelta(days=3))],
        ),
        "past_session": await new(
            "session behind",
            kind="schedule",
            datetimes=[_iso(now - timedelta(days=3))],
        ),
        "quota_ended": await new(
            "quota over",
            kind="recurring_quota",
            period="week",
            count=3,
            end_date=_iso(now - timedelta(days=5)),
        ),
        "days_open": await new(
            "still running",
            kind="recurring_days",
            mode="every_n_days",
            n=1,
        ),
    }

    # due_date and lifecycle_status aren't part of the create payload's
    # interesting surface here, so they're set directly.
    await client.patch(
        f"/challenges/{ids['future_due']}",
        json={"due_date": _iso(now + timedelta(days=10))},
    )
    await client.patch(
        f"/challenges/{ids['past_due']}",
        json={"due_date": _iso(now - timedelta(days=1))},
    )
    await client.patch(
        f"/challenges/{ids['archived']}", json={"lifecycle_status": "archived"}
    )
    await client.patch(
        f"/challenges/{ids['draft']}", json={"lifecycle_status": "draft"}
    )
    return ids


EXPECTED = {
    "no_dates": STATUS_ACTIVE,
    "future_due": STATUS_ACTIVE,
    "past_due": STATUS_FINISHED,
    "archived": STATUS_FINISHED,
    "draft": STATUS_UPCOMING,
    "future_session": STATUS_UPCOMING,
    # Only cadence.datetimes[0] is reachable portably from SQL, so a schedule
    # whose sessions have all passed stays active until its due_date passes or
    # the owner archives it. Documented, and asserted so it stays deliberate.
    "past_session": STATUS_ACTIVE,
    "quota_ended": STATUS_FINISHED,
    "days_open": STATUS_ACTIVE,
}


def _ids(resp) -> set[int]:
    return {c["id"] for c in resp.json()}


@pytest.mark.asyncio
async def test_python_rule_buckets_each_case(client: AsyncClient, db: AsyncSession):
    ids = await _seed(client, db)
    by_id = {cid: key for key, cid in ids.items()}

    rows = (await db.execute(select(Challenge))).scalars().all()
    got = {by_id[c.id]: challenge_status(c) for c in rows if c.id in by_id}

    assert got == EXPECTED


@pytest.mark.asyncio
async def test_sql_filter_matches_the_python_rule(
    client: AsyncClient, db: AsyncSession
):
    ids = await _seed(client, db)

    for status in (STATUS_UPCOMING, STATUS_ACTIVE, STATUS_FINISHED):
        expected = {ids[key] for key, value in EXPECTED.items() if value == status}
        resp = await client.get(f"/challenges/?status={status}&limit=100")
        assert resp.status_code == 200, resp.text
        assert _ids(resp) == expected, status


@pytest.mark.asyncio
async def test_status_all_is_the_default_and_filters_nothing(
    client: AsyncClient, db: AsyncSession
):
    ids = await _seed(client, db)

    default = await client.get("/challenges/?limit=100")
    explicit = await client.get("/challenges/?status=all&limit=100")

    assert _ids(default) == set(ids.values())
    assert _ids(explicit) == set(ids.values())


@pytest.mark.asyncio
async def test_unknown_status_is_rejected(client: AsyncClient, db: AsyncSession):
    await _seed(client, db)
    assert (await client.get("/challenges/?status=bogus")).status_code == 422


@pytest.mark.asyncio
async def test_status_survives_the_infinite_scroll_fragment(
    client: AsyncClient, db: AsyncSession
):
    """The fragment endpoint pages the same list, so dropping the parameter
    there would leak the other statuses in on scroll."""
    ids = await _seed(client, db)
    finished = {ids[key] for key, value in EXPECTED.items() if value == STATUS_FINISHED}

    resp = await client.get(
        "/views/challenges/fragment?status=finished&offset=0&limit=100"
    )
    assert resp.status_code == 200, resp.text
    assert resp.headers["X-Has-More"] == "false"
    assert resp.text.count('data-status="finished"') == len(finished)
    assert 'data-status="active"' not in resp.text
    for cid in finished:
        assert f'href="/views/challenges/{cid}"' in resp.text


@pytest.mark.asyncio
async def test_list_page_renders_a_status_badge_per_card(
    client: AsyncClient, db: AsyncSession
):
    ids = await _seed(client, db)

    resp = await client.get("/views/challenges/")
    assert resp.status_code == 200, resp.text
    # One pill per card, plus the labels a reader actually sees -- colour on
    # its own can't carry the status.
    assert resp.text.count('class="status-pill"') == len(ids)
    for label in ("شروع نشده", "در حال اجرا", "تمام‌شده"):
        assert label in resp.text
