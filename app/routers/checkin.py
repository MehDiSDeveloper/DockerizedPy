from __future__ import annotations

import logging
from datetime import UTC, date, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import TypeAdapter
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_user_id
from app.database import get_db
from app.logging_config import log_event
from app.models.challenge import Challenge
from app.models.checkin import CheckIn
from app.models.enrollment import Enrollment
from app.models.stats import ChallengeStats
from app.occurrences import compute_streaks, is_key_writable, local_today
from app.roadmaps import advance_after_checkin
from app.schemas.cadence import (
    CadenceUnion,
    OnceCadence,
    RecurringDaysCadence,
    RecurringQuotaCadence,
    ScheduleCadence,
)
from app.schemas.checkin import CheckInCreate, CheckInRead, CheckInUpdate

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/checkins", tags=["checkins"])

_cadence_adapter = TypeAdapter(CadenceUnion)


def parse_cadence(challenge: Challenge) -> CadenceUnion:
    return _cadence_adapter.validate_python(challenge.cadence)


def occurrence_local_date(
    cadence: CadenceUnion, key: str, tz: str, now_utc: datetime
) -> date:
    """The calendar date an occurrence key refers to, derived from the key
    itself (never from "today") -- except for kinds with no per-day key,
    where the check-in is simply dated to the moment it was recorded."""
    if isinstance(cadence, OnceCadence):
        return local_today(tz, now_utc)
    if isinstance(cadence, ScheduleCadence):
        dt = datetime.fromisoformat(key[1:])
        return dt.astimezone(ZoneInfo(tz)).date()
    if isinstance(cadence, RecurringDaysCadence):
        return date.fromisoformat(key[1:])
    if isinstance(cadence, RecurringQuotaCadence):
        if "#" not in key:
            # a bare period key (from expected_keys_desc) -- anchor at the
            # period's start date rather than "today".
            if key.startswith("W"):
                return date.fromisoformat(key[1:])
            if key.startswith("M"):
                year_str, month_str = key[1:].split("-")
                return date(int(year_str), int(month_str), 1)
        return local_today(tz, now_utc)
    raise ValueError(f"unsupported cadence kind: {cadence.kind}")


