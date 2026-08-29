"""The avatar catalogue: what may be stored, and what an empty pick renders as.

Two rules are worth pinning. First, ``Users.avatar`` holds an *id from a fixed
catalogue*, and the id is what later becomes a static path -- so an id nobody
published must be refused at the schema boundary rather than reaching the
column and turning into a fetch for a file. Second, "no pick" is a permanent,
legitimate state (every account predating the column has it), so every reader
has to answer the placeholder instead of a broken image.

The signup body carrying ``avatar: null`` is the same path an existing account
takes, which is why the null case is asserted through the real endpoint rather
than only against ``avatar_url``.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from httpx import AsyncClient

from app.avatars import AVATAR_IDS, DEFAULT_AVATAR_ID, avatar_url, is_valid_avatar
from app.config import BASE_DIR

AVATAR_DIR = Path(BASE_DIR) / "static" / "img" / "avatars"


def test_every_catalogued_id_has_a_file():
    """The catalogue is the list the picker renders -- an id without a file
    would ship as a broken tile in the signup grid."""
    missing = [a for a in AVATAR_IDS if not (AVATAR_DIR / f"{a}.svg").is_file()]
    assert missing == []
    assert (AVATAR_DIR / f"{DEFAULT_AVATAR_ID}.svg").is_file()


def test_ids_are_unique():
    assert len(set(AVATAR_IDS)) == len(AVATAR_IDS)


@pytest.mark.parametrize("value", [None, *AVATAR_IDS])
def test_valid_values_are_accepted(value: str | None):
    assert is_valid_avatar(value)


@pytest.mark.parametrize(
    "value", ["", "nope", "../_default", "clay-ivy.svg", "/etc/passwd"]
)
def test_unknown_values_are_rejected(value: str):
    assert not is_valid_avatar(value)


def test_unknown_and_missing_values_render_the_placeholder():
    assert avatar_url(None) == avatar_url("nope") == f"/static/img/avatars/{DEFAULT_AVATAR_ID}.svg"
    assert avatar_url(AVATAR_IDS[0]) == f"/static/img/avatars/{AVATAR_IDS[0]}.svg"


async def test_signup_stores_a_chosen_avatar(client: AsyncClient):
    response = await client.post(
        "/users/",
        json={
            "name": "Avatar Picker",
            "email": "picker@example.com",
            "password": "password123",
            "confirm_password": "password123",
            "avatar": AVATAR_IDS[0],
        },
    )
    assert response.status_code == 201
    assert response.json()["avatar"] == AVATAR_IDS[0]


async def test_signup_without_a_pick_leaves_it_null(client: AsyncClient):
    response = await client.post(
        "/users/",
        json={
            "name": "No Picker",
            "email": "nopicker@example.com",
            "password": "password123",
            "confirm_password": "password123",
            "avatar": None,
        },
    )
    assert response.status_code == 201
    assert response.json()["avatar"] is None


async def test_signup_refuses_an_uncatalogued_avatar(client: AsyncClient):
    """422 from the schema, not a stored string that later becomes a path."""
    response = await client.post(
        "/users/",
        json={
            "name": "Bad Picker",
            "email": "bad@example.com",
            "password": "password123",
            "confirm_password": "password123",
            "avatar": "../../etc/passwd",
        },
    )
    assert response.status_code == 422


async def test_signup_page_renders_the_whole_catalogue(client: AsyncClient):
    """The picker is server-rendered from ``AVATAR_IDS``, so the page is where
    a catalogue change shows up -- not a second list in the template."""
    response = await client.get("/views/auth/")
    assert response.status_code == 200
    for avatar in AVATAR_IDS:
        assert f'data-avatar="{avatar}"' in response.text


# --- changing the picture after signup --------------------------------------
#
# The profile picker is a `PATCH /users/{id}` carrying nothing but `avatar`,
# which is only a valid body because `UserUpdate` makes every field optional.
# `name` is the trap in that change: the column is NOT NULL, so "absent" and
# "explicitly null" have to stay different answers.


async def _signup(client: AsyncClient, email: str, avatar: str | None = None) -> int:
    response = await client.post(
        "/users/",
        json={
            "name": "Avatar Changer",
            "email": email,
            "password": "password123",
            "confirm_password": "password123",
            "avatar": avatar,
        },
    )
    assert response.status_code == 201
    return response.json()["id"]


async def test_patch_can_change_the_avatar_alone(client: AsyncClient):
    user_id = await _signup(client, "change@example.com", AVATAR_IDS[0])
    response = await client.patch(f"/users/{user_id}", json={"avatar": AVATAR_IDS[5]})
    assert response.status_code == 200
    assert response.json()["avatar"] == AVATAR_IDS[5]
    assert response.json()["name"] == "Avatar Changer"


async def test_patch_can_clear_the_avatar(client: AsyncClient):
    """Un-picking has to stay reachable: `None` is a real state, not a gap."""
    user_id = await _signup(client, "clear@example.com", AVATAR_IDS[1])
    response = await client.patch(f"/users/{user_id}", json={"avatar": None})
    assert response.status_code == 200
    assert response.json()["avatar"] is None


async def test_patch_refuses_an_uncatalogued_avatar(client: AsyncClient):
    """The same schema gate as signup -- both front doors inherit it."""
    user_id = await _signup(client, "badpatch@example.com")
    response = await client.patch(f"/users/{user_id}", json={"avatar": "totally-made-up"})
    assert response.status_code == 422


async def test_patch_refuses_an_explicitly_null_name(client: AsyncClient):
    """Optional means "may be omitted", not "may be erased" -- the column is
    NOT NULL, so this has to fail at the schema, not at the database."""
    user_id = await _signup(client, "nullname@example.com")
    response = await client.patch(f"/users/{user_id}", json={"name": None})
    assert response.status_code == 422


async def test_profile_page_renders_the_picker_catalogue(client: AsyncClient):
    """The sheet's options are server-built from ``AVATAR_IDS`` (same as the
    signup grid), so a catalogue change shows up here rather than in a second
    list inside the template."""
    user_id = await _signup(client, "profilepicker@example.com", AVATAR_IDS[2])
    response = await client.get(f"/views/users/{user_id}")
    assert response.status_code == 200
    assert 'id="avatarEdit"' in response.text
    for avatar in AVATAR_IDS:
        assert avatar in response.text
