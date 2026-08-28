from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import Select, and_, func, not_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_user_id, get_optional_user_id
from app.database import get_db
from app.models.challenge import (
    Challenge,
    ChallengeCategory,
    LifecycleStatus,
    Visibility,
)
from app.models.checkin import CheckIn
from app.models.enrollment import Enrollment
from app.models.stats import ChallengeStats
from app.occurrences import local_today
from app.schemas.challenge import ChallengeCreate, ChallengeRead, ChallengeUpdate
from app.schemas.checkin import ChallengeStatsRead

VELOCITY_WINDOW_DAYS = 7

router = APIRouter(prefix="/challenges", tags=["challenges"])

# Sentinel used by the "all categories" filter chip -- keep in sync with the
# `data-cat` value on the "همه" chip in challenge-list.html.
ALL_CATEGORIES = "همه"
DEFAULT_PAGE_SIZE = 20
MAX_PAGE_SIZE = 100
DEFAULT_TIMEZONE = "Asia/Tehran"


def resolve_timezone(tz: str | None) -> str:
    """Validate a client-supplied IANA timezone, falling back to Asia/Tehran (D5)."""
    if tz:
        try:
            ZoneInfo(tz)
            return tz
        except (ZoneInfoNotFoundError, ValueError):
            pass
    return DEFAULT_TIMEZONE


def challenge_visibility_filter(user_id: int | None):
    """A challenge is visible if it's public, unlisted, or the requester owns it or is enrolled in it."""
    if user_id is None:
        return Challenge.visibility == Visibility.PUBLIC.value
    enrolled_challenge_ids = select(Enrollment.challenge_id).where(
        Enrollment.user_id == user_id
    )
    return or_(
        Challenge.visibility.in_([Visibility.PUBLIC.value, Visibility.UNLISTED.value]),
        Challenge.owner_id == user_id,
        Challenge.id.in_(enrolled_challenge_ids),
    )


def listing_visibility_filter(user_id: int | None):
    """Like challenge_visibility_filter, but excludes unlisted challenges (D11):
    they're reachable at their own URL but never appear in a listing."""
    if user_id is None:
        return Challenge.visibility == Visibility.PUBLIC.value
    enrolled_challenge_ids = select(Enrollment.challenge_id).where(
        Enrollment.user_id == user_id
    )
    return or_(
        Challenge.visibility == Visibility.PUBLIC.value,
        Challenge.owner_id == user_id,
        Challenge.id.in_(enrolled_challenge_ids),
    )


async def count_non_owner_enrollments(
    db: AsyncSession, challenge_id: int, owner_id: int
) -> int:
    """D1: the creator is auto-enrolled, so lock/visibility guards must count
    only *other* participants."""
    result = await db.execute(
        select(func.count())
        .select_from(Enrollment)
        .where(Enrollment.challenge_id == challenge_id, Enrollment.user_id != owner_id)
    )
    return result.scalar_one()


# The three states of the "my challenges" filter -- keep in sync with the
# data-mine values on the segmented control in challenge-list.html.
MINE_ALL = "all"  # default: mine listed alongside everyone else's
MINE_ONLY = "only"  # only challenges I own or am enrolled in
MINE_HIDE = "hide"  # everyone else's challenges only
MINE_VALUES = (MINE_ALL, MINE_ONLY, MINE_HIDE)


def mine_filter(user_id: int | None, mine: str | None):
    """Clause for the "my challenges" filter, or None when it doesn't apply.

    "Mine" means owned *or* enrolled -- the same involvement the list card
    already reflects. Anonymous callers have no challenges of their own, so
    the filter is a no-op for them rather than an empty list."""
    if user_id is None or mine in (None, MINE_ALL) or mine not in MINE_VALUES:
        return None
    enrolled_challenge_ids = select(Enrollment.challenge_id).where(
        Enrollment.user_id == user_id
    )
    involved = or_(
        Challenge.owner_id == user_id,
        Challenge.id.in_(enrolled_challenge_ids),
    )
    return involved if mine == MINE_ONLY else ~involved


