from __future__ import annotations

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from tests.test_challenge_lifecycle import authenticate, create_challenge, make_user


async def _setup(client: AsyncClient, db: AsyncSession) -> tuple[int, int, int]:
    """Owner has one challenge; a stranger has another. Returns
    (owner_id, own_challenge_id, other_challenge_id)."""
    stranger = await make_user(db, "Stranger")
    authenticate(client, stranger.id)
    other_id = (await create_challenge(client, title="Someone Else's")).json()["id"]

    owner = await make_user(db, "Owner")
    authenticate(client, owner.id)
    own_id = (await create_challenge(client, title="Mine")).json()["id"]
    return owner.id, own_id, other_id


def _ids(resp) -> set[int]:
    return {c["id"] for c in resp.json()}


@pytest.mark.asyncio
async def test_mine_filter_all_only_hide(client: AsyncClient, db: AsyncSession):
    _owner_id, own_id, other_id = await _setup(client, db)

    assert _ids(await client.get("/challenges/")) == {own_id, other_id}
    assert _ids(await client.get("/challenges/?mine=all")) == {own_id, other_id}
    assert _ids(await client.get("/challenges/?mine=only")) == {own_id}
    assert _ids(await client.get("/challenges/?mine=hide")) == {other_id}


@pytest.mark.asyncio
async def test_mine_only_includes_enrolled_not_owned(
    client: AsyncClient, db: AsyncSession
):
    _owner_id, own_id, other_id = await _setup(client, db)

    resp = await client.post(f"/enrollments/{other_id}")
    assert resp.status_code in (200, 201), resp.text

    assert _ids(await client.get("/challenges/?mine=only")) == {own_id, other_id}
    assert _ids(await client.get("/challenges/?mine=hide")) == set()


@pytest.mark.asyncio
async def test_mine_filter_is_a_noop_for_anonymous(
    client: AsyncClient, db: AsyncSession
):
    _owner_id, own_id, other_id = await _setup(client, db)
    client.cookies.clear()

    assert _ids(await client.get("/challenges/?mine=only")) == {own_id, other_id}
    assert _ids(await client.get("/challenges/?mine=hide")) == {own_id, other_id}


@pytest.mark.asyncio
async def test_mine_filter_rejects_unknown_value(client: AsyncClient, db: AsyncSession):
    await _setup(client, db)
    assert (await client.get("/challenges/?mine=bogus")).status_code == 422


@pytest.mark.asyncio
async def test_list_page_and_fragment_honour_mine(client: AsyncClient, db: AsyncSession):
    _owner_id, _own_id, _other_id = await _setup(client, db)

    page = await client.get("/views/challenges/?mine=only")
    assert page.status_code == 200
    assert "Mine" in page.text and "Someone Else&#39;s" not in page.text

    fragment = await client.get("/views/challenges/fragment?mine=hide&offset=0&limit=20")
    assert fragment.status_code == 200
    assert "Mine" not in fragment.text
