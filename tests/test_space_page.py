"""«فضای من» and the one management entrance.

Two rules this phase moved, and both fail silently if they slip back:

* **Groups and roadmaps are a destination, not a setting.** They live on one
  page with two segments, reachable from every screen through the bottom nav,
  and the settings screen keeps preferences only. A content link creeping back
  into settings is exactly the state this replaced.
* **A challenge is managed the way a group and a course are** -- a page of
  rows behind a gear, gated by ``can(..., CHALLENGE_EDIT)``. Which means an
  operator, who holds no ``CHALLENGE_EDIT``, is refused here as well.
"""

from __future__ import annotations

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import UserRole
from tests.group_helpers import make_challenge, make_group, make_user, sign_in


async def test_the_space_page_carries_both_lists(
    client: AsyncClient, db: AsyncSession
) -> None:
    member = await make_user(db, "Space Member")
    await make_group(db, member, name="شرکت سبز")
    sign_in(client, member)

    res = await client.get("/views/space/")
    assert res.status_code == 200
    body = res.text

    # Both segments and both lists are server-rendered: switching is a class
    # toggle, so neither panel may depend on a request to exist.
    assert 'data-tab="groups"' in body and 'data-tab="roadmaps"' in body
    assert 'id="grList"' in body and 'id="rmList"' in body
    assert "شرکت سبز" in body
    # A bottom-nav destination clears the trail.
    assert "data-crumb-root" in body


async def test_the_old_list_addresses_open_their_segment(
    client: AsyncClient, db: AsyncSession
) -> None:
    """One template, three URLs -- every link that pointed at the old lists
    still lands, and lands on the right half."""
    member = await make_user(db, "Old Links")
    sign_in(client, member)

    groups = await client.get("/views/groups/")
    roadmaps = await client.get("/views/roadmaps/")
    assert groups.status_code == 200 and roadmaps.status_code == 200
    assert 'data-tab="groups"' in groups.text
    assert 'id="panel-roadmaps" role="tabpanel"' in roadmaps.text
    assert 'aria-selected="true"' in roadmaps.text


async def test_every_page_reaches_the_space_in_one_tap(
    client: AsyncClient, db: AsyncSession
) -> None:
    member = await make_user(db, "Nav Member")
    sign_in(client, member)
    for path in ("/views/home/", "/views/today/", "/views/challenges/"):
        assert 'href="/views/space/"' in (await client.get(path)).text, path


async def test_settings_keeps_preferences_only(
    client: AsyncClient, db: AsyncSession
) -> None:
    member = await make_user(db, "Settings Member")
    sign_in(client, member)
    body = (await client.get("/views/settings/")).text

    assert 'href="/views/groups/"' not in body
    assert 'href="/views/roadmaps/"' not in body
    # The preference that is actually a preference stays.
    assert "data-theme-choice" in body


async def test_the_owner_manages_a_challenge_on_its_own_page(
    client: AsyncClient, db: AsyncSession
) -> None:
    owner = await make_user(db, "Challenge Owner")
    challenge = await make_challenge(db, owner, title="چالش مدیریت")
    sign_in(client, owner)

    detail = (await client.get(f"/views/challenges/{challenge.id}")).text
    assert f'href="/views/challenges/{challenge.id}/manage"' in detail

    manage = await client.get(f"/views/challenges/{challenge.id}/manage")
    assert manage.status_code == 200
    assert "چالش مدیریت" in manage.text


async def test_managing_is_authorship_not_moderation(
    client: AsyncClient, db: AsyncSession
) -> None:
    """No app-wide role grants ``CHALLENGE_EDIT``, so the gear is not the
    operator's door either -- the moderation roster is."""
    owner = await make_user(db, "Author")
    challenge = await make_challenge(db, owner, title="چالش دیگری")
    admin = await make_user(db, "Operator", role=UserRole.ADMIN.value)

    sign_in(client, admin)
    detail = (await client.get(f"/views/challenges/{challenge.id}")).text
    assert f'href="/views/challenges/{challenge.id}/manage"' not in detail
    res = await client.get(f"/views/challenges/{challenge.id}/manage")
    assert res.status_code == 404