# ==========================================================================
# Derived challenge status: "شروع نشده" / "در حال اجرا" / "تمام شده"
# ==========================================================================
# A Challenge has no start/end column of its own, so its status is derived
# from what it does have: `lifecycle_status`, `due_date`, and the two date
# keys the cadence JSON can carry (`end_date`, `datetimes[0]`).
#
#   finished  archived, or due_date / cadence.end_date already past
#   upcoming  not finished, and either still a draft or its first scheduled
#             session is still ahead
#   active    everything else
#
# The rule is written twice on purpose -- `challenge_status` for rendering a
# card, `status_filter` as a SQL clause -- because the list is paginated
# server-side and a Python-side filter cannot page. Both are restricted to the
# same four inputs so the badge and the filter can never disagree: in
# particular a `schedule` challenge whose sessions have all passed keeps
# reading as active until its due_date passes or the owner archives it, since
# only element [0] of cadence.datetimes is reachable portably from SQL
# (SQLite's json_extract has no negative index). One honest rule beats a badge
# the filter can't reproduce.
STATUS_ALL = "all"
STATUS_UPCOMING = "upcoming"
STATUS_ACTIVE = "active"
STATUS_FINISHED = "finished"
STATUS_VALUES = (STATUS_ALL, STATUS_UPCOMING, STATUS_ACTIVE, STATUS_FINISHED)


def _cadence_now_iso(now: datetime) -> str:
    """Cadence dates live in the JSON column as pydantic `mode="json"` output
    -- a fixed `2026-09-14T06:00:00Z` shape -- so comparing them as strings is
    exact and needs no per-dialect timestamp casting."""
    return now.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def challenge_status(challenge: Challenge, now: datetime | None = None) -> str:
    """The Python half of the rule above -- what a card badge renders."""
    now = now or datetime.now(UTC)
    now_iso = _cadence_now_iso(now)
    cadence = challenge.cadence or {}

    due = challenge.due_date
    if due is not None and due.tzinfo is None:
        # SQLite has no aware datetime type and hands back naive UTC.
        due = due.replace(tzinfo=UTC)

    end_date = cadence.get("end_date")
    if (
        challenge.lifecycle_status == LifecycleStatus.ARCHIVED.value
        or (due is not None and due < now)
        or (isinstance(end_date, str) and end_date < now_iso)
    ):
        return STATUS_FINISHED

    datetimes = cadence.get("datetimes") or []
    first = datetimes[0] if datetimes else None
    if challenge.lifecycle_status == LifecycleStatus.DRAFT.value or (
        isinstance(first, str) and first > now_iso
    ):
        return STATUS_UPCOMING

    return STATUS_ACTIVE


def status_filter(status: str | None, now: datetime | None = None):
    """The SQL half of the rule above, or None when the filter doesn't apply.

    Every leg is guarded with an explicit IS NOT NULL so no branch evaluates to
    SQL NULL -- otherwise NOT(finished) would silently drop every challenge
    without a due_date from the "active" bucket."""
    if status in (None, STATUS_ALL) or status not in STATUS_VALUES:
        return None
    now = now or datetime.now(UTC)
    now_iso = _cadence_now_iso(now)
    cadence_end = Challenge.cadence["end_date"].as_string()
    first_session = Challenge.cadence["datetimes"][0].as_string()

    finished = or_(
        Challenge.lifecycle_status == LifecycleStatus.ARCHIVED.value,
        and_(Challenge.due_date.isnot(None), Challenge.due_date < now),
        and_(cadence_end.isnot(None), cadence_end < now_iso),
    )
    if status == STATUS_FINISHED:
        return finished

    not_started = or_(
        Challenge.lifecycle_status == LifecycleStatus.DRAFT.value,
        and_(first_session.isnot(None), first_session > now_iso),
    )
    if status == STATUS_UPCOMING:
        return and_(not_(finished), not_started)
    return and_(not_(finished), not_(not_started))


def apply_challenge_filters(
    stmt: Select,
    category: str | None,
    q: str | None,
) -> Select:
    """Apply the optional category/search filters shared by the JSON API,
    the SSR challenge list page, and its infinite-scroll fragment endpoint."""
    if category and category != ALL_CATEGORIES:
        try:
            stmt = stmt.where(Challenge.category == ChallengeCategory(category))
        except ValueError:
            # Unknown category value -- fall through with no category filter
            # rather than erroring out on a malformed/stale query param.
            pass
    q = (q or "").strip()
    if q:
        escaped = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        stmt = stmt.where(Challenge.title.ilike(f"%{escaped}%", escape="\\"))
    return stmt


