"""The two role axes, and the line between them.

`Users.role` (member/admin) answers "may you run the place";
`Enrollments.role` (participant/owner) answers "what are you inside this one
challenge". The tests that matter most here are the *negative* ones -- an
admin is a plain participant in someone else's challenge, and an owner has
no reach outside their own -- because the failure mode of merging the two
axes is silent: everything keeps working, and an admin quietly becomes the
owner of everything.

`app/permissions.py` is the only place a role is turned into an answer, so
the routes are exercised through the API rather than the function: a grant
map nothing checks is documentation, not a gate.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import create_session_cookie, hash_password
from app.models.challenge import Challenge, ChallengeCategory
from app.models.enrollment import ChallengeRole, Enrollment
from app.models.user import User, UserRole
from app.permissions import Perm, can


async def make_user(db: AsyncSession, name: str, role: str = UserRole.MEMBER.value):
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


async def make_challenge(db: AsyncSession, owner: User) -> Challenge:
    challenge = Challenge(
        title="A challenge",
        owner_id=owner.id,
        category=ChallengeCategory.OTHER,
        cadence_kind="once",
        cadence={"kind": "once"},
        visibility="public",
        lifecycle_status="active",
    )
    db.add(challenge)
    await db.flush()
    db.add(
        Enrollment(
            challenge_id=challenge.id,
            user_id=owner.id,
            start_date=date(2026, 1, 1),
            role=ChallengeRole.OWNER.value,
        )
    )
    await db.commit()
    await db.refresh(challenge)
    return challenge


# --- Users.role: the app-wide axis -----------------------------------------


async def test_new_account_is_a_plain_member(client: AsyncClient, db: AsyncSession):
    """Signup must not be a way to pick a role."""
    response = await client.post(
        "/users/",
        json={
            "name": "Newcomer",
            "email": "newcomer@example.com",
            "password": "password123",
            "confirm_password": "password123",
            "role": "admin",  # ignored: not part of any create shape
        },
    )
    assert response.status_code == 201
    user = (
        await db.execute(select(User).where(User.email == "newcomer@example.com"))
    ).scalar_one()
    assert user.role == UserRole.MEMBER.value


async def test_member_cannot_list_every_account(client: AsyncClient, db: AsyncSession):
    member = await make_user(db, "Plain Member")
    sign_in(client, member)
    assert (await client.get("/users/")).status_code == 403


async def test_admin_lists_every_account(client: AsyncClient, db: AsyncSession):
    await make_user(db, "Someone Else")
    admin = await make_user(db, "The Admin", role=UserRole.ADMIN.value)
    sign_in(client, admin)

    response = await client.get("/users/")
    assert response.status_code == 200
    rows = response.json()
    assert len(rows) == 2
    # The admin shape carries what the panel administers, and nothing more.
    assert set(rows[0]) == {"id", "name", "email", "avatar", "role"}


async def test_member_cannot_promote_themselves(client: AsyncClient, db: AsyncSession):
    """The escalation path has exactly one door, and it is admin-only."""
    member = await make_user(db, "Ambitious Member")
    sign_in(client, member)

    assert (
        await client.patch(f"/users/{member.id}/role", json={"role": "admin"})
    ).status_code == 403

    # And the profile PATCH is not a side door either.
    response = await client.patch(f"/users/{member.id}", json={"role": "admin"})
    assert response.status_code == 200
    await db.refresh(member)
    assert member.role == UserRole.MEMBER.value


async def test_admin_promotes_someone_but_not_themselves(
    client: AsyncClient, db: AsyncSession
):
    member = await make_user(db, "Future Admin")
    admin = await make_user(db, "Sitting Admin", role=UserRole.ADMIN.value)
    sign_in(client, admin)

    response = await client.patch(f"/users/{member.id}/role", json={"role": "admin"})
    assert response.status_code == 200
    await db.refresh(member)
    assert member.role == UserRole.ADMIN.value

    # Self-demotion would lock the panel with no console to undo it from.
    assert (
        await client.patch(f"/users/{admin.id}/role", json={"role": "member"})
    ).status_code == 400


async def test_unknown_role_is_refused(client: AsyncClient, db: AsyncSession):
    member = await make_user(db, "Target Member")
    admin = await make_user(db, "Gatekeeper", role=UserRole.ADMIN.value)
    sign_in(client, admin)
    assert (
        await client.patch(f"/users/{member.id}/role", json={"role": "superadmin"})
    ).status_code == 422


# --- Enrollments.role: the per-challenge axis ------------------------------


async def test_creator_enrolment_carries_the_owner_role(
    client: AsyncClient, db: AsyncSession
):
    """D1's auto-enrolment is the row that states ownership."""
    owner = await make_user(db, "Challenge Owner")
    sign_in(client, owner)

    response = await client.post(
        "/challenges/",
        json={
            "title": "Mine",
            "category": ChallengeCategory.OTHER.value,
            "cadence": {"kind": "once"},
        },
    )
    assert response.status_code == 201
    enrollment = (
        await db.execute(
            select(Enrollment).where(Enrollment.challenge_id == response.json()["id"])
        )
    ).scalar_one()
    assert enrollment.role == ChallengeRole.OWNER.value


