import logging
from datetime import UTC, date, datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.auth import get_current_user_id
from app.database import get_db
from app.groups import is_group_member, leaving_is_allowed
from app.identity import asks_the_joiner, resolve_anonymity
from app.logging_config import log_event
from app.models.audit_base import newest_first
from app.models.challenge import Challenge
from app.models.checkin import CheckIn
from app.models.enrollment import Enrollment
from app.models.notification import NotificationKind
from app.models.stats import ChallengeStats
from app.models.user import User
from app.notifications import membership_is_announced, notify
from app.occurrences import derive_state, expected_keys_desc, local_today
from app.routers.challenge import challenge_visibility_filter, resolve_timezone
from app.routers.checkin import occurrence_local_date, parse_cadence
from app.routers.user import DEFAULT_MEMBER_PAGE_SIZE, apply_member_filters
from app.schemas.cadence import RecurringQuotaCadence
from app.schemas.checkin import EnrollmentHistoryItem
from app.schemas.enrollment import (
    EnrollmentAnonymityUpdate,
    EnrollmentCreate,
    EnrollmentRead,
    EnrollmentUpdate,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/enrollments", tags=["enrollments"])


async def fetch_participant_page(
    db: AsyncSession,
    *,
    challenge_id: int,
    q: str | None = None,
    offset: int = 0,
    limit: int = DEFAULT_MEMBER_PAGE_SIZE,
) -> tuple[list[Enrollment], bool]:
    """One page of *who is enrolled in one challenge*, plus whether more remain.

    Query logic lives in the API router and presentation in ``views/``, the
    same split ``fetch_challenge_page`` and ``fetch_member_page`` follow, so
    the panel can never disagree with anything else about what a search
    matches. The search itself is ``apply_member_filters`` with no role cut --
    reused rather than rewritten, so "search spans name and email" stays one
    rule.

    **This is an operator-only read and must stay one.** Profiles in this app
    are own-only (``profile_visibility_filter``), and a participant roster is
    exactly the thing that rule exists to withhold from members. It is
    therefore not exposed as a JSON endpoint and its only caller is the admin
    panel, gated on ``Perm.USER_LIST`` + ``Perm.CHALLENGE_LIST_ALL``; the
    rows it renders link to nobody's profile. Shipping a member-facing
    participant list means loosening ``profile_visibility_filter`` first --
    that one function -- not adding a second door here.

    ``limit + 1`` is fetched and trimmed so "is there another page" costs no
    second COUNT, and the order is ``newest_first(Enrollment)`` like every
    other paginated list in the app -- the id tie-break matters more here
    than anywhere: a challenge seeded or opened to a burst of joins writes
    whole blocks of rows sharing one ``created_at`` second.
    """
    stmt = (
        select(Enrollment)
        .join(User, User.id == Enrollment.user_id)
        .where(Enrollment.challenge_id == challenge_id)
    )
    stmt = apply_member_filters(stmt, q, None)
    stmt = (
        stmt.order_by(*newest_first(Enrollment))
        .offset(offset)
        .limit(limit + 1)
        .options(selectinload(Enrollment.user))
    )
    enrollments = list((await db.execute(stmt)).scalars().all())
    has_more = len(enrollments) > limit
    return enrollments[:limit], has_more


# ---------------------------------------------------------------------------
# The leaderboard
# ---------------------------------------------------------------------------
#
# `fetch_participant_page` above answers "who is in this" for an *operator*.
# This answers "how are we all doing" for the people actually in it, and the
# two are deliberately different queries with different gates: the roster is
# searchable, links to profiles and is admin-only; the leaderboard is neither
# searchable nor linked, and is open to anyone enrolled in that one challenge.
# Its own gate lives at its only caller, `routers/views/challenge.py`.

# The one state that counts. Spelled here rather than reaching for
# `build_my_stats`'s copy so this module needs nothing from a views router.
COMPLETED_STATE = "completed"

# How a leaderboard can be ordered. Two keys because they answer two genuinely
# different questions -- «چقدر انجام داده‌ای» and «چند وقت است که نبریده‌ای» --
# and a habit app that only ever ranks by volume tells a newcomer they can
# never catch up. Exactly the `?sort=` shape the moderation roster uses: one
# query with a swapped leading key, not a second list on a second URL.
LEADERBOARD_COMPLETIONS = "completions"
LEADERBOARD_STREAK = "streak"
LEADERBOARD_SORTS = (LEADERBOARD_COMPLETIONS, LEADERBOARD_STREAK)

DEFAULT_LEADERBOARD_PAGE_SIZE = 20
MAX_LEADERBOARD_PAGE_SIZE = 50


def _completions_expr():
    """Completed check-ins for one enrollment, as a correlated subquery.

    Counted **live off `CheckIns`**, never off `ChallengeStats` or
    `Enrollments.legacy_completed_count` -- the first is a monotonic
    challenge-wide counter and the second is frozen pre-migration data that
    must never surface as a live figure (CLAUDE.md). A subquery rather than a
    GROUP BY join so the statement still yields one row per enrollment and
    `selectinload` keeps working on it.
    """
    return (
        select(func.count(CheckIn.id))
        .where(
            CheckIn.enrollment_id == Enrollment.id,
            CheckIn.state == COMPLETED_STATE,
        )
        .correlate(Enrollment)
        .scalar_subquery()
    )


def leaderboard_score_expr(sort: str):
    """The single column a leaderboard is *ranked* by, for `sort`.

    Ranking reads one key and one only: a rank is the answer to "how many are
    ahead of me", and that question has no answer if the ordering is a tuple
    nobody can see. The other metric still orders rows -- it is the tie-break
    below -- but it does not move anyone's number.

    Streaks come from `Enrollments.current_streak`, which is legitimate stored
    state precisely because it is *recomputed* by `compute_streaks` and never
    incremented.
    """
    if sort == LEADERBOARD_STREAK:
        return Enrollment.current_streak
    return _completions_expr()


def _leaderboard_order(sort: str, completions):
    """Ranking key first, the other metric as a meaningful tie-break, then the
    app's own `newest_first` pair so the order is total.

    That last part is not decoration: an infinite scroller pages by offset, so
    two rows the database is free to return in either order will drop one
    member and repeat another across a page boundary -- and a challenge opened
    to a burst of joins writes whole blocks of rows sharing one `created_at`
    second (CLAUDE.md, List ordering).
    """
    if sort == LEADERBOARD_STREAK:
        leading = (Enrollment.current_streak.desc(), completions.desc())
    else:
        leading = (completions.desc(), Enrollment.current_streak.desc())
    return (*leading, *newest_first(Enrollment))


async def fetch_leaderboard_page(
    db: AsyncSession,
    *,
    challenge_id: int,
    sort: str = LEADERBOARD_COMPLETIONS,
    offset: int = 0,
    limit: int = DEFAULT_LEADERBOARD_PAGE_SIZE,
) -> tuple[list[dict], bool]:
    """One page of the standings, plus whether more remain.

    Query logic in the API router, presentation in ``views/`` -- the same
    split `fetch_challenge_page`, `fetch_member_page` and
    `fetch_participant_page` follow, so the page and its `/fragment` can never
    disagree about what is being ranked.

    Every enrollment is listed, including ones held anonymously: hiding the
    *name* is the promise the challenge made, and dropping the *row* would let
    everyone else derive who is missing. Who the name belongs to is
    `participant_display`'s answer, not this function's.
    """
    completions = _completions_expr().label("completions")
    score = leaderboard_score_expr(sort)
    stmt = (
        select(Enrollment, completions, score.label("score"))
        .where(Enrollment.challenge_id == challenge_id)
        .order_by(*_leaderboard_order(sort, completions))
        .offset(offset)
        .limit(limit + 1)
        .options(selectinload(Enrollment.user))
    )
    rows = (await db.execute(stmt)).all()
    has_more = len(rows) > limit
    return [
        {
            "enrollment": enrollment,
            "completions": completed,
            "streak": enrollment.current_streak or 0,
            "score": score_value,
        }
        for enrollment, completed, score_value in rows[:limit]
    ], has_more


async def leaderboard_rank(
    db: AsyncSession, *, challenge_id: int, sort: str, score: int
) -> int:
    """The rank a participant scoring `score` holds: one more than the number
    of participants strictly ahead of them.

    Competition ranking, so equal scores share a rank -- ranking two people
    with the same eleven check-ins 3rd and 4th invents a difference the data
    does not contain, and whichever of them is shown 4th is being told
    something untrue. It is also the *only* definition under which the number
    on somebody's own row and the number on the «رتبه تو» card above it are
    guaranteed to agree, because both are this one query.
    """
    expr = leaderboard_score_expr(sort)
    stmt = (
        select(func.count())
        .select_from(Enrollment)
        .where(Enrollment.challenge_id == challenge_id, expr > score)
    )
    return int((await db.execute(stmt)).scalar_one()) + 1


async def leaderboard_standing(
    db: AsyncSession, enrollment: Enrollment, sort: str
) -> dict:
    """One participant's own line on the board: their two figures and rank.

    The «رتبه تو» card needs this for a member sitting at position 47, who
    would otherwise have to scroll to find out they are at position 47. It
    goes through `leaderboard_score_expr` and `leaderboard_rank` -- the same
    two functions the list itself ranks with -- so the card and the row it
    points at cannot disagree.
    """
    completions = int(
        (
            await db.execute(
                select(func.count(CheckIn.id)).where(
                    CheckIn.enrollment_id == enrollment.id,
                    CheckIn.state == COMPLETED_STATE,
                )
            )
        ).scalar_one()
    )
    streak = enrollment.current_streak or 0
    score = streak if sort == LEADERBOARD_STREAK else completions
    rank = await leaderboard_rank(
        db,
        challenge_id=enrollment.challenge_id,
        sort=sort,
        score=score,
    )
    return {
        "completions": completions,
        "streak": streak,
        "score": score,
        "rank": rank,
    }


def assign_ranks(rows: list[dict], *, offset: int, first_rank: int) -> list[dict]:
    """Stamp each row of one page with its competition rank.

    A page knows its own rows and its `offset`, but not what came before it,
    so the first row's rank is asked of `leaderboard_rank` and the rest is
    arithmetic: a row whose score differs from the one above it starts a new
    rank at its own global position, and a row that ties inherits. That keeps
    ties shared *across* a page boundary too, which a bare `offset + i + 1`
    silently would not.
    """
    rank = first_rank
    previous = None
    for index, row in enumerate(rows):
        if previous is not None and row["score"] != previous:
            rank = offset + index + 1
        row["rank"] = rank
        previous = row["score"]
    return rows



@router.get("/", response_model=list[EnrollmentRead])
async def list_my_enrollments(
    current_user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Enrollment)
        .where(Enrollment.user_id == current_user_id)
        .order_by(*newest_first(Enrollment))
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
    # The owner comes back with the visibility check rather than in a second
    # query: joining is the event their notification is about, and a lookup
    # made only when someone happens to be enrolling is a lookup that drifts.
    challenge_result = await db.execute(
        select(
            Challenge.owner_id,
            Challenge.visibility,
            Challenge.identity_mode,
            Challenge.group_id,
        ).where(
            Challenge.id == challenge_id,
            challenge_visibility_filter(current_user_id),
        )
    )
    # Visibility and identity_mode come back on the same read the ownership
    # check already costs: whether the owner hears about this enrolment
    # depends on the first (see `membership_is_announced`) and whether they
    # are told *who* on the second, and a second query for columns that are
    # already in hand is a query that eventually disagrees with this one.
    challenge_row = challenge_result.one_or_none()
    if challenge_row is None:
        raise HTTPException(status_code=404, detail="Challenge not found")
    owner_id, visibility, identity_mode, group_id = challenge_row

    result = await db.execute(
        select(Enrollment).where(
            Enrollment.challenge_id == challenge_id,
            Enrollment.user_id == current_user_id,
        )
    )
    if result.scalar_one_or_none():
        raise HTTPException(status_code=409, detail="Already enrolled")

    tz = resolve_timezone(payload.timezone if payload else None)
    # The write boundary for anonymity, and the only one: the challenge's
    # policy decides, the body is consulted only where that policy defers to
    # it, and what lands on the row is the resolved answer. See
    # `app/identity.py` on why the answer is stored rather than re-derived.
    is_anonymous = resolve_anonymity(
        identity_mode, payload.is_anonymous if payload else False
    )
    enrollment = Enrollment(
        challenge_id=challenge_id,
        user_id=current_user_id,
        timezone=tz,
        start_date=local_today(tz, datetime.now(UTC)),
        is_anonymous=is_anonymous,
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

    # Added to *this* transaction, not committed separately: an enrolment
    # that rolls back must not leave behind a notification saying it
    # happened. `notify` drops this itself when the owner is the one
    # enrolling -- which is exactly what the creator's own D1 auto-enrolment
    # does -- so there is no condition to remember here.
    if membership_is_announced(visibility):
        await notify(
            db,
            user_id=owner_id,
            kind=NotificationKind.ENROLLMENT_JOINED,
            actor_user_id=current_user_id,
            challenge_id=challenge_id,
            group_id=group_id,
            anonymous_actor=is_anonymous,
        )

    await db.commit()
    await db.refresh(enrollment)
    log_event(
        logger,
        "enrollment.joined",
        challenge_id=challenge_id,
        enrollment_id=enrollment.id,
        is_anonymous=is_anonymous,
        group_id=group_id,
    )
    return enrollment


@router.patch("/{challenge_id}/anonymity", response_model=EnrollmentRead)
async def update_my_anonymity(
    challenge_id: int,
    update: EnrollmentAnonymityUpdate,
    current_user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    """Change your mind about being named -- where the challenge allows it.

    The second and last write boundary for anonymity, and it goes through the
    same `resolve_anonymity` the first one does: the challenge's policy
    decides, and this body is consulted only where the policy defers to it.
    A challenge that answers for everyone is refused with **409** rather than
    silently ignoring the request, because unlike a *join* -- where "hide me"
    is simply not the question that challenge asks -- this request has no
    other purpose, and answering 201-with-nothing-changed would leave the
    member believing something the row does not say.

    It exists for the group case: an administrator enrolled these people, so
    they were never asked, and `resolve_assigned_anonymity` stored the
    private answer on their behalf. This is how they give the other one. It
    is keyed on the session user like every other route here -- there is no
    client-supplied `user_id` in this module.
    """
    enrollment = (
        await db.execute(
            select(Enrollment).where(
                Enrollment.challenge_id == challenge_id,
                Enrollment.user_id == current_user_id,
            )
        )
    ).scalar_one_or_none()
    if not enrollment:
        raise HTTPException(status_code=404, detail="Enrollment not found")

    identity_mode = (
        await db.execute(
            select(Challenge.identity_mode).where(Challenge.id == challenge_id)
        )
    ).scalar_one_or_none()
    if not asks_the_joiner(identity_mode):
        raise HTTPException(
            status_code=409,
            detail="This challenge decides anonymity for everyone",
        )

    enrollment.is_anonymous = resolve_anonymity(identity_mode, update.is_anonymous)
    enrollment.updated_at = datetime.now(UTC)
    enrollment.last_modifier_user_id = current_user_id
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

    # Read before the delete, so the row is still there to be asked about --
    # and no visibility clause, because being enrolled is already the
    # strongest proof there is that this challenge is reachable.
    challenge = (
        await db.execute(select(Challenge).where(Challenge.id == challenge_id))
    ).scalar_one_or_none()
    owner_id = challenge.owner_id if challenge else None
    visibility = challenge.visibility if challenge else None

    # A mandatory group challenge cannot be left while you are still in the
    # group that set it -- the whole of `ParticipationMode`, asked through
    # `leaving_is_allowed` so the refusal here and the button
    # challenge-detail decides whether to draw are the same question. The
    # membership is what the obligation hangs on, not the person: somebody
    # who has left the group falls straight through this and may leave
    # normally, keeping everything they logged.
    if challenge is not None and challenge.group_id is not None:
        still_in_group = await is_group_member(
            db, challenge.group_id, current_user_id
        )
        if not leaving_is_allowed(challenge, still_in_group):
            raise HTTPException(
                status_code=403,
                detail="This challenge is required while you are in the group",
            )

    # Read off the row while it still exists -- the departure notification is
    # raised after the delete, and the member's choice goes with the
    # enrollment. This is the case that makes anonymising a notification a
    # write-time decision rather than a render-time one: there would be
    # nothing left to ask by the time the feed rendered.
    was_anonymous = enrollment.is_anonymous

    await db.delete(enrollment)

    stats_result = await db.execute(
        select(ChallengeStats).where(ChallengeStats.challenge_id == challenge_id)
    )
    stats = stats_result.scalar_one_or_none()
    if stats is not None and stats.participant_count:
        stats.participant_count = max(stats.participant_count - 1, 0)

    # Someone leaving is the other half of the pair the owner is told about:
    # a feed that only ever reports growth is a feed that flatters -- and it
    # is silenced on a public challenge by the same rule that silences the
    # join, so the pair cannot come apart. `notify` drops it when the owner
    # leaves their own challenge.
    if owner_id is not None and membership_is_announced(visibility):
        await notify(
            db,
            user_id=owner_id,
            kind=NotificationKind.ENROLLMENT_LEFT,
            actor_user_id=current_user_id,
            challenge_id=challenge_id,
            group_id=challenge.group_id if challenge else None,
            anonymous_actor=was_anonymous,
        )

    await db.commit()
    # Paired with `enrollment.joined` on purpose: churn is only visible when
    # both halves are logged, exactly as the owner's notifications are.
    log_event(logger, "enrollment.left", challenge_id=challenge_id)


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
    the per-occurrence `writable`/`editable` flags its calendar needs to offer
    an action. Keep both in sync by hand if the occurrence-state rules here
    change.
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
