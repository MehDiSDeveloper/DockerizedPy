from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from app.schemas.cadence import (
    CadenceUnion,
    OnceCadence,
    RecurringDaysCadence,
    RecurringQuotaCadence,
    ScheduleCadence,
)

BACKFILL_DAYS = 2
IR_WEEK_OFFSET = 2
MAX_STREAK_WALK = 400


@dataclass
class DueOccurrence:
    key: str
    local_date: date
    opens_at_utc: datetime
    closes_at_utc: datetime | None
    sequence: int | None
    quota_done: int | None
    quota_target: int | None


def to_ir_weekday(d: date) -> int:
    return (d.weekday() + IR_WEEK_OFFSET) % 7


def week_start(d: date) -> date:
    return d - timedelta(days=to_ir_weekday(d))


def week_key(d: date) -> str:
    return "W" + week_start(d).isoformat()


def month_key(d: date) -> str:
    return f"M{d.year:04d}-{d.month:02d}"


def period_key(d: date, period: str) -> str:
    if period == "week":
        return week_key(d)
    if period == "month":
        return month_key(d)
    raise ValueError(f"unknown period: {period}")


def period_bounds(d: date, period: str) -> tuple[date, date]:
    if period == "week":
        start = week_start(d)
        return start, start + timedelta(days=6)
    if period == "month":
        start = d.replace(day=1)
        last_day = calendar.monthrange(d.year, d.month)[1]
        return start, d.replace(day=last_day)
    raise ValueError(f"unknown period: {period}")


def local_today(tz: str, now_utc: datetime) -> date:
    if now_utc.tzinfo is None:
        now_utc = now_utc.replace(tzinfo=UTC)
    return now_utc.astimezone(ZoneInfo(tz)).date()


def day_bounds_utc(d: date, tz: str) -> tuple[datetime, datetime]:
    z = ZoneInfo(tz)
    start = datetime.combine(d, time.min, tzinfo=z).astimezone(UTC)
    end = datetime.combine(d + timedelta(days=1), time.min, tzinfo=z).astimezone(UTC)
    return start, end


def _parse_period_key(period: str, pkey: str) -> date | None:
    try:
        if period == "week":
            if not pkey.startswith("W"):
                return None
            period_start = date.fromisoformat(pkey[1:])
        elif period == "month":
            if not pkey.startswith("M"):
                return None
            year_str, month_str = pkey[1:].split("-")
            period_start = date(int(year_str), int(month_str), 1)
        else:
            return None
    except (ValueError, IndexError):
        return None
    if period_key(period_start, period) != pkey:
        return None
    return period_start


def is_occurrence_day(cadence: RecurringDaysCadence, start_date: date, d: date) -> bool:
    if d < start_date:
        return False
    if cadence.end_date is not None and d > cadence.end_date.date():
        return False
    if cadence.mode == "weekdays":
        return to_ir_weekday(d) in cadence.weekdays
    if cadence.mode == "every_n_days":
        return (d - start_date).days % cadence.n == 0
    if cadence.mode == "every_n_weeks":
        if to_ir_weekday(d) != to_ir_weekday(start_date):
            return False
        weeks_between = (week_start(d) - week_start(start_date)).days // 7
        return weeks_between % cadence.n == 0
    if cadence.mode == "every_n_months":
        months_between = (d.year - start_date.year) * 12 + (d.month - start_date.month)
        if months_between % cadence.n != 0:
            return False
        target_day = min(start_date.day, calendar.monthrange(d.year, d.month)[1])
        return d.day == target_day
    return False


def occurrences_due(
    cadence: CadenceUnion,
    *,
    start_date: date,
    tz: str,
    now_utc: datetime,
    existing_keys: set[str],
    period_completed_counts: dict[str, int],
) -> list[DueOccurrence]:
    today = local_today(tz, now_utc)
    results: list[DueOccurrence] = []

    if isinstance(cadence, OnceCadence):
        if "single" not in existing_keys:
            opens_at, _ = day_bounds_utc(today, tz)
            results.append(
                DueOccurrence(
                    key="single",
                    local_date=today,
                    opens_at_utc=opens_at,
                    closes_at_utc=None,
                    sequence=None,
                    quota_done=None,
                    quota_target=None,
                )
            )
        return results

    if isinstance(cadence, ScheduleCadence):
        for dt in cadence.datetimes:
            local_date = dt.astimezone(ZoneInfo(tz)).date()
            if local_date != today:
                continue
            key = f"S{dt.isoformat()}"
            if key in existing_keys:
                continue
            _, closes_at = day_bounds_utc(local_date, tz)
            results.append(
                DueOccurrence(
                    key=key,
                    local_date=local_date,
                    opens_at_utc=dt,
                    closes_at_utc=closes_at,
                    sequence=None,
                    quota_done=None,
                    quota_target=None,
                )
            )
        return results

    if isinstance(cadence, RecurringDaysCadence):
        if is_occurrence_day(cadence, start_date, today):
            key = f"D{today.isoformat()}"
            if key not in existing_keys:
                opens_at, closes_at = day_bounds_utc(today, tz)
                results.append(
                    DueOccurrence(
                        key=key,
                        local_date=today,
                        opens_at_utc=opens_at,
                        closes_at_utc=closes_at,
                        sequence=None,
                        quota_done=None,
                        quota_target=None,
                    )
                )
        return results

    if isinstance(cadence, RecurringQuotaCadence):
        pkey = period_key(today, cadence.period)
        done = period_completed_counts.get(pkey, 0)
        if done < cadence.count:
            seq = done + 1
            key = f"{pkey}#{seq}"
            period_start, period_end = period_bounds(today, cadence.period)
            opens_at, _ = day_bounds_utc(period_start, tz)
            _, closes_at = day_bounds_utc(period_end, tz)
            results.append(
                DueOccurrence(
                    key=key,
                    local_date=today,
                    opens_at_utc=opens_at,
                    closes_at_utc=closes_at,
                    sequence=seq,
                    quota_done=done,
                    quota_target=cadence.count,
                )
            )
        return results

    return results


