from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

from app.occurrences import (
    day_bounds_utc,
    local_today,
    occurrences_due,
    week_key,
    week_start,
)
from app.schemas.cadence import RecurringDaysCadence


def test_tehran_instant_rolls_to_next_local_date():
    now_utc = datetime(2026, 9, 14, 20, 45, tzinfo=UTC)
    assert local_today("Asia/Tehran", now_utc) == date(2026, 9, 15)

    cadence = RecurringDaysCadence(mode="every_n_days", n=1)
    due = occurrences_due(
        cadence,
        start_date=date(2026, 9, 1),
        tz="Asia/Tehran",
        now_utc=now_utc,
        existing_keys=set(),
        period_completed_counts={},
    )
    assert due[0].key == "D2026-09-15"


def test_same_instant_different_timezones_derive_different_keys():
    now_utc = datetime(2026, 9, 14, 20, 45, tzinfo=UTC)
    cadence = RecurringDaysCadence(mode="every_n_days", n=1)

    due_tehran = occurrences_due(
        cadence,
        start_date=date(2026, 9, 1),
        tz="Asia/Tehran",
        now_utc=now_utc,
        existing_keys=set(),
        period_completed_counts={},
    )
    due_ny = occurrences_due(
        cadence,
        start_date=date(2026, 9, 1),
        tz="America/New_York",
        now_utc=now_utc,
        existing_keys=set(),
        period_completed_counts={},
    )
    assert due_tehran[0].key != due_ny[0].key
    assert due_tehran[0].key == "D2026-09-15"
    assert due_ny[0].key == "D2026-09-14"


def test_day_bounds_utc_roundtrips_across_dst_transition_berlin():
    tz = "Europe/Berlin"
    # 2026-03-29 is a DST spring-forward day in Europe/Berlin -> 23h local day
    short_day = date(2026, 3, 29)
    start, end = day_bounds_utc(short_day, tz)
    assert (end - start) == timedelta(hours=23)
    assert start.astimezone(ZoneInfo(tz)).date() == short_day
    assert start.astimezone(ZoneInfo(tz)).time().isoformat() == "00:00:00"

    # 2026-10-25 is a DST fall-back day in Europe/Berlin -> 25h local day
    long_day = date(2026, 10, 25)
    start2, end2 = day_bounds_utc(long_day, tz)
    assert (end2 - start2) == timedelta(hours=25)


def test_week_start_is_always_saturday():
    base = date(2024, 1, 6)  # a known Saturday
    for i in range(7):
        d = base + timedelta(days=i)
        assert week_start(d) == base


def test_week_key_rolls_over_friday_night_to_saturday_morning():
    friday_late = datetime(2026, 9, 11, 20, 15, tzinfo=UTC)  # ~23:45 Tehran Fri
    saturday_early = datetime(2026, 9, 11, 21, 0, tzinfo=UTC)  # ~00:30 Tehran Sat

    tz = "Asia/Tehran"
    d_before = local_today(tz, friday_late)
    d_after = local_today(tz, saturday_early)
    assert d_before != d_after
    assert week_key(d_before) != week_key(d_after)
