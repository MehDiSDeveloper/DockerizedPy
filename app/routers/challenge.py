import logging
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import Select, and_, func, not_, or_, select, true
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_user, get_optional_user_id
from app.database import get_db
from app.groups import (
    assign_participants,
    group_member_ids,
    group_scope_filter,
    standing_in,
)
from app.logging_config import log_event
from app.media import discard, discard_replaced
from app.models.audit_base import newest_first
from app.models.challenge import (
    Challenge,
    ChallengeCategory,
    GroupAudience,
    LifecycleStatus,
    Visibility,
)
from app.models.checkin import CheckIn
from app.models.enrollment import ChallengeRole, Enrollment
from app.models.group import Group
from app.models.notification import NotificationKind
from app.models.stats import ChallengeStats
from app.models.user import User
from app.notifications import notify_many
from app.occurrences import local_today
from app.permissions import Perm, can
from app.roadmaps import roadmap_scope_filter, skip_steps_for_challenge
from app.schemas.challenge import ChallengeCreate, ChallengeRead, ChallengeUpdate
from app.schemas.checkin import ChallengeStatsRead

VELOCITY_WINDOW_DAYS = 7

logger = logging.getLogger(__name__)

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


# Both filters below are `group_scope_filter AND <the rule that already
# existed>`. The group gate is a *conjunct*, never a replacement, and that
# single line is how a group's challenges reach its members and nobody else:
#
#   * outside a group (`group_id IS NULL`) the gate is a no-op and the app
#     behaves exactly as it did;
#   * inside one, the existing three-way rule runs unchanged, so `public`
#     means public *to the group* and `private` means only its participants.
#
# One rule, scoped -- rather than a second rule that would have to be kept in
# agreement with the first. See `app/groups.py` for the whole argument,
# including why an existing enrollment is a key of its own.
#
# `roadmap_scope_filter` is the other shape a scope can take, and it is worth
# naming the difference: a group *narrows* what somebody may see, so it is a
# conjunct; a roadmap *widens* it for the person walking it, so it is one more
# leg of the disjunction below, beside "public", "mine" and "enrolled".
# Walking a course is a key to the challenges it is made of -- you will be
# enrolled in them as you reach them, and the whole point of the screen is
# that you can read what is ahead of you before it opens. It is deliberately
# absent from `listing_visibility_filter`: a private challenge two steps ahead
# is reachable at its own URL and has no business in the app-wide list of
# things to discover, which is exactly what `unlisted` already means here.
def challenge_visibility_filter(user_id: int | None):
    """A challenge is visible if it's public, unlisted, the requester owns it,
    is enrolled in it, or is walking a roadmap that has it as a step -- and,
    if it belongs to a group, only to that group (`group_scope_filter`)."""
    if user_id is None:
        return and_(
            group_scope_filter(None),
            Challenge.visibility == Visibility.PUBLIC.value,
        )
    enrolled_challenge_ids = select(Enrollment.challenge_id).where(
        Enrollment.user_id == user_id
    )
    return and_(
        group_scope_filter(user_id),
        or_(
            Challenge.visibility.in_(
                [Visibility.PUBLIC.value, Visibility.UNLISTED.value]
            ),
            Challenge.owner_id == user_id,
            Challenge.id.in_(enrolled_challenge_ids),
            roadmap_scope_filter(user_id),
        ),
    )


def listing_visibility_filter(user_id: int | None):
    """Like challenge_visibility_filter, but excludes unlisted challenges (D11):
    they're reachable at their own URL but never appear in a listing."""
    if user_id is None:
        return and_(
            group_scope_filter(None),
            Challenge.visibility == Visibility.PUBLIC.value,
        )
    enrolled_challenge_ids = select(Enrollment.challenge_id).where(
        Enrollment.user_id == user_id
    )
    return and_(
        group_scope_filter(user_id),
        or_(
            Challenge.visibility == Visibility.PUBLIC.value,
            Challenge.owner_id == user_id,
            Challenge.id.in_(enrolled_challenge_ids),
        ),
    )


