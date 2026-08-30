"""The onboarding tour's targets are a contract between two files.

``app/static/js/tour.js`` names each step's target as a ``data-tour`` id and
the templates carry the matching attribute. Nothing links the two at import
time, so a step whose target was renamed or removed fails the only way a
tour can fail: silently. It drops out of that page's run, the counter quietly
says «۲ از ۳» instead of «۳ از ۳», and nobody notices until a new member is
shown three quarters of an introduction.

These tests pin both directions -- every step names an anchor that exists,
and every anchor in the templates belongs to a step -- plus the handful of
anchors that are on the page unconditionally, rendered through the real app.
The امروز *card* anchor is the one exception: it only exists when the member
has an occurrence due today, which is exactly why tour.js defers a missing
target rather than pointing at nothing, so it is checked in the template
source rather than in a rendered response.
"""

from __future__ import annotations

import re
from pathlib import Path

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import create_session_cookie, hash_password
from app.models.user import User

ROOT = Path(__file__).resolve().parent.parent
TOUR_JS = ROOT / "app" / "static" / "js" / "tour.js"
TEMPLATES = ROOT / "app" / "templates"

# `target: "<id>",` inside a TOUR_STEPS entry, paired with the `path:` above it.
STEP_RE = re.compile(r'path:\s*"([^"]+)".*?target:\s*"([^"]+)"', re.DOTALL)
ANCHOR_RE = re.compile(r'data-tour="([^"]+)"')

# The امروز list is empty for a member with nothing due, so its card anchor
# cannot be asserted against a rendered page.
DATA_DEPENDENT = {"today-item"}


def tour_steps() -> list[tuple[str, str]]:
    source = TOUR_JS.read_text(encoding="utf-8")
    body = source.split("TOUR_STEPS = [", 1)[1].split("\n  ];", 1)[0]
    steps = STEP_RE.findall(body)
    assert steps, "TOUR_STEPS did not parse -- has the step shape changed?"
    return steps


def template_anchors() -> set[str]:
    found: set[str] = set()
    for path in TEMPLATES.rglob("*.html"):
        found.update(ANCHOR_RE.findall(path.read_text(encoding="utf-8")))
    return found


async def sign_in(client: AsyncClient, db: AsyncSession) -> User:
    user = User(name="Tour User", password_hash=hash_password("password123"))
    db.add(user)
    await db.commit()
    await db.refresh(user)
    client.cookies.set("session", create_session_cookie(user.id))
    return user


def test_every_step_names_an_anchor_that_exists():
    anchors = template_anchors()
    missing = sorted({t for _, t in tour_steps()} - anchors)
    assert not missing, f"tour.js points at anchors no template carries: {missing}"


def test_every_anchor_belongs_to_a_step():
    # The reverse direction: a `data-tour` left behind by a removed step is a
    # promise to a reader of the template that the tour no longer keeps.
    targets = {t for _, t in tour_steps()}
    orphans = sorted(template_anchors() - targets)
    assert not orphans, f"templates carry anchors no step uses: {orphans}"


async def test_toured_pages_render_their_anchors(client: AsyncClient, db: AsyncSession):
    await sign_in(client, db)

    by_page: dict[str, set[str]] = {}
    for path, target in tour_steps():
        if target not in DATA_DEPENDENT:
            by_page.setdefault(path, set()).add(target)

    for path, targets in by_page.items():
        body = (await client.get(path)).text
        for target in sorted(targets):
            assert f'data-tour="{target}"' in body, f"{path} is missing {target}"


def test_the_today_card_carries_its_anchor():
    fragment = (TEMPLATES / "today" / "_today_cards.html").read_text(encoding="utf-8")
    assert 'data-tour="today-item"' in fragment


async def test_toured_pages_load_the_tour_script(client: AsyncClient, db: AsyncSession):
    await sign_in(client, db)
    for path in {p for p, _ in tour_steps()}:
        assert "/js/tour.js" in (await client.get(path)).text, f"{path} omits tour.js"


async def test_pages_name_the_signed_in_member(client: AsyncClient, db: AsyncSession):
    # tour.js keys its progress on this id, because onboarding belongs to an
    # account and localStorage belongs to a device: without it, a phone that
    # already walked one member through the app stays silent for the next one
    # who signs in on it. Losing the attribute would not break a page -- the
    # tour would just quietly fall back to a shared key again.
    user = await sign_in(client, db)
    for path in {p for p, _ in tour_steps()}:
        body = (await client.get(path)).text
        assert f'data-user-id="{user.id}"' in body, f"{path} omits the member id"


async def test_settings_no_longer_carries_the_tour(client: AsyncClient, db: AsyncSession):
    # The tour is per-page now and each toured page replays its own run from
    # the topbar, so the settings row that used to be the only way back is
    # gone -- leaving it would start a run on a page that has no steps.
    await sign_in(client, db)
    body = (await client.get("/views/settings/")).text

    assert "data-tour-restart" not in body
    assert "/js/tour.js" not in body


async def test_toured_pages_offer_the_help_button(client: AsyncClient, db: AsyncSession):
    # Auto-start fires once per step; the «؟» button is the only way to see a
    # page's tour again, so a toured page without it is a tour with no replay.
    await sign_in(client, db)
    for path in {p for p, _ in tour_steps()}:
        body = (await client.get(path)).text
        assert "data-tour-help" in body, f"{path} omits the «؟» tour button"