def is_key_writable(
    cadence: CadenceUnion,
    *,
    start_date: date,
    tz: str,
    key: str,
    now_utc: datetime,
) -> bool:
    today = local_today(tz, now_utc)

    if isinstance(cadence, OnceCadence):
        return key == "single"

    if isinstance(cadence, ScheduleCadence):
        if not key.startswith("S"):
            return False
        try:
            dt = datetime.fromisoformat(key[1:])
        except ValueError:
            return False
        if dt.tzinfo is None:
            return False
        dt_utc = dt.astimezone(UTC)
        if dt_utc not in cadence.datetimes:
            return False
        local_date = dt_utc.astimezone(ZoneInfo(tz)).date()
        delta = (today - local_date).days
        return 0 <= delta <= BACKFILL_DAYS

    if isinstance(cadence, RecurringDaysCadence):
        if not key.startswith("D"):
            return False
        try:
            d = date.fromisoformat(key[1:])
        except ValueError:
            return False
        if not is_occurrence_day(cadence, start_date, d):
            return False
        delta = (today - d).days
        return 0 <= delta <= BACKFILL_DAYS

    if isinstance(cadence, RecurringQuotaCadence):
        if "#" not in key:
            return False
        pkey, _, seq_str = key.rpartition("#")
        if not seq_str.isdigit():
            return False
        seq = int(seq_str)
        if not (1 <= seq <= cadence.count):
            return False
        period_start = _parse_period_key(cadence.period, pkey)
        if period_start is None:
            return False
        _, period_end = period_bounds(period_start, cadence.period)
        if period_end < start_date:
            return False
        if cadence.end_date is not None and period_start > cadence.end_date.date():
            return False
        if period_start <= today <= period_end:
            return True
        delta = (today - period_end).days
        return 0 <= delta <= BACKFILL_DAYS

    return False


def _window_still_open(cadence: CadenceUnion, tz: str, key: str, today: date) -> bool:
    if isinstance(cadence, OnceCadence):
        return True

    if isinstance(cadence, ScheduleCadence):
        if not key.startswith("S"):
            return False
        try:
            dt = datetime.fromisoformat(key[1:])
        except ValueError:
            return False
        local_date = dt.astimezone(ZoneInfo(tz)).date()
        return local_date == today

    if isinstance(cadence, RecurringDaysCadence):
        if not key.startswith("D"):
            return False
        try:
            d = date.fromisoformat(key[1:])
        except ValueError:
            return False
        return d == today

    if isinstance(cadence, RecurringQuotaCadence):
        pkey = key.split("#", 1)[0]
        period_start = _parse_period_key(cadence.period, pkey)
        if period_start is None:
            return False
        _, period_end = period_bounds(period_start, cadence.period)
        return period_start <= today <= period_end

    return False


def derive_state(
    cadence: CadenceUnion,
    *,
    start_date: date,
    tz: str,
    key: str,
    now_utc: datetime,
    row_state: str | None,
) -> str:
    if row_state is not None:
        return row_state
    today = local_today(tz, now_utc)
    if _window_still_open(cadence, tz, key, today):
        return "pending"
    return "missed"


def expected_keys_desc(
    cadence: CadenceUnion,
    *,
    start_date: date,
    tz: str,
    until: date,
    limit: int = MAX_STREAK_WALK,
) -> list[str]:
    keys: list[str] = []

    if isinstance(cadence, OnceCadence):
        if until >= start_date:
            keys.append("single")
        return keys

    if isinstance(cadence, ScheduleCadence):
        items = []
        for dt in cadence.datetimes:
            local_date = dt.astimezone(ZoneInfo(tz)).date()
            if start_date <= local_date <= until:
                items.append(dt)
        items.sort(reverse=True)
        for dt in items[:limit]:
            keys.append(f"S{dt.isoformat()}")
        return keys

    if isinstance(cadence, RecurringDaysCadence):
        d = until
        while d >= start_date and len(keys) < limit:
            if is_occurrence_day(cadence, start_date, d):
                keys.append(f"D{d.isoformat()}")
            d -= timedelta(days=1)
        return keys

    if isinstance(cadence, RecurringQuotaCadence):
        d = until
        while len(keys) < limit:
            period_start, period_end = period_bounds(d, cadence.period)
            if period_end < start_date:
                break
            if cadence.end_date is None or period_start <= cadence.end_date.date():
                keys.append(period_key(d, cadence.period))
            d = period_start - timedelta(days=1)
        return keys

    return keys


def compute_streaks(
    cadence: CadenceUnion,
    *,
    start_date: date,
    tz: str,
    now_utc: datetime,
    completed_keys: set[str],
    period_completed_counts: dict[str, int],
) -> tuple[int, int]:
    today = local_today(tz, now_utc)
    keys = expected_keys_desc(cadence, start_date=start_date, tz=tz, until=today)

    def satisfied(k: str) -> bool:
        if isinstance(cadence, RecurringQuotaCadence):
            return period_completed_counts.get(k, 0) >= cadence.count
        return k in completed_keys

    i = 0
    if keys and not satisfied(keys[0]) and _window_still_open(cadence, tz, keys[0], today):
        i = 1

    current = 0
    while i < len(keys) and satisfied(keys[i]):
        current += 1
        i += 1

    longest = 0
    run = 0
    for k in reversed(keys):
        run = run + 1 if satisfied(k) else 0
        longest = max(longest, run)
    longest = max(longest, current)

    return current, longest
