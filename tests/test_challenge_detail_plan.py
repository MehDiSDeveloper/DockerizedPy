"""The cadence plan card on challenge-detail.

Two layers are covered here: the pure occurrence-engine helpers that look
*forward* (`upcoming_occurrences` / `count_occurrences_between` -- everything
else in occurrences.py only ever walks backwards), and the Farsi summary the
view builds on top of them, which has to say something correct for every
cadence kind.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession
from test_challenge_lifecycle import authenticate, create_challenge, make_user

from app.occurrences import count_occurrences_between, upcoming_occurrences
from app.routers.views.challenge import build_cadence_plan, describe_cadence
from app.schemas.cadence import (
    OnceCadence,
    RecurringDaysCadence,
    RecurringQuotaCadence,
    ScheduleCadence,
)

TZ = "Asia/Tehran"
# A Wednesday. 1405-06-06 Jalali, mid-Shahrivar -- far enough from either
# Jalali month edge that a month-period assertion isn't accidentally testing
# the boundary code that test_timezone_boundaries.py already owns.
NOW = datetime(2026, 8, 27, 9, 0, tzinfo=UTC)
START = date(2026, 8, 1)


def test_upcoming_recurring_days_lists_only_real_occurrence_days():
    cadence = RecurringDaysCadence(mode="weekdays", weekdays=[0, 2, 4])  # ش/د/چ
    upcoming = upcoming_occurrences(
        cadence, start_date=START, tz=TZ, now_utc=NOW, limit=3
    )

    assert [u.local_date.isoformat() for u in upcoming] == [
        "2026-08-29",  # Saturday
        "2026-08-31",  # Monday
        "2026-09-02",  # Wednesday
    ]
    assert [u.key for u in upcoming] == [
        "D2026-08-29",
        "D2026-08-31",
        "D2026-09-02",
    ]


def test_upcoming_skips_days_before_the_enrollment_starts():
    cadence = RecurringDaysCadence(mode="every_n_days", n=1)
    future_start = date(2026, 9, 10)
    upcoming = upcoming_occurrences(
        cadence, start_date=future_start, tz=TZ, now_utc=NOW, limit=2
    )
    assert [u.local_date for u in upcoming] == [date(2026, 9, 10), date(2026, 9, 11)]


def test_upcoming_schedule_keeps_earlier_today_but_drops_yesterday():
    cadence = ScheduleCadence(
        datetimes=[
            datetime(2026, 8, 26, 6, 0, tzinfo=UTC),  # yesterday: gone
            datetime(2026, 8, 27, 3, 0, tzinfo=UTC),  # earlier today: still writable
            datetime(2026, 9, 1, 6, 0, tzinfo=UTC),
        ]
    )
    upcoming = upcoming_occurrences(cadence, start_date=START, tz=TZ, now_utc=NOW)

    assert [u.at_utc.isoformat() for u in upcoming] == [
        "2026-08-27T03:00:00+00:00",
        "2026-09-01T06:00:00+00:00",
    ]


def test_upcoming_quota_walks_jalali_months():
    cadence = RecurringQuotaCadence(period="month", count=5)
    upcoming = upcoming_occurrences(
        cadence, start_date=START, tz=TZ, now_utc=NOW, limit=3
    )
    # Shahrivar, Mehr, Aban 1405 -- not Gregorian months.
    assert [u.key for u in upcoming] == ["M1405-06", "M1405-07", "M1405-08"]


def test_count_occurrences_between_counts_only_the_window():
    cadence = RecurringDaysCadence(mode="every_n_days", n=3)
    n = count_occurrences_between(
        cadence,
        start_date=START,
        tz=TZ,
        since=date(2026, 8, 27),
        until=date(2026, 9, 25),
    )
    assert n == 10  # 30 days / every third day


def test_describe_cadence_covers_every_kind():
    assert describe_cadence(OnceCadence()) == "یک بار، هر وقت آماده بودی"
    assert "3" in describe_cadence(
        ScheduleCadence(
            datetimes=[
                datetime(2026, 9, d, 6, 0, tzinfo=UTC) for d in (1, 2, 3)
            ]
        )
    )
    assert describe_cadence(
        RecurringDaysCadence(mode="weekdays", weekdays=[0, 2])
    ) == "هر هفته: شنبه و دوشنبه"
    assert describe_cadence(
        RecurringDaysCadence(mode="every_n_days", n=1)
    ) == "هر روز"
    assert describe_cadence(
        RecurringDaysCadence(mode="every_n_weeks", n=2)
    ) == "هر 2 هفته یک بار"
    assert describe_cadence(
        RecurringQuotaCadence(period="week", count=4)
    ) == "4 بار در هر هفته"


def test_plan_for_once_has_no_calendar_to_preview():
    plan = build_cadence_plan(OnceCadence(), start_date=START, tz=TZ, now_utc=NOW)
    assert plan["kind"] == "once"
    assert plan["upcoming"] == []
    assert plan["weekdays"] is None
    assert any(f["label"] == "مهلت ثبت" for f in plan["facts"])


def test_plan_for_schedule_reports_sessions_passed():
    cadence = ScheduleCadence(
        datetimes=[
            datetime(2026, 8, 10, 6, 0, tzinfo=UTC),
            datetime(2026, 8, 20, 6, 0, tzinfo=UTC),
            datetime(2026, 9, 1, 6, 0, tzinfo=UTC),
            datetime(2026, 9, 8, 6, 0, tzinfo=UTC),
        ]
    )
    plan = build_cadence_plan(cadence, start_date=START, tz=TZ, now_utc=NOW)

    assert plan["progress"] == {
        "done": 2,
        "total": 4,
        "pct": 50,
        "label": "جلسهٔ سپری‌شده",
    }
    assert len(plan["upcoming"]) == 2
    assert plan["upcoming"][0]["is_next"] is True
    assert plan["upcoming"][0]["at"] is not None


def test_plan_for_weekdays_marks_all_seven_days():
    cadence = RecurringDaysCadence(mode="weekdays", weekdays=[0, 4])
    plan = build_cadence_plan(cadence, start_date=START, tz=TZ, now_utc=NOW)

    assert [d["on"] for d in plan["weekdays"]] == [
        True, False, False, False, True, False, False
    ]
    assert plan["weekdays"][0]["name"] == "شنبه"
    assert plan["density"] == "8 نوبت در 30 روز آینده"


def test_plan_for_quota_names_the_current_jalali_period():
    cadence = RecurringQuotaCadence(period="month", count=5)
    plan = build_cadence_plan(cadence, start_date=START, tz=TZ, now_utc=NOW)

    current = next(f for f in plan["facts"] if f["label"] == "دورهٔ جاری")
    assert current["date"] == "2026-08-23"  # 1 Shahrivar 1405
    assert current["date_to"] == "2026-09-22"
    # The current period is the quota history strip's job, so the preview
    # starts at the *next* one.
    assert plan["upcoming"][0]["key"] == "M1405-07"
    assert plan["upcoming_format"] == "month"


@pytest.mark.asyncio
async def test_detail_page_renders_the_plan_for_a_recurring_challenge(
    client: AsyncClient, db: AsyncSession
):
    owner = await make_user(db, "Owner")
    authenticate(client, owner.id)
    created = await create_challenge(
        client,
        cadence={"kind": "recurring_days", "mode": "weekdays", "weekdays": [0, 2]},
    )
    challenge_id = created.json()["id"]

    page = await client.get(f"/views/challenges/{challenge_id}")
    assert page.status_code == 200, page.text
    html = page.text

    assert "برنامهٔ چالش" in html
    assert "هر هفته: شنبه و دوشنبه" in html
    assert "weekday-strip" in html
    assert "نوبت‌های بعدی" in html
    # The details grid and personal panel come with it.
    assert "وضعیت من" in html
    assert "Owner" in html


@pytest.mark.asyncio
async def test_detail_page_plan_is_visible_without_enrolling(
    client: AsyncClient, db: AsyncSession
):
    owner = await make_user(db, "Owner")
    visitor = await make_user(db, "Visitor")

    authenticate(client, owner.id)
    created = await create_challenge(
        client, cadence={"kind": "recurring_quota", "period": "week", "count": 3}
    )
    challenge_id = created.json()["id"]

    authenticate(client, visitor.id)
    page = await client.get(f"/views/challenges/{challenge_id}")
    assert page.status_code == 200, page.text

    assert "3 بار در هر هفته" in page.text
    assert "دورهٔ جاری" in page.text
    # ...but nothing personal, since the visitor has no enrollment.
    assert "وضعیت من" not in page.text


def test_plan_for_quota_reports_the_real_jalali_period_length():
    # Shahrivar has 31 days; Mehr has 30. The label must come from the actual
    # period bounds, not from a hardcoded month length.
    plan = build_cadence_plan(
        RecurringQuotaCadence(period="month", count=10),
        start_date=START,
        tz=TZ,
        now_utc=NOW,
    )
    assert plan["density"] == "هر دوره 31 روزه"
