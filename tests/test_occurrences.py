from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

from app.occurrences import (
    compute_streaks,
    derive_state,
    expected_keys_desc,
    is_key_writable,
    is_occurrence_day,
    occurrences_due,
    period_key,
    to_ir_weekday,
    week_start,
)
from app.schemas.cadence import (
    OnceCadence,
    RecurringDaysCadence,
    RecurringQuotaCadence,
    ScheduleCadence,
)

TZ = "Asia/Tehran"

# 2024-01-06 is a real-world Saturday; the following week is Sat..Fri.
SAT, SUN, MON, TUE, WED, THU, FRI = (
    date(2024, 1, 6),
    date(2024, 1, 7),
    date(2024, 1, 8),
    date(2024, 1, 9),
    date(2024, 1, 10),
    date(2024, 1, 11),
    date(2024, 1, 12),
)


def now_at(d: date) -> datetime:
    # local noon in Tehran (UTC+3:30) stays on the same local date
    return datetime(d.year, d.month, d.day, 8, 30, tzinfo=UTC)


# ---------- to_ir_weekday ----------


def test_to_ir_weekday_matches_iranian_calendar():
    assert to_ir_weekday(SAT) == 0
    assert to_ir_weekday(SUN) == 1
    assert to_ir_weekday(MON) == 2
    assert to_ir_weekday(TUE) == 3
    assert to_ir_weekday(WED) == 4
    assert to_ir_weekday(THU) == 5
    assert to_ir_weekday(FRI) == 6


def test_week_start_is_saturday_for_every_weekday():
    for d in (SAT, SUN, MON, TUE, WED, THU, FRI):
        assert week_start(d) == SAT


# ---------- once ----------


def test_once_yields_exactly_one_occurrence():
    cadence = OnceCadence()
    due = occurrences_due(
        cadence,
        start_date=SAT,
        tz=TZ,
        now_utc=now_at(TUE),
        existing_keys=set(),
        period_completed_counts={},
    )
    assert [o.key for o in due] == ["single"]
    assert due[0].closes_at_utc is None


def test_once_is_suppressed_once_completed():
    cadence = OnceCadence()
    due = occurrences_due(
        cadence,
        start_date=SAT,
        tz=TZ,
        now_utc=now_at(TUE),
        existing_keys={"single"},
        period_completed_counts={},
    )
    assert due == []


def test_once_is_never_missed():
    cadence = OnceCadence()
    for d in (SAT, WED, FRI):
        state = derive_state(
            cadence,
            start_date=SAT,
            tz=TZ,
            key="single",
            now_utc=now_at(d),
            row_state=None,
        )
        assert state == "pending"


# ---------- schedule ----------


def test_schedule_only_due_on_matching_local_date():
    dt1 = datetime(2024, 1, 6, 6, 0, tzinfo=UTC)
    dt2 = datetime(2024, 1, 8, 6, 0, tzinfo=UTC)
    dt3 = datetime(2024, 1, 10, 6, 0, tzinfo=UTC)
    cadence = ScheduleCadence(datetimes=[dt1, dt2, dt3])

    due = occurrences_due(
        cadence,
        start_date=SAT,
        tz=TZ,
        now_utc=now_at(MON),
        existing_keys=set(),
        period_completed_counts={},
    )
    assert len(due) == 1
    assert due[0].key == f"S{dt2.isoformat()}"

    # past instants are not "due today"
    due_later = occurrences_due(
        cadence,
        start_date=SAT,
        tz=TZ,
        now_utc=now_at(FRI),
        existing_keys=set(),
        period_completed_counts={},
    )
    assert due_later == []


def test_schedule_key_round_trips():
    dt = datetime(2024, 1, 8, 6, 30, tzinfo=UTC)
    cadence = ScheduleCadence(datetimes=[dt])
    due = occurrences_due(
        cadence,
        start_date=SAT,
        tz=TZ,
        now_utc=now_at(MON),
        existing_keys=set(),
        period_completed_counts={},
    )
    key = due[0].key
    parsed = datetime.fromisoformat(key[1:])
    assert parsed == dt


# ---------- recurring_days: weekdays ----------


