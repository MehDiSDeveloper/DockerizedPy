"""The boot-time operator account (`app.scripts.seed_admin`).

The rules worth pinning are the ones a "just make sure the admin exists"
helper gets wrong: it must be idempotent, it must promote an account the
operator already signed up with, and it must **not** rewrite the password of
an account that already exists -- otherwise every deploy silently reverts a
changed password, and the env var becomes a way to take over any account.
"""

from __future__ import annotations

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import hash_password, verify_password
from app.config import settings
from app.models.user import User, UserRole
from app.scripts.seed_admin import ensure_seed_admin

EMAIL = "operator@example.com"


@pytest_asyncio.fixture(autouse=True)
def seed_settings():
    """Configure a seed account for the test, then put settings back."""
    before = {
        name: getattr(settings, name)
        for name in (
            "seed_admin_email",
            "seed_admin_password",
            "seed_admin_name",
            "seed_admin_reset_password",
        )
    }
    settings.seed_admin_email = EMAIL
    settings.seed_admin_password = "correct-horse"
    settings.seed_admin_name = "اپراتور"
    settings.seed_admin_reset_password = False
    yield settings
    for name, value in before.items():
        setattr(settings, name, value)


async def _get(db: AsyncSession) -> User | None:
    return (
        await db.execute(select(User).where(User.email == EMAIL))
    ).scalar_one_or_none()


async def test_creates_the_admin_when_missing(db: AsyncSession):
    await ensure_seed_admin(db)

    user = await _get(db)
    assert user is not None
    assert user.role == UserRole.ADMIN.value
    assert verify_password("correct-horse", user.password_hash)


async def test_is_idempotent_across_boots(db: AsyncSession):
    await ensure_seed_admin(db)
    first = await _get(db)
    hash_before = first.password_hash

    await ensure_seed_admin(db)

    rows = (await db.execute(select(User).where(User.email == EMAIL))).scalars().all()
    assert len(rows) == 1
    assert rows[0].password_hash == hash_before


async def test_promotes_an_existing_member(db: AsyncSession):
    db.add(
        User(
            name="خودم",
            email=EMAIL,
            password_hash=hash_password("my-own-password"),
            role=UserRole.MEMBER.value,
        )
    )
    await db.commit()

    await ensure_seed_admin(db)

    user = await _get(db)
    assert user.role == UserRole.ADMIN.value
    # Promotion only -- the account keeps the password its owner chose.
    assert verify_password("my-own-password", user.password_hash)
    assert not verify_password("correct-horse", user.password_hash)


async def test_reset_flag_rewrites_the_password(db: AsyncSession):
    db.add(
        User(
            name="خودم",
            email=EMAIL,
            password_hash=hash_password("forgotten"),
            role=UserRole.ADMIN.value,
        )
    )
    await db.commit()
    settings.seed_admin_reset_password = True

    await ensure_seed_admin(db)

    user = await _get(db)
    assert verify_password("correct-horse", user.password_hash)


@pytest.mark.parametrize("field", ["seed_admin_email", "seed_admin_password"])
async def test_unconfigured_seeds_nothing(db: AsyncSession, field: str):
    setattr(settings, field, "")

    await ensure_seed_admin(db)

    assert (await db.execute(select(User))).scalars().all() == []


async def test_the_seeded_admin_can_log_in(client, db: AsyncSession):
    """End to end: the point of the whole script is a usable login."""
    await ensure_seed_admin(db)

    response = await client.post(
        "/auth/login", json={"email": EMAIL, "password": "correct-horse"}
    )

    assert response.status_code == 200, response.text
    assert "session" in response.cookies
