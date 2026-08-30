"""The profile's «اطلاعات حساب» section — optional contact/location fields.

Everything here is optional and own-profile-only, so the rules worth pinning
are the ones a UI cannot enforce: a cleared box has to reach the column as
NULL (one spelling of "unset", not two), a phone has to be stored in one
normalized form whichever digits were typed, and none of it may leak into the
public user shape.
"""

from httpx import AsyncClient

from app.models.user import User


async def _signup(client: AsyncClient, email: str = "info@example.com") -> int:
    response = await client.post(
        "/users/",
        json={
            "name": "Info Owner",
            "email": email,
            "password": "password123",
            "confirm_password": "password123",
        },
    )
    assert response.status_code == 201
    return response.json()["id"]


async def test_patch_saves_the_optional_details(client: AsyncClient):
    user_id = await _signup(client)
    response = await client.patch(
        f"/users/{user_id}",
        json={
            "phone": "09120000000",
            "country": "ایران",
            "city": "تهران",
            "address": "خیابان ولیعصر، پلاک ۱",
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["phone"] == "09120000000"
    assert body["city"] == "تهران"
    assert body["address"] == "خیابان ولیعصر، پلاک ۱"
    # the name it was not editing is untouched -- the sheet is a PATCH
    assert body["name"] == "Info Owner"


async def test_a_cleared_box_is_stored_as_null(client: AsyncClient, db):
    """The sheet submits every field it renders, so "" means "not given"."""
    user_id = await _signup(client, "clearinfo@example.com")
    await client.patch(f"/users/{user_id}", json={"city": "تهران"})
    response = await client.patch(f"/users/{user_id}", json={"city": "   "})
    assert response.status_code == 200
    assert response.json()["city"] is None
    assert (await db.get(User, user_id)).city is None


async def test_phone_is_normalized_to_ascii_digits(client: AsyncClient):
    """۰۹۱۲… and 0912… are the same number, so they must not be two strings."""
    user_id = await _signup(client, "phone@example.com")
    response = await client.patch(f"/users/{user_id}", json={"phone": "۰۹۱۲ ۰۰۰-۰۰۰۰"})
    assert response.status_code == 200
    assert response.json()["phone"] == "09120000000"


async def test_phone_refuses_prose(client: AsyncClient):
    user_id = await _signup(client, "badphone@example.com")
    response = await client.patch(f"/users/{user_id}", json={"phone": "بعداً می‌گویم"})
    assert response.status_code == 422


async def test_details_stay_out_of_the_public_shape(client: AsyncClient):
    """`UserPublicRead` is what `GET /users/{id}` returns -- contact details
    are not part of it, and adding a column must not quietly change that.

    Read through the detail route rather than the list: `GET /users/` became
    the admin panel's roster and answers `UserAdminRead` to an admin only, so
    it is no longer where the public shape can be observed.
    """
    user_id = await _signup(client, "public@example.com")
    await client.patch(f"/users/{user_id}", json={"phone": "09120000000", "city": "تهران"})
    shown = await client.get(f"/users/{user_id}")
    assert shown.status_code == 200
    row = shown.json()
    assert "phone" not in row and "city" not in row and "address" not in row
    assert "email" not in row


async def test_a_taken_email_is_a_409_not_a_500(client: AsyncClient):
    """`email` is UNIQUE and now editable from the profile."""
    await _signup(client, "taken@example.com")
    second_id = await _signup(client, "second@example.com")
    response = await client.patch(f"/users/{second_id}", json={"email": "taken@example.com"})
    assert response.status_code == 409


async def test_profile_page_renders_the_section(client: AsyncClient):
    user_id = await _signup(client, "page@example.com")
    await client.patch(f"/users/{user_id}", json={"city": "شیراز"})
    page = await client.get(f"/views/users/{user_id}")
    assert page.status_code == 200
    assert "اطلاعات حساب" in page.text
    assert 'data-info="city"' in page.text
    assert "شیراز" in page.text
    # an unset optional field keeps its row rather than vanishing
    assert 'data-info="address"' in page.text
    assert "ثبت نشده" in page.text
