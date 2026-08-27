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
from app.models.checkin import CheckIn
from app.models.enrollment import Enrollment
from app.models.stats import ChallengeStats
from app.occurrences import (
    derive_state,
    expected_keys_desc,
    is_key_writable,
    local_today,
    occurrences_due,
    period_bounds,
    period_key,
    week_start,
)
from app.routers.challenge import (
    DEFAULT_PAGE_SIZE,
    DEFAULT_TIMEZONE,
    MAX_PAGE_SIZE,
    challenge_visibility_filter,
    fetch_challenge_page,
    resolve_timezone,
)
from app.routers.checkin import occurrence_local_date, parse_cadence
from app.schemas.cadence import CadenceUnion, RecurringQuotaCadence
from app.schemas.challenge import ChallengeCreate

logger = logging.getLogger(__name__)

# How many occurrences the challenge-detail history timeline shows before
# collapsing the rest behind a "show older" note.
HISTORY_LIMIT = 12

# How many past periods (weeks/months) the recurring_quota history strip
# shows, in addition to the current period.
QUOTA_PERIODS_BACK = 5

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


async def build_history_timeline(
    db: AsyncSession,
    enrollment: Enrollment,
    cadence: CadenceUnion,
    now_utc: datetime,
) -> tuple[list[dict], dict, bool]:
    """Occurrence-shaped history: one entry per real occurrence, newest
    first -- deliberately NOT one per calendar day. A "every Tuesday"
    challenge has ~1 occurrence a week, so a day grid renders 6/7 empty
    cells per row and reads as broken; walking expected_keys_desc gives
    only the days the cadence actually asks for."""
    today = local_today(enrollment.timezone, now_utc)
    keys = expected_keys_desc(
        cadence, start_date=enrollment.start_date, tz=enrollment.timezone, until=today
    )

    result = await db.execute(
        select(CheckIn).where(CheckIn.enrollment_id == enrollment.id)
    )
    by_key = {c.occurrence_key: c for c in result.scalars().all()}

    def date_for(key: str):
        row = by_key.get(key)
        if row is not None:
            return row.occurrence_local_date
        return occurrence_local_date(cadence, key, enrollment.timezone, now_utc)

    completed = sum(
        1 for k in keys if k in by_key and by_key[k].state == "completed"
    )
    this_week_start = week_start(today)
    week_keys = [k for k in keys if date_for(k) >= this_week_start]
    week_completed = sum(
        1 for k in week_keys if k in by_key and by_key[k].state == "completed"
    )

    items = []
    for key in keys[:HISTORY_LIMIT]:
        row = by_key.get(key)
        state = derive_state(
            cadence,
            start_date=enrollment.start_date,
            tz=enrollment.timezone,
            key=key,
            now_utc=now_utc,
            row_state=row.state if row else None,
        )
        local_date = date_for(key)
        items.append(
            {
                "key": key,
                "date": local_date,
                "is_today": local_date == today,
                "state": state,
                "amount": row.amount if row else None,
                "note": row.note if row else None,
                # Only offer the sheet for occurrences with nothing recorded
                # yet -- an already completed/skipped row has no PATCH/DELETE
                # UI here, and re-POSTing it would silently no-op (the
                # idempotent-conflict path in POST /checkins returns 200 for
                # the existing row without changing its state).
                "writable": state in ("pending", "missed")
                and is_key_writable(
                    cadence,
                    start_date=enrollment.start_date,
                    tz=enrollment.timezone,
                    key=key,
                    now_utc=now_utc,
                ),
            }
        )

    summary = {
        "completed": completed,
        "total": len(keys),
        "unit": "نوبت",
        "pct": round(completed / len(keys) * 100) if keys else 0,
        "period_done": week_completed,
        "period_total": len(week_keys),
        "period_label": "این هفته",
    }
    return items, summary, len(keys) > HISTORY_LIMIT


