"""Correctness of the Jalali <-> Gregorian converter in `app.jalali`.

The anchors below are ICU's answers (`Intl.DateTimeFormat("en-u-ca-persian")`),
which is the same authority the browser UI formats dates with -- so a drift
between these tables and `app/jalali.py` is exactly the drift that would make a
server-computed occurrence key disagree with the date shown on the card.

Nowruz is the demanding case: 1 Farvardin lands on 20/21/22 March depending on
the 33-year leap cycle, so pinning 121 consecutive years pins the cycle itself.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from app.jalali import (
    days_in_month,
    from_jalali,
    is_leap_year,
    month_bounds,
    to_jalali,
)

# (jalali year, Gregorian date of 1 Farvardin) for 1300..1420, per ICU.
NOWRUZ = [
    (1300, "1921-03-21"), (1301, "1922-03-22"), (1302, "1923-03-22"), (1303, "1924-03-21"), (1304, "1925-03-21"), (1305, "1926-03-22"),
    (1306, "1927-03-22"), (1307, "1928-03-21"), (1308, "1929-03-21"), (1309, "1930-03-21"), (1310, "1931-03-22"), (1311, "1932-03-21"),
    (1312, "1933-03-21"), (1313, "1934-03-21"), (1314, "1935-03-22"), (1315, "1936-03-21"), (1316, "1937-03-21"), (1317, "1938-03-21"),
    (1318, "1939-03-22"), (1319, "1940-03-21"), (1320, "1941-03-21"), (1321, "1942-03-21"), (1322, "1943-03-22"), (1323, "1944-03-21"),
    (1324, "1945-03-21"), (1325, "1946-03-21"), (1326, "1947-03-22"), (1327, "1948-03-21"), (1328, "1949-03-21"), (1329, "1950-03-21"),
    (1330, "1951-03-22"), (1331, "1952-03-21"), (1332, "1953-03-21"), (1333, "1954-03-21"), (1334, "1955-03-22"), (1335, "1956-03-21"),
    (1336, "1957-03-21"), (1337, "1958-03-21"), (1338, "1959-03-22"), (1339, "1960-03-21"), (1340, "1961-03-21"), (1341, "1962-03-21"),
    (1342, "1963-03-21"), (1343, "1964-03-21"), (1344, "1965-03-21"), (1345, "1966-03-21"), (1346, "1967-03-21"), (1347, "1968-03-21"),
    (1348, "1969-03-21"), (1349, "1970-03-21"), (1350, "1971-03-21"), (1351, "1972-03-21"), (1352, "1973-03-21"), (1353, "1974-03-21"),
    (1354, "1975-03-21"), (1355, "1976-03-21"), (1356, "1977-03-21"), (1357, "1978-03-21"), (1358, "1979-03-21"), (1359, "1980-03-21"),
    (1360, "1981-03-21"), (1361, "1982-03-21"), (1362, "1983-03-21"), (1363, "1984-03-21"), (1364, "1985-03-21"), (1365, "1986-03-21"),
    (1366, "1987-03-21"), (1367, "1988-03-21"), (1368, "1989-03-21"), (1369, "1990-03-21"), (1370, "1991-03-21"), (1371, "1992-03-21"),
    (1372, "1993-03-21"), (1373, "1994-03-21"), (1374, "1995-03-21"), (1375, "1996-03-20"), (1376, "1997-03-21"), (1377, "1998-03-21"),
    (1378, "1999-03-21"), (1379, "2000-03-20"), (1380, "2001-03-21"), (1381, "2002-03-21"), (1382, "2003-03-21"), (1383, "2004-03-20"),
    (1384, "2005-03-21"), (1385, "2006-03-21"), (1386, "2007-03-21"), (1387, "2008-03-20"), (1388, "2009-03-21"), (1389, "2010-03-21"),
    (1390, "2011-03-21"), (1391, "2012-03-20"), (1392, "2013-03-21"), (1393, "2014-03-21"), (1394, "2015-03-21"), (1395, "2016-03-20"),
    (1396, "2017-03-21"), (1397, "2018-03-21"), (1398, "2019-03-21"), (1399, "2020-03-20"), (1400, "2021-03-21"), (1401, "2022-03-21"),
    (1402, "2023-03-21"), (1403, "2024-03-20"), (1404, "2025-03-21"), (1405, "2026-03-21"), (1406, "2027-03-21"), (1407, "2028-03-20"),
    (1408, "2029-03-20"), (1409, "2030-03-21"), (1410, "2031-03-21"), (1411, "2032-03-20"), (1412, "2033-03-20"), (1413, "2034-03-21"),
    (1414, "2035-03-21"), (1415, "2036-03-20"), (1416, "2037-03-20"), (1417, "2038-03-21"), (1418, "2039-03-21"), (1419, "2040-03-20"),
    (1420, "2041-03-20"),
]

# Jalali leap years in the same span, per ICU (Esfand has 30 days).
LEAP_YEARS = {
    1300, 1304, 1309, 1313, 1317, 1321, 1325, 1329, 1333, 1337,
    1342, 1346, 1350, 1354, 1358, 1362, 1366, 1370, 1375, 1379,
    1383, 1387, 1391, 1395, 1399, 1403, 1408, 1412, 1416, 1420,
}

RANGE_START = date(1921, 3, 21)  # 1 Farvardin 1300
RANGE_END = date(2042, 3, 20)  # 29 Esfand 1420


@pytest.mark.parametrize(("jy", "gregorian"), NOWRUZ)
def test_nowruz_matches_icu(jy: int, gregorian: str):
    d = date.fromisoformat(gregorian)
    assert from_jalali(jy, 1, 1) == d
    assert to_jalali(d) == (jy, 1, 1)
    # the day before Nowruz is the last day of the previous Esfand
    prev = to_jalali(d - timedelta(days=1))
    assert prev[:2] == (jy - 1, 12)
    assert prev[2] == days_in_month(jy - 1, 12)


def test_leap_years_match_icu():
    got = {jy for jy, _ in NOWRUZ if is_leap_year(jy)}
    assert got == LEAP_YEARS


def test_roundtrip_every_day_in_range():
    """Every day from 1300 to 1420 survives Gregorian -> Jalali -> Gregorian,
    and the Jalali days advance by exactly one each time (no gaps, no repeats)."""
    d = RANGE_START
    previous = None
    days = 0
    while d <= RANGE_END:
        jy, jm, jd = to_jalali(d)
        assert from_jalali(jy, jm, jd) == d
        assert 1 <= jm <= 12
        assert 1 <= jd <= days_in_month(jy, jm)
        if previous is not None:
            pjy, pjm, pjd = previous
            if pjd < days_in_month(pjy, pjm):
                assert (jy, jm, jd) == (pjy, pjm, pjd + 1)
            elif pjm < 12:
                assert (jy, jm, jd) == (pjy, pjm + 1, 1)
            else:
                assert (jy, jm, jd) == (pjy + 1, 1, 1)
        previous = (jy, jm, jd)
        d += timedelta(days=1)
        days += 1
    assert days == 44195


def test_month_lengths():
    for jy, _ in NOWRUZ:
        assert [days_in_month(jy, m) for m in range(1, 7)] == [31] * 6
        assert [days_in_month(jy, m) for m in range(7, 12)] == [30] * 5
        assert days_in_month(jy, 12) == (30 if is_leap_year(jy) else 29)
        total = sum(days_in_month(jy, m) for m in range(1, 13))
        assert total == (366 if is_leap_year(jy) else 365)


def test_month_bounds_spans_one_jalali_month():
    # Shahrivar 1405 -- the month the Gregorian-month bug used to split in two
    start, end = month_bounds(date(2026, 8, 27))
    assert (start, end) == (date(2026, 8, 23), date(2026, 9, 22))
    assert to_jalali(start) == (1405, 6, 1)
    assert to_jalali(end) == (1405, 6, 31)
    # every day inside reports the same bounds; the neighbours do not
    d = start
    while d <= end:
        assert month_bounds(d) == (start, end)
        d += timedelta(days=1)
    assert month_bounds(start - timedelta(days=1)) != (start, end)
    assert month_bounds(end + timedelta(days=1)) != (start, end)


def test_rejects_out_of_range():
    with pytest.raises(ValueError):
        from_jalali(1405, 13, 1)
    with pytest.raises(ValueError):
        from_jalali(1405, 12, 30)  # 1405 is not a leap year
    with pytest.raises(ValueError):
        from_jalali(9999, 1, 1)
