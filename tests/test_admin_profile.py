"""An operator can open a member's profile, and correct what it holds.

This is the one widening of ``profile_visibility_filter``, and the suite is
written around the three things that widening must not cost:

* **the member's own rule is unchanged.** Own-only is what keeps a walk of
  ``1..n`` from harvesting the membership, and it is still the answer for
  everyone without ``Perm.USER_VIEW_ANY`` -- as a 404 on both front doors, so
  "not yours" and "not there" stay indistinguishable.
* **reach and authorship stay separate.** An operator may correct an
  account's own fields; they may not become it. The role keeps its own audited
  door, deletion is refused outright, and every edit is stamped with the
  operator's id -- which is the whole reason the grant is acceptable.
* **the affordance and the gate agree.** The panel's rows link to the
  profile, the profile renders its administration section only for someone
  who can actually use it, and neither trusts the other: the route gates
  itself either way.
"""

from __future__ import annotations

from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import create_session_cookie, hash_password
from app.models.user import User, UserRole

PANEL_PATH = "/views/admin/"


async def make_user(
    db: AsyncSession, name: str, role: str = UserRole.MEMBER.value
) -> User:
    user = User(
        name=name,
        email=f"{name.lower().replace(' ', '.')}@example.com",
        password_hash=hash_password("password123"),
        role=role,
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


def sign_in(client: AsyncClient, user: User) -> None:
    client.cookies.set("session", create_session_cookie(user.id))


# ==========================================================================
# Reach -- the widened half
# ==========================================================================


async def test_admin_opens_a_members_profile(client: AsyncClient, db: AsyncSession):
    admin = await make_user(db, "Ops", UserRole.ADMIN.value)
    member = await make_user(db, "Zahra", UserRole.MEMBER.value)
    sign_in(client, admin)

    page = await client.get(f"/views/users/{member.id}")
    assert page.status_code == 200
    assert "Zahra" in page.text
    # The account details are the point of going there -- an operator who can
    # see only what the roster already showed has gained nothing.
    assert "zahra@example.com" in page.text


async def test_a_member_still_cannot_open_someone_elses(
    client: AsyncClient, db: AsyncSession
):
    """The widening is a permission, not a relaxation.

    Pinned here as well as in ``test_object_authorization.py`` because it is
    *this* change that could break it: the filter now has a branch, and a
    branch that answers the wrong way is a silent, total leak.
    """
    viewer = await make_user(db, "Viewer")
    other = await make_user(db, "Other Member")
    sign_in(client, viewer)

    assert (await client.get(f"/views/users/{other.id}")).status_code == 404
    assert (await client.get(f"/users/{other.id}")).status_code == 404


async def test_the_json_door_widens_with_the_page(
    client: AsyncClient, db: AsyncSession
):
    """One clause, both front doors -- the reason it is a composed filter and
    not a check inside each handler."""
    admin = await make_user(db, "Ops", UserRole.ADMIN.value)
    member = await make_user(db, "Zahra")
    sign_in(client, admin)

    response = await client.get(f"/users/{member.id}")
    assert response.status_code == 200
    assert response.json()["name"] == "Zahra"
    # ...but still through the *public* read shape: the wider `UserAdminRead`
    # belongs to the roster route that requires `Perm.USER_LIST`.
    assert "email" not in response.json()


# ==========================================================================
# Editing -- support, not authorship
# ==========================================================================


async def test_admin_corrects_a_members_details(
    client: AsyncClient, db: AsyncSession
):
    admin = await make_user(db, "Ops", UserRole.ADMIN.value)
    member = await make_user(db, "Zahra")
    sign_in(client, admin)

    response = await client.patch(
        f"/users/{member.id}", json={"city": "شیراز", "phone": "09120000000"}
    )
    assert response.status_code == 200
    assert response.json()["city"] == "شیراز"


async def test_the_edit_is_stamped_with_the_operator(
    client: AsyncClient, db: AsyncSession
):
    """``last_modifier_user_id`` is the acting user, not the row's owner.

    It is the only record that an edit was made on someone's behalf, so an
    operator's write landing under the member's own id would erase the one
    thing that makes the grant defensible.
    """
    admin = await make_user(db, "Ops", UserRole.ADMIN.value)
    member = await make_user(db, "Zahra")
    admin_id, member_id = admin.id, member.id
    sign_in(client, admin)

    await client.patch(f"/users/{member_id}", json={"city": "شیراز"})

    # Read the row back rather than trusting the response body: the stamp is
    # not in any read shape, which is the point -- it is a record, not a field.
    db.expire_all()
    row = (await db.execute(select(User).where(User.id == member_id))).scalar_one()
    assert row.last_modifier_user_id == admin_id


async def test_a_member_still_cannot_edit_another_account(
    client: AsyncClient, db: AsyncSession
):
    viewer = await make_user(db, "Viewer")
    other = await make_user(db, "Other Member")
    sign_in(client, viewer)

    response = await client.patch(f"/users/{other.id}", json={"city": "شیراز"})
    assert response.status_code == 403


async def test_an_operator_cannot_delete_a_member(
    client: AsyncClient, db: AsyncSession
):
    """Correcting a record is support; erasing the person is not, and no
    permission grants it."""
    admin = await make_user(db, "Ops", UserRole.ADMIN.value)
    member = await make_user(db, "Zahra")
    sign_in(client, admin)

    assert (await client.delete(f"/users/{member.id}")).status_code == 403


# ==========================================================================
# The screen
# ==========================================================================


async def test_the_profile_says_whose_account_it_is(
    client: AsyncClient, db: AsyncSession
):
    """An operator arriving from a roster of near-identical rows must be able
    to tell at a glance that what they are about to change is not theirs."""
    admin = await make_user(db, "Ops", UserRole.ADMIN.value)
    member = await make_user(db, "Zahra")
    sign_in(client, admin)

    page = await client.get(f"/views/users/{member.id}")
    assert "profile-note" in page.text
    assert 'id="roleEdit"' in page.text


async def test_the_admin_section_is_absent_on_your_own_profile(
    client: AsyncClient, db: AsyncSession
):
    """``can_administer`` is false on your own profile even holding the
    permission: the server refuses self-demotion, and offering a control that
    can only fail is worse than not offering it."""
    admin = await make_user(db, "Ops", UserRole.ADMIN.value)
    sign_in(client, admin)

    page = await client.get(f"/views/users/{admin.id}")
    assert page.status_code == 200
    assert 'id="roleEdit"' not in page.text
    assert "profile-note" not in page.text
    # ...while the things that are only ever your own are still there.
    assert 'id="logoutBtn"' in page.text


async def test_a_member_profile_carries_no_administration(
    client: AsyncClient, db: AsyncSession
):
    member = await make_user(db, "Zahra")
    sign_in(client, member)

    page = await client.get(f"/views/users/{member.id}")
    assert 'id="roleEdit"' not in page.text


async def test_someone_elses_profile_offers_no_personal_actions(
    client: AsyncClient, db: AsyncSession
):
    """Logout, settings and the panel row belong to the person *looking*, and
    the page they are looking at is not theirs -- so none of them render."""
    admin = await make_user(db, "Ops", UserRole.ADMIN.value)
    member = await make_user(db, "Zahra")
    sign_in(client, admin)

    page = await client.get(f"/views/users/{member.id}")
    assert 'id="logoutBtn"' not in page.text
    # The panel row from the menu list, matched on its own markup: the back
    # button legitimately points at the panel (that is where an operator came
    # from) and the role row's hint names it in prose, so neither the path
    # nor the words alone identify the row.
    assert '<span class="mi-label">پنل مدیریت</span>' not in page.text


async def test_the_roster_links_each_row_to_its_profile(
    client: AsyncClient, db: AsyncSession
):
    """The way in. A link rather than a sheet, so the row is openable in a new
    tab and the administering happens in front of the evidence for it."""
    admin = await make_user(db, "Ops", UserRole.ADMIN.value)
    member = await make_user(db, "Zahra")
    sign_in(client, admin)

    page = await client.get(PANEL_PATH)
    assert f'href="/views/users/{member.id}"' in page.text
    assert f'href="/views/users/{admin.id}"' in page.text
