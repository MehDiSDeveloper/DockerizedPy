from datetime import UTC, date, datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_user_id
from app.database import get_db
from app.models.challenge import Challenge
from app.models.checkin import CheckIn
from app.models.enrollment import Enrollment
from app.models.stats import ChallengeStats
from app.occurrences import derive_state, expected_keys_desc, local_today
from app.routers.challenge import challenge_visibility_filter, resolve_timezone
from app.routers.checkin import occurrence_local_date, parse_cadence
from app.schemas.cadence import RecurringQuotaCadence
from app.schemas.checkin import EnrollmentHistoryItem
from app.schemas.enrollment import EnrollmentCreate, EnrollmentRead, EnrollmentUpdate

router = APIRouter(prefix="/enrollments", tags=["enrollments"])


@router.get("/", response_model=list[EnrollmentRead])
async def list_my_enrollments(
    current_user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Enrollment).where(Enrollment.user_id == current_user_id)
    )
    return result.scalars().all()


@router.get("/{challenge_id}", response_model=EnrollmentRead)
async def get_my_enrollment(
    challenge_id: int,
    current_user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Enrollment).where(
            Enrollment.challenge_id == challenge_id,
            Enrollment.user_id == current_user_id,
        )
    )
    enrollment = result.scalar_one_or_none()
    if not enrollment:
        raise HTTPException(status_code=404, detail="Enrollment not found")
    return enrollment


@router.post("/{challenge_id}", response_model=EnrollmentRead, status_code=201)
async def enroll(
    challenge_id: int,
    payload: EnrollmentCreate | None = None,
    current_user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    challenge_result = await db.execute(
        select(Challenge.id).where(
            Challenge.id == challenge_id,
            challenge_visibility_filter(current_user_id),
        )
    )
    if challenge_result.scalar_one_or_none() is None:
        raise HTTPException(status_code=404, detail="Challenge not found")

    result = await db.execute(
        select(Enrollment).where(
            Enrollment.challenge_id == challenge_id,
            Enrollment.user_id == current_user_id,
        )
    )
    if result.scalar_one_or_none():
        raise HTTPException(status_code=409, detail="Already enrolled")

    tz = resolve_timezone(payload.timezone if payload else None)
    enrollment = Enrollment(
        challenge_id=challenge_id,
        user_id=current_user_id,
        timezone=tz,
        start_date=local_today(tz, datetime.now(UTC)),
    )
    db.add(enrollment)

    stats_result = await db.execute(
        select(ChallengeStats).where(ChallengeStats.challenge_id == challenge_id)
    )
    stats = stats_result.scalar_one_or_none()
    if stats is None:
        db.add(ChallengeStats(challenge_id=challenge_id, participant_count=1))
    else:
        stats.participant_count = (stats.participant_count or 0) + 1

    await db.commit()
    await db.refresh(enrollment)
    return enrollment


@router.patch("/{challenge_id}", response_model=EnrollmentRead)
async def update_my_enrollment(
    challenge_id: int,
    update: EnrollmentUpdate,
    current_user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Enrollment).where(
            Enrollment.challenge_id == challenge_id,
            Enrollment.user_id == current_user_id,
        )
    )
    enrollment = result.scalar_one_or_none()
    if not enrollment:
        raise HTTPException(status_code=404, detail="Enrollment not found")

    for field, value in update.model_dump(exclude_unset=True).items():
        setattr(enrollment, field, value)
    enrollment.updated_at = datetime.now(UTC)
    enrollment.last_modifier_user_id = current_user_id

    await db.commit()
    await db.refresh(enrollment)
    return enrollment


@router.delete("/{challenge_id}", status_code=204)
async def unenroll(
    challenge_id: int,
    current_user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Enrollment).where(
            Enrollment.challenge_id == challenge_id,
            Enrollment.user_id == current_user_id,
        )
    )
    enrollment = result.scalar_one_or_none()
    if not enrollment:
        raise HTTPException(status_code=404, detail="Enrollment not found")

    await db.delete(enrollment)

    stats_result = await db.execute(
        select(ChallengeStats).where(ChallengeStats.challenge_id == challenge_id)
    )
    stats = stats_result.scalar_one_or_none()
    if stats is not None and stats.participant_count:
        stats.participant_count = max(stats.participant_count - 1, 0)

    await db.commit()


async def build_enrollment_history(
    db: AsyncSession,
    enrollment: Enrollment,
    challenge: Challenge,
    from_: date,
    to: date,
) -> list[EnrollmentHistoryItem]:
    """Backs the JSON `/history` endpoint only.

    Not reused by the challenge-detail page -- views/challenge.py has its own
    build_history_timeline/build_quota_period_rows, which additionally derive
    per-occurrence `writable` flags and truncate to HISTORY_LIMIT. Keep both
    in sync by hand if the occurrence-state rules here change.
    """
    cadence = parse_cadence(challenge)
    now_utc = datetime.now(UTC)

    checkins_result = await db.execute(
        select(CheckIn).where(
            CheckIn.enrollment_id == enrollment.id,
            CheckIn.occurrence_local_date >= from_,
            CheckIn.occurrence_local_date <= to,
        )
    )
    by_key = {c.occurrence_key: c for c in checkins_result.scalars().all()}

    items: list[EnrollmentHistoryItem] = [
        EnrollmentHistoryItem(
            occurrence_key=row.occurrence_key,
            local_date=row.occurrence_local_date,
            state=row.state,
            amount=row.amount,
        )
        for row in by_key.values()
    ]

    # Quota periods have no single calendar cell of their own -- the actual
    # check-ins already appear above via `by_key`, each dated to when it was
    # recorded, so there is nothing meaningful to backfill here for them.
    if not isinstance(cadence, RecurringQuotaCadence):
        expected_keys = expected_keys_desc(
            cadence, start_date=enrollment.start_date, tz=enrollment.timezone, until=to
        )
        for key in expected_keys:
            if key in by_key:
                continue
            local_date = occurrence_local_date(cadence, key, enrollment.timezone, now_utc)
            if not (from_ <= local_date <= to):
                continue
            state = derive_state(
                cadence,
                start_date=enrollment.start_date,
                tz=enrollment.timezone,
                key=key,
                now_utc=now_utc,
                row_state=None,
            )
            items.append(
                EnrollmentHistoryItem(
                    occurrence_key=key, local_date=local_date, state=state, amount=None
                )
            )

    items.sort(key=lambda it: it.local_date)
    return items


@router.get("/{challenge_id}/history", response_model=list[EnrollmentHistoryItem])
async def get_enrollment_history(
    challenge_id: int,
    from_: date = Query(alias="from"),
    to: date = Query(),
    current_user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Enrollment).where(
            Enrollment.challenge_id == challenge_id,
            Enrollment.user_id == current_user_id,
        )
    )
    enrollment = result.scalar_one_or_none()
    if enrollment is None:
        raise HTTPException(status_code=404, detail="Enrollment not found")

    challenge_result = await db.execute(
        select(Challenge).where(Challenge.id == challenge_id)
    )
    challenge = challenge_result.scalar_one_or_none()
    if challenge is None:
        raise HTTPException(status_code=404, detail="Challenge not found")

    return await build_enrollment_history(db, enrollment, challenge, from_, to)
