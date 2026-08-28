"""The management affordances on challenge-detail.

Both the owner's manage sheet and the check-in edit button exist to reach
JSON endpoints that already worked but had no caller in the SSR layer. The
value of these tests is that the page only *offers* an action the API would
actually accept -- the guards live in the routers, and the template must
mirror them rather than invent its own.
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession
from test_challenge_lifecycle import authenticate, create_challenge, make_user


async def _make_challenge(client: AsyncClient, **kwargs) -> int:
    created = await create_challenge(client, **kwargs)
    assert created.status_code == 201, created.text
    return created.json()["id"]


@pytest.mark.asyncio
async def test_manage_button_shown_to_owner_only(
    client: AsyncClient, db: AsyncSession
):
    owner = await make_user(db, "Owner")
    visitor = await make_user(db, "Visitor")

    authenticate(client, owner.id)
    challenge_id = await _make_challenge(client)

    page = await client.get(f"/views/challenges/{challenge_id}")
    assert 'id="manageBtn"' in page.text
    assert "مدیریت چالش" in page.text

    authenticate(client, visitor.id)
    page = await client.get(f"/views/challenges/{challenge_id}")
    assert page.status_code == 200
    assert 'id="manageBtn"' not in page.text


@pytest.mark.asyncio
async def test_anonymous_visitor_gets_no_manage_button(
    client: AsyncClient, db: AsyncSession
):
    owner = await make_user(db, "Owner")
    authenticate(client, owner.id)
    challenge_id = await _make_challenge(client, visibility="public")

    client.cookies.clear()
    page = await client.get(f"/views/challenges/{challenge_id}")
    assert page.status_code == 200
    assert 'id="manageBtn"' not in page.text


@pytest.mark.asyncio
async def test_delete_offered_only_while_owner_is_alone(
    client: AsyncClient, db: AsyncSession
):
    """can_delete must track delete_challenge's own 409 guard -- offering a
    delete the API would refuse is worse than not offering one."""
    owner = await make_user(db, "Owner")
    other = await make_user(db, "Other")

    authenticate(client, owner.id)
    challenge_id = await _make_challenge(client)

    page = await client.get(f"/views/challenges/{challenge_id}")
    assert "const canDelete = true;" in page.text

    authenticate(client, other.id)
    enrolled = await client.post(f"/enrollments/{challenge_id}")
    assert enrolled.status_code == 201, enrolled.text

    authenticate(client, owner.id)
    page = await client.get(f"/views/challenges/{challenge_id}")
    assert "const canDelete = false;" in page.text


@pytest.mark.asyncio
async def test_recorded_checkin_offers_edit_instead_of_record(
    client: AsyncClient, db: AsyncSession
):
    owner = await make_user(db, "Owner")
    authenticate(client, owner.id)
    challenge_id = await _make_challenge(
        client, cadence={"kind": "recurring_days", "mode": "every_n_days", "n": 1}
    )

    # Match the rendered button, not the handler that references the class.
    edit_button = 'class="tl-action tl-edit"'

    page = await client.get(f"/views/challenges/{challenge_id}")
    assert edit_button not in page.text

    today_key = None
    for line in page.text.splitlines():
        if 'class="tl-action"' in line and "data-key=" in line:
            today_key = line.split('data-key="')[1].split('"')[0]
            break
    assert today_key is not None, "expected a writable occurrence to record"

    checkin = await client.post(
        "/checkins/",
        json={
            "challenge_id": challenge_id,
            "occurrence_key": today_key,
            "state": "completed",
        },
    )
    assert checkin.status_code == 201, checkin.text
    checkin_id = checkin.json()["id"]

    page = await client.get(f"/views/challenges/{challenge_id}")
    assert edit_button in page.text
    assert f'data-checkin-id="{checkin_id}"' in page.text


@pytest.mark.asyncio
async def test_owner_can_edit_and_archive_through_the_json_api(
    client: AsyncClient, db: AsyncSession
):
    """The exact payload the manage sheet sends -- the fields it exposes are
    the ones that stay editable after others enrol."""
    owner = await make_user(db, "Owner")
    other = await make_user(db, "Other")

    authenticate(client, owner.id)
    challenge_id = await _make_challenge(client)

    authenticate(client, other.id)
    await client.post(f"/enrollments/{challenge_id}")

    authenticate(client, owner.id)
    resp = await client.patch(
        f"/challenges/{challenge_id}",
        json={
            "title": "عنوان اصلاح‌شده",
            "description": "توضیح تازه",
            "rules": None,
            "visibility": "public",
            "lifecycle_status": "archived",
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["title"] == "عنوان اصلاح‌شده"
    assert body["lifecycle_status"] == "archived"
