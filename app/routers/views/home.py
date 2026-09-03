# routers/views/home.py
from datetime import UTC, date, datetime, timedelta
from typing import Literal

from fastapi import APIRouter, Depends, Query, Request
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, not_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.auth import get_current_user_id, get_page_user
from app.config import BASE_DIR
from app.database import get_db
from app.explainers import register_explainer_filters
from app.icons import register_icon_filters
from app.jalali import to_jalali
from app.models.audit_base import newest_first
from app.models.challenge import Challenge
from app.models.checkin import CheckIn
from app.models.enrollment import Enrollment, EnrollmentStatus
from app.models.user import User
from app.occurrences import local_today, week_start
from app.roadmaps import register_roadmap_filters
from app.routers.challenge import DEFAULT_TIMEZONE, STATUS_FINISHED, status_filter
from app.routers.today import get_today_items
from app.routers.views.roadmap import home_roadmap_card

router = APIRouter(prefix="/views/home", tags=["home-views"])
templates = Jinja2Templates(directory=BASE_DIR / "templates")
register_explainer_filters(templates.env)
register_icon_filters(templates.env)
# The «قدم فعلی تو» card wears the roadmap's own Farsi labels, so this
# environment owes the same registration the roadmap screens make.
register_roadmap_filters(templates.env)

DEFAULT_PAGE_SIZE = 20
MAX_PAGE_SIZE = 100

# --- dashboard shape -------------------------------------------------------
# How far back the activity grid reaches. Twelve Saturday-aligned weeks is
# about a season of habit -- long enough for a pattern to show, short enough
# that a gap in it still reads as recent -- and thirteen columns (the twelve
# weeks plus the weekday-label column) is the most that stays square inside
# the 480px shell.
DASHBOARD_WEEKS = 12

# Window the "where is my effort going" split reads over. A month is the
# shortest span that survives one bad week without re-ranking the whole list.
FOCUS_WINDOW_DAYS = 30

# How many categories that split names. Six exist; past the fourth the rows
# are single check-ins and the ranking stops carrying information.
FOCUS_LIMIT = 4

# Completions in one day that fill the grid's darkest cell. Someone running
# three or four challenges reaches it on a good day, so the top of the scale
# is somewhere a real day lands rather than decoration.
HEATMAP_MAX_LEVEL = 4

# 0 = Saturday .. 6 = Friday (to_ir_weekday). One letter each, because a row
# label gets exactly one cell-width; the full names live in WEEKDAY_NAMES in
# routers/views/challenge.py.
WEEKDAY_INITIALS = ("ش", "ی", "د", "س", "چ", "پ", "ج")


async def _fetch_enrollment_page(
    db: AsyncSession,
    *,
    user_id: int,
    status: str,
    offset: int,
    limit: int,
) -> tuple[list[Enrollment], bool]:
    stmt = (
        select(Enrollment)
        .join(Challenge, Enrollment.challenge_id == Challenge.id)
        .options(selectinload(Enrollment.challenge).selectinload(Challenge.enrollments))
        .where(Enrollment.user_id == user_id)
    )
    # "Finished" is the challenge's own derived status (D-status), never
    # `Enrollments.status` -- no route ever writes anything but `active` onto
    # that column, so a branch keyed on it was always empty.
    finished = status_filter(STATUS_FINISHED)
    if status == "completed":
        stmt = stmt.where(finished)
    else:
        stmt = stmt.where(not_(finished))
    stmt = stmt.order_by(*newest_first(Enrollment)).offset(offset).limit(limit + 1)
    result = await db.execute(stmt)
    enrollments = list(result.scalars().all())
    has_more = len(enrollments) > limit
    return enrollments[:limit], has_more


