from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.auth import get_current_user_id
from app.database import get_db
from app.models.checkin import CheckIn
from app.models.enrollment import Enrollment, EnrollmentStatus
from app.occurrences import occurrences_due
from app.routers.checkin import parse_cadence
from app.schemas.checkin import TodayItem

router = APIRouter(prefix="/today", tags=["today"])


async def _load_existing_state(
    db: AsyncSession, enrollment_ids: list[int]
) -> dict[int, tuple[set[str], dict[str, int]]]:
    """Per enrollment: every occurrence_key ever recorded (any state -- a
    skipped occurrence must not show up as due again either), plus a
    completed-only count per recurring_quota period key."""
    by_enrollment: dict[int, tuple[set[str], dict[str, int]]] = {
        eid: (set(), {}) for eid in enrollment_ids
    }
    if not enrollment_ids:
        return by_enrollment

    result = await db.execute(
        select(CheckIn.enrollment_id, CheckIn.occurrence_key, CheckIn.state).where(
            CheckIn.enrollment_id.in_(enrollment_ids)
        )
    )
    for enrollment_id, key, state in result.all():
        keys, period_counts = by_enrollment[enrollment_id]
        keys.add(key)
        if state == "completed" and "#" in key:
            pkey = key.split("#", 1)[0]
            period_counts[pkey] = period_counts.get(pkey, 0) + 1
    return by_enrollment


async def get_today_items(
    db: AsyncSession, *, user_id: int, now_utc: datetime
) -> list[TodayItem]:
    result = await db.execute(
        select(Enrollment)
        .options(selectinload(Enrollment.challenge))
        .where(
            Enrollment.user_id == user_id,
            Enrollment.status == EnrollmentStatus.ACTIVE,
        )
    )
    enrollments = [e for e in result.scalars().all() if e.challenge is not None]

    existing = await _load_existing_state(db, [e.id for e in enrollments])

    items: list[TodayItem] = []
    for enrollment in enrollments:
        challenge = enrollment.challenge
        cadence = parse_cadence(challenge)
        existing_keys, period_counts = existing[enrollment.id]
        due = occurrences_due(
            cadence,
            start_date=enrollment.start_date,
            tz=enrollment.timezone,
            now_utc=now_utc,
            existing_keys=existing_keys,
            period_completed_counts=period_counts,
        )
        for occ in due:
            items.append(
                TodayItem(
                    challenge_id=challenge.id,
                    challenge_title=challenge.title,
                    category=challenge.category.value,
                    occurrence_key=occ.key,
                    opens_at_utc=occ.opens_at_utc,
                    closes_at_utc=occ.closes_at_utc,
                    quota_done=occ.quota_done,
                    quota_target=occ.quota_target,
                    goal_unit=challenge.goal_unit,
                )
            )

    items.sort(key=lambda it: (it.challenge_title, it.occurrence_key))
    return items


@router.get("/", response_model=list[TodayItem])
async def get_today(
    current_user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    return await get_today_items(
        db, user_id=current_user_id, now_utc=datetime.now(UTC)
    )
