# routers/views/challenge.py
import logging
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.auth import get_current_user_id, get_optional_user_id
from app.config import BASE_DIR
from app.database import get_db
from app.models.challenge import Challenge, ChallengeCategory
from app.models.enrollment import Enrollment
from app.models.stats import ChallengeStats
from app.occurrences import expected_keys_desc, is_key_writable, local_today, week_start
from app.routers.challenge import (
    DEFAULT_PAGE_SIZE,
    DEFAULT_TIMEZONE,
    MAX_PAGE_SIZE,
    challenge_visibility_filter,
    fetch_challenge_page,
    resolve_timezone,
)
from app.routers.checkin import parse_cadence
from app.routers.enrollment import build_enrollment_history
from app.schemas.challenge import ChallengeCreate

logger = logging.getLogger(__name__)

# How many past weeks the challenge-detail heatmap shows, in addition to the
# current week (Saturday-first, per D6).
HEATMAP_WEEKS_BACK = 7

router = APIRouter(prefix="/views/challenges", tags=["challenge-views"])
templates = Jinja2Templates(directory=BASE_DIR / "templates")


@router.get("/create")
async def create_challenge_form(
    request: Request, current_user_id: int | None = Depends(get_optional_user_id)
):
    if current_user_id is None:
        return RedirectResponse(
            url="/views/auth/?next=/views/challenges/create", status_code=303
        )
    return templates.TemplateResponse(
        "challenge/create-challenge.html",
        {
            "title": "ساخت چالش جدید",
            "request": request,
            "current_user_id": current_user_id,
        },
    )


@router.post("/create")
async def create_challenge(
    challenge: ChallengeCreate,
    request: Request,
    current_user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    try:
        data = challenge.model_dump(exclude={"cadence", "timezone"})
        db_challenge = Challenge(**data)
        db_challenge.cadence_kind = challenge.cadence.kind
        db_challenge.cadence = challenge.cadence.model_dump(mode="json")
        db_challenge.owner_id = current_user_id
        db_challenge.last_modifier_user_id = current_user_id
        db.add(db_challenge)
        await db.flush()

        # D1: the creator is auto-enrolled. Kept in sync with the JSON API's
        # create_challenge -- see CLAUDE.md on duplicated create logic.
        tz = resolve_timezone(challenge.timezone)
        enrollment = Enrollment(
            challenge_id=db_challenge.id,
            user_id=current_user_id,
            timezone=tz,
            start_date=local_today(tz, datetime.now(UTC)),
        )
        db.add(enrollment)
        db.add(ChallengeStats(challenge_id=db_challenge.id, participant_count=1))

        # Read the id before commit expires the instance -- the caller is a
        # fetch() in create-challenge.html, so it wants JSON, not a redirect
        # it would follow into a full page download it then discards.
        new_id = db_challenge.id
        await db.commit()
        return JSONResponse({"id": new_id}, status_code=201)
    except HTTPException:
        await db.rollback()
        raise
    except SQLAlchemyError:
        await db.rollback()
        logger.exception("Failed to create challenge for user_id=%s", current_user_id)
        return JSONResponse(
            {"detail": "ساخت چالش با خطا مواجه شد. لطفاً دوباره تلاش کن."},
            status_code=500,
        )


@router.get("/")
async def challenge_list(
    request: Request,
    current_user_id: int | None = Depends(get_optional_user_id),
    db: AsyncSession = Depends(get_db),
    category: str | None = None,
    q: str | None = Query(default=None, max_length=100),
):
    challenges, has_more = await fetch_challenge_page(
        db,
        current_user_id=current_user_id,
        category=category,
        q=q,
        offset=0,
        limit=DEFAULT_PAGE_SIZE,
        sort="velocity",
        options=(selectinload(Challenge.enrollments),),
    )
    return templates.TemplateResponse(
        "challenge/challenge-list.html",
        {
            "title": "لیست چالش ها",
            "request": request,
            "challenges": challenges,
            "categories": ChallengeCategory,
            "active_category": category or None,
            "query": q or "",
            "has_more": has_more,
            "page_size": DEFAULT_PAGE_SIZE,
            "current_user_id": current_user_id,
            "active_nav": "challenges",
        },
    )


@router.get("/fragment")
async def challenge_list_fragment(
    request: Request,
    current_user_id: int | None = Depends(get_optional_user_id),
    db: AsyncSession = Depends(get_db),
    category: str | None = None,
    q: str | None = Query(default=None, max_length=100),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE),
):
    """Returns just the next page of challenge cards, for infinite scroll."""
    challenges, has_more = await fetch_challenge_page(
        db,
        current_user_id=current_user_id,
        category=category,
        q=q,
        offset=offset,
        limit=limit,
        sort="velocity",
        options=(selectinload(Challenge.enrollments),),
    )
    response = templates.TemplateResponse(
        "challenge/_challenge_cards.html",
        {"request": request, "challenges": challenges},
    )
    response.headers["X-Has-More"] = "true" if has_more else "false"
    return response


