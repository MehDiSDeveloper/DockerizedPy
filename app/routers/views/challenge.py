# routers/views/challenge.py
import logging
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.auth import get_current_user_id, get_optional_user_id, get_page_user
from app.config import BASE_DIR
from app.database import get_db
from app.icons import register_icon_filters
from app.models.challenge import Challenge, ChallengeCategory
from app.models.checkin import CheckIn
from app.models.enrollment import Enrollment
from app.models.stats import ChallengeStats
from app.models.user import User
from app.occurrences import (
    count_occurrences_between,
    derive_state,
    expected_keys_desc,
    is_key_writable,
    local_today,
    occurrences_due,
    period_bounds,
    period_key,
    upcoming_occurrences,
    week_start,
)
from app.routers.challenge import (
    DEFAULT_PAGE_SIZE,
    DEFAULT_TIMEZONE,
    MAX_PAGE_SIZE,
    MINE_ALL,
    STATUS_ALL,
    challenge_status,
    challenge_visibility_filter,
    fetch_challenge_page,
    resolve_timezone,
)
from app.routers.checkin import occurrence_local_date, parse_cadence
from app.schemas.cadence import (
    CadenceUnion,
    OnceCadence,
    RecurringDaysCadence,
    RecurringQuotaCadence,
    ScheduleCadence,
)
from app.schemas.challenge import ChallengeCreate

logger = logging.getLogger(__name__)

# How many occurrences the challenge-detail history timeline shows before
# collapsing the rest behind a "show older" note.
HISTORY_LIMIT = 12

# How many occurrences the compact history strip above the timeline plots.
# It is a shape, not a log -- one square per occurrence, no labels -- so it
# can carry ~3x what the readable timeline does in a fraction of the height.
HISTORY_STRIP_LIMIT = 30

# How many past periods (weeks/months) the recurring_quota history strip
# shows, in addition to the current period.
QUOTA_PERIODS_BACK = 5

# How many upcoming occurrences the cadence plan card previews, and how far
# ahead the "how often is this" density line looks.
UPCOMING_LIMIT = 4
DENSITY_WINDOW_DAYS = 30

# 0 = Saturday .. 6 = Friday -- the Iranian indexing used everywhere in this
# codebase (see to_ir_weekday). Spelled the same way as create-challenge.html's
# weekdayNames so a day reads identically on both screens.
WEEKDAY_NAMES = ("شنبه", "یکشنبه", "دوشنبه", "سه‌شنبه", "چهارشنبه", "پنجشنبه", "جمعه")

# CLAUDE.md (D7): these enums carry English codes and there is no shared Farsi
# display map, so each surface spells its own labels -- same as
# create-challenge.html's cadenceKindLabels.
CADENCE_LABELS = {
    "once": "یک‌باره",
    "schedule": "زمان‌بندی‌شده",
    "recurring_days": "تکرار روزانه",
    "recurring_quota": "سهمیه‌ای",
}

VISIBILITY_LABELS = {
    "public": "عمومی",
    "unlisted": "فقط با لینک",
    "private": "خصوصی",
}

LIFECYCLE_LABELS = {
    "draft": "پیش‌نویس",
    "active": "فعال",
    "archived": "بایگانی‌شده",
}

# How the derived status (app.routers.challenge.challenge_status) reads on a
# card: a Farsi label, an icon from app.js's set, and the CSS colour key the
# card's accent stripe and pill share. Same per-surface labelling pattern as
# CADENCE_LABELS above (D7 -- there is no shared Farsi display map).
STATUS_META = {
    "upcoming": {"label": "شروع نشده", "icon": "clock"},
    "active": {"label": "در حال اجرا", "icon": "flame"},
    "finished": {"label": "تمام شده", "icon": "seal"},
}

router = APIRouter(prefix="/views/challenges", tags=["challenge-views"])
templates = Jinja2Templates(directory=BASE_DIR / "templates")


def _clean_number(value) -> str:
    """Numeric(12,2) round-trips as "100.00" -- nobody writes a goal that way."""
    if value is None:
        return ""
    text = f"{float(value):.2f}".rstrip("0").rstrip(".")
    return text or "0"