async def build_quota_period_rows(
    db: AsyncSession,
    enrollment: Enrollment,
    cadence: RecurringQuotaCadence,
    now_utc: datetime,
) -> tuple[list[dict], dict]:
    """One row per quota period (week/month), most recent first. Quota
    occurrences have no per-day calendar slot of their own (CLAUDE.md: D6/D9
    cadence notes), so a day grid doesn't fit them -- a period is the
    natural unit here instead."""
    today = local_today(enrollment.timezone, now_utc)
    current_pkey = period_key(today, cadence.period)

    checkins_result = await db.execute(
        select(CheckIn.occurrence_key).where(
            CheckIn.enrollment_id == enrollment.id,
            CheckIn.state == "completed",
        )
    )
    done_by_period: dict[str, int] = {}
    for (key,) in checkins_result.all():
        pkey = key.split("#", 1)[0]
        done_by_period[pkey] = done_by_period.get(pkey, 0) + 1

    due = occurrences_due(
        cadence,
        start_date=enrollment.start_date,
        tz=enrollment.timezone,
        now_utc=now_utc,
        existing_keys=set(),
        period_completed_counts=done_by_period,
    )
    next_writable_key = due[0].key if due else None

    rows = []
    d = today
    for _ in range(QUOTA_PERIODS_BACK + 1):
        pkey = period_key(d, cadence.period)
        period_start, _ = period_bounds(d, cadence.period)
        done = min(done_by_period.get(pkey, 0), cadence.count)
        rows.append(
            {
                "pkey": pkey,
                "start": period_start,
                "is_current": pkey == current_pkey,
                "done": done,
                "target": cadence.count,
                "writable_key": next_writable_key if pkey == current_pkey else None,
            }
        )
        d = period_start - timedelta(days=1)
        if d < enrollment.start_date:
            break

    # A quota period either meets its target or doesn't -- so the headline
    # metric counts *periods met*, not individual check-ins (which is what
    # the timeline's "نوبت" counts). Labelled accordingly.
    met = sum(1 for r in rows if r["done"] >= r["target"])
    current = next((r for r in rows if r["is_current"]), None)
    is_week = cadence.period == "week"
    summary = {
        "completed": met,
        "total": len(rows),
        "unit": "هفته" if is_week else "ماه",
        "pct": round(met / len(rows) * 100) if rows else 0,
        "period_done": current["done"] if current else 0,
        "period_total": cadence.count,
        "period_label": "این هفته" if is_week else "این ماه",
    }
    return rows, summary


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

    # Personal history -- only the logged-in user's own enrollment has any.
    # recurring_quota is grouped into period rows (a quota has no per-day
    # slot of its own); every other cadence gets the occurrence timeline.
    timeline = []
    quota_rows = []
    history_summary = None
    has_more_history = False
    if my_enrollment is not None:
        if isinstance(cadence, RecurringQuotaCadence):
            quota_rows, history_summary = await build_quota_period_rows(
                db, my_enrollment, cadence, now_utc
            )
        else:
            timeline, history_summary, has_more_history = await build_history_timeline(
                db, my_enrollment, cadence, now_utc
            )

    return templates.TemplateResponse(
        "challenge/challenge-detail.html",
        {
            "title": f"چالش: {db_challenge.title}",
            "request": request,
            "challenge": db_challenge,
            "cadence_kind": db_challenge.cadence_kind,
            "is_enrolled": is_enrolled,
            "participant_count": participant_count,
            "total_completions": total_completions,
            "total_amount": total_amount,
            "progress_pct": progress_pct,
            "my_streak": my_enrollment.current_streak if my_enrollment else None,
            "my_longest_streak": my_enrollment.longest_streak if my_enrollment else None,
            "timeline": timeline,
            "has_more_history": has_more_history,
            "quota_rows": quota_rows,
            "history_summary": history_summary,
            "goal_unit": db_challenge.goal_unit,
            "current_user_id": current_user_id,
            "active_nav": "challenges",
        },
    )
