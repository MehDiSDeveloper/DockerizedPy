"""The verification loop's JSON door: a queue, and one ruling per report.

Two routes. ``GET /verifications/`` is what is waiting on this referee;
``POST /verifications/{checkin_id}`` is the ruling. There is deliberately no
``PATCH`` and no way to change a verdict once written -- a second ruling is
not a correction, it is two referees disagreeing after the fact, and the
thing that unblocks a genuine mistake is the doer amending their report,
which puts the question back in the queue by itself
(``routers/checkin.py``).

**One ruling has to move four things at once**, and they have to move in one
transaction or the app is briefly wrong about somebody's record: the
verdict, the streaks (recomputed, never adjusted), the challenge's monotonic
counters, and the roadmap engine, because an approval is exactly the moment a
step's ``count(n)`` rule can become true. That is why the route does the work
rather than the service module -- it is the same four things
``routers/checkin.py`` does, in the same order, for the same reasons.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.actlog import record
from app.auth import get_current_user_id
from app.database import get_db
from app.logging_config import log_event
from app.models.act import ActEventKind
from app.models.notification import NotificationKind
from app.notifications import notify
from app.roadmaps import advance_after_checkin
from app.routers.checkin import (
    _apply_streaks_and_stats,
    _get_or_create_stats,
    parse_cadence,
)
from app.schemas.act import VerdictWrite
from app.schemas.checkin import CheckInRead
from app.verification import (
    VERDICT_APPROVED,
    NotReviewable,
    apply_verdict,
    count_review_queue,
    fetch_review_queue,
    load_reviewable,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/verifications", tags=["verifications"])

DEFAULT_QUEUE_PAGE_SIZE = 20
MAX_QUEUE_PAGE_SIZE = 50


@router.get("/", response_model=list[CheckInRead])
async def my_queue(
    offset: int = Query(default=0, ge=0),
    limit: int = Query(
        default=DEFAULT_QUEUE_PAGE_SIZE, ge=1, le=MAX_QUEUE_PAGE_SIZE
    ),
    current_user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    """Reports waiting on me, oldest first.

    There is no client-supplied referee anywhere: the queue is keyed on the
    session user, exactly as every statement in ``routers/notification.py``
    is keyed on the session recipient.
    """
    rows, _has_more = await fetch_review_queue(
        db, referee_user_id=current_user_id, offset=offset, limit=limit
    )
    return rows


@router.get("/count")
async def my_queue_count(
    current_user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    """How many are waiting. One indexed count, for the badge.

    Its own route for the reason ``GET /notifications/unread-count`` is one:
    answering it server-side on every page render would mean every view
    router paying for a COUNT, and a route that forgot would show a stale
    number.
    """
    return {"count": await count_review_queue(db, referee_user_id=current_user_id)}


@router.post("/{checkin_id}", response_model=CheckInRead)
async def rule_on_report(
    checkin_id: int,
    payload: VerdictWrite,
    current_user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    """Approve or reject one report.

    One route for both answers rather than ``/approve`` and ``/reject``,
    because they are one act with two values and everything around them --
    the gate, the four things that move, the log line, the notification -- is
    identical. It is the same call ``PUT /reactions/.../likes`` makes about
    taking a *state* rather than being a toggle.
    """
    try:
        checkin, enrollment, challenge = await load_reviewable(
            db, checkin_id=checkin_id, referee_user_id=current_user_id
        )
    except NotReviewable as exc:
        raise HTTPException(status_code=exc.status, detail=exc.detail) from exc

    now_utc = datetime.now(UTC)
    approved = payload.verdict == VERDICT_APPROVED
    apply_verdict(
        checkin,
        verdict=payload.verdict,
        by_user_id=current_user_id,
        note=(payload.note or None),
        now_utc=now_utc,
    )
    await db.flush()

    # Streaks are recomputed from scratch, never adjusted -- so an approval
    # restores the streak the pending report was holding open, and a
    # rejection leaves it broken, with no arithmetic anywhere.
    cadence = parse_cadence(challenge)
    await _apply_streaks_and_stats(
        db,
        enrollment=enrollment,
        challenge=challenge,
        cadence=cadence,
        now_utc=now_utc,
    )

    # The challenge's counters are monotonic, and a pending report never
    # moved them -- so an approval is the only thing that does, and a
    # rejection is a no-op rather than a decrement.
    if approved:
        stats = await _get_or_create_stats(db, challenge.id)
        stats.total_completions = (stats.total_completions or 0) + 1
        if checkin.amount is not None:
            stats.total_amount = (stats.total_amount or 0) + checkin.amount
        stats.last_checkin_at = now_utc
        stats.updated_at = now_utc

    # An approval is exactly when a roadmap step's `count(n)` can become
    # true, so the engine runs here for the same reason it runs after a
    # check-in -- and for the *doer*, never for the referee.
    await advance_after_checkin(
        db,
        user_id=enrollment.user_id,
        challenge_id=challenge.id,
        timezone=enrollment.timezone,
    )

    record(
        db,
        challenge_id=challenge.id,
        kind=(
            ActEventKind.CHECKIN_APPROVED
            if approved
            else ActEventKind.CHECKIN_REJECTED
        ),
        actor_user_id=current_user_id,
        subject_user_id=enrollment.user_id,
        checkin_id=checkin.id,
        occurrence_key=checkin.occurrence_key,
        note=payload.note or None,
    )
    await notify(
        db,
        user_id=enrollment.user_id,
        kind=(
            NotificationKind.CHECKIN_APPROVED
            if approved
            else NotificationKind.CHECKIN_REJECTED
        ),
        actor_user_id=current_user_id,
        challenge_id=challenge.id,
    )

    await db.commit()
    await db.refresh(checkin)
    log_event(
        logger,
        "checkin.ruled",
        level=logging.INFO if approved else logging.WARNING,
        checkin_id=checkin.id,
        challenge_id=challenge.id,
        verdict=payload.verdict,
        referee_user_id=current_user_id,
    )
    return checkin
