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
from app.avatars import register_avatar_filters
from app.config import BASE_DIR
from app.database import get_db
from app.explainers import register_explainer_filters
from app.groups import is_group_member, leaving_is_allowed, register_group_filters
from app.icons import register_icon_filters
from app.identity import (
    asks_the_joiner,
    participant_display,
    register_identity_filters,
)
from app.models.challenge import Challenge, ChallengeCategory
from app.models.checkin import CheckIn
from app.models.enrollment import ChallengeRole, Enrollment
from app.models.group import GroupMembership
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
from app.permissions import Perm, can, challenge_role
from app.routers.challenge import (
    DEFAULT_PAGE_SIZE,
    DEFAULT_TIMEZONE,
    MAX_PAGE_SIZE,
    MINE_ALL,
    STATUS_ALL,
    challenge_status,
    challenge_visibility_filter,
    fetch_challenge_page,
    resolve_group_for_create,
    resolve_timezone,
    seed_group_participants,
)
from app.routers.checkin import occurrence_local_date, parse_cadence
from app.routers.enrollment import (
    DEFAULT_LEADERBOARD_PAGE_SIZE,
    LEADERBOARD_COMPLETIONS,
    LEADERBOARD_SORTS,
    LEADERBOARD_STREAK,
    MAX_LEADERBOARD_PAGE_SIZE,
    assign_ranks,
    fetch_leaderboard_page,
    leaderboard_rank,
    leaderboard_standing,
)
from app.routers.group import (
    fetch_group_member_page,
    load_group,
    member_rows,
)
from app.routers.user import MAX_MEMBER_PAGE_SIZE
from app.schemas.cadence import (
    CadenceUnion,
    OnceCadence,
    RecurringDaysCadence,
    RecurringQuotaCadence,
    ScheduleCadence,
)
from app.schemas.challenge import ChallengeCreate

logger = logging.getLogger(__name__)

# How many occurrences the compact history strip plots. It is a shape, not a
# log -- one square per occurrence, no labels. The readable history is not
# capped alongside it: the calendar beside the strip pages through the whole
# run a month at a time, so there is no "show older" cut-off to pick.
HISTORY_STRIP_LIMIT = 30

# How many past periods (weeks/months) the recurring_quota history strip
# shows, in addition to the current period.
QUOTA_PERIODS_BACK = 5

# How many upcoming occurrences the cadence plan card previews, and how far
# ahead the "how often is this" density line looks.
UPCOMING_LIMIT = 4

# How many upcoming occurrences the history calendar plots. Not a preview
# like UPCOMING_LIMIT -- the calendar is meant to be the whole shape of the
# challenge, so it takes everything `upcoming_occurrences` will give before
# its own MAX_LOOKAHEAD_DAYS horizon cuts it off.
CALENDAR_UPCOMING_LIMIT = 400
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
    "recurring_days": "روزهای مشخص",
    "recurring_quota": "سهمیه‌ای",
}

VISIBILITY_LABELS = {
    "public": "عمومی",
    "unlisted": "فقط با لینک",
    "private": "خصوصی",
}

# How each identity mode reads on this page. Per-surface Farsi labelling,
# same pattern as VISIBILITY_LABELS above (D7): the create wizard builds its
# pills client-side and keeps its own copy, exactly as it does for cadence.
# `hint` is the one line the join sheet prints under the question -- «ناشناس»
# is a promise, and a promise nobody spelled out is a promise nobody trusts.
IDENTITY_LABELS = {
    "named": "با نام",
    "anonymous": "ناشناس",
    "member_choice": "به انتخاب هر عضو",
}

IDENTITY_HINTS = {
    "named": "همهٔ اعضا با نام و آواتار خودشان دیده می‌شوند.",
    "anonymous": "هیچ عضوی نام یا آواتارش را نمی‌بیند؛ فقط شمارش‌ها دیده می‌شود.",
    "member_choice": "هر عضو موقع پیوستن خودش انتخاب می‌کند.",
}

