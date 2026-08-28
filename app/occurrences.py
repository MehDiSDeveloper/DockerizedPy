from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from app.jalali import days_in_month, from_jalali, to_jalali
from app.jalali import month_bounds as jalali_month_bounds
from app.schemas.cadence import (
    CadenceUnion,
    OnceCadence,
    RecurringDaysCadence,
    RecurringQuotaCadence,
    ScheduleCadence,
)

BACKFILL_DAYS = 2
# Guard rail for `M<year>-<month>` period keys, which are Jalali. Wide enough
# to never reject real data, narrow enough to reject a Gregorian year.
PLAUSIBLE_JALALI_YEARS = (1200, 1700)
IR_WEEK_OFFSET = 2
MAX_STREAK_WALK = 400
# How far forward the "what's coming up" preview on challenge-detail is
# allowed to walk before giving up. A `every_n_months` cadence can leave
# months between occurrences, so this is deliberately wider than a year.
MAX_LOOKAHEAD_DAYS = 400


@dataclass
class DueOccurrence:
    key: str
    local_date: date
    opens_at_utc: datetime
    closes_at_utc: datetime | None
    sequence: int | None
    quota_done: int | None
    quota_target: int | None


@dataclass
class UpcomingOccurrence:
    """A future (or still-open) occurrence, for previewing a cadence.

    Unlike `DueOccurrence` this says nothing about whether anything was
    recorded -- it is pure calendar shape, so the detail page can describe a
    challenge to someone who isn't even enrolled in it.
    """

    key: str
    local_date: date
    at_utc: datetime | None
    sequence: int | None


def to_ir_weekday(d: date) -> int:
    return (d.weekday() + IR_WEEK_OFFSET) % 7


def week_start(d: date) -> date:
    return d - timedelta(days=to_ir_weekday(d))


def week_key(d: date) -> str:
    return "W" + week_start(d).isoformat()


def month_key(d: date) -> str:
    """`M<jalali year>-<jalali month>`.

    Months are Jalali, not Gregorian: a "10 times a month" challenge has to
    reset when the user's calendar says a new month started (شهریور), not on
    1 August. Weeks are already Iranian (Saturday-start) for the same reason.
    """
    jy, jm, _ = to_jalali(d)
    return f"M{jy:04d}-{jm:02d}"


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
        return jalali_month_bounds(d)
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
            jy = int(year_str)
            # A pre-migration key (`M2026-08`, Gregorian) is a *syntactically*
            # valid Jalali year, so without this it would resolve ~600 years
            # out instead of being rejected. Fail closed on anything outside a
            # plausible Jalali year rather than silently mis-bucketing it.
            if not PLAUSIBLE_JALALI_YEARS[0] <= jy <= PLAUSIBLE_JALALI_YEARS[1]:
                return None
            period_start = from_jalali(jy, int(month_str), 1)
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
        # Jalali months here too -- "every 2 months" from ۱ شهریور must land on
        # ۱ آبان, not on the 1st of some Gregorian month the user never sees.
        jy, jm, jd = to_jalali(d)
        sy, sm, sd = to_jalali(start_date)
        months_between = (jy - sy) * 12 + (jm - sm)
        if months_between % cadence.n != 0:
            return False
        target_day = min(sd, days_in_month(jy, jm))
        return jd == target_day
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


def upcoming_occurrences(
    cadence: CadenceUnion,
    *,
    start_date: date,
    tz: str,
    now_utc: datetime,
    limit: int = 5,
    lookahead_days: int = MAX_LOOKAHEAD_DAYS,
) -> list[UpcomingOccurrence]:
    """The next `limit` occurrences at or after today, soonest first.

    Mirror image of `expected_keys_desc`, which only ever looks backwards.
    Same key formats, same Iranian week / Jalali month rules -- so a date
    previewed here is the same date that later shows up in the history.
    """
    today = local_today(tz, now_utc)
    horizon = today + timedelta(days=lookahead_days)
    results: list[UpcomingOccurrence] = []

    if isinstance(cadence, OnceCadence):
        # `once` never closes, so it is "upcoming" until it is recorded --
        # which this layer deliberately doesn't know about.
        return [
            UpcomingOccurrence(
                key="single",
                local_date=max(today, start_date),
                at_utc=None,
                sequence=None,
            )
        ]

    if isinstance(cadence, ScheduleCadence):
        for dt in cadence.datetimes:
            local_date = dt.astimezone(ZoneInfo(tz)).date()
            # An instant earlier today is still shown: it stays writable for
            # the rest of the local day (see `is_key_writable`).
            if local_date < today:
                continue
            if local_date > horizon:
                break
            results.append(
                UpcomingOccurrence(
                    key=f"S{dt.isoformat()}",
                    local_date=local_date,
                    at_utc=dt,
                    sequence=None,
                )
            )
            if len(results) >= limit:
                break
        return results

    if isinstance(cadence, RecurringDaysCadence):
        d = max(today, start_date)
        while d <= horizon and len(results) < limit:
            if is_occurrence_day(cadence, start_date, d):
                results.append(
                    UpcomingOccurrence(
                        key=f"D{d.isoformat()}",
                        local_date=d,
                        at_utc=None,
                        sequence=None,
                    )
                )
            d += timedelta(days=1)
        return results

    if isinstance(cadence, RecurringQuotaCadence):
        d = max(today, start_date)
        while d <= horizon and len(results) < limit:
            period_start, period_end = period_bounds(d, cadence.period)
            if cadence.end_date is not None and period_start > cadence.end_date.date():
                break
            results.append(
                UpcomingOccurrence(
                    key=period_key(d, cadence.period),
                    local_date=max(period_start, start_date),
                    at_utc=None,
                    sequence=None,
                )
            )
            d = period_end + timedelta(days=1)
        return results

    return results


def count_occurrences_between(
    cadence: CadenceUnion,
    *,
    start_date: date,
    tz: str,
    since: date,
    until: date,
) -> int:
    """How many occurrences fall in [since, until] -- the honest way to say
    "how often is this, really" for a cadence whose rule doesn't map onto a
    round number per week (every 10 days, every 2 Jalali months, ...)."""
    if until < since:
        return 0

    if isinstance(cadence, OnceCadence):
        # A one-off has no scheduled date -- it counts as pending work for
        # every window that hasn't already ended before it became available.
        return 1 if start_date <= until else 0

    if isinstance(cadence, ScheduleCadence):
        return sum(
            1
            for dt in cadence.datetimes
            if since <= dt.astimezone(ZoneInfo(tz)).date() <= until
        )

    if isinstance(cadence, RecurringDaysCadence):
        count = 0
        d = since
        while d <= until:
            if is_occurrence_day(cadence, start_date, d):
                count += 1
            d += timedelta(days=1)
        return count

    if isinstance(cadence, RecurringQuotaCadence):
        periods = 0
        d = since
        while d <= until:
            _, period_end = period_bounds(d, cadence.period)
            if cadence.end_date is not None and d > cadence.end_date.date():
                break
            periods += 1
            d = period_end + timedelta(days=1)
        return periods * cadence.count

    return 0


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