async def _load_enrollment_and_challenge(
    db: AsyncSession, challenge_id: int, user_id: int
) -> tuple[Enrollment, Challenge]:
    result = await db.execute(
        select(Enrollment).where(
            Enrollment.challenge_id == challenge_id,
            Enrollment.user_id == user_id,
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
    return enrollment, challenge


async def _load_enrollment_for_checkin(
    db: AsyncSession, checkin: CheckIn, user_id: int
) -> Enrollment:
    """The caller's own enrollment behind a check-in id, or 404.

    404 and not 403: check-in ids are bare sequential integers and the row is
    only ever reachable through the caller's own enrollment, so a 403 here
    would tell anyone walking ``/checkins/1..n`` exactly which ids exist
    across every account -- and there is no page or link that could
    legitimately hand someone another member's check-in id. Matching the
    "check-in not found" answer above makes the two indistinguishable. (The
    403s that remain on these routes are backfill-window refusals, which are
    about *your own* row and do have to be told apart from a miss.)
    """
    result = await db.execute(
        select(Enrollment).where(Enrollment.id == checkin.enrollment_id)
    )
    enrollment = result.scalar_one_or_none()
    if enrollment is None or enrollment.user_id != user_id:
        raise HTTPException(status_code=404, detail="Check-in not found")
    return enrollment


async def _load_completed_state(
    db: AsyncSession, enrollment_id: int
) -> tuple[set[str], dict[str, int]]:
    result = await db.execute(
        select(CheckIn.occurrence_key).where(
            CheckIn.enrollment_id == enrollment_id, CheckIn.state == "completed"
        )
    )
    keys = [row[0] for row in result.all()]
    completed_keys = set(keys)
    period_completed_counts: dict[str, int] = {}
    for k in keys:
        if "#" in k:
            pkey = k.split("#", 1)[0]
            period_completed_counts[pkey] = period_completed_counts.get(pkey, 0) + 1
    return completed_keys, period_completed_counts


async def _period_keys(db: AsyncSession, enrollment_id: int, pkey: str) -> set[str]:
    """Every occurrence key already recorded in one recurring_quota period,
    whatever its state -- a skipped seat is taken just as a completed one is."""
    result = await db.execute(
        select(CheckIn.occurrence_key).where(
            CheckIn.enrollment_id == enrollment_id,
            CheckIn.occurrence_key.startswith(f"{pkey}#"),
        )
    )
    return {row[0] for row in result.all()}


async def _select_checkin(
    db: AsyncSession, enrollment_id: int, key: str
) -> CheckIn | None:
    result = await db.execute(
        select(CheckIn).where(
            CheckIn.enrollment_id == enrollment_id, CheckIn.occurrence_key == key
        )
    )
    return result.scalar_one_or_none()


async def _get_or_create_stats(db: AsyncSession, challenge_id: int) -> ChallengeStats:
    result = await db.execute(
        select(ChallengeStats).where(ChallengeStats.challenge_id == challenge_id)
    )
    stats = result.scalar_one_or_none()
    if stats is None:
        stats = ChallengeStats(challenge_id=challenge_id)
        db.add(stats)
    return stats


async def _insert_checkin(
    db: AsyncSession,
    *,
    enrollment: Enrollment,
    challenge: Challenge,
    cadence: CadenceUnion,
    key: str,
    payload: CheckInCreate,
    now_utc: datetime,
    current_user_id: int,
) -> CheckIn | None:
    checkin = CheckIn(
        enrollment_id=enrollment.id,
        challenge_id=challenge.id,
        occurrence_key=key,
        state=payload.state,
        occurrence_local_date=occurrence_local_date(
            cadence, key, enrollment.timezone, now_utc
        ),
        occurred_at_utc=now_utc,
        timezone=enrollment.timezone,
        amount=payload.amount,
        unit=challenge.goal_unit,
        note=payload.note,
        photo_url=payload.photo_url,
        last_modifier_user_id=current_user_id,
    )
    db.add(checkin)
    try:
        await db.flush()
    except IntegrityError:
        await db.rollback()
        # rollback expires every object in the session; refresh explicitly
        # (awaited) rather than letting a later plain attribute access
        # trigger an implicit lazy-load outside of greenlet context.
        await db.refresh(enrollment)
        await db.refresh(challenge)
        return None
    return checkin


async def _apply_streaks_and_stats(
    db: AsyncSession,
    *,
    enrollment: Enrollment,
    challenge: Challenge,
    cadence: CadenceUnion,
    now_utc: datetime,
) -> None:
    completed_keys, period_counts = await _load_completed_state(db, enrollment.id)
    current, longest = compute_streaks(
        cadence,
        start_date=enrollment.start_date,
        tz=enrollment.timezone,
        now_utc=now_utc,
        completed_keys=completed_keys,
        period_completed_counts=period_counts,
    )
    enrollment.current_streak = current
    enrollment.longest_streak = longest

    last_date_result = await db.execute(
        select(func.max(CheckIn.occurrence_local_date)).where(
            CheckIn.enrollment_id == enrollment.id, CheckIn.state == "completed"
        )
    )
    enrollment.last_checkin_local_date = last_date_result.scalar_one_or_none()


@router.post("/", response_model=CheckInRead, status_code=201)
async def create_checkin(
    payload: CheckInCreate,
    response: Response,
    current_user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    enrollment, challenge = await _load_enrollment_and_challenge(
        db, payload.challenge_id, current_user_id
    )
    cadence = parse_cadence(challenge)
    now_utc = datetime.now(UTC)

    if not is_key_writable(
        cadence,
        start_date=enrollment.start_date,
        tz=enrollment.timezone,
        key=payload.occurrence_key,
        now_utc=now_utc,
    ):
        raise HTTPException(status_code=403, detail="Occurrence is not writable")

    if (
        challenge.goal_unit is not None
        and payload.state == "completed"
        and payload.amount is None
    ):
        raise HTTPException(
            status_code=422, detail="amount is required for this challenge"
        )

    key = payload.occurrence_key
    checkin = await _insert_checkin(
        db,
        enrollment=enrollment,
        challenge=challenge,
        cadence=cadence,
        key=key,
        payload=payload,
        now_utc=now_utc,
        current_user_id=current_user_id,
    )

    if checkin is None and isinstance(cadence, RecurringQuotaCadence):
        # The seat the client asked for is taken. Retry on the first *free*
        # one in the period rather than on `completed + 1`, which is the same
        # seat again whenever the taken row was a skip.
        taken = await _period_keys(db, enrollment.id, key.split("#", 1)[0])
        pkey = key.split("#", 1)[0]
        retry_key = next(
            (
                f"{pkey}#{s}"
                for s in range(1, cadence.count + 1)
                if f"{pkey}#{s}" not in taken
            ),
            key,
        )
        if retry_key != key and is_key_writable(
            cadence,
            start_date=enrollment.start_date,
            tz=enrollment.timezone,
            key=retry_key,
            now_utc=now_utc,
        ):
            checkin = await _insert_checkin(
                db,
                enrollment=enrollment,
                challenge=challenge,
                cadence=cadence,
                key=retry_key,
                payload=payload,
                now_utc=now_utc,
                current_user_id=current_user_id,
            )
            if checkin is not None:
                key = retry_key

    if checkin is None:
        existing = await _select_checkin(db, enrollment.id, key)
        if existing is None:
            raise HTTPException(status_code=409, detail="Conflict recording check-in")
        response.status_code = 200
        return existing

    await _apply_streaks_and_stats(
        db, enrollment=enrollment, challenge=challenge, cadence=cadence, now_utc=now_utc
    )

    stats = await _get_or_create_stats(db, challenge.id)
    if checkin.state == "completed":
        stats.total_completions = (stats.total_completions or 0) + 1
        if checkin.amount is not None:
            stats.total_amount = (stats.total_amount or 0) + checkin.amount
    stats.last_checkin_at = now_utc
    stats.updated_at = now_utc

    # A check-in is the event that can open the next step of a roadmap -- see
    # `advance_after_checkin`. One indexed query for a member with no
    # roadmaps, and it lands in this transaction like everything else here.
    await advance_after_checkin(
        db,
        user_id=current_user_id,
        challenge_id=challenge.id,
        timezone=enrollment.timezone,
    )

    await db.commit()
    await db.refresh(checkin)
    log_event(
        logger,
        "checkin.created",
        checkin_id=checkin.id,
        challenge_id=challenge.id,
        enrollment_id=enrollment.id,
        occurrence_key=key,
        state=checkin.state,
    )
    return checkin


@router.patch("/{checkin_id}", response_model=CheckInRead)
async def update_checkin(
    checkin_id: int,
    update: CheckInUpdate,
    current_user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(CheckIn).where(CheckIn.id == checkin_id))
    checkin = result.scalar_one_or_none()
    if checkin is None:
        raise HTTPException(status_code=404, detail="Check-in not found")

    enrollment = await _load_enrollment_for_checkin(db, checkin, current_user_id)

    challenge_result = await db.execute(
        select(Challenge).where(Challenge.id == checkin.challenge_id)
    )
    challenge = challenge_result.scalar_one_or_none()
    if challenge is None:
        raise HTTPException(status_code=404, detail="Challenge not found")
    cadence = parse_cadence(challenge)
    now_utc = datetime.now(UTC)

    if not is_key_writable(
        cadence,
        start_date=enrollment.start_date,
        tz=enrollment.timezone,
        key=checkin.occurrence_key,
        now_utc=now_utc,
    ):
        raise HTTPException(status_code=403, detail="Backfill window has closed")

    old_state = checkin.state
    old_amount = checkin.amount

    for field, value in update.model_dump(exclude_unset=True).items():
        setattr(checkin, field, value)

    if (
        challenge.goal_unit is not None
        and checkin.state == "completed"
        and checkin.amount is None
    ):
        raise HTTPException(
            status_code=422, detail="amount is required for this challenge"
        )

    checkin.updated_at = now_utc
    checkin.last_modifier_user_id = current_user_id
    await db.flush()

    await _apply_streaks_and_stats(
        db, enrollment=enrollment, challenge=challenge, cadence=cadence, now_utc=now_utc
    )

    delta_completions = (1 if checkin.state == "completed" else 0) - (
        1 if old_state == "completed" else 0
    )
    new_amount = (checkin.amount if checkin.state == "completed" else None) or Decimal(0)
    prev_amount = (old_amount if old_state == "completed" else None) or Decimal(0)
    delta_amount = new_amount - prev_amount
    stats = await _get_or_create_stats(db, challenge.id)
    stats.total_completions = (stats.total_completions or 0) + delta_completions
    if delta_amount:
        stats.total_amount = (stats.total_amount or 0) + delta_amount
    stats.updated_at = now_utc

    # A check-in is the event that can open the next step of a roadmap -- see
    # `advance_after_checkin`. One indexed query for a member with no
    # roadmaps, and it lands in this transaction like everything else here.
    await advance_after_checkin(
        db,
        user_id=current_user_id,
        challenge_id=challenge.id,
        timezone=enrollment.timezone,
    )

    await db.commit()
    log_event(logger, "checkin.deleted", checkin_id=checkin_id, challenge_id=challenge.id)
    await db.refresh(checkin)
    return checkin


@router.delete("/{checkin_id}", status_code=204)
async def delete_checkin(
    checkin_id: int,
    current_user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(CheckIn).where(CheckIn.id == checkin_id))
    checkin = result.scalar_one_or_none()
    if checkin is None:
        raise HTTPException(status_code=404, detail="Check-in not found")

    enrollment = await _load_enrollment_for_checkin(db, checkin, current_user_id)

    challenge_result = await db.execute(
        select(Challenge).where(Challenge.id == checkin.challenge_id)
    )
    challenge = challenge_result.scalar_one_or_none()
    if challenge is None:
        raise HTTPException(status_code=404, detail="Challenge not found")
    cadence = parse_cadence(challenge)
    now_utc = datetime.now(UTC)

    if not is_key_writable(
        cadence,
        start_date=enrollment.start_date,
        tz=enrollment.timezone,
        key=checkin.occurrence_key,
        now_utc=now_utc,
    ):
        raise HTTPException(status_code=403, detail="Backfill window has closed")

    delta_completions = -1 if checkin.state == "completed" else 0
    delta_amount = -(checkin.amount or 0) if checkin.state == "completed" else 0

    await db.delete(checkin)
    await db.flush()

    await _apply_streaks_and_stats(
        db, enrollment=enrollment, challenge=challenge, cadence=cadence, now_utc=now_utc
    )

    stats = await _get_or_create_stats(db, challenge.id)
    stats.total_completions = (stats.total_completions or 0) + delta_completions
    if delta_amount:
        stats.total_amount = (stats.total_amount or 0) + delta_amount
    stats.updated_at = now_utc

    # A check-in is the event that can open the next step of a roadmap -- see
    # `advance_after_checkin`. One indexed query for a member with no
    # roadmaps, and it lands in this transaction like everything else here.
    await advance_after_checkin(
        db,
        user_id=current_user_id,
        challenge_id=challenge.id,
        timezone=enrollment.timezone,
    )

    await db.commit()