def _velocity_count() -> Select:
    """Check-ins per challenge in the last VELOCITY_WINDOW_DAYS, correlated to
    the outer Challenges query -- hits ix_checkins_challenge_created, needs no
    background job (that's why ChallengeStats has no checkins_7d column)."""
    since = datetime.now(UTC) - timedelta(days=VELOCITY_WINDOW_DAYS)
    return (
        select(func.count(CheckIn.id))
        .where(CheckIn.challenge_id == Challenge.id, CheckIn.created_at >= since)
        .correlate(Challenge)
        .scalar_subquery()
    )


async def fetch_challenge_page(
    db: AsyncSession,
    *,
    current_user_id: int | None,
    category: str | None,
    q: str | None,
    offset: int,
    limit: int,
    sort: str = "recent",
    mine: str | None = None,
    status: str | None = None,
    options=(),
) -> tuple[list[Challenge], bool]:
    """Fetch one page of visible challenges plus whether more pages remain."""
    stmt = select(Challenge).where(listing_visibility_filter(current_user_id))
    stmt = apply_challenge_filters(stmt, category, q)
    mine_clause = mine_filter(current_user_id, mine)
    if mine_clause is not None:
        stmt = stmt.where(mine_clause)
    status_clause = status_filter(status)
    if status_clause is not None:
        stmt = stmt.where(status_clause)
    if sort == "velocity":
        stmt = stmt.order_by(_velocity_count().desc(), Challenge.id.desc())
    else:
        stmt = stmt.order_by(Challenge.id.desc())
    stmt = stmt.offset(offset).limit(limit + 1)
    for opt in options:
        stmt = stmt.options(opt)
    result = await db.execute(stmt)
    challenges = list(result.scalars().all())
    has_more = len(challenges) > limit
    return challenges[:limit], has_more


@router.post("/", response_model=ChallengeRead, status_code=201)
async def create_challenge(
    challenge: ChallengeCreate,
    current_user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    data = challenge.model_dump(exclude={"cadence", "timezone"})
    db_challenge = Challenge(**data)
    db_challenge.cadence_kind = challenge.cadence.kind
    db_challenge.cadence = challenge.cadence.model_dump(mode="json")
    db_challenge.owner_id = current_user_id
    db_challenge.last_modifier_user_id = current_user_id
    db.add(db_challenge)
    await db.flush()

    # D1: the creator is auto-enrolled.
    tz = resolve_timezone(challenge.timezone)
    enrollment = Enrollment(
        challenge_id=db_challenge.id,
        user_id=current_user_id,
        timezone=tz,
        start_date=local_today(tz, datetime.now(UTC)),
    )
    db.add(enrollment)
    db.add(ChallengeStats(challenge_id=db_challenge.id, participant_count=1))

    await db.commit()
    await db.refresh(db_challenge)
    return db_challenge


@router.get("/", response_model=list[ChallengeRead])
async def list_challenges(
    current_user_id: int | None = Depends(get_optional_user_id),
    db: AsyncSession = Depends(get_db),
    category: str | None = None,
    q: str | None = Query(default=None, max_length=100),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE),
    sort: str = Query(default="recent", pattern="^(recent|velocity)$"),
    mine: str = Query(default=MINE_ALL, pattern="^(all|only|hide)$"),
    status: str = Query(
        default=STATUS_ALL, pattern="^(all|upcoming|active|finished)$"
    ),
):
    challenges, _has_more = await fetch_challenge_page(
        db,
        current_user_id=current_user_id,
        category=category,
        q=q,
        offset=offset,
        limit=limit,
        sort=sort,
        mine=mine,
        status=status,
    )
    return challenges


@router.get("/{challenge_id}", response_model=ChallengeRead)
async def get_challenge(
    challenge_id: int,
    current_user_id: int | None = Depends(get_optional_user_id),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Challenge).where(
            Challenge.id == challenge_id, challenge_visibility_filter(current_user_id)
        )
    )
    db_challenge = result.scalar_one_or_none()
    if not db_challenge:
        raise HTTPException(status_code=404, detail="Challenge not found")
    return db_challenge


