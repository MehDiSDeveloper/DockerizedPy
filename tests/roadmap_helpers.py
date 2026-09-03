"""Fixtures the roadmap tests share.

A module rather than a growing ``conftest.py``, exactly as
``tests/group_helpers.py`` is: these builders are about one subsystem and the
rest of the suite has no use for them. ``make_user`` / ``sign_in`` are
borrowed from the group helpers rather than written twice.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.challenge import Challenge, ChallengeCategory
from app.models.checkin import CheckIn
from app.models.enrollment import ChallengeRole, Enrollment
from app.models.roadmap import Roadmap, RoadmapStep
from app.models.stats import ChallengeStats
from app.models.user import User

# Re-exported so a test file imports one place.
from tests.group_helpers import make_user, sign_in  # noqa: F401


async def make_challenge(
    db: AsyncSession,
    owner: User,
    *,
    title: str = "چالش",
    visibility: str = "public",
    cadence: dict | None = None,
    goal_unit: str | None = None,
    lifecycle: str = "active",
) -> Challenge:
    """A challenge with its owner's auto-enrolment and stats row, as
    ``create_challenge_record`` writes them."""
    cadence = cadence or {"kind": "once"}
    challenge = Challenge(
        title=title,
        owner_id=owner.id,
        category=ChallengeCategory.OTHER,
        cadence_kind=cadence["kind"],
        cadence=cadence,
        visibility=visibility,
        lifecycle_status=lifecycle,
        goal_unit=goal_unit,
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
    db.add(ChallengeStats(challenge_id=challenge.id, participant_count=1))
    await db.commit()
    await db.refresh(challenge)
    return challenge


async def make_roadmap(
    db: AsyncSession,
    owner: User,
    *,
    title: str = "مسیر",
    visibility: str = "public",
    strict: bool = False,
) -> Roadmap:
    roadmap = Roadmap(
        title=title, owner_id=owner.id, visibility=visibility, strict=strict
    )
    db.add(roadmap)
    await db.commit()
    await db.refresh(roadmap)
    return roadmap


async def add_step(
    db: AsyncSession,
    roadmap: Roadmap,
    challenge: Challenge,
    *,
    rule: dict | None = None,
    stage: int | None = None,
    required: bool = True,
) -> RoadmapStep:
    """Append a step. ``stage`` defaults to "after everything already here",
    which is what ``POST /roadmaps/{id}/steps`` does."""
    if stage is None:
        existing = [s for s in await _steps(db, roadmap.id)]
        stage = (max((s.stage_index for s in existing), default=-1)) + 1
    step = RoadmapStep(
        roadmap_id=roadmap.id,
        challenge_id=challenge.id,
        stage_index=stage,
        order_in_stage=0,
        required=required,
        completion_rule=rule or {"kind": "manual"},
    )
    db.add(step)
    await db.commit()
    await db.refresh(step)
    return step


async def _steps(db: AsyncSession, roadmap_id: int) -> list[RoadmapStep]:
    return list(
        (
            await db.execute(
                select(RoadmapStep).where(RoadmapStep.roadmap_id == roadmap_id)
            )
        )
        .scalars()
        .all()
    )


async def log_checkins(
    db: AsyncSession,
    *,
    user: User,
    challenge: Challenge,
    count: int = 1,
    amount=None,
    state: str = "completed",
) -> None:
    """Record ``count`` completed occurrences for this member on this
    challenge, straight into the table.

    The check-in *route* is exercised by its own tests; what the roadmap tests
    need is the rows a rule counts, so they are written directly and the
    engine is asked what it makes of them.
    """
    enrollment = (
        await db.execute(
            select(Enrollment).where(
                Enrollment.challenge_id == challenge.id,
                Enrollment.user_id == user.id,
            )
        )
    ).scalar_one()
    # Continue from what is already there: `uq_enrollment_occurrence` is the
    # app's idempotency guarantee, so a helper that restarted at day one would
    # fail the second time a test logged anything.
    already = (
        await db.execute(
            select(func.count())
            .select_from(CheckIn)
            .where(CheckIn.enrollment_id == enrollment.id)
        )
    ).scalar_one()
    now = datetime.now(UTC)
    for offset in range(count):
        index = already + offset
        db.add(
            CheckIn(
                enrollment_id=enrollment.id,
                challenge_id=challenge.id,
                occurrence_key=f"D2026-01-{index + 1:02d}",
                state=state,
                occurrence_local_date=date(2026, 1, index + 1),
                occurred_at_utc=now,
                timezone=enrollment.timezone,
                amount=amount,
                created_at=now,
            )
        )
    await db.commit()


async def set_streak(db: AsyncSession, *, user: User, challenge: Challenge, n: int):
    enrollment = (
        await db.execute(
            select(Enrollment).where(
                Enrollment.challenge_id == challenge.id,
                Enrollment.user_id == user.id,
            )
        )
    ).scalar_one()
    enrollment.current_streak = n
    await db.commit()
