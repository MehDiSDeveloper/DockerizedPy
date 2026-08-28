"""Who may see *this particular row* -- not merely "is there a session".

``test_page_authorization.py`` pins the authentication layer: a page bounces an
anonymous visitor to the login form, a fragment 401s. These pin the layer above
it. Every case here is a request that carries a perfectly valid session and is
still refused, because the resource belongs to somebody else.

Two conventions the assertions below encode:

* **404, not 403, wherever a 403 would answer the enumerator's question.**
  Profiles and check-ins are addressed by bare sequential integers with no
  in-app link to another member's, so "not yours" and "not there" must be
  indistinguishable. Where the caller can already *see* the resource -- a
  public challenge they do not own -- 403 leaks nothing and stays.
* **The refusal keeps the API's status codes.** None of these paths may answer
  with the pages' 303 to login; the caller is signed in, so there is nothing to
  log in to.
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession
from test_challenge_lifecycle import authenticate, create_challenge, make_user

# ==========================================================================
# Profiles -- own only, on both front doors
# ==========================================================================


@pytest.mark.asyncio
async def test_profile_page_shows_your_own(client: AsyncClient, db: AsyncSession):
    user = await make_user(db, "Self")
    authenticate(client, user.id)

    page = await client.get(f"/views/users/{user.id}")
    assert page.status_code == 200
    assert "Self" in page.text


@pytest.mark.asyncio
async def test_profile_page_404s_on_someone_elses(
    client: AsyncClient, db: AsyncSession
):
    """The gap this suite was written for: authentication was checked, the id
    in the path was not."""
    viewer = await make_user(db, "Viewer")
    other = await make_user(db, "Other Member")
    authenticate(client, viewer.id)

    page = await client.get(f"/views/users/{other.id}")
    assert page.status_code == 404
    assert "Other Member" not in page.text


@pytest.mark.asyncio
async def test_profile_page_404s_the_same_for_a_nonexistent_id(
    client: AsyncClient, db: AsyncSession
):
    """A member id and an unused one must be indistinguishable, or the 404 is
    a slower 403 and the walk of ``1..n`` still works."""
    viewer = await make_user(db, "Viewer")
    other = await make_user(db, "Other Member")
    authenticate(client, viewer.id)

    real = await client.get(f"/views/users/{other.id}")
    absent = await client.get(f"/views/users/{other.id + 999}")
    assert real.status_code == absent.status_code == 404
    assert real.text == absent.text


@pytest.mark.asyncio
async def test_profile_page_still_redirects_when_signed_out(
    client: AsyncClient, db: AsyncSession
):
    """Authorization must not swallow the authentication case: no session is
    still a 303 to the login form, not a 404."""
    user = await make_user(db, "Self")
    response = await client.get(f"/views/users/{user.id}")
    assert response.status_code == 303
    assert response.headers["location"].startswith("/views/auth/?next=")


@pytest.mark.asyncio
async def test_user_api_returns_your_own_row(client: AsyncClient, db: AsyncSession):
    user = await make_user(db, "Self")
    authenticate(client, user.id)

    resp = await client.get(f"/users/{user.id}")
    assert resp.status_code == 200
    assert resp.json()["name"] == "Self"
    assert "email" not in resp.json()


@pytest.mark.asyncio
async def test_user_api_404s_on_someone_elses(client: AsyncClient, db: AsyncSession):
    """The JSON twin of the profile page -- gating only the page would leave
    the harvest one ``curl`` away."""
    viewer = await make_user(db, "Viewer")
    other = await make_user(db, "Other Member")
    authenticate(client, viewer.id)

    resp = await client.get(f"/users/{other.id}")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_user_api_401s_when_anonymous(client: AsyncClient, db: AsyncSession):
    """A real 401, never a redirect -- this is the JSON API."""
    user = await make_user(db, "Self")
    resp = await client.get(f"/users/{user.id}")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_user_list_is_closed_to_anonymous_callers(
    client: AsyncClient, db: AsyncSession
):
    """``GET /users/`` is held for a future admin panel and has no caller in
    the app. There is no role column to check yet, so authentication is the
    only gate it can carry -- but it must at least carry that, or the whole
    membership is readable without an account."""
    await make_user(db, "Someone")
    resp = await client.get("/users/")
    assert resp.status_code == 401


# ==========================================================================
# Check-ins -- addressed by id, so a cross-user miss looks like a miss
# ==========================================================================


async def _record_checkin(client: AsyncClient) -> tuple[int, int]:
    """Create a `once` challenge as the signed-in user and check in on it."""
    created = await create_challenge(client)
    assert created.status_code == 201, created.text
    challenge_id = created.json()["id"]

    checkin = await client.post(
        "/checkins/",
        json={
            "challenge_id": challenge_id,
            "occurrence_key": "single",
            "state": "completed",
        },
    )
    assert checkin.status_code == 201, checkin.text
    return challenge_id, checkin.json()["id"]


@pytest.mark.asyncio
async def test_another_users_checkin_is_not_editable(
    client: AsyncClient, db: AsyncSession
):
    owner = await make_user(db, "Owner")
    stranger = await make_user(db, "Stranger")

    authenticate(client, owner.id)
    _challenge_id, checkin_id = await _record_checkin(client)

    authenticate(client, stranger.id)
    patched = await client.patch(f"/checkins/{checkin_id}", json={"note": "not mine"})
    assert patched.status_code == 404

    deleted = await client.delete(f"/checkins/{checkin_id}")
    assert deleted.status_code == 404


@pytest.mark.asyncio
async def test_another_users_checkin_id_is_indistinguishable_from_an_unused_one(
    client: AsyncClient, db: AsyncSession
):
    """Sequential ids: if "exists but not yours" answered differently from
    "does not exist", walking ``/checkins/1..n`` would map every member's
    logged history."""
    owner = await make_user(db, "Owner")
    stranger = await make_user(db, "Stranger")

    authenticate(client, owner.id)
    _challenge_id, checkin_id = await _record_checkin(client)

    authenticate(client, stranger.id)
    real = await client.delete(f"/checkins/{checkin_id}")
    absent = await client.delete(f"/checkins/{checkin_id + 999}")
    assert real.status_code == absent.status_code == 404
    assert real.json() == absent.json()


@pytest.mark.asyncio
async def test_your_own_checkin_stays_editable(client: AsyncClient, db: AsyncSession):
    """The 404 above is about the *other* user's row -- it must not have made
    the ordinary edit path stricter."""
    owner = await make_user(db, "Owner")
    authenticate(client, owner.id)
    _challenge_id, checkin_id = await _record_checkin(client)

    patched = await client.patch(f"/checkins/{checkin_id}", json={"note": "mine"})
    assert patched.status_code == 200, patched.text


# ==========================================================================
# Challenges -- mutations follow the same visibility filter as reads
# ==========================================================================


@pytest.mark.asyncio
async def test_mutating_an_invisible_challenge_looks_like_it_is_absent(
    client: AsyncClient, db: AsyncSession
):
    """A private challenge is hidden from ``GET``; PATCH/DELETE must hide it
    too, or the write path becomes the oracle the read path refuses to be."""
    owner = await make_user(db, "Owner")
    stranger = await make_user(db, "Stranger")

    authenticate(client, owner.id)
    created = await create_challenge(client, visibility="private")
    challenge_id = created.json()["id"]

    authenticate(client, stranger.id)
    assert (await client.get(f"/challenges/{challenge_id}")).status_code == 404
    assert (
        await client.patch(f"/challenges/{challenge_id}", json={"title": "Hijacked"})
    ).status_code == 404
    assert (await client.delete(f"/challenges/{challenge_id}")).status_code == 404


@pytest.mark.asyncio
async def test_mutating_a_visible_challenge_you_do_not_own_is_403(
    client: AsyncClient, db: AsyncSession
):
    """A public challenge is already readable by anyone, so 403 tells the
    caller nothing they could not see -- and it is the more honest answer."""
    owner = await make_user(db, "Owner")
    stranger = await make_user(db, "Stranger")

    authenticate(client, owner.id)
    created = await create_challenge(client, visibility="public")
    challenge_id = created.json()["id"]

    authenticate(client, stranger.id)
    assert (await client.get(f"/challenges/{challenge_id}")).status_code == 200
    assert (
        await client.patch(f"/challenges/{challenge_id}", json={"title": "Hijacked"})
    ).status_code == 403
    assert (await client.delete(f"/challenges/{challenge_id}")).status_code == 403


@pytest.mark.asyncio
async def test_owner_keeps_full_control_of_their_own_challenge(
    client: AsyncClient, db: AsyncSession
):
    owner = await make_user(db, "Owner")
    authenticate(client, owner.id)
    created = await create_challenge(client, visibility="private")
    challenge_id = created.json()["id"]

    patched = await client.patch(
        f"/challenges/{challenge_id}", json={"title": "Renamed"}
    )
    assert patched.status_code == 200, patched.text
    assert (await client.delete(f"/challenges/{challenge_id}")).status_code == 204


# ==========================================================================
# Enrollment-scoped reads -- keyed on the session user, never on a param
# ==========================================================================


@pytest.mark.asyncio
async def test_enrollment_reads_are_scoped_to_the_caller(
    client: AsyncClient, db: AsyncSession
):
    """``/enrollments/{challenge_id}`` is addressed by *challenge*, so a
    stranger asking about a challenge they can see still gets their own
    (absent) enrollment, never the owner's."""
    owner = await make_user(db, "Owner")
    stranger = await make_user(db, "Stranger")

    authenticate(client, owner.id)
    created = await create_challenge(client, visibility="public")
    challenge_id = created.json()["id"]

    authenticate(client, stranger.id)
    assert (await client.get(f"/enrollments/{challenge_id}")).status_code == 404
    history = await client.get(
        f"/enrollments/{challenge_id}/history",
        params={"from": "2026-01-01", "to": "2026-12-31"},
    )
    assert history.status_code == 404
    assert (await client.get("/enrollments/")).json() == []
