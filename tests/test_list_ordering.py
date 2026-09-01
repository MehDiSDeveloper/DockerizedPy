"""Every paginated list in the app is ordered newest-first, by `created_at`.

`newest_first` (``app/models/audit_base.py``) is the one ordering, shared by
the member-facing challenge list, the home dashboard's enrolments and the
admin panel's roster, so a reader never has to learn a second rule for the
same kind of screen.

Two properties are worth pinning, and the second is the one that breaks
silently: the *direction* (a newer row comes first even when it was given a
lower id, which is what proves the sort reads `created_at` rather than the
autoincrement that usually agrees with it), and the *tie-break* -- rows
sharing one timestamp are ordered by id, without which a page boundary
falling inside such a block can drop or repeat a row. Both cases are real
here: `server_default=func.now()` has second granularity on SQLite, so a
seed run or a burst of signups writes whole blocks sharing one instant.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import hash_password
from app.models.challenge import Challenge
from app.models.enrollment import Enrollment
from app.models.user import User
from app.routers.challenge import fetch_challenge_page
from app.routers.user import fetch_member_page
from app.routers.views.home import _fetch_enrollment_page

BASE = datetime(2026, 8, 1, 12, 0, tzinfo=UTC)


async def make_user(db: AsyncSession, n: int, created_at: datetime) -> User:
    user = User(
        name=f"Member {n}",
        email=f"member{n}@example.com",
        password_hash=hash_password("password123"),
        created_at=created_at,
    )
    db.add(user)
    await db.flush()
    return user


async def make_challenge(
    db: AsyncSession, n: int, owner: User, created_at: datetime
) -> Challenge:
    challenge = Challenge(
        title=f"Challenge {n}",
        owner_id=owner.id,
        cadence={"kind": "once"},
        created_at=created_at,
    )
    db.add(challenge)
    await db.flush()
    return challenge


async def test_challenge_list_is_newest_first(db: AsyncSession):
    owner = await make_user(db, 0, BASE)
    # Written oldest-id-first but timestamped in the opposite order, so an
    # `id.desc()` sort and a `created_at.desc()` one disagree.
    for n in range(3):
        await make_challenge(db, n, owner, BASE + timedelta(days=n))
    await db.commit()

    challenges, _ = await fetch_challenge_page(
        db, current_user_id=owner.id, category=None, q=None, offset=0, limit=10
    )
    assert [c.title for c in challenges] == [
        "Challenge 2",
        "Challenge 1",
        "Challenge 0",
    ]


async def test_challenge_list_breaks_ties_by_id(db: AsyncSession):
    owner = await make_user(db, 0, BASE)
    for n in range(3):
        await make_challenge(db, n, owner, BASE)
    await db.commit()

    first_page, has_more = await fetch_challenge_page(
        db, current_user_id=owner.id, category=None, q=None, offset=0, limit=2
    )
    second_page, _ = await fetch_challenge_page(
        db, current_user_id=owner.id, category=None, q=None, offset=2, limit=2
    )
    assert has_more
    assert [c.title for c in first_page] == ["Challenge 2", "Challenge 1"]
    assert [c.title for c in second_page] == ["Challenge 0"]


async def test_member_roster_is_newest_first(db: AsyncSession):
    for n in range(3):
        await make_user(db, n, BASE + timedelta(days=n))
    await db.commit()

    members, _ = await fetch_member_page(db)
    assert [m.name for m in members] == ["Member 2", "Member 1", "Member 0"]


async def test_home_enrollments_are_newest_first(db: AsyncSession):
    owner = await make_user(db, 0, BASE)
    for n in range(3):
        challenge = await make_challenge(db, n, owner, BASE)
        db.add(
            Enrollment(
                challenge_id=challenge.id,
                user_id=owner.id,
                start_date=date(2026, 8, 1),
                created_at=BASE + timedelta(days=n),
            )
        )
    await db.commit()

    enrollments, _ = await _fetch_enrollment_page(
        db, user_id=owner.id, status="active", offset=0, limit=10
    )
    assert [e.challenge.title for e in enrollments] == [
        "Challenge 2",
        "Challenge 1",
        "Challenge 0",
    ]