# The fields moderation is allowed to touch. An admin changes *what state a
# challenge is in* -- take it off the floor, hide it -- and nothing about what
# it says: title, rules, cadence and goal are authorship, and an edit to them
# from a non-owner would be invisible in the record. See app/permissions.py.
MODERATABLE_FIELDS = frozenset({"lifecycle_status", "visibility"})


def reachable_for(user: User | None):
    """Which challenges a mutation by ``user`` may even *find*.

    Everyone is narrowed to what they could already read, so PATCH/DELETE can
    never confirm a private challenge that GET hides -- the 404-vs-403 split
    in CLAUDE.md. A moderator holds `CHALLENGE_LIST_ALL`, which is precisely
    "may see every challenge", so for them the clause is a no-op and an
    unreachable row stops being a thing that exists.
    """
    if can(user, Perm.CHALLENGE_LIST_ALL):
        return true()
    return challenge_visibility_filter(user.id if user else None)


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


# The three orderings a challenge list can be read in. "recent" is the
# app-wide rule (`newest_first`, D-list-ordering); "velocity" is Discover's
# "what is people doing right now"; "members" is the moderation panel's
# "which challenges actually carry the membership". All three fall back to
# `newest_first` for their ties, so paging stays stable inside a block of
# equal leading keys.
SORT_RECENT = "recent"
SORT_VELOCITY = "velocity"
SORT_MEMBERS = "members"
SORT_VALUES = (SORT_RECENT, SORT_VELOCITY, SORT_MEMBERS)
SORT_PATTERN = "^({})$".format("|".join(SORT_VALUES))


def _member_count() -> Select:
    """Enrollments per challenge, correlated to the outer Challenges query.

    Counted live off `Enrollments` rather than read from
    `ChallengeStats.participant_count`: that column is a monotonic counter
    that never comes down when someone unenrols, so ordering by it would rank
    a challenge everyone has left above one they are still in. The same
    reason Discover computes its velocity here instead of caching it.
    """
    return (
        select(func.count(Enrollment.id))
        .where(Enrollment.challenge_id == Challenge.id)
        .correlate(Challenge)
        .scalar_subquery()
    )


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
    sort: str = SORT_RECENT,
    mine: str | None = None,
    status: str | None = None,
    scope_all: bool = False,
    group_id: int | None = None,
    options=(),
) -> tuple[list[Challenge], bool]:
    """Fetch one page of visible challenges plus whether more pages remain.

    `group_id` narrows to one group's challenges and is the group page; it
    is applied *on top of* the visibility clause, never instead of it.

    `scope_all` drops the visibility narrowing entirely and is the moderation
    roster -- the challenge-side twin of `GET /users/` ignoring
    `profile_visibility_filter`. It is a parameter rather than a second query
    builder so the admin panel and the public list can never disagree about
    what a search or a status filter matches; the caller must have checked
    `Perm.CHALLENGE_LIST_ALL` first.
    """
    stmt = select(Challenge)
    if not scope_all:
        stmt = stmt.where(listing_visibility_filter(current_user_id))
    if group_id is not None:
        # Narrowing, never widening: the visibility clause above still runs,
        # so asking for a group you are not in returns nothing rather than
        # that group's challenges. The group page passes this instead of
        # building its own query, so «چالش‌های این گروه» and the app-wide
        # list can never disagree about what a search matches.
        stmt = stmt.where(Challenge.group_id == group_id)
    stmt = apply_challenge_filters(stmt, category, q)
    mine_clause = mine_filter(current_user_id, mine)
    if mine_clause is not None:
        stmt = stmt.where(mine_clause)
    status_clause = status_filter(status)
    if status_clause is not None:
        stmt = stmt.where(status_clause)
    if sort == SORT_VELOCITY:
        stmt = stmt.order_by(_velocity_count().desc(), *newest_first(Challenge))
    elif sort == SORT_MEMBERS:
        stmt = stmt.order_by(_member_count().desc(), *newest_first(Challenge))
    else:
        stmt = stmt.order_by(*newest_first(Challenge))
    stmt = stmt.offset(offset).limit(limit + 1)
    for opt in options:
        stmt = stmt.options(opt)
    result = await db.execute(stmt)
    challenges = list(result.scalars().all())
    has_more = len(challenges) > limit
    return challenges[:limit], has_more