IDENTITY_ICONS = {
    "named": "users",
    "anonymous": "mask",
    "member_choice": "help",
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

# How the leaderboard can be ordered, and what each ordering is called.
# Per-surface Farsi labelling, same pattern as CADENCE_LABELS above (D7).
# Two options and no more: «velocity» is a discovery sort and «تازه‌ترین» is
# not a ranking, so neither belongs on a board whose whole subject is who is
# ahead.
LEADERBOARD_SORT_LABELS = {
    LEADERBOARD_COMPLETIONS: "بیشترین ثبت",
    LEADERBOARD_STREAK: "بلندترین رشته",
}

LEADERBOARD_SORT_ICONS = {
    LEADERBOARD_COMPLETIONS: "check",
    LEADERBOARD_STREAK: "flame",
}

LEADERBOARD_SORT_PATTERN = "^({})$".format("|".join(LEADERBOARD_SORTS))


router = APIRouter(prefix="/views/challenges", tags=["challenge-views"])
templates = Jinja2Templates(directory=BASE_DIR / "templates")
register_explainer_filters(templates.env)


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
register_avatar_filters(templates.env)
register_identity_filters(templates.env)
# The card's group chip names the group's kind. The Farsi maps live in
# `app/groups.py` because more than one views router renders them, and they
# arrive here through the same registration contract `register_icon_filters`
# has -- as globals rather than context keys, so none of the three routes
# that render a card can forget to pass them.
register_group_filters(templates.env)


@router.get("/create")
async def create_challenge_form(
    request: Request,
    db_user: User = Depends(get_page_user),
    db: AsyncSession = Depends(get_db),
    group: int | None = Query(default=None),
):
    """The wizard. **Unchanged for somebody creating a personal challenge.**

    That is the constraint this route is written to: `?group=` is absent for
    every existing entrance (the challenge list's «+», the home dashboard),
    so the extra questions -- who in the group this is for, and whether it is
    optional -- do not exist on that path. A group challenge is created from
    inside its group, which is also what makes «چالش از همان ابتدا ذیل گروه
    ساخته شود» true by construction rather than by a validation rule.

    The permission is checked *here*, not only at the POST: offering a wizard
    that will be refused on the last tap is worse than not offering it. It is
    checked at the POST as well, because a rendered form is not a permission.
    """
    group_obj = None
    group_members = []
    if group is not None:
        group_obj, membership = await load_group(db, group, db_user)
        if not can(
            db_user, Perm.GROUP_CREATE_CHALLENGE, group=group_obj, membership=membership
        ):
            raise HTTPException(
                status_code=403, detail="Not allowed to create a challenge here"
            )
        roster, _ = await fetch_group_member_page(
            db, group_id=group, offset=0, limit=MAX_MEMBER_PAGE_SIZE
        )
        group_members = [
            row.model_dump() for row in member_rows(roster) if row.user_id != db_user.id
        ]
    return templates.TemplateResponse(
        "challenge/create-challenge.html",
        {
            "title": "ساخت چالش جدید",
            "request": request,
            "current_user_id": db_user.id,
            "group": group_obj,
            "group_members": group_members,
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
        # The same two helpers the JSON API calls, rather than a second copy
        # of the rules: CLAUDE.md already flags this duplicated create path
        # as the place an edit gets applied to one half only, and a missing
        # permission check is the worst possible thing to leave behind here.
        current_user = (
            await db.execute(select(User).where(User.id == current_user_id))
        ).scalar_one()
        group = await resolve_group_for_create(db, challenge.group_id, current_user)

        data = challenge.model_dump(exclude={"cadence", "timezone", "member_ids"})
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
            role=ChallengeRole.OWNER.value,
        )
        db.add(enrollment)
        db.add(ChallengeStats(challenge_id=db_challenge.id, participant_count=1))
        await db.flush()

        await seed_group_participants(
            db,
            challenge=db_challenge,
            group=group,
            member_ids=challenge.member_ids,
            actor_user_id=current_user_id,
            timezone=tz,
        )

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
        # `Challenge.group` is loaded for the card's group chip: the session
        # is async, so a lazy load during rendering surfaces as
        # MissingGreenlet rather than as a query error (CLAUDE.md).
        options=(selectinload(Challenge.enrollments), selectinload(Challenge.group)),
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
        options=(selectinload(Challenge.enrollments), selectinload(Challenge.group)),
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


def build_calendar_marks(
    challenge: Challenge,
    cadence: CadenceUnion,
    *,
    enrollment: Enrollment,
    tz: str,
) -> dict[str, list[dict]]:
    """The challenge's own milestones, by the day they land on.

    Occurrences are what the *member* did; these are what the *challenge*
    does, and a calendar that shows only the former cannot explain why the
    occurrences start where they do or stop where they stop.

    Deliberately derived from exactly the fields `challenge_status` reads --
    `due_date`, `cadence.end_date`, `cadence.datetimes[0]` -- plus the
    enrollment's own start. A `Challenge` has no start column, so there is no
    challenge-wide "start" to show for a cadence that does not carry one, and
    inventing one (created_at, say) would put a date on the calendar that no
    other surface agrees with.
    """
    marks: dict[str, list[dict]] = {}

    def add(day: date, kind: str, label: str) -> None:
        marks.setdefault(day.isoformat(), []).append({"kind": kind, "label": label})

    # Always present, and the reason this member's history begins where it
    # does -- occurrences before it simply do not exist for them.
    add(enrollment.start_date, "member_start", "شروع عضویت من")

    if isinstance(cadence, ScheduleCadence) and cadence.datetimes:
        zone = ZoneInfo(tz)
        dates = sorted(dt.astimezone(zone).date() for dt in cadence.datetimes)
        add(dates[0], "challenge_start", "اولین جلسه")
        if dates[-1] != dates[0]:
            add(dates[-1], "challenge_end", "آخرین جلسه")

    end_date = getattr(cadence, "end_date", None)
    if end_date is not None:
        add(end_date.date(), "cadence_end", "پایان تکرار")

    # Two different endings and both are real: the cadence stops producing
    # occurrences on one, the challenge itself is over on the other.
    if challenge.due_date is not None:
        add(challenge.due_date.date(), "due", "مهلت چالش")

    return marks


async def build_history_timeline(
    db: AsyncSession,
    enrollment: Enrollment,
    cadence: CadenceUnion,
    now_utc: datetime,
    challenge: Challenge,
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

    # Every key gets its derived state once, up front: the strip plots only
    # the most recent HISTORY_STRIP_LIMIT of them, but the donut and the
    # calendar describe the whole enrollment, and "missed" is derived -- never
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
    for key in keys:
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
    # The same entries the timeline used, bucketed by the day they fall on --
    # a calendar cell is a *day*, and a day can hold more than one occurrence
    # (a `schedule` cadence may list two sessions on one date), so the value
    # is a list rather than a single entry. Oldest-first inside a day, since
    # that is the order they happened in.
    by_date: dict[str, list[dict]] = {}
    for entry in reversed(items):
        by_date.setdefault(entry["date"].isoformat(), []).append(
            {
                "key": entry["key"],
                "state": entry["state"],
                "amount": _clean_number(entry["amount"]),
                "note": entry["note"] or "",
                "checkin_id": entry["checkin_id"],
                "writable": entry["writable"],
                "editable": entry["editable"],
            }
        )

    # What the cadence still has coming. Nothing here is actionable -- an
    # occurrence cannot be recorded before it opens -- so these carry no
    # writable/editable flags and are drawn as an outline rather than a fill:
    # a day that has not happened is not a day that was missed, the same call
    # the home heatmap's `is-future` cells make.
    upcoming = upcoming_occurrences(
        cadence,
        start_date=enrollment.start_date,
        tz=enrollment.timezone,
        now_utc=now_utc,
        limit=CALENDAR_UPCOMING_LIMIT,
    )
    # A cadence goes on generating occurrences forever; the challenge does
    # not. `challenge_status` calls it finished once `due_date` or
    # `cadence.end_date` passes, so plotting nobat beyond whichever comes
    # first would have the calendar promising a year of them for a challenge
    # the rest of the page already calls over.
    horizon = min(
        (
            d.date()
            for d in (challenge.due_date, getattr(cadence, "end_date", None))
            if d is not None
        ),
        default=None,
    )
    for occ in upcoming:
        if horizon is not None and occ.local_date > horizon:
            continue
        iso = occ.local_date.isoformat()
        # `upcoming_occurrences` counts today's still-open occurrence as
        # upcoming; the history above already has that one, with its actions.
        if iso in by_date and any(e["key"] == occ.key for e in by_date[iso]):
            continue
        by_date.setdefault(iso, []).append(
            {
                "key": occ.key,
                "state": "upcoming",
                "amount": "",
                "note": "",
                "checkin_id": None,
                "writable": False,
                "editable": False,
            }
        )

    marks = build_calendar_marks(
        challenge, cadence, enrollment=enrollment, tz=enrollment.timezone
    )

    # The window the arrows may page through: every day the calendar has
    # anything to say about. Derived from the content rather than from
    # enrollment-start..today, or a challenge whose deadline or last session
    # sits outside that range would have a milestone the member cannot reach.
    spans = list(by_date) + list(marks) + [today.isoformat()]

    calendar = {
        "days": by_date,
        "marks": marks,
        "start": min(spans),
        "end": max(spans),
        "today": today.isoformat(),
    }

    return {
        "summary": summary,
        "strip": strip,
        "calendar": calendar,
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
            selectinload(Challenge.group),
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
    # Asked through `challenge_role` rather than compared to `owner_id` here,
    # so the page and the API answer "is this mine" from one definition -- the
    # button this decides to render must match the guard on the route it calls.
    is_owner = (
        challenge_role(
            current_user_id, challenge=db_challenge, enrollment=my_enrollment
        )
        == ChallengeRole.OWNER.value
    )
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
    # slot of its own, so it has no cell on a calendar either); every other
    # cadence gets the day-shaped history calendar.
    quota_rows = []
    history_strip = []
    history_calendar = None
    history_summary = None
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
                db, my_enrollment, cadence, now_utc, db_challenge
            )
            history_strip = history["strip"]
            history_calendar = history["calendar"]
        history_summary = history["summary"]
        today_key = history["today_key"]

    # --- the group this challenge belongs to, if any ---------------------
    # Three answers the page needs and one is not derivable from the others:
    # whether the viewer administers the group (the participants screen),
    # whether they may leave (`leaving_is_allowed` -- asked here so the button
    # and the route it calls agree), and whether the anonymity switch applies.
    group_can_manage = False
    can_leave = True
    if db_challenge.group_id is not None:
        still_in_group = await is_group_member(
            db, db_challenge.group_id, current_user_id
        ) if current_user_id is not None else False
        can_leave = leaving_is_allowed(db_challenge, still_in_group)
        if still_in_group:
            group_membership = (
                await db.execute(
                    select(GroupMembership).where(
                        GroupMembership.group_id == db_challenge.group_id,
                        GroupMembership.user_id == current_user_id,
                    )
                )
            ).scalar_one_or_none()
            viewer = (
                await db.execute(select(User).where(User.id == current_user_id))
            ).scalar_one_or_none()
            group_can_manage = can(
                viewer,
                Perm.GROUP_MANAGE_MEMBERS,
                group=db_challenge.group,
                membership=group_membership,
            )

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
            "group_can_manage": group_can_manage,
            "can_leave": can_leave,
            # Whether this member may change their own answer, and what it
            # currently is. `asks_the_joiner` rather than an enum comparison
            # here, so the switch and `resolve_anonymity` cannot drift.
            "can_set_anonymity": is_enrolled
            and asks_the_joiner(db_challenge.identity_mode),
            "my_is_anonymous": bool(my_enrollment.is_anonymous)
            if my_enrollment
            else False,
            "can_delete": is_owner and not has_other_participants,
            # The same count as `can_delete`, named separately because it
            # answers a different question and could stop agreeing: hard
            # delete protects other people's logged history, this protects
            # the deal they joined under (see `locked_fields` in
            # routers/challenge.py). The sheet offers only what the API would
            # accept, so it drops the field rather than showing one that 409s.
            "can_edit_identity": is_owner and not has_other_participants,
            "identity_label": IDENTITY_LABELS.get(
                db_challenge.identity_mode, db_challenge.identity_mode
            ),
            "identity_hint": IDENTITY_HINTS.get(db_challenge.identity_mode, ""),
            # The join button only needs to know whether joining is a
            # question; asking through `asks_the_joiner` keeps the template
            # from restating the enum comparison `resolve_anonymity` owns.
            "identity_asks_joiner": asks_the_joiner(db_challenge.identity_mode),
            "identity_options": [
                {
                    "value": value,
                    "label": label,
                    "hint": IDENTITY_HINTS[value],
                    "icon": IDENTITY_ICONS[value],
                }
                for value, label in IDENTITY_LABELS.items()
            ],
            "visibility_options": VISIBILITY_LABELS,
            "lifecycle_options": LIFECYCLE_LABELS,
            "participant_count": participant_count,
            "total_completions": total_completions,
            "total_amount": total_amount,
            "progress_pct": progress_pct,
            "my_streak": my_enrollment.current_streak if my_enrollment else None,
            "my_longest_streak": my_enrollment.longest_streak if my_enrollment else None,
            "quota_rows": quota_rows,
            "history_strip": history_strip,
            "history_calendar": history_calendar,
            "history_summary": history_summary,
            "today_key": today_key,
            "goal_unit": db_challenge.goal_unit,
            "current_user_id": current_user_id,
            "active_nav": "challenges",
        },
    )


# ---------------------------------------------------------------------------
# The leaderboard
# ---------------------------------------------------------------------------
#
# One challenge's participants, ranked. It is a page of its own rather than a
# fourth tab on challenge-detail for the reason the moderation roster is one:
# it is ordered and lazily paged, and a list that pages cannot live inside a
# panel that is only ever a screenful.
#
# **It does not widen `profile_visibility_filter`, and that is deliberate.**
# The board prints a name and a picture, and links to nothing -- so the one
# rule that decides whether a member may open another member's profile is
# untouched, and making the standings readable stays a single step. Reaching
# a profile from here is a second step, and it belongs to that one function
# whenever it is taken.
#
# What it *does* is answer "who is in this" to a fellow participant, which is
# why the gate below is membership rather than mere visibility: joining a
# shared challenge is joining a room, and the people in a room can see each
# other. Someone who has not joined a public challenge can still read it, its
# size and its collective progress -- everything except who the other people
# are.


async def _leaderboard_challenge(
    db: AsyncSession, challenge_id: int, viewer_id: int
) -> tuple[Challenge, Enrollment | None]:
    """Load a challenge for a leaderboard read, or refuse -- 404 then 403.

    Two gates in the order that keeps the 404-vs-403 split honest
    (CLAUDE.md): reachability first, through the same
    `challenge_visibility_filter` composed into the query that every other
    challenge read uses, so a private challenge stays a plain "no such row";
    then membership, which is a **403** because by that point the caller can
    demonstrably already see the challenge, and a 404 there would hide the
    one thing they need to be told -- that joining is what opens the board.

    Membership is asked through `challenge_role`, not by comparing ids, so an
    owner who has unenrolled from their own challenge still reaches it -- the
    same resolution the manage sheet on challenge-detail is rendered from.
    """
    result = await db.execute(
        select(Challenge)
        .options(selectinload(Challenge.owner))
        .where(
            Challenge.id == challenge_id,
            challenge_visibility_filter(viewer_id),
        )
    )
    challenge = result.scalar_one_or_none()
    if challenge is None:
        raise HTTPException(status_code=404, detail="Challenge not found")

    enrollment = (
        await db.execute(
            select(Enrollment).where(
                Enrollment.challenge_id == challenge_id,
                Enrollment.user_id == viewer_id,
            )
        )
    ).scalar_one_or_none()
    if challenge_role(viewer_id, challenge=challenge, enrollment=enrollment) is None:
        raise HTTPException(
            status_code=403, detail="Only participants can see the leaderboard"
        )
    return challenge, enrollment


async def build_leaderboard_rows(
    db: AsyncSession,
    *,
    challenge_id: int,
    viewer_id: int,
    sort: str,
    offset: int,
    limit: int,
) -> tuple[list[dict], bool]:
    """One page of standings, ready to render.

    The query is `fetch_leaderboard_page` in the API router and the ranks are
    `assign_ranks` beside it; this adds only the two presentation answers --
    who each row is allowed to look like (`participant_display`, the one
    place anonymity becomes a name) and which of the two figures is the one
    being ranked on, so the template never restates the sort.
    """
    rows, has_more = await fetch_leaderboard_page(
        db, challenge_id=challenge_id, sort=sort, offset=offset, limit=limit
    )
    if rows:
        first_rank = await leaderboard_rank(
            db, challenge_id=challenge_id, sort=sort, score=rows[0]["score"]
        )
        assign_ranks(rows, offset=offset, first_rank=first_rank)
    for row in rows:
        row["who"] = participant_display(row["enrollment"], viewer_id)
        # `score` is whatever the active sort ranks by and `other` is the
        # figure it does not -- resolved here rather than in the template so
        # a row does not have to re-derive the sort from its own labels.
        row["other"] = (
            row["completions"] if sort == LEADERBOARD_STREAK else row["streak"]
        )
    return rows, has_more


def _leaderboard_context(sort: str) -> dict:
    """The bits of the page that only depend on which ordering is showing."""
    return {
        "active_sort": sort,
        "sort_options": [
            {
                "value": value,
                "label": label,
                "icon": LEADERBOARD_SORT_ICONS[value],
            }
            for value, label in LEADERBOARD_SORT_LABELS.items()
        ],
        # What the ranked column is called in a row, and what the other one
        # is. Named once here so the fragment -- which has no page context --
        # renders a row identically to the first page.
        "score_label": "ثبت" if sort == LEADERBOARD_COMPLETIONS else "روز پیاپی",
        "other_label": "روز پیاپی" if sort == LEADERBOARD_COMPLETIONS else "ثبت",
    }


@router.get("/{challenge_id}/leaderboard")
async def challenge_leaderboard(
    challenge_id: int,
    request: Request,
    viewer: User = Depends(get_page_user),
    db: AsyncSession = Depends(get_db),
    sort: str = Query(
        default=LEADERBOARD_COMPLETIONS, pattern=LEADERBOARD_SORT_PATTERN
    ),
):
    """The standings inside one challenge.

    `get_page_user` rather than `get_optional_user_id`: the board is for the
    people in the room, so a signed-out visitor is an authentication failure
    (303 to login, carrying `next`) long before it is an authorization one --
    the same split every other gated page uses.

    The sort is read off the query string and mirrored back into it by the
    page's own JS, so a board someone is looking at is a link they can send.
    """
    challenge, enrollment = await _leaderboard_challenge(db, challenge_id, viewer.id)
    rows, has_more = await build_leaderboard_rows(
        db,
        challenge_id=challenge_id,
        viewer_id=viewer.id,
        sort=sort,
        offset=0,
        limit=DEFAULT_LEADERBOARD_PAGE_SIZE,
    )
    total = int(
        (
            await db.execute(
                select(func.count())
                .select_from(Enrollment)
                .where(Enrollment.challenge_id == challenge_id)
            )
        ).scalar_one()
    )
    # An owner who has unenrolled reaches the board but stands on no row of
    # it, so the card above it is simply not rendered rather than inventing a
    # rank for somebody who is not competing.
    standing = (
        await leaderboard_standing(db, enrollment, sort)
        if enrollment is not None
        else None
    )
    return templates.TemplateResponse(
        "challenge/leaderboard.html",
        {
            "title": f"رتبه‌بندی: {challenge.title}",
            "request": request,
            "challenge": challenge,
            "rows": rows,
            "standing": standing,
            "total": total,
            "has_more": has_more,
            "page_size": DEFAULT_LEADERBOARD_PAGE_SIZE,
            "current_user_id": viewer.id,
            "active_nav": "challenges",
            **_leaderboard_context(sort),
        },
    )


@router.get("/{challenge_id}/leaderboard/fragment")
async def challenge_leaderboard_fragment(
    challenge_id: int,
    request: Request,
    current_user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
    sort: str = Query(
        default=LEADERBOARD_COMPLETIONS, pattern=LEADERBOARD_SORT_PATTERN
    ),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(
        default=DEFAULT_LEADERBOARD_PAGE_SIZE, ge=1, le=MAX_LEADERBOARD_PAGE_SIZE
    ),
):
    """The next page of rows. `get_current_user_id` for the reason every
    `/fragment` in the app takes it: `createInfiniteScroller()` reads a real
    401 to redirect on, and the 303 the page dependency raises would be
    followed silently and the login form appended as if it were standings."""
    await _leaderboard_challenge(db, challenge_id, current_user_id)
    rows, has_more = await build_leaderboard_rows(
        db,
        challenge_id=challenge_id,
        viewer_id=current_user_id,
        sort=sort,
        offset=offset,
        limit=limit,
    )
    response = templates.TemplateResponse(
        "challenge/_leaderboard_rows.html",
        {"request": request, "rows": rows, **_leaderboard_context(sort)},
    )
    response.headers["X-Has-More"] = "true" if has_more else "false"
    return response