async def test_joining_someone_elses_challenge_is_participant(
    client: AsyncClient, db: AsyncSession
):
    owner = await make_user(db, "Owner One")
    joiner = await make_user(db, "Joiner One")
    challenge = await make_challenge(db, owner)

    sign_in(client, joiner)
    assert (await client.post(f"/enrollments/{challenge.id}")).status_code == 201

    enrollment = (
        await db.execute(
            select(Enrollment).where(
                Enrollment.challenge_id == challenge.id,
                Enrollment.user_id == joiner.id,
            )
        )
    ).scalar_one()
    assert enrollment.role == ChallengeRole.PARTICIPANT.value


@pytest.mark.parametrize("role", [UserRole.MEMBER.value, UserRole.ADMIN.value])
async def test_no_app_wide_role_can_edit_someone_elses_challenge(
    client: AsyncClient, db: AsyncSession, role: str
):
    """The line between the two axes, stated as a test.

    An admin is a participant like anyone else inside a challenge they do not
    own -- if this ever passes for `admin`, the two role axes have been
    merged somewhere and every admin now owns every challenge.

    *Editing* is the axis line and is what this pins. An admin's separate
    moderation reach -- archiving, hiding, removing -- is a different grant
    with names of its own, and `tests/test_admin_challenges.py` pins both
    what it allows and that it stops short of authorship.
    """
    owner = await make_user(db, f"Owner For {role}")
    outsider = await make_user(db, f"Outsider {role}", role=role)
    challenge = await make_challenge(db, owner)

    sign_in(client, outsider)
    assert (
        await client.patch(f"/challenges/{challenge.id}", json={"title": "Hijacked"})
    ).status_code == 403


async def test_owner_still_owns_after_unenrolling(client: AsyncClient, db: AsyncSession):
    """Leaving your own challenge deletes the row that carries `owner`.

    `Challenge.owner_id` is what keeps the answer stable -- without the
    resolution in `challenge_role`, unenrolling would silently strip the
    creator of the challenge they still own.
    """
    owner = await make_user(db, "Leaving Owner")
    challenge = await make_challenge(db, owner)
    sign_in(client, owner)

    assert (await client.delete(f"/enrollments/{challenge.id}")).status_code == 204
    assert (
        await client.patch(f"/challenges/{challenge.id}", json={"title": "Renamed"})
    ).status_code == 200


# --- the permission layer itself -------------------------------------------


def test_grants_are_scoped_to_their_own_axis():
    """A unit check on `can()`, because the maps are the policy.

    Nothing per-challenge is reachable through an app-wide role, and nothing
    app-wide through a challenge role -- the routes above prove it end to
    end, this proves it cannot happen for a permission no route uses yet.
    """
    admin = User(id=1, name="Admin", role=UserRole.ADMIN.value)
    member = User(id=2, name="Member", role=UserRole.MEMBER.value)
    challenge = Challenge(id=10, owner_id=member.id)

    assert can(admin, Perm.USER_LIST)
    assert not can(member, Perm.USER_LIST)

    assert can(member, Perm.CHALLENGE_EDIT, challenge=challenge)
    assert not can(admin, Perm.CHALLENGE_EDIT, challenge=challenge)
    assert not can(None, Perm.USER_LIST)

    # Moderation is app-wide and does not leak the other way either: the
    # owner of this challenge holds none of it.
    assert can(admin, Perm.CHALLENGE_MODERATE)
    assert not can(member, Perm.CHALLENGE_MODERATE, challenge=challenge)
    assert not can(member, Perm.CHALLENGE_DELETE_ANY, challenge=challenge)


def test_an_enrollment_only_speaks_for_its_own_member():
    """A row belonging to someone else grants nothing.

    Cheap to get wrong: `challenge_role` is handed an enrollment by the
    caller, and without the `user_id` check it would answer that role to
    whoever asked.
    """
    stranger = User(id=3, name="Stranger", role=UserRole.MEMBER.value)
    someone_elses = Enrollment(
        user_id=99,
        challenge_id=10,
        role=ChallengeRole.OWNER.value,
        start_date=datetime.now(UTC).date(),
    )
    assert not can(stranger, Perm.CHALLENGE_EDIT, enrollment=someone_elses)