@router.get("/{challenge_id}")
async def challenge_detail(
    challenge_id: int,
    request: Request,
    current_user_id: int | None = Depends(get_optional_user_id),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Challenge)
        .options(selectinload(Challenge.enrollments).selectinload(Enrollment.user))
        .where(
            Challenge.id == challenge_id,
            challenge_visibility_filter(current_user_id),
        )
    )
    db_challenge = result.scalar_one_or_none()
    if not db_challenge:
        raise HTTPException(status_code=404, detail="Challenge not found")

    my_enrollment = None
    if current_user_id is not None:
        my_enrollment = next(
            (e for e in db_challenge.enrollments if e.user_id == current_user_id), None
        )
    is_enrolled = my_enrollment is not None

    stats_result = await db.execute(
        select(ChallengeStats).where(ChallengeStats.challenge_id == challenge_id)
    )
    stats = stats_result.scalar_one_or_none()
    participant_count = (
        stats.participant_count if stats else len(db_challenge.enrollments)
    )
    total_completions = stats.total_completions if stats else 0
    total_amount = stats.total_amount if stats else 0

    now_utc = datetime.now(UTC)
    cadence = parse_cadence(db_challenge)

    # Collective progress (D9: due_date is unrelated to cadence, so it plays
    # no part here). With a goal, progress is amount-based; without one it's
    # completions against a rough expected-occurrences-per-participant count.
    created_local = (
        db_challenge.created_at.astimezone(ZoneInfo(DEFAULT_TIMEZONE)).date()
        if db_challenge.created_at
        else local_today(DEFAULT_TIMEZONE, now_utc)
    )
    occurrences_count = len(
        expected_keys_desc(
            cadence,
            start_date=created_local,
            tz=DEFAULT_TIMEZONE,
            until=local_today(DEFAULT_TIMEZONE, now_utc),
        )
    )
    if db_challenge.goal_amount:
        progress = float(total_amount) / float(db_challenge.goal_amount)
    else:
        denom = participant_count * occurrences_count
        progress = (total_completions / denom) if denom else 0.0
    progress_pct = max(0, min(100, round(progress * 100)))

    # Personal history heatmap (Saturday-first weeks, per D6) -- only the
    # logged-in user's own enrollment has history to show.
    heatmap_weeks = []
    if my_enrollment is not None:
        my_today = local_today(my_enrollment.timezone, now_utc)
        end_week_start = week_start(my_today)
        start_week_start = end_week_start - timedelta(weeks=HEATMAP_WEEKS_BACK)
        history = await build_enrollment_history(
            db, my_enrollment, db_challenge, start_week_start, my_today
        )
        by_date = {item.local_date: item for item in history}

        d = start_week_start
        while d <= end_week_start:
            week_cells = []
            for offset in range(7):
                day = d + timedelta(days=offset)
                item = by_date.get(day)
                if day > my_today:
                    cell_state = "future"
                elif item is not None:
                    cell_state = item.state
                else:
                    cell_state = "none"
                # Only offer the sheet for occurrences with nothing recorded
                # yet -- an already completed/skipped cell has no PATCH/DELETE
                # UI here, and re-POSTing it would just silently no-op (the
                # idempotent-conflict path in POST /checkins returns 200 for
                # the existing row without changing its state).
                writable = (
                    item is not None
                    and item.state in ("pending", "missed")
                    and is_key_writable(
                        cadence,
                        start_date=my_enrollment.start_date,
                        tz=my_enrollment.timezone,
                        key=item.occurrence_key,
                        now_utc=now_utc,
                    )
                )
                week_cells.append(
                    {
                        "date": day,
                        "state": cell_state,
                        "key": item.occurrence_key if item else None,
                        "writable": writable,
                    }
                )
            heatmap_weeks.append(week_cells)
            d += timedelta(weeks=1)

    return templates.TemplateResponse(
        "challenge/challenge-detail.html",
        {
            "title": f"چالش: {db_challenge.title}",
            "request": request,
            "challenge": db_challenge,
            "is_enrolled": is_enrolled,
            "participant_count": participant_count,
            "total_completions": total_completions,
            "total_amount": total_amount,
            "progress_pct": progress_pct,
            "my_streak": my_enrollment.current_streak if my_enrollment else None,
            "heatmap_weeks": heatmap_weeks,
            "current_user_id": current_user_id,
            "active_nav": "challenges",
        },
    )