templates.env.filters["num"] = _clean_number
# A card renders the derived status; the fragment endpoint renders the same
# card template with its own context, so the label/icon map is a global rather
# than something both routes have to remember to pass.
templates.env.filters["challenge_status"] = challenge_status
templates.env.globals["status_meta"] = STATUS_META
register_icon_filters(templates.env)


@router.get("/create")
async def create_challenge_form(
    request: Request, db_user: User = Depends(get_page_user)
):
    current_user_id = db_user.id
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
    mine: str = Query(default=MINE_ALL, pattern="^(all|only|hide)$"),
    status: str = Query(
        default=STATUS_ALL, pattern="^(all|upcoming|active|finished)$"
    ),
):
    challenges, has_more = await fetch_challenge_page(
        db,
        current_user_id=current_user_id,
        category=category,
        q=q,
        offset=0,
        limit=DEFAULT_PAGE_SIZE,
        sort="velocity",
        mine=mine,
        status=status,
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
            "active_mine": mine,
            "active_status": status,
            "status_options": STATUS_META,
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
    mine: str = Query(default=MINE_ALL, pattern="^(all|only|hide)$"),
    status: str = Query(
        default=STATUS_ALL, pattern="^(all|upcoming|active|finished)$"
    ),
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
        mine=mine,
        status=status,
        options=(selectinload(Challenge.enrollments),),
    )
    response = templates.TemplateResponse(
        "challenge/_challenge_cards.html",
        {"request": request, "challenges": challenges},
    )
    response.headers["X-Has-More"] = "true" if has_more else "false"
    return response


def _join_fa(parts: list[str]) -> str:
    """«شنبه، دوشنبه و چهارشنبه» -- Farsi joins the last item with «و»."""
    if len(parts) <= 1:
        return "".join(parts)
    return "، ".join(parts[:-1]) + " و " + parts[-1]


def describe_cadence(cadence: CadenceUnion) -> str:
    """One Farsi sentence naming the rule this cadence encodes."""
    if isinstance(cadence, OnceCadence):
        return "یک بار، هر وقت آماده بودی"

    if isinstance(cadence, ScheduleCadence):
        return f"{len(cadence.datetimes)} جلسهٔ زمان‌بندی‌شده"

    if isinstance(cadence, RecurringDaysCadence):
        if cadence.mode == "weekdays":
            days = _join_fa([WEEKDAY_NAMES[d] for d in cadence.weekdays])
            return f"هر هفته: {days}"
        if cadence.mode == "every_n_days":
            return "هر روز" if cadence.n == 1 else f"هر {cadence.n} روز یک بار"
        if cadence.mode == "every_n_weeks":
            return "هر هفته" if cadence.n == 1 else f"هر {cadence.n} هفته یک بار"
        if cadence.mode == "every_n_months":
            return "هر ماه" if cadence.n == 1 else f"هر {cadence.n} ماه یک بار"

    if isinstance(cadence, RecurringQuotaCadence):
        period = "ماه" if cadence.period == "month" else "هفته"
        return f"{cadence.count} بار در هر {period}"

    return ""