# ==========================================================================
# Group challenges
# ==========================================================================
# Creating a challenge under a group is the one moment the group axis and
# the challenge axis touch, and it is deliberately the *only* one: the check
# below asks `Perm.GROUP_CREATE_CHALLENGE` in that group, and from then on
# the challenge has an owner like any other and every later question about
# it is answered by `challenge_role`. A group administrator does not become
# the editor of a challenge somebody else wrote under their group, and the
# app-wide operator does not become an administrator of the group -- see
# `app/permissions.py`.
#
# Both create paths (this router and `POST /views/challenges/create`) call
# these two helpers rather than repeating the rules, because CLAUDE.md's
# note about the duplicated create logic is exactly the place a permission
# check goes missing from one copy.


async def resolve_group_for_create(
    db: AsyncSession, group_id: int | None, user: User
) -> Group | None:
    """The group a new challenge is being created under, or None.

    404 for a group the caller is not in -- the group visibility rule, so a
    non-member cannot learn a group id is real by trying to post to it -- and
    403 for a member who is not allowed to create there, which leaks nothing
    they could not already see from inside the group.
    """
    if group_id is None:
        return None
    group = (
        await db.execute(select(Group).where(Group.id == group_id))
    ).scalar_one_or_none()
    # The caller's standing here, resolved across the group's ancestry, so an
    # administrator of the company may put a challenge in one of its
    # departments -- the same answer `load_group` gives the group screens.
    standing = (
        await standing_in(db, group, user.id) if group is not None else None
    )
    if group is None or (standing is None and group.owner_id != user.id):
        raise HTTPException(status_code=404, detail="Group not found")
    if not can(
        user, Perm.GROUP_CREATE_CHALLENGE, group=group, membership=standing
    ):
        raise HTTPException(
            status_code=403, detail="Not allowed to create a challenge here"
        )
    return group


async def seed_group_participants(
    db: AsyncSession,
    *,
    challenge: Challenge,
    group: Group | None,
    member_ids: list[int],
    actor_user_id: int,
    timezone: str,
) -> None:
    """Enrol the audience a group challenge was created for.

    `all` is read off the group's membership rather than off the request, so
    the list cannot be stale by the time it is submitted -- and
    `apply_standing_audience` re-reads it for everybody who joins later, which
    is what makes «همه اعضا» a standing answer.

    `selected` is intersected with the membership before anything is written.
    A body naming somebody outside the group would otherwise enrol a stranger
    into a challenge they cannot even see -- the one place a group challenge
    could acquire a participant who is not in the group, closed here rather
    than trusted to the client that built the list.
    """
    if group is None:
        return
    # The group's *people*, which with nested groups means everybody in its
    # subtree (`group_member_ids`): a company-wide challenge reaches the
    # departments, or «همه» quietly means "the few rows on the parent".
    members = set(
        (await db.execute(group_member_ids(group.id))).scalars().all()
    )
    if challenge.group_audience == GroupAudience.ALL.value:
        wanted = set(members)
    else:
        wanted = members & set(member_ids)
    # The creator already has the owner enrolment written by the caller.
    wanted = wanted - {actor_user_id}
    if wanted:
        await assign_participants(
            db,
            challenge=challenge,
            user_ids=sorted(wanted),
            actor_user_id=actor_user_id,
            timezone=timezone,
        )