def _build_activity_grid(
    by_date: dict[date, int], *, grid_start: date, today: date
) -> dict:
    """The 12-week activity grid, emitted column-major so CSS can lay it out
    with `grid-auto-flow:column` over seven rows.

    Columns are Saturday-aligned weeks (week_start), not rolling 7-day
    chunks: every *row* is then one weekday, which is the whole point -- it
    is what makes "I never manage Thursdays" visible at a glance. In RTL the
    first column renders rightmost, so the grid reads oldest-right /
    newest-left, the same direction as the challenge-detail occurrence strip.
    """
    columns = []
    previous_month = None
    for week_index in range(DASHBOARD_WEEKS):
        col_start = grid_start + timedelta(weeks=week_index)
        days = []
        for offset in range(7):
            day = col_start + timedelta(days=offset)
            count = by_date.get(day, 0)
            days.append(
                {
                    "date": day,
                    "count": count,
                    # Capped, not scaled to the member's own busiest day: a
                    # scale that re-normalises would repaint an unchanged past
                    # every time one heavy day is added.
                    "level": min(count, HEATMAP_MAX_LEVEL),
                    "is_future": day > today,
                    "is_today": day == today,
                }
            )
        # Months are Jalali, so the label changes where the *member's* calendar
        # changes month, not where the Gregorian one does. Only the decision is
        # made here -- the label itself is an ISO date that renderDates()
        # formats, same as every other date in the UI.
        month = to_jalali(col_start)[1]
        columns.append(
            {
                "start": col_start,
                "days": days,
                "starts_month": month != previous_month,
            }
        )
        previous_month = month

    window_days = (today - grid_start).days + 1
    active_days = sum(
        1 for day, count in by_date.items() if count and grid_start <= day <= today
    )
    return {
        "columns": columns,
        "weekdays": WEEKDAY_INITIALS,
        "active_days": active_days,
        "window_days": window_days,
        "weeks": DASHBOARD_WEEKS,
        # +1 for the weekday-label column, which lives inside the same grid so
        # the labels line up with their rows without a second track to keep in
        # sync.
        "cols": DASHBOARD_WEEKS + 1,
        "max_level": HEATMAP_MAX_LEVEL,
    }


async def build_dashboard(
    db: AsyncSession, *, user_id: int, now_utc: datetime
) -> dict:
    """Everything the home page says about this member's own run.

    Read live off `CheckIns` rather than off any stored counter, for the same
    reason build_my_stats is (routers/views/challenge.py): the only personal
    numbers kept on a row are the two streaks -- recomputed, never
    incremented -- and `legacy_completed_count` is frozen pre-migration data
    that must never surface as a live figure.
    """
    # The dashboard's calendar frame. Each check-in was already bucketed into
    # its own enrollment's local date when it was written, so the grid groups
    # on that stored column and never re-derives a day; only "which day is
    # today" needs a zone, and that is a member-level question, so it takes
    # the app default exactly as today/index.html's date line does.
    today = local_today(DEFAULT_TIMEZONE, now_utc)
    grid_start = week_start(today) - timedelta(weeks=DASHBOARD_WEEKS - 1)

    daily = await db.execute(
        select(CheckIn.occurrence_local_date, func.count(CheckIn.id))
        .select_from(CheckIn)
        .join(Enrollment, CheckIn.enrollment_id == Enrollment.id)
        .where(
            Enrollment.user_id == user_id,
            CheckIn.state == "completed",
            CheckIn.occurrence_local_date >= grid_start,
        )
        .group_by(CheckIn.occurrence_local_date)
    )
    by_date: dict[date, int] = dict(daily.all())

    total_completed = (
        await db.execute(
            select(func.count(CheckIn.id))
            .select_from(CheckIn)
            .join(Enrollment, CheckIn.enrollment_id == Enrollment.id)
            .where(Enrollment.user_id == user_id, CheckIn.state == "completed")
        )
    ).scalar_one()

    focus_result = await db.execute(
        select(Challenge.category, func.count(CheckIn.id))
        .select_from(CheckIn)
        .join(Enrollment, CheckIn.enrollment_id == Enrollment.id)
        .join(Challenge, CheckIn.challenge_id == Challenge.id)
        .where(
            Enrollment.user_id == user_id,
            CheckIn.state == "completed",
            CheckIn.occurrence_local_date > today - timedelta(days=FOCUS_WINDOW_DAYS),
        )
        .group_by(Challenge.category)
        .order_by(func.count(CheckIn.id).desc())
        .limit(FOCUS_LIMIT)
    )
    focus_rows = [
        {"category": category, "count": count} for category, count in focus_result.all()
    ]
    # The bars are a ranking, so they scale to the leader rather than to the
    # window's total: split across six categories, even the thing the member
    # spent most of the month on is a stub on a share-of-total bar.
    focus_top = max((row["count"] for row in focus_rows), default=0)
    for row in focus_rows:
        row["pct"] = round(row["count"] / focus_top * 100) if focus_top else 0

    # A current streak only means something while its enrollment is running;
    # a record is a record, so that one is read across every enrollment the
    # member has ever had, finished ones included.
    streak_current = (
        await db.execute(
            select(func.max(Enrollment.current_streak)).where(
                Enrollment.user_id == user_id,
                Enrollment.status == EnrollmentStatus.ACTIVE,
            )
        )
    ).scalar()
    streak_best = (
        await db.execute(
            select(func.max(Enrollment.longest_streak)).where(
                Enrollment.user_id == user_id
            )
        )
    ).scalar()

    # What is still open today, from the same source the امروز tab renders, so
    # the two screens can never disagree about how much is left.
    due_today = len(await get_today_items(db, user_id=user_id, now_utc=now_utc))
    done_today = by_date.get(today, 0)
    total_today = done_today + due_today

    this_week_start = week_start(today)
    week_done = sum(
        count for day, count in by_date.items() if this_week_start <= day <= today
    )

    return {
        "today": {
            "done": done_today,
            "due": due_today,
            "total": total_today,
            "pct": round(done_today / total_today * 100) if total_today else 0,
        },
        "streak_current": streak_current or 0,
        "streak_best": streak_best or 0,
        "week_done": week_done,
        "total_completed": total_completed,
        "grid": _build_activity_grid(by_date, grid_start=grid_start, today=today),
        "focus": {"rows": focus_rows, "window_days": FOCUS_WINDOW_DAYS},
        "local_today": today,
    }