@router.get("/{challenge_id}/stats", response_model=ChallengeStatsRead)
async def get_challenge_stats(
    challenge_id: int,
    current_user_id: int | None = Depends(get_optional_user_id),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Challenge.id).where(
            Challenge.id == challenge_id, challenge_visibility_filter(current_user_id)
        )
    )
    if result.scalar_one_or_none() is None:
        raise HTTPException(status_code=404, detail="Challenge not found")

    stats_result = await db.execute(
        select(ChallengeStats).where(ChallengeStats.challenge_id == challenge_id)
    )
    stats = stats_result.scalar_one_or_none()
    if stats is None:
        raise HTTPException(status_code=404, detail="Stats not found")
    return stats


@router.patch("/{challenge_id}", response_model=ChallengeRead)
async def update_challenge(
    challenge_id: int,
    challenge: ChallengeUpdate,
    current_user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    # Composed with the visibility filter, not a bare id lookup: a challenge
    # the caller cannot even *read* must answer 404 here too, or PATCH becomes
    # an oracle for private challenges that GET already hides. Visible but not
    # owned stays a 403 -- that leaks nothing they could not see anyway.
    result = await db.execute(
        select(Challenge).where(
            Challenge.id == challenge_id,
            challenge_visibility_filter(current_user_id),
        )
    )
    db_challenge = result.scalar_one_or_none()
    if not db_challenge:
        raise HTTPException(status_code=404, detail="Challenge not found")
    if db_challenge.owner_id != current_user_id:
        raise HTTPException(
            status_code=403, detail="Not allowed to edit this challenge"
        )

    updates = challenge.model_dump(exclude_unset=True)

    locked_fields = {"cadence", "goal_amount", "goal_unit"}
    changing_locked = bool(locked_fields & updates.keys())
    reverting_from_public = (
        "visibility" in updates
        and db_challenge.visibility == Visibility.PUBLIC.value
        and updates["visibility"] != Visibility.PUBLIC.value
    )

    if changing_locked or reverting_from_public:
        non_owner_count = await count_non_owner_enrollments(
            db, challenge_id, db_challenge.owner_id
        )
        if changing_locked and non_owner_count > 0:
            raise HTTPException(
                status_code=409,
                detail="Cadence and goal are locked once others have enrolled",
            )
        if reverting_from_public and non_owner_count > 0:
            raise HTTPException(
                status_code=409,
                detail="Cannot leave public while others are enrolled",
            )

    cadence_provided = "cadence" in updates
    updates.pop("cadence", None)
    for field, value in updates.items():
        setattr(db_challenge, field, value)
    if cadence_provided:
        db_challenge.cadence_kind = challenge.cadence.kind
        db_challenge.cadence = challenge.cadence.model_dump(mode="json")

    db_challenge.updated_at = datetime.now(UTC)
    db_challenge.last_modifier_user_id = current_user_id

    await db.commit()
    await db.refresh(db_challenge)
    return db_challenge


@router.delete("/{challenge_id}", status_code=204)
async def delete_challenge(
    challenge_id: int,
    current_user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    # Same 404/403 split as update_challenge above.
    result = await db.execute(
        select(Challenge).where(
            Challenge.id == challenge_id,
            challenge_visibility_filter(current_user_id),
        )
    )
    db_challenge = result.scalar_one_or_none()
    if not db_challenge:
        raise HTTPException(status_code=404, detail="Challenge not found")
    if db_challenge.owner_id != current_user_id:
        raise HTTPException(
            status_code=403, detail="Not allowed to delete this challenge"
        )

    # Hard delete is for "this should never have existed" -- a typo, a
    # duplicate -- and it cascades through every enrollment and check-in on
    # the challenge. Once someone else has joined, that cascade would destroy
    # *their* logged history, so the same threshold that locks cadence/goal
    # (count_non_owner_enrollments) blocks deletion outright. Archiving via
    # PATCH lifecycle_status is the way out of a challenge that has run.
    non_owner_count = await count_non_owner_enrollments(
        db, challenge_id, db_challenge.owner_id
    )
    if non_owner_count > 0:
        raise HTTPException(
            status_code=409,
            detail="Cannot delete a challenge others have joined; archive it instead",
        )

    await db.delete(db_challenge)
    await db.commit()