async def create_challenge_record(
    db: AsyncSession,
    *,
    payload: ChallengeCreate,
    current_user: User,
) -> Challenge:
    """Write a challenge, its creator's owner enrolment, its stats row and the
    group audience it was created for.

    The one implementation both front doors call. It used to be two copies,
    and they had already drifted -- the SSR half was writing the creator's
    enrolment without `is_anonymous=False`, so an `anonymous` challenge could
    hide its own author. See CLAUDE.md on duplicated create logic.
    """
    current_user_id = current_user.id
    # Checked before anything is written: a challenge under a group the
    # caller may not create in must not exist even briefly.
    group = await resolve_group_for_create(db, payload.group_id, current_user)

    data = payload.model_dump(exclude={"cadence", "timezone", "member_ids"})
    db_challenge = Challenge(**data)
    db_challenge.cadence_kind = payload.cadence.kind
    db_challenge.cadence = payload.cadence.model_dump(mode="json")
    db_challenge.owner_id = current_user_id
    db_challenge.last_modifier_user_id = current_user_id
    db.add(db_challenge)
    await db.flush()

    # D1: the creator is auto-enrolled -- and that enrolment is the one that
    # carries `owner`, so the row states the role instead of every caller
    # re-deriving it from `owner_id`.
    tz = resolve_timezone(payload.timezone)
    enrollment = Enrollment(
        challenge_id=db_challenge.id,
        user_id=current_user_id,
        timezone=tz,
        start_date=local_today(tz, datetime.now(UTC)),
        role=ChallengeRole.OWNER.value,
        # Named in every mode, including `anonymous`: anonymity here is about
        # participation, and challenge-detail names the creator as «سازنده»
        # regardless -- a challenge whose content nobody is accountable for
        # is a moderation problem, not a privacy feature. See
        # `app/models/challenge.py::IdentityMode`. `resolve_anonymity` is
        # deliberately not consulted for this row.
        is_anonymous=False,
    )
    db.add(enrollment)
    db.add(ChallengeStats(challenge_id=db_challenge.id, participant_count=1))
    await db.flush()

    await seed_group_participants(
        db,
        challenge=db_challenge,
        group=group,
        member_ids=payload.member_ids,
        actor_user_id=current_user_id,
        timezone=tz,
    )

    # Logged here rather than in each caller: this function is the single
    # funnel both front doors go through, so a create can never be made
    # without a line. The id is only assigned on flush, which
    # `create_challenge_record` has already done.
    log_event(
        logger,
        "challenge.created",
        challenge_id=db_challenge.id,
        cadence=db_challenge.cadence_kind,
        visibility=db_challenge.visibility,
        group_id=db_challenge.group_id,
    )
    return db_challenge


