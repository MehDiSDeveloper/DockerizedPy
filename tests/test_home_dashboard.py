"""The home dashboard: what it counts, and what it does when there is nothing.

``build_dashboard`` is the only place in the app that reads a member's whole
run at once rather than one challenge's, so the things worth pinning are the
seams: every figure it shows is a ratio or a share, and each of them divides
by something that is zero on a brand-new account. The other half is the
activity grid's frame -- Saturday-aligned weeks, Iranian weekday order -- which
is what makes each of its rows a single weekday, and is silently wrong (rather
than broken) if the alignment ever slips.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.occurrences import to_ir_weekday, week_start
from app.routers.views.home import DASHBOARD_WEEKS, build_dashboard
from tests.test_checkin_idempotency import (
    TZ,
    authenticate,
    make_challenge_with_enrollment,
    make_user,
    post_checkin,
    today_tehran,
)

DAILY_CADENCE = {
    "kind": "recurring_days",
    "mode": "every_n_days",
    "n": 1,
    "weekdays": [],
    "end_date": None,
}


def now_utc() -> datetime:
    return datetime.now(UTC)


async def _daily_challenge(db: AsyncSession, owner, *, start_days_ago: int = 10):
    return await make_challenge_with_enrollment(
        db,
        owner,
        cadence_kind="recurring_days",
        cadence=DAILY_CADENCE,
        start_date=today_tehran() - timedelta(days=start_days_ago),
    )


@pytest.mark.asyncio
async def test_dashboard_of_an_empty_account_is_all_zeroes(
    client: AsyncClient, db: AsyncSession
):
    """Every headline figure is a share of something, and on a fresh account
    that something is zero. None of them may divide by it."""
    member = await make_user(db, "Newcomer")

    dash = await build_dashboard(db, user_id=member.id, now_utc=now_utc())

    assert dash["today"] == {"done": 0, "due": 0, "total": 0, "pct": 0}
    assert dash["streak_current"] == 0
    assert dash["streak_best"] == 0
    assert dash["total_completed"] == 0
    assert dash["focus"]["rows"] == []
    # The grid is still drawn -- an empty season is a shape too.
    assert len(dash["grid"]["columns"]) == DASHBOARD_WEEKS
    assert dash["grid"]["active_days"] == 0


@pytest.mark.asyncio
async def test_home_page_renders_for_an_account_with_nothing(
    client: AsyncClient, db: AsyncSession
):
    member = await make_user(db, "Newcomer")
    authenticate(client, member.id)

    response = await client.get("/views/home/")

    assert response.status_code == 200, response.text
    # The ring is replaced by its empty line, and the focus section -- which
    # would be four bars of zero -- is dropped entirely rather than shown flat.
    assert "امروز نوبتی نداری" in response.text
    assert "تمرکز تو" not in response.text


@pytest.mark.asyncio
async def test_today_ring_splits_into_done_and_still_due(
    client: AsyncClient, db: AsyncSession
):
    """The ring is today's completions over everything today asked for, and
    the remainder comes from the same source the امروز page renders -- so
    recording an occurrence has to move it from one side to the other."""
    owner = await make_user(db, "Runner")
    authenticate(client, owner.id)
    challenge, _ = await _daily_challenge(db, owner)

    before = await build_dashboard(db, user_id=owner.id, now_utc=now_utc())
    assert before["today"] == {"done": 0, "due": 1, "total": 1, "pct": 0}

    today = today_tehran()
    resp = await post_checkin(
        client, challenge_id=challenge.id, key=f"D{today.isoformat()}"
    )
    assert resp.status_code == 201, resp.text

    after = await build_dashboard(db, user_id=owner.id, now_utc=now_utc())
    assert after["today"] == {"done": 1, "due": 0, "total": 1, "pct": 100}
    assert after["week_done"] == 1
    assert after["total_completed"] == 1


@pytest.mark.asyncio
async def test_only_completed_check_ins_count(client: AsyncClient, db: AsyncSession):
    """A skipped occurrence is a decision, not an activity: it must not darken
    a grid cell or inflate the tally."""
    owner = await make_user(db, "Skipper")
    authenticate(client, owner.id)
    challenge, _ = await _daily_challenge(db, owner)

    today = today_tehran()
    resp = await post_checkin(
        client,
        challenge_id=challenge.id,
        key=f"D{today.isoformat()}",
        state="skipped",
    )
    assert resp.status_code == 201, resp.text

    dash = await build_dashboard(db, user_id=owner.id, now_utc=now_utc())
    assert dash["total_completed"] == 0
    assert dash["today"]["done"] == 0
    assert dash["grid"]["active_days"] == 0
    # ...but it is no longer *due* either -- occurrences_due skips any key
    # already recorded in any state.
    assert dash["today"]["due"] == 0


@pytest.mark.asyncio
async def test_grid_columns_are_saturday_weeks_ending_on_today(
    client: AsyncClient, db: AsyncSession
):
    """Each column is one Saturday-aligned week, so each *row* is one weekday.
    Lose that and the grid still renders -- it just stops meaning anything."""
    member = await make_user(db, "Gridder")
    dash = await build_dashboard(db, user_id=member.id, now_utc=now_utc())
    columns = dash["grid"]["columns"]

    assert len(columns) == DASHBOARD_WEEKS
    for column in columns:
        assert to_ir_weekday(column["start"]) == 0  # Saturday
        assert len(column["days"]) == 7
        assert [d["date"] for d in column["days"]] == [
            column["start"] + timedelta(days=i) for i in range(7)
        ]

    today = datetime.now(UTC).astimezone(ZoneInfo(TZ)).date()
    # The newest column is the week today falls in, and every day past today
    # in it is marked future rather than missed.
    assert columns[-1]["start"] == week_start(today)
    last = columns[-1]["days"]
    assert [d["is_today"] for d in last] == [d["date"] == today for d in last]
    assert [d["is_future"] for d in last] == [d["date"] > today for d in last]


@pytest.mark.asyncio
async def test_grid_darkens_the_day_a_check_in_was_recorded_for(
    client: AsyncClient, db: AsyncSession
):
    """Cells are keyed on the stored occurrence_local_date, not on when the row
    was written -- so a backfilled occurrence lands on the day it was *for*."""
    owner = await make_user(db, "Backfiller")
    authenticate(client, owner.id)
    challenge, _ = await _daily_challenge(db, owner)

    yesterday = today_tehran() - timedelta(days=1)
    resp = await post_checkin(
        client, challenge_id=challenge.id, key=f"D{yesterday.isoformat()}"
    )
    assert resp.status_code == 201, resp.text

    dash = await build_dashboard(db, user_id=owner.id, now_utc=now_utc())
    by_day: dict[date, int] = {
        day["date"]: day["level"]
        for column in dash["grid"]["columns"]
        for day in column["days"]
    }
    assert by_day[yesterday] == 1
    assert by_day[today_tehran()] == 0
    assert dash["today"]["done"] == 0  # yesterday's row is not today's ring
