"""Server-side Jalali rendering -- the same sentences `app.js` used to write.

Every date a Farsi reader sees is Jalali (see CLAUDE.md, «Dates and times in
the UI»). That conversion used to happen only in the browser, so the first
paint carried a Gregorian ISO string and JS corrected it a frame later. The
markup contract is unchanged -- `data-jalali` still travels on an *instant*,
because only the browser knows how to resolve one into a zone it was judged
in -- but the text a template renders is now already Farsi.

The formats mirror `JALALI_FORMATS` in `app/static/js/app.js` one for one,
including the two places CLDR's `fa` patterns come out year-first and app.js
reassembles them. `tests/test_date_filters.py` pins that against a fixture
generated from ICU itself, so the two halves cannot drift.

Conversion comes from `app.jalali`, which is exact and dependency-free.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.jalali import to_jalali

DEFAULT_FORMAT = "day-month"

# Only the fallback for a challenge-level instant with no enrollment behind
# it -- mirrors APP_TIMEZONE in app.js and DEFAULT_TIMEZONE in
# app/routers/challenge.py.
APP_TIMEZONE = "Asia/Tehran"

PERSIAN_DIGITS = "۰۱۲۳۴۵۶۷۸۹"
_LATIN_TO_PERSIAN = str.maketrans("0123456789", PERSIAN_DIGITS)

MONTH_NAMES = (
    "فروردین", "اردیبهشت", "خرداد", "تیر", "مرداد", "شهریور",
    "مهر", "آبان", "آذر", "دی", "بهمن", "اسفند",
)
# Indexed by Python's `weekday()` (Monday=0), not the Iranian 0=Saturday the
# occurrence engine uses -- this is a display lookup, not calendar arithmetic.
WEEKDAY_NAMES = (
    "دوشنبه", "سه‌شنبه", "چهارشنبه", "پنجشنبه", "جمعه", "شنبه", "یکشنبه",
)


def fa_num(value) -> str:
    """Latin digits -> Persian digits, leaving everything else alone."""
    if value is None:
        return ""
    return str(value).translate(_LATIN_TO_PERSIAN)


def _coerce(value) -> tuple[date | datetime, bool] | None:
    """Return (value, is_instant), or None when it cannot be read.

    A bare ``YYYY-MM-DD`` is a floating calendar date and is never shifted
    between zones; anything carrying a time is an absolute instant, and one
    with no offset is UTC (SQLite has no aware datetime type). Same rule as
    `parseDateValue` in app.js.
    """
    if isinstance(value, datetime):
        return value, True
    if isinstance(value, date):
        return value, False
    text = str(value or "").strip()
    if not text:
        return None
    try:
        if "T" not in text:
            return date.fromisoformat(text), False
        return datetime.fromisoformat(text), True
    except ValueError:
        return None


def _in_zone(moment: datetime, tz: str | None) -> datetime:
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    try:
        return moment.astimezone(ZoneInfo(tz or APP_TIMEZONE))
    except (ZoneInfoNotFoundError, ValueError):
        return moment.astimezone(UTC)


def jalali(value, format: str = DEFAULT_FORMAT, tz: str | None = None) -> str:
    """Render a date or instant as Farsi text, ready for the first paint.

    Answers "" for anything unreadable, so a template never prints a traceback
    or a raw ISO string where a date belongs.
    """
    coerced = _coerce(value)
    if coerced is None:
        return ""
    moment, is_instant = coerced
    if is_instant:
        moment = _in_zone(moment, tz)
        day, clock = moment.date(), moment
    else:
        day, clock = moment, None

    jy, jm, jd = to_jalali(day)
    month = MONTH_NAMES[jm - 1]
    weekday = WEEKDAY_NAMES[day.weekday()]

    if format == "numeric":
        return fa_num(f"{jy:04d}/{jm:02d}/{jd:02d}")
    if format == "month-only":
        return month
    if format == "month":
        # CLDR's fa year+month skeleton is year-first («۱۴۰۵ شهریور»), which no
        # Iranian writes; app.js reassembles it from parts and so does this.
        return fa_num(f"{month} {jy}")
    if format == "weekday-day-month":
        return fa_num(f"{weekday} {jd} {month}")
    if format == "day-month-year":
        return fa_num(f"{jd} {month} {jy}")

    if clock is None:
        # A time-shaped format asked of a floating date has no clock to print;
        # fall back to the date rather than inventing midnight.
        return fa_num(f"{jd} {month}")
    if format == "time":
        return fa_num(f"{clock.hour:02d}:{clock.minute:02d}")
    if format == "datetime":
        return fa_num(f"{jd} {month} ساعت {clock.hour:02d}:{clock.minute:02d}")
    if format == "datetime-full":
        # timeStyle:"short" does not pad the hour, unlike hour:"2-digit".
        return fa_num(f"{jd} {month} {jy} ساعت {clock.hour}:{clock.minute:02d}")
    return fa_num(f"{jd} {month}")


def register_date_filters(env) -> None:
    """Expose the two lookups on one templates environment.

    Same registration contract as `register_icon_filters`: every views router
    builds its own `Jinja2Templates`, and a missing call is a render-time
    error rather than an import-time one.
    """
    env.filters["jalali"] = jalali
    env.filters["fa_num"] = fa_num