def test_recurring_days_weekdays_mode():
    cadence = RecurringDaysCadence(mode="weekdays", weekdays=[0, 2])  # Sat, Mon
    start = SAT
    days = [start + timedelta(days=i) for i in range(14)]
    for d in days:
        expected = to_ir_weekday(d) in (0, 2)
        assert is_occurrence_day(cadence, start, d) == expected


# ---------- recurring_days: every_n_days ----------


def test_every_n_days():
    cadence = RecurringDaysCadence(mode="every_n_days", n=3)
    start = date(2026, 3, 1)
    hits = [i for i in range(10) if is_occurrence_day(cadence, start, start + timedelta(days=i))]
    assert hits == [0, 3, 6, 9]


# ---------- recurring_days: every_n_weeks ----------


def test_every_n_weeks():
    cadence = RecurringDaysCadence(mode="every_n_weeks", n=2)
    start = MON  # 2024-01-08
    for i in range(0, 70, 7):
        d = start + timedelta(days=i)
        week_index = i // 7
        expected = week_index % 2 == 0
        assert is_occurrence_day(cadence, start, d) == expected
    # a same-weekday date in an off week is not an occurrence
    off_week = start + timedelta(days=7)
    assert is_occurrence_day(cadence, start, off_week) is False
    # a different weekday in an on week is not an occurrence
    on_week_wrong_day = start + timedelta(days=15)
    assert is_occurrence_day(cadence, start, on_week_wrong_day) is False


# ---------- recurring_days: every_n_months ----------


def test_every_n_months_pattern():
    cadence = RecurringDaysCadence(mode="every_n_months", n=2)
    start = date(2026, 1, 15)
    assert is_occurrence_day(cadence, start, date(2026, 1, 15)) is True
    assert is_occurrence_day(cadence, start, date(2026, 2, 15)) is False
    assert is_occurrence_day(cadence, start, date(2026, 3, 15)) is True
    assert is_occurrence_day(cadence, start, date(2026, 4, 15)) is False
    assert is_occurrence_day(cadence, start, date(2026, 5, 15)) is True


def test_every_n_months_end_of_month_clamp():
    cadence = RecurringDaysCadence(mode="every_n_months", n=1)
    # 2026 is not a leap year
    start_non_leap = date(2026, 1, 31)
    assert is_occurrence_day(cadence, start_non_leap, date(2026, 2, 28)) is True
    assert is_occurrence_day(cadence, start_non_leap, date(2026, 2, 27)) is False
    # 2028 is a leap year
    start_leap = date(2028, 1, 31)
    assert is_occurrence_day(cadence, start_leap, date(2028, 2, 29)) is True
    assert is_occurrence_day(cadence, start_leap, date(2028, 2, 28)) is False


# ---------- recurring_quota ----------


def test_recurring_quota_week_sequencing_and_refusal():
    cadence = RecurringQuotaCadence(period="week", count=3)
    start = SAT
    pkey = period_key(SAT, "week")

    due1 = occurrences_due(
        cadence, start_date=start, tz=TZ, now_utc=now_at(SUN),
        existing_keys=set(), period_completed_counts={},
    )
    assert due1[0].key == f"{pkey}#1"

    due2 = occurrences_due(
        cadence, start_date=start, tz=TZ, now_utc=now_at(MON),
        existing_keys=set(), period_completed_counts={pkey: 1},
    )
    assert due2[0].key == f"{pkey}#2"

    due3 = occurrences_due(
        cadence, start_date=start, tz=TZ, now_utc=now_at(TUE),
        existing_keys=set(), period_completed_counts={pkey: 2},
    )
    assert due3[0].key == f"{pkey}#3"

    due4 = occurrences_due(
        cadence, start_date=start, tz=TZ, now_utc=now_at(WED),
        existing_keys=set(), period_completed_counts={pkey: 3},
    )
    assert due4 == []
    assert is_key_writable(
        cadence, start_date=start, tz=TZ, key=f"{pkey}#4", now_utc=now_at(WED)
    ) is False


