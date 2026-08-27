"""Jalali (Solar Hijri) <-> Gregorian conversion.

Pure arithmetic, no dependencies and no I/O -- deliberately. A Jalali package
would have to be added to `requirements.txt`, which is UTF-16 encoded and easy
to corrupt (see CLAUDE.md), and `faker` already demonstrates how a dependency
that only exists in the local venv breaks inside the Docker image.

This implements Borkowski's algorithm, the same one behind jalaali-js and
`jdatetime`. It is exact -- `tests/test_jalali.py` checks every one of the
~44,000 days from 1300-01-01 to 1420-12-29 against ICU's Persian calendar
(via `Intl.DateTimeFormat`), which is the reference the browser UI formats with.

Used by `app.occurrences` so that a `recurring_quota` challenge with
`period="month"` counts a *Jalali* month (شهریور) rather than a Gregorian one
(August/September) -- see the month bullet in CLAUDE.md.
"""

from __future__ import annotations

from datetime import date

# Years at which the 33-year leap cycle shifts. Straight from Borkowski; the
# algorithm is only defined between the first and last of them.
_BREAKS = (
    -61, 9, 38, 199, 426, 686, 756, 818, 1111, 1181,
    1210, 1635, 1701, 1866, 2020, 2317, 2394, 2456, 3178,
)

MIN_JALALI_YEAR = _BREAKS[0] + 1
MAX_JALALI_YEAR = _BREAKS[-1] - 1

# Months 1-6 have 31 days, 7-11 have 30, and Esfand has 29 or 30.
_LONG_MONTHS = 6
_SHORT_MONTHS = 11


def _div(a: int, b: int) -> int:
    """Integer division truncated toward zero (not Python's floor)."""
    q, neg = abs(a) // abs(b), (a < 0) != (b < 0)
    return -q if neg else q


def _mod(a: int, b: int) -> int:
    return a - _div(a, b) * b


def _jal_cal(jy: int) -> tuple[int, int, int]:
    """(is_leap, gregorian_year, march_day_of_1_farvardin) for a Jalali year."""
    if not (MIN_JALALI_YEAR <= jy <= MAX_JALALI_YEAR):
        raise ValueError(f"Jalali year out of range: {jy}")

    gy = jy + 621
    leap_j = -14
    jp = _BREAKS[0]
    jump = 0
    for jm in _BREAKS[1:]:
        jump = jm - jp
        if jy < jm:
            break
        leap_j += _div(jump, 33) * 8 + _div(_mod(jump, 33), 4)
        jp = jm

    n = jy - jp
    leap_j += _div(n, 33) * 8 + _div(_mod(n, 33) + 3, 4)
    if _mod(jump, 33) == 4 and jump - n == 4:
        leap_j += 1

    leap_g = _div(gy, 4) - _div((_div(gy, 100) + 1) * 3, 4) - 150
    march = 20 + leap_j - leap_g

    if jump - n < 6:
        n = n - jump + _div(jump + 4, 33) * 33
    leap = _mod(_mod(n + 1, 33) - 1, 4)
    if leap == -1:
        leap = 4

    return leap, gy, march


def _g2d(gy: int, gm: int, gd: int) -> int:
    """Gregorian date -> Julian Day Number."""
    d = (
        _div((gy + _div(gm - 8, 6) + 100100) * 1461, 4)
        + _div(153 * _mod(gm + 9, 12) + 2, 5)
        + gd
        - 34840408
    )
    return d - _div(_div(gy + 100100 + _div(gm - 8, 6), 100) * 3, 4) + 752


def _d2g(jdn: int) -> tuple[int, int, int]:
    """Julian Day Number -> Gregorian date."""
    j = 4 * jdn + 139361631
    j += _div(_div(4 * jdn + 183187720, 146097) * 3, 4) * 4 - 3908
    i = _div(_mod(j, 1461), 4) * 5 + 308
    gd = _div(_mod(i, 153), 5) + 1
    gm = _mod(_div(i, 153), 12) + 1
    gy = _div(j, 1461) - 100100 + _div(8 - gm, 6)
    return gy, gm, gd


def is_leap_year(jy: int) -> bool:
    return _jal_cal(jy)[0] == 0


def days_in_month(jy: int, jm: int) -> int:
    if not 1 <= jm <= 12:
        raise ValueError(f"Jalali month out of range: {jm}")
    if jm <= _LONG_MONTHS:
        return 31
    if jm <= _SHORT_MONTHS:
        return 30
    return 30 if is_leap_year(jy) else 29


def to_jalali(d: date) -> tuple[int, int, int]:
    """Gregorian `date` -> (jalali_year, jalali_month, jalali_day)."""
    jdn = _g2d(d.year, d.month, d.day)
    gy = _d2g(jdn)[0]
    jy = gy - 621
    leap, _, march = _jal_cal(jy)
    k = jdn - _g2d(gy, 3, march)

    if k >= 0:
        if k <= 185:
            return jy, 1 + _div(k, 31), _mod(k, 31) + 1
        k -= 186
    else:
        jy -= 1
        k += 179
        if leap == 1:
            k += 1
    return jy, 7 + _div(k, 30), _mod(k, 30) + 1


def from_jalali(jy: int, jm: int, jd: int) -> date:
    """(jalali_year, jalali_month, jalali_day) -> Gregorian `date`."""
    if not 1 <= jm <= 12:
        raise ValueError(f"Jalali month out of range: {jm}")
    if not 1 <= jd <= days_in_month(jy, jm):
        raise ValueError(f"Jalali day out of range: {jy}-{jm}-{jd}")
    _, gy, march = _jal_cal(jy)
    jdn = _g2d(gy, 3, march) + (jm - 1) * 31 - _div(jm, 7) * (jm - 7) + jd - 1
    return date(*_d2g(jdn))


def month_bounds(d: date) -> tuple[date, date]:
    """First and last Gregorian day of the Jalali month containing `d`."""
    jy, jm, _ = to_jalali(d)
    return from_jalali(jy, jm, 1), from_jalali(jy, jm, days_in_month(jy, jm))
