"""The server's Jalali text must equal what app.js used to render.

The fixture in `tests/data/jalali_format_reference.json` was generated from
ICU (`Intl.DateTimeFormat("fa-IR-u-ca-persian")`) with exactly the option bags
`JALALI_FORMATS` in app.js declares, including the two year-first patterns
app.js reassembles. If this fails, the two halves have drifted and a card and
its JS-corrected twin would word one date differently.
"""

import importlib
import json
from datetime import date, datetime
from pathlib import Path

import pytest

from app.date_filters import fa_num, jalali

ROOT = Path(__file__).resolve().parent.parent
TEMPLATES = ROOT / "app" / "templates"

#: Every module that builds a Jinja2Templates environment. Listed rather than
#: discovered, so *adding* a view router without registering the filters fails
#: at the same moment as forgetting the call in one that already exists --
#: the same contract `tests/test_explainers.py` pins for `explain`.
TEMPLATE_MODULES = [
    "app.main",
    "app.routers.views.admin",
    "app.routers.views.auth",
    "app.routers.views.challenge",
    "app.routers.views.group",
    "app.routers.views.home",
    "app.routers.views.notification",
    "app.routers.views.roadmap",
    "app.routers.views.settings",
    "app.routers.views.today",
    "app.routers.views.user",
]

REFERENCE = json.loads(
    (Path(__file__).parent / "data" / "jalali_format_reference.json").read_text("utf-8")
)


def _cases(section, with_tz):
    for key, expected in REFERENCE[section].items():
        raw, tz = (key.split("|") if with_tz else (key, None))
        for fmt, text in expected.items():
            yield pytest.param(raw, fmt, tz, text, id=f"{key}-{fmt}")


@pytest.mark.parametrize("raw,fmt,tz,expected", list(_cases("dates", False)))
def test_floating_dates_match_icu(raw, fmt, tz, expected):
    assert jalali(raw, fmt) == expected
    assert jalali(date.fromisoformat(raw), fmt) == expected


@pytest.mark.parametrize("raw,fmt,tz,expected", list(_cases("instants", True)))
def test_instants_match_icu(raw, fmt, tz, expected):
    assert jalali(raw, fmt, tz) == expected
    assert jalali(datetime.fromisoformat(raw), fmt, tz) == expected


def test_offsetless_instant_is_read_as_utc():
    """SQLite hands back naive datetimes; treating one as local would move the day."""
    assert jalali("2026-08-27T20:05:00", "time", "Asia/Tehran") == "۲۳:۳۵"


def test_unreadable_values_render_as_empty_text():
    for value in (None, "", "not a date"):
        assert jalali(value, "day-month") == ""


def test_fa_num_only_touches_digits():
    assert fa_num("۱۲ نفر از 30") == "۱۲ نفر از ۳۰"
    assert fa_num(7) == "۷"


# --------------------------------------------------------------------------
# The registration contract
# --------------------------------------------------------------------------


@pytest.mark.parametrize("module_name", TEMPLATE_MODULES)
@pytest.mark.parametrize("filter_name", ["jalali", "fa_num"])
def test_every_template_environment_can_render_a_date(module_name, filter_name):
    """A router that forgets the call is a 500 on whatever page it serves.

    Every date and figure in the app is now written by the server, so this
    registration is not chrome on a few screens -- it is on nearly all of
    them, and discovering a missing one at render time means discovering it
    in production.
    """
    env = importlib.import_module(module_name).templates.env
    assert filter_name in env.filters, (
        f"{module_name} builds a Jinja2Templates environment without calling "
        "register_date_filters(templates.env)"
    )


def test_no_template_prints_a_gregorian_date():
    """`.strftime(...)` was the pre-JS fallback the server now renders whole.

    It is the one shape that comes back on its own: a new page copied from an
    old one, or a merge that keeps both halves. A Gregorian date in the markup
    is invisible in review and obvious to the reader.
    """
    guilty = [
        str(path.relative_to(ROOT))
        for path in sorted(TEMPLATES.rglob("*.html"))
        if ".strftime(" in path.read_text(encoding="utf-8")
    ]
    assert not guilty, (
        "templates render a Gregorian date; use the `jalali` filter instead: "
        f"{guilty}"
    )