def test_recurring_quota_rolls_over_on_saturday():
    cadence = RecurringQuotaCadence(period="week", count=3)
    start = SAT
    key_this_week = period_key(FRI, "week")
    next_sat = date(2024, 1, 13)
    key_next_week = period_key(next_sat, "week")
    assert key_this_week != key_next_week

    due = occurrences_due(
        cadence, start_date=start, tz=TZ, now_utc=now_at(next_sat),
        existing_keys=set(), period_completed_counts={key_this_week: 3},
    )
    assert due[0].key == f"{key_next_week}#1"


# ---------- end_date / start_date suppression ----------


def test_end_date_cuts_off_occurrences():
    cadence = RecurringDaysCadence(
        mode="every_n_days", n=1, end_date=datetime(2026, 3, 10, tzinfo=UTC)
    )
    start = date(2026, 3, 1)
    assert is_occurrence_day(cadence, start, date(2026, 3, 10)) is True
    assert is_occurrence_day(cadence, start, date(2026, 3, 11)) is False


def test_start_date_suppresses_earlier_occurrences():
    cadence = RecurringDaysCadence(mode="every_n_days", n=1)
    start = date(2026, 3, 5)
    assert is_occurrence_day(cadence, start, date(2026, 3, 4)) is False
    assert is_occurrence_day(cadence, start, date(2026, 3, 5)) is True


# ---------- compute_streaks ----------


def test_compute_streaks_once():
    cadence = OnceCadence()
    current, longest = compute_streaks(
        cadence, start_date=SAT, tz=TZ, now_utc=now_at(WED),
        completed_keys={"single"}, period_completed_counts={},
    )
    assert (current, longest) == (1, 1)

    current, longest = compute_streaks(
        cadence, start_date=SAT, tz=TZ, now_utc=now_at(WED),
        completed_keys=set(), period_completed_counts={},
    )
    assert (current, longest) == (0, 0)


def test_compute_streaks_today_open_does_not_break_streak():
    cadence = RecurringDaysCadence(mode="every_n_days", n=1)
    start = SAT
    today = date(2024, 1, 11)  # Thursday, 5 days after SAT
    completed = {
        f"D{(SAT).isoformat()}",
        f"D{date(2024, 1, 7).isoformat()}",
        f"D{date(2024, 1, 8).isoformat()}",
        f"D{date(2024, 1, 9).isoformat()}",
        f"D{date(2024, 1, 10).isoformat()}",
    }
    # today (Jan 11) has no check-in yet, but its window is still open
    current, longest = compute_streaks(
        cadence, start_date=start, tz=TZ, now_utc=now_at(today),
        completed_keys=completed, period_completed_counts={},
    )
    assert current == 5
    assert longest == 5


def test_compute_streaks_missed_day_breaks_current_streak():
    cadence = RecurringDaysCadence(mode="every_n_days", n=1)
    start = SAT
    today = date(2024, 1, 11)
    completed = {
        f"D{SAT.isoformat()}",
        f"D{date(2024, 1, 7).isoformat()}",
        f"D{date(2024, 1, 8).isoformat()}",
        # 2024-01-09 missing (a real gap, not just "today")
        f"D{date(2024, 1, 10).isoformat()}",
    }
    current, longest = compute_streaks(
        cadence, start_date=start, tz=TZ, now_utc=now_at(today),
        completed_keys=completed, period_completed_counts={},
    )
    # today (open, uncompleted) is skipped; the streak then runs into the
    # 2024-01-09 gap after counting the single completed day (01-10) before it
    assert current == 1
    assert longest == 3


def test_compute_streaks_recurring_quota():
    cadence = RecurringQuotaCadence(period="week", count=3)
    start = SAT
    pkey = period_key(SAT, "week")
    current, longest = compute_streaks(
        cadence, start_date=start, tz=TZ, now_utc=now_at(FRI),
        completed_keys=set(), period_completed_counts={pkey: 3},
    )
    assert current == 1
    assert longest == 1


def test_expected_keys_desc_recurring_quota_uses_bare_period_keys():
    cadence = RecurringQuotaCadence(period="week", count=3)
    start = SAT
    keys = expected_keys_desc(cadence, start_date=start, tz=TZ, until=FRI)
    assert keys == [period_key(SAT, "week")]