@router.post("/", response_model=ChallengeRead, status_code=201)
async def create_challenge(
    challenge: ChallengeCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    db_challenge = await create_challenge_record(
        db, payload=challenge, current_user=current_user
    )
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
    sort: str = Query(default=SORT_RECENT, pattern=SORT_PATTERN),
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


# Which lifecycle states are worth telling participants about, and what each
# arrival is called. Keyed on the *new* state alone rather than on the pair:
# what a member needs to know is that the challenge they are in stopped or
# started counting, not which of the other two states it came from. `draft` is
# absent deliberately -- a challenge moving back to draft has no participants
# to tell that its owner has not already told.
LIFECYCLE_NOTIFICATIONS = {
    LifecycleStatus.ARCHIVED.value: NotificationKind.CHALLENGE_ARCHIVED,
    LifecycleStatus.ACTIVE.value: NotificationKind.CHALLENGE_ACTIVATED,
}


async def notify_lifecycle_change(
    db: AsyncSession,
    *,
    challenge: Challenge,
    previous_lifecycle: str,
    actor_user_id: int,
) -> None:
    """Tell everyone enrolled that this challenge changed state.

    The one broadcast in the app, and the reason it exists: a lifecycle
    change is the only edit to a challenge that silently changes what the
    app expects of *other* people -- an archived challenge stops producing
    occurrences, so a member who is not told simply watches their streak
    stop. Every other field a PATCH can carry is visible on the page they
    would already be looking at.

    It reads participant ids only, not rows -- a broadcast has no use for
    anything else -- and adds to the caller's transaction without
    committing, so the announcement and the change land together or not at
    all. `notify` drops the actor's own copy, which covers both the owner
    archiving their own challenge and a moderator acting on one they happen
    to be enrolled in.
    """
    kind = LIFECYCLE_NOTIFICATIONS.get(challenge.lifecycle_status)
    if kind is None or challenge.lifecycle_status == previous_lifecycle:
        return
    participant_ids = list(
        (
            await db.execute(
                select(Enrollment.user_id).where(
                    Enrollment.challenge_id == challenge.id
                )
            )
        )
        .scalars()
        .all()
    )
    await notify_many(
        db,
        user_ids=participant_ids,
        kind=kind,
        actor_user_id=actor_user_id,
        challenge_id=challenge.id,
        group_id=challenge.group_id,
    )


@router.patch("/{challenge_id}", response_model=ChallengeRead)
async def update_challenge(
    challenge_id: int,
    challenge: ChallengeUpdate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    current_user_id = current_user.id
    # Composed with the visibility filter, not a bare id lookup: a challenge
    # the caller cannot even *read* must answer 404 here too, or PATCH becomes
    # an oracle for private challenges that GET already hides. Visible but not
    # owned stays a 403 -- that leaks nothing they could not see anyway.
    result = await db.execute(
        select(Challenge).where(
            Challenge.id == challenge_id,
            reachable_for(current_user),
        )
    )
    db_challenge = result.scalar_one_or_none()
    if not db_challenge:
        raise HTTPException(status_code=404, detail="Challenge not found")

    updates = challenge.model_dump(exclude_unset=True)

    # Editing is per-challenge, so being an app-wide admin grants nothing
    # here -- see app/permissions.py on why the two role axes stay apart. A
    # moderator gets past this only for the two state fields, and only when
    # the request touches nothing else: one body that renames *and* archives
    # is refused whole rather than half-applied.
    if not can(current_user, Perm.CHALLENGE_EDIT, challenge=db_challenge) and not (
        can(current_user, Perm.CHALLENGE_MODERATE)
        and updates.keys() <= MODERATABLE_FIELDS
    ):
        raise HTTPException(
            status_code=403, detail="Not allowed to edit this challenge"
        )

    # `identity_mode` is locked by the same count for a different reason:
    # loosening it unmasks people who joined on the promise of anonymity, and
    # tightening it hides participants the others already know. Either way the
    # deal a member signed up to would change under them, which is exactly
    # what a stored-and-never-re-derived `Enrollment.is_anonymous` is designed
    # to prevent (see app/identity.py). Before anyone else joins there is
    # nothing to break, so an owner can still fix a mistake -- and the only
    # enrolment that exists then is their own, which is named in every mode.
    locked_fields = {"cadence", "goal_amount", "goal_unit", "identity_mode"}
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
                detail=(
                    "Cadence, goal and identity mode are locked once others "
                    "have enrolled"
                ),
            )
        if reverting_from_public and non_owner_count > 0:
            raise HTTPException(
                status_code=409,
                detail="Cannot leave public while others are enrolled",
            )

    # Captured before the fields are written: the notification below is about
    # a *transition*, and after the loop there is nothing left to compare to.
    previous_lifecycle = db_challenge.lifecycle_status

    # The files behind the pictures this PATCH is about to replace. Captured
    # before the write, deleted after the commit: a picture that outlives the
    # row pointing at it is bytes nobody can ever reach again, and this is
    # where the app has the old value in hand (see `app/media.py` on why
    # there is no sweep).
    replaced_media = {
        field: getattr(db_challenge, field)
        for field in ("image_square", "image_tall")
        if field in updates
    }

    cadence_provided = "cadence" in updates
    updates.pop("cadence", None)
    for field, value in updates.items():
        setattr(db_challenge, field, value)
    if cadence_provided:
        db_challenge.cadence_kind = challenge.cadence.kind
        db_challenge.cadence = challenge.cadence.model_dump(mode="json")

    db_challenge.updated_at = datetime.now(UTC)
    db_challenge.last_modifier_user_id = current_user_id

    await notify_lifecycle_change(
        db,
        challenge=db_challenge,
        previous_lifecycle=previous_lifecycle,
        actor_user_id=current_user_id,
    )

    # An archived challenge produces no more occurrences, so any roadmap step
    # pointing at it has become impossible to finish -- and a `required` step
    # nobody can finish strands everybody behind it, for a decision taken by
    # somebody who may not even know the roadmap exists. The step is passed
    # over and its builder is told. Here rather than inside
    # `notify_lifecycle_change` because it is a *state change*, not an
    # announcement, and it is guarded by the same "only on a real transition"
    # test that function makes.
    if (
        db_challenge.lifecycle_status == LifecycleStatus.ARCHIVED.value
        and previous_lifecycle != LifecycleStatus.ARCHIVED.value
    ):
        await skip_steps_for_challenge(
            db,
            challenge=db_challenge,
            actor_user_id=current_user_id,
            timezone=DEFAULT_TIMEZONE,
        )

    await db.commit()
    await db.refresh(db_challenge)
    for field, previous in replaced_media.items():
        discard_replaced(previous, getattr(db_challenge, field))
    log_event(
        logger,
        "challenge.updated",
        challenge_id=db_challenge.id,
        fields=sorted(updates),
        lifecycle=db_challenge.lifecycle_status,
        # An operator changing somebody else's challenge is the line worth
        # finding later; `moderated` is what makes that one query.
        moderated=db_challenge.owner_id != current_user_id,
    )
    return db_challenge


@router.delete("/{challenge_id}", status_code=204)
async def delete_challenge(
    challenge_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    # Same 404/403 split as update_challenge above.
    result = await db.execute(
        select(Challenge).where(
            Challenge.id == challenge_id,
            reachable_for(current_user),
        )
    )
    db_challenge = result.scalar_one_or_none()
    if not db_challenge:
        raise HTTPException(status_code=404, detail="Challenge not found")
    # The owner deletes their own; a moderator removes anyone's. The 409
    # below then applies to both alike -- see its comment.
    if not (
        can(current_user, Perm.CHALLENGE_DELETE, challenge=db_challenge)
        or can(current_user, Perm.CHALLENGE_DELETE_ANY)
    ):
        raise HTTPException(
            status_code=403, detail="Not allowed to delete this challenge"
        )

    # Hard delete is for "this should never have existed" -- a typo, a
    # duplicate -- and it cascades through every enrollment and check-in on
    # the challenge. Once someone else has joined, that cascade would destroy
    # *their* logged history, so the same threshold that locks cadence/goal
    # (count_non_owner_enrollments) blocks deletion outright. Archiving via
    # PATCH lifecycle_status is the way out of a challenge that has run -- and
    # that holds for a moderator too: an operator with a reason to remove a
    # busy challenge still has archiving, which takes it off every screen
    # without erasing what its participants logged.
    non_owner_count = await count_non_owner_enrollments(
        db, challenge_id, db_challenge.owner_id
    )
    if non_owner_count > 0:
        raise HTTPException(
            status_code=409,
            detail="Cannot delete a challenge others have joined; archive it instead",
        )

    owner_id = db_challenge.owner_id
    # Its pictures go with it. The cascades take the child *rows*; nothing on
    # disk is a row, so this is the one place the files are named.
    orphaned_media = (db_challenge.image_square, db_challenge.image_tall)
    await db.delete(db_challenge)
    await db.commit()
    for key in orphaned_media:
        discard(key)
    # A destructive, irreversible act -- the one challenge event that has to
    # survive in the log after the row itself is gone.
    log_event(
        logger,
        "challenge.deleted",
        level=logging.WARNING,
        challenge_id=challenge_id,
        moderated=owner_id != current_user.id,
    )
