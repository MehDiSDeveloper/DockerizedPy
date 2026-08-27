from __future__ import annotations

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import create_session_cookie, hash_password
from app.models.enrollment import Enrollment
from app.models.stats import ChallengeStats
from app.models.user import User

CATEGORY = "سایر"  # ChallengeCategory.OTHER


async def make_user(db: AsyncSession, name: str = "Test User") -> User:
    user = User(name=name, password_hash=hash_password("password123"))
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


def authenticate(client: AsyncClient, user_id: int) -> None:
    client.cookies.set("session", create_session_cookie(user_id))


async def create_challenge(
    client: AsyncClient,
    *,
    title: str = "Test Challenge",
    visibility: str = "public",
    cadence: dict | None = None,
    goal_amount: str | None = None,
    goal_unit: str | None = None,
):
    payload = {
        "title": title,
        "category": CATEGORY,
        "visibility": visibility,
        "cadence": cadence or {"kind": "once"},
    }
    if goal_amount is not None:
        payload["goal_amount"] = goal_amount
    if goal_unit is not None:
        payload["goal_unit"] = goal_unit
    return await client.post("/challenges/", json=payload)


@pytest.mark.asyncio
async def test_owner_is_auto_enrolled_on_create(client: AsyncClient, db: AsyncSession):
    owner = await make_user(db, "Owner")
    authenticate(client, owner.id)

    resp = await create_challenge(client)
    assert resp.status_code == 201, resp.text
    challenge_id = resp.json()["id"]

    enrollment_result = await db.execute(
        select(Enrollment).where(
            Enrollment.challenge_id == challenge_id, Enrollment.user_id == owner.id
        )
    )
    assert enrollment_result.scalar_one_or_none() is not None

    stats_result = await db.execute(
        select(ChallengeStats).where(ChallengeStats.challenge_id == challenge_id)
    )
    stats = stats_result.scalar_one()
    assert stats.participant_count == 1


@pytest.mark.asyncio
async def test_lock_rule_blocks_cadence_and_goal_once_others_enrolled(
    client: AsyncClient, db: AsyncSession
):
    owner = await make_user(db, "Owner")
    other = await make_user(db, "Other")

    authenticate(client, owner.id)
    created = await create_challenge(client)
    challenge_id = created.json()["id"]

    authenticate(client, other.id)
    enrolled = await client.post(f"/enrollments/{challenge_id}")
    assert enrolled.status_code == 201, enrolled.text

    authenticate(client, owner.id)

    cadence_edit = await client.patch(
        f"/challenges/{challenge_id}", json={"cadence": {"kind": "once"}}
    )
    assert cadence_edit.status_code == 409

    goal_edit = await client.patch(
        f"/challenges/{challenge_id}", json={"goal_amount": "10.00"}
    )
    assert goal_edit.status_code == 409

    unit_edit = await client.patch(
        f"/challenges/{challenge_id}", json={"goal_unit": "km"}
    )
    assert unit_edit.status_code == 409

    title_edit = await client.patch(
        f"/challenges/{challenge_id}", json={"title": "Renamed Challenge"}
    )
    assert title_edit.status_code == 200
    assert title_edit.json()["title"] == "Renamed Challenge"


@pytest.mark.asyncio
async def test_lock_rule_allows_cadence_change_with_no_other_participants(
    client: AsyncClient, db: AsyncSession
):
    owner = await make_user(db, "Owner")
    authenticate(client, owner.id)
    created = await create_challenge(client)
    challenge_id = created.json()["id"]

    resp = await client.patch(
        f"/challenges/{challenge_id}", json={"cadence": {"kind": "once"}}
    )
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_visibility_leaving_public_blocked_while_others_enrolled(
    client: AsyncClient, db: AsyncSession
):
    owner = await make_user(db, "Owner")
    other = await make_user(db, "Other")

    authenticate(client, owner.id)
    created = await create_challenge(client, visibility="public")
    challenge_id = created.json()["id"]

    authenticate(client, other.id)
    enrolled = await client.post(f"/enrollments/{challenge_id}")
    assert enrolled.status_code == 201, enrolled.text

    authenticate(client, owner.id)
    resp = await client.patch(
        f"/challenges/{challenge_id}", json={"visibility": "private"}
    )
    assert resp.status_code == 409


@pytest.mark.asyncio
async def test_visibility_moving_to_public_always_allowed(
    client: AsyncClient, db: AsyncSession
):
    owner = await make_user(db, "Owner")
    other = await make_user(db, "Other")

    authenticate(client, owner.id)
    created = await create_challenge(client, visibility="unlisted")
    challenge_id = created.json()["id"]

    # unlisted is reachable/enrollable by any authenticated user via URL.
    authenticate(client, other.id)
    enrolled = await client.post(f"/enrollments/{challenge_id}")
    assert enrolled.status_code == 201, enrolled.text

    authenticate(client, owner.id)
    resp = await client.patch(
        f"/challenges/{challenge_id}", json={"visibility": "public"}
    )
    assert resp.status_code == 200
    assert resp.json()["visibility"] == "public"


@pytest.mark.asyncio
async def test_unlisted_excluded_from_listing_but_reachable_by_url(
    client: AsyncClient, db: AsyncSession
):
    owner = await make_user(db, "Owner")
    stranger = await make_user(db, "Stranger")

    authenticate(client, owner.id)
    created = await create_challenge(
        client, title="Secret Unlisted", visibility="unlisted"
    )
    assert created.status_code == 201, created.text
    challenge_id = created.json()["id"]

    authenticate(client, stranger.id)
    listing = await client.get("/challenges/")
    assert listing.status_code == 200
    assert all(c["id"] != challenge_id for c in listing.json())

    detail = await client.get(f"/challenges/{challenge_id}")
    assert detail.status_code == 200
    assert detail.json()["id"] == challenge_id