def build_cadence_plan(
    cadence: CadenceUnion,
    *,
    start_date: date,
    tz: str,
    now_utc: datetime,
) -> dict:
    """The "how this challenge actually runs" card.

    Cadence-specific, and deliberately built for non-participants too: the
    shape of a challenge is the main thing someone weighs before enrolling.

    Dates come back as ISO strings plus a `format` hint rather than as
    formatted text -- renderDates() in app.js is the only place this app turns
    a date into something a human reads, and it renders Jalali.
    """
    today = local_today(tz, now_utc)
    upcoming = upcoming_occurrences(
        cadence,
        start_date=start_date,
        tz=tz,
        now_utc=now_utc,
        limit=UPCOMING_LIMIT,
    )

    plan: dict = {
        "kind": cadence.kind,
        "label": CADENCE_LABELS.get(cadence.kind, cadence.kind),
        "rule": describe_cadence(cadence),
        "weekdays": None,
        "timezone": tz,
        "facts": [],
        "upcoming": [
            {
                "key": u.key,
                "date": u.local_date.isoformat(),
                "at": u.at_utc.isoformat() if u.at_utc else None,
                "is_next": i == 0,
            }
            for i, u in enumerate(upcoming)
        ],
        "upcoming_label": "نوبت‌های بعدی",
        "upcoming_format": "weekday-day-month",
        "upcoming_prefix": "",
        "density": None,
        "progress": None,
        "end_date": None,
    }

    end_date = getattr(cadence, "end_date", None)
    if end_date is not None:
        plan["end_date"] = end_date.date().isoformat()
        plan["facts"].append(
            {
                "label": "پایان تکرار",
                "date": plan["end_date"],
                "format": "day-month-year",
            }
        )

    if isinstance(cadence, OnceCadence):
        # A one-off has no calendar to preview -- listing "today" as an
        # upcoming occurrence would just restate the CTA.
        plan["upcoming"] = []
        plan["facts"].append({"label": "مهلت ثبت", "text": "بدون محدودیت زمانی"})

    elif isinstance(cadence, ScheduleCadence):
        plan["upcoming_label"] = "جلسه‌های پیش رو"
        zone = ZoneInfo(tz)
        dates = sorted(dt.astimezone(zone).date() for dt in cadence.datetimes)
        passed = sum(1 for d in dates if d < today)
        plan["progress"] = {
            "done": passed,
            "total": len(dates),
            "pct": round(passed / len(dates) * 100) if dates else 0,
            "label": "جلسهٔ سپری‌شده",
        }
        plan["facts"].append(
            {
                "label": "اولین جلسه",
                "date": dates[0].isoformat(),
                "format": "day-month-year",
            }
        )
        plan["facts"].append(
            {
                "label": "آخرین جلسه",
                "date": dates[-1].isoformat(),
                "format": "day-month-year",
            }
        )

    elif isinstance(cadence, RecurringDaysCadence):
        if cadence.mode == "weekdays":
            plan["weekdays"] = [
                {"index": i, "name": WEEKDAY_NAMES[i], "on": i in cadence.weekdays}
                for i in range(7)
            ]
        # Counted rather than derived: "every 10 days" or "every 2 Jalali
        # months" has no round per-week answer to quote.
        n = count_occurrences_between(
            cadence,
            start_date=start_date,
            tz=tz,
            since=today,
            until=today + timedelta(days=DENSITY_WINDOW_DAYS - 1),
        )
        plan["density"] = f"{n} نوبت در {DENSITY_WINDOW_DAYS} روز آینده"

    elif isinstance(cadence, RecurringQuotaCadence):
        plan["upcoming_label"] = "دوره‌های بعدی"
        plan["upcoming_format"] = "month" if cadence.period == "month" else "day-month"
        plan["upcoming_prefix"] = "" if cadence.period == "month" else "هفتهٔ "
        period_start, period_end = period_bounds(today, cadence.period)
        plan["facts"].append(
            {
                "label": "دورهٔ جاری",
                "date": period_start.isoformat(),
                "format": "day-month",
                "date_to": period_end.isoformat(),
            }
        )
        # The rule line already says "N times a month"; the useful extra fact
        # is how long a period actually is -- Jalali months are 29/30/31 days.
        plan["density"] = f"هر دوره {(period_end - period_start).days + 1} روزه"
        # The current period leads the upcoming list, but the quota history
        # strip below already covers it in far more detail.
        plan["upcoming"] = plan["upcoming"][1:]

    return plan


