"""The management affordances on challenge-detail.

The gear in the topbar and the check-in edit button exist to reach JSON
endpoints that already worked but had no caller in the SSR layer. The value
of these tests is that the page only *offers* an action the API would
actually accept -- the guards live in the routers, and the template must
mirror them rather than invent its own.

The owner's editing itself now lives on `/views/challenges/{id}/manage`, one
row per field, the same screen a group and a course have; the detail page's
job is the gear that opens it.
"""

from __future__ import annotations

import json
import re

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession
from test_challenge_lifecycle import authenticate, create_challenge, make_user


async def _make_challenge(client: AsyncClient, **kwargs) -> int:
    created = await create_challenge(client, **kwargs)
    assert created.status_code == 201, created.text
    return created.json()["id"]


def _today_calendar_entry(page_text: str) -> dict:
    """The one occurrence dated today, out of the page's history calendar.

    challenge-detail hands the calendar every occurrence of the enrollment as
    JSON and draws the month client-side, so this payload -- not any rendered
    row -- is where the page states what each day may do.
    """
    match = re.search(r"const historyCalendarData = (\{.*?\});\n", page_text, re.S)
    assert match, "challenge-detail did not embed a history calendar"
    calendar = json.loads(match.group(1))
    entries = calendar["days"].get(calendar["today"])
    assert entries, f"no occurrence on {calendar['today']}"
    assert len(entries) == 1, "a daily cadence asks for exactly one a day"
    return entries[0]


@pytest.mark.asyncio
async def test_manage_button_shown_to_owner_only(
    client: AsyncClient, db: AsyncSession
):
    owner = await make_user(db, "Owner")
    visitor = await make_user(db, "Visitor")

    authenticate(client, owner.id)
    challenge_id = await _make_challenge(client)

    gear = f'href="/views/challenges/{challenge_id}/manage"'
    page = await client.get(f"/views/challenges/{challenge_id}")
    assert gear in page.text
    assert "مدیریت چالش" in page.text

    authenticate(client, visitor.id)
    page = await client.get(f"/views/challenges/{challenge_id}")
    assert page.status_code == 200
    assert gear not in page.text
    # ...and the page behind it refuses them too, so the affordance and the
    # gate agree without either trusting the other.
    assert (
        await client.get(f"/views/challenges/{challenge_id}/manage")
    ).status_code == 404


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
    assert f'href="/views/challenges/{challenge_id}/manage"' not in page.text


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

    page = await client.get(f"/views/challenges/{challenge_id}/manage")
    assert 'id="chDelete"' in page.text

    authenticate(client, other.id)
    enrolled = await client.post(f"/enrollments/{challenge_id}")
    assert enrolled.status_code == 201, enrolled.text

    authenticate(client, owner.id)
    page = await client.get(f"/views/challenges/{challenge_id}/manage")
    assert 'id="chDelete"' not in page.text


@pytest.mark.asyncio
async def test_recorded_checkin_offers_edit_instead_of_record(
    client: AsyncClient, db: AsyncSession
):
    owner = await make_user(db, "Owner")
    authenticate(client, owner.id)
    challenge_id = await _make_challenge(
        client, cadence={"kind": "recurring_days", "mode": "every_n_days", "n": 1}
    )

    # The two flags are asserted on the calendar's own payload rather than on
    # rendered buttons: the history calendar draws its days client-side, so
    # `writable`/`editable` per occurrence *is* the contract the page ships,
    # and the button is one reading of it.
    page = await client.get(f"/views/challenges/{challenge_id}")
    today_entry = _today_calendar_entry(page.text)
    assert today_entry["writable"] is True, "expected a writable occurrence to record"
    assert today_entry["editable"] is False
    assert today_entry["checkin_id"] is None

    checkin = await client.post(
        "/checkins/",
        json={
            "challenge_id": challenge_id,
            "occurrence_key": today_entry["key"],
            "state": "completed",
        },
    )
    assert checkin.status_code == 201, checkin.text
    checkin_id = checkin.json()["id"]

    # Recorded: re-POSTing would silently no-op, so the day must now offer
    # PATCH/DELETE instead -- never both, never neither.
    page = await client.get(f"/views/challenges/{challenge_id}")
    today_entry = _today_calendar_entry(page.text)
    assert today_entry["writable"] is False
    assert today_entry["editable"] is True
    assert today_entry["checkin_id"] == checkin_id


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