@router.get("/")
async def home(
    request: Request,
    db_user: User = Depends(get_page_user),
    db: AsyncSession = Depends(get_db),
):
    current_user_id = db_user.id

    # "Done" is the challenge's own derived status (D-status), never
    # `Enrollments.status` -- see `_fetch_enrollment_page` above.
    finished = status_filter(STATUS_FINISHED)
    stat_active = (
        await db.execute(
            select(func.count())
            .select_from(Enrollment)
            .join(Challenge, Enrollment.challenge_id == Challenge.id)
            .where(Enrollment.user_id == current_user_id, not_(finished))
        )
    ).scalar_one()
    stat_done = (
        await db.execute(
            select(func.count())
            .select_from(Enrollment)
            .join(Challenge, Enrollment.challenge_id == Challenge.id)
            .where(Enrollment.user_id == current_user_id, finished)
        )
    ).scalar_one()

    dashboard = await build_dashboard(
        db, user_id=current_user_id, now_utc=datetime.now(UTC)
    )

    enrollments, has_more = await _fetch_enrollment_page(
        db, user_id=current_user_id, status="active", offset=0, limit=DEFAULT_PAGE_SIZE
    )

    return templates.TemplateResponse(
        "home/index.html",
        {
            "title": "خانه",
            # Empty for a member with no roadmaps, and the section is not
            # rendered at all then -- so this costs one indexed query and no
            # pixels to everybody who has never opened a course.
            "roadmap_steps": await home_roadmap_card(db, user_id=current_user_id),
            "request": request,
            "user": db_user,
            "dash": dashboard,
            "enrollments": enrollments,
            "stat_active": stat_active,
            "stat_done": stat_done,
            "has_more": has_more,
            "page_size": DEFAULT_PAGE_SIZE,
            "current_user_id": current_user_id,
            "active_nav": "home",
        },
    )


@router.get("/fragment")
async def home_fragment(
    request: Request,
    current_user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
    status: Literal["active", "completed"] = "active",
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE),
):
    """Returns just the next page of enrollment cards, for infinite scroll."""
    enrollments, has_more = await _fetch_enrollment_page(
        db, user_id=current_user_id, status=status, offset=offset, limit=limit
    )
    response = templates.TemplateResponse(
        "home/_enrollment_cards.html",
        {"request": request, "enrollments": enrollments},
    )
    response.headers["X-Has-More"] = "true" if has_more else "false"
    return response