async def build_history_timeline(
    db: AsyncSession,
    enrollment: Enrollment,
    cadence: CadenceUnion,
    now_utc: datetime,
) -> dict:
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

    # Every key gets its derived state once, up front: the timeline shows only
    # the most recent HISTORY_LIMIT of them, but the donut and the strip above
    # it describe the whole enrollment, and "missed" is derived -- never
    # stored -- so it cannot be counted off the CheckIns rows alone.
    states = {
        key: derive_state(
            cadence,
            start_date=enrollment.start_date,
            tz=enrollment.timezone,
            key=key,
            now_utc=now_utc,
            row_state=by_key[key].state if key in by_key else None,
        )
        for key in keys
    }
    tally = {"completed": 0, "skipped": 0, "missed": 0, "pending": 0}
    for state in states.values():
        if state in tally:
            tally[state] += 1
    completed = tally["completed"]

    this_week_start = week_start(today)
    week_keys = [k for k in keys if date_for(k) >= this_week_start]
    week_completed = sum(1 for k in week_keys if states.get(k) == "completed")

    # Oldest-first so the strip reads in the direction the page does: in RTL
    # the first square sits on the right, and "now" ends up on the left,
    # next to the timeline's newest row.
    strip = [
        {"state": states[k], "date": date_for(k)}
        for k in reversed(keys[:HISTORY_STRIP_LIMIT])
    ]

    today_key = None
    items = []
    for key in keys[:HISTORY_LIMIT]:
        row = by_key.get(key)
        state = states[key]
        local_date = date_for(key)
        # The backfill window governs recording *and* correcting an occurrence
        # -- POST, PATCH and DELETE on /checkins all check the same predicate,
        # so one flag decides which action the row can offer.
        key_writable = is_key_writable(
            cadence,
            start_date=enrollment.start_date,
            tz=enrollment.timezone,
            key=key,
            now_utc=now_utc,
        )
        items.append(
            {
                "key": key,
                "date": local_date,
                "is_today": local_date == today,
                "state": state,
                "amount": row.amount if row else None,
                "note": row.note if row else None,
                # Nothing recorded yet -> offer the check-in sheet. Re-POSTing
                # an existing row would silently no-op (the idempotent-conflict
                # path returns 200 without changing state), so a recorded row
                # gets the edit path below instead.
                "writable": state in ("pending", "missed") and key_writable,
                # Recorded and still inside the window -> offer PATCH/DELETE,
                # so a mis-logged amount or a wrong state isn't permanent.
                "checkin_id": row.id if row is not None else None,
                "editable": row is not None and key_writable,
            }
        )
        # The one occurrence the sticky CTA can act on. Keys come back
        # newest-first, so today's is the first that qualifies; `once` has no
        # calendar day of its own but dates itself to today (see
        # occurrence_local_date), which is what makes this cover it too.
        if today_key is None and items[-1]["writable"] and items[-1]["is_today"]:
            today_key = key

    summary = {
        "completed": completed,
        "skipped": tally["skipped"],
        "missed": tally["missed"],
        "pending": tally["pending"],
        "total": len(keys),
        "unit": "نوبت",
        "pct": round(completed / len(keys) * 100) if keys else 0,
        "period_done": week_completed,
        "period_total": len(week_keys),
        "period_label": "این هفته",
    }
    return {
        "items": items,
        "summary": summary,
        "has_more": len(keys) > HISTORY_LIMIT,
        "strip": strip,
        "today_key": today_key,
    }


async def build_quota_period_rows(
    db: AsyncSession,
    enrollment: Enrollment,
    cadence: RecurringQuotaCadence,
    now_utc: datetime,
) -> dict:
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
    # The period still running hasn't failed -- it just hasn't finished. Only
    # a closed period that fell short counts as missed.
    open_short = 1 if current is not None and current["done"] < current["target"] else 0
    is_week = cadence.period == "week"
    summary = {
        "completed": met,
        # A period is met or it isn't -- there is no third state to colour, so
        # the donut's remainder is simply "not met" and the legend says so.
        "skipped": 0,
        "missed": len(rows) - met - open_short,
        "pending": open_short,
        "total": len(rows),
        "unit": "هفته" if is_week else "ماه",
        "pct": round(met / len(rows) * 100) if rows else 0,
        "period_done": current["done"] if current else 0,
        "period_total": cadence.count,
        "period_label": "این هفته" if is_week else "این ماه",
        "missed_label": "تکمیل‌نشده",
    }
    return {"rows": rows, "summary": summary, "today_key": next_writable_key}


async def build_my_stats(
    db: AsyncSession, enrollment: Enrollment
) -> dict:
    """The participant's own tally on this challenge.

    Read live off `CheckIns` rather than off `Enrollments`: the only personal
    counters kept on the enrollment row are the two streaks (recomputed, never
    incremented -- see compute_streaks), and `legacy_completed_count` is
    frozen pre-migration data that must not be shown as a live number.
    """
    result = await db.execute(
        select(
            CheckIn.state,
            func.count(CheckIn.id),
            func.sum(CheckIn.amount),
            func.max(CheckIn.occurred_at_utc),
        )
        .where(CheckIn.enrollment_id == enrollment.id)
        .group_by(CheckIn.state)
    )
    completed = skipped = 0
    total_amount = 0.0
    last_at = None
    for state, count, amount_sum, max_at in result.all():
        if state == "completed":
            completed = count
            total_amount = float(amount_sum or 0)
        elif state == "skipped":
            skipped = count
        if max_at is not None and (last_at is None or max_at > last_at):
            last_at = max_at

    return {
        "completed": completed,
        "skipped": skipped,
        "total_amount": total_amount,
        "last_checkin_at": last_at,
        "start_date": enrollment.start_date,
        "timezone": enrollment.timezone,
        "status": enrollment.status,
        "current_streak": enrollment.current_streak,
        "longest_streak": enrollment.longest_streak,
    }


@router.get("/{challenge_id}")
async def challenge_detail(
    challenge_id: int,
    request: Request,
    current_user_id: int | None = Depends(get_optional_user_id),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Challenge)
        .options(
            selectinload(Challenge.owner),
            selectinload(Challenge.enrollments).selectinload(Enrollment.user),
        )
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

    # The owner's management affordances mirror the API's own guards rather
    # than guessing at them: cadence/goal are locked and hard delete is
    # refused once anyone else has joined (see update_challenge /
    # delete_challenge), so the sheet only offers what would actually succeed.
    is_owner = current_user_id is not None and db_challenge.owner_id == current_user_id
    has_other_participants = False
    if is_owner:
        has_other_participants = any(
            e.user_id != db_challenge.owner_id for e in db_challenge.enrollments
        )

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

    # The plan is read in the viewer's own enrollment timezone when they have
    # one -- two people enrolled in the same challenge from different zones do
    # genuinely have different occurrence dates (CLAUDE.md, timezone note).
    # A non-participant gets the app default, same as the collective progress
    # figures below.
    plan_tz = my_enrollment.timezone if my_enrollment else DEFAULT_TIMEZONE

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
    plan_start = my_enrollment.start_date if my_enrollment else created_local
    cadence_plan = build_cadence_plan(
        cadence, start_date=plan_start, tz=plan_tz, now_utc=now_utc
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
    history_strip = []
    history_summary = None
    has_more_history = False
    my_stats = None
    # The occurrence the sticky CTA offers to record. An enrolled visitor's
    # primary action on this page is checking in, not leaving -- but only when
    # something is actually open, so the CTA falls back to "leave" otherwise.
    today_key = None
    if my_enrollment is not None:
        my_stats = await build_my_stats(db, my_enrollment)
        if isinstance(cadence, RecurringQuotaCadence):
            history = await build_quota_period_rows(
                db, my_enrollment, cadence, now_utc
            )
            quota_rows = history["rows"]
        else:
            history = await build_history_timeline(
                db, my_enrollment, cadence, now_utc
            )
            timeline = history["items"]
            has_more_history = history["has_more"]
            history_strip = history["strip"]
        history_summary = history["summary"]
        today_key = history["today_key"]

    return templates.TemplateResponse(
        "challenge/challenge-detail.html",
        {
            "title": f"چالش: {db_challenge.title}",
            "request": request,
            "challenge": db_challenge,
            "cadence_kind": db_challenge.cadence_kind,
            "cadence_plan": cadence_plan,
            "visibility_label": VISIBILITY_LABELS.get(
                db_challenge.visibility, db_challenge.visibility
            ),
            "lifecycle_label": LIFECYCLE_LABELS.get(
                db_challenge.lifecycle_status, db_challenge.lifecycle_status
            ),
            "default_timezone": DEFAULT_TIMEZONE,
            "last_activity_at": stats.last_checkin_at if stats else None,
            "my_stats": my_stats,
            "is_enrolled": is_enrolled,
            "is_owner": is_owner,
            "can_delete": is_owner and not has_other_participants,
            "visibility_options": VISIBILITY_LABELS,
            "lifecycle_options": LIFECYCLE_LABELS,
            "participant_count": participant_count,
            "total_completions": total_completions,
            "total_amount": total_amount,
            "progress_pct": progress_pct,
            "my_streak": my_enrollment.current_streak if my_enrollment else None,
            "my_longest_streak": my_enrollment.longest_streak if my_enrollment else None,
            "timeline": timeline,
            "has_more_history": has_more_history,
            "quota_rows": quota_rows,
            "history_strip": history_strip,
            "history_summary": history_summary,
            "today_key": today_key,
            "goal_unit": db_challenge.goal_unit,
            "current_user_id": current_user_id,
            "active_nav": "challenges",
        },
    )
