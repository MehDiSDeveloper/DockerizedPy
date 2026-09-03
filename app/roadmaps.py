"""«مسیر» as a predicate, and the engine that opens the next step.

The shape of this module is the shape of ``app/groups.py``: domain logic both
front doors read, with no router and no template in it, so the rules below are
stated once and imported rather than restated at each call site.

**Reachability is a composed clause, twice over.**
:func:`roadmap_visibility_filter` is the fourth such filter in the app, after
``challenge_visibility_filter``, ``profile_visibility_filter`` and
``group_visibility_filter``, and it follows their rule exactly -- ``.where()``-ed
into the statement so a miss falls out as "no such row" and answers **404**.
:func:`roadmap_scope_filter` is the other direction: **one more leg on the
challenge visibility rule**, so that walking a course is a key to the
challenges in it.

That second one is the security question the whole feature turns on, so it is
worth stating plainly: **putting a private challenge into a public roadmap does
not publish it.** The roadmap's visibility and the challenge's are two separate
answers and neither rewrites the other. What reaches through is *roadmap
enrollment* -- somebody actually walking the course -- which is the same trade
``group_scope_filter`` makes with its third leg: you were let into the room, so
the rows in the room are readable. Somebody merely *looking* at a public
roadmap gets the steps whose challenges they could already see, and a
placeholder for the rest.

**The next step opens by recomputation, never by a counter.**
:func:`refresh_progress` re-reads live ``CheckIns`` and re-decides every step
each time it runs, the same rule ``compute_streaks`` follows -- there is no
``+= 1`` anywhere here, and running it twice is running it once. The only thing
it *writes* that it could not derive again is ``unlocked_at``: when a course
reached a step has no other source in the app, and it is the line a ``count``
rule counts after and the clock a ``duration`` rule counts from.

**A step is reached by writing an ordinary enrollment.** Nothing about the
occurrence engine, «امروز», streaks, the leaderboard or the backfill window
knows that roadmaps exist -- which is also why a locked step cannot show up in
«امروز»: it has no enrollment at all. That is an absence, not a filter, and
``routers/today.py`` is untouched by this feature.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from pydantic import TypeAdapter, ValidationError
from sqlalchemy import and_, false, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.groups import assign_participants, group_ids_for
from app.models.challenge import CadenceKind, Challenge, Visibility
from app.models.checkin import CheckIn
from app.models.enrollment import Enrollment
from app.models.notification import NotificationKind
from app.models.roadmap import (
    Roadmap,
    RoadmapEnrollment,
    RoadmapEnrollmentStatus,
    RoadmapStep,
    RoadmapStepProgress,
    RoadmapStepState,
)
from app.notifications import notify
from app.schemas.completion import (
    AmountRule,
    ChallengeFinishedRule,
    CompletionUnion,
    CountRule,
    DurationRule,
    ManualRule,
    StreakRule,
)

#: What a check-in must be to count towards a step. `skipped` is a real state
#: a member records, and it is deliberately not progress.
COMPLETED_STATE = "completed"


# ---------------------------------------------------------------------------
# Reachability
# ---------------------------------------------------------------------------


def my_roadmap_ids(user_id: int):
    """The roadmaps this member is walking, as a subquery.

    A subquery and not a loaded list, for ``group_ids_for``'s reason: every
    caller needs it *inside* a ``WHERE``.
    """
    return select(RoadmapEnrollment.roadmap_id).where(
        RoadmapEnrollment.user_id == user_id
    )


def roadmap_scope_filter(user_id: int | None):
    """The roadmap leg of the **challenge** visibility rule.

    ``AND``-ed nowhere: this is an extra ``OR`` leg inside
    ``challenge_visibility_filter``'s disjunction, alongside "public", "mine"
    and "enrolled". A group narrows what you may see and a roadmap widens it,
    so the two compose in opposite directions -- which is why
    ``group_scope_filter`` is a conjunct and this is not.

    **Walking the course is the key, not looking at it.** A roadmap you have
    merely opened grants nothing; one you have joined lets you read the
    challenges it is made of, because you are going to be enrolled in them
    anyway and the whole point of the screen is that you can see what is ahead
    of you before it opens. A signed-out visitor has no enrollments, so this
    is ``false`` for them and the rule is exactly what it was.

    It is left off ``listing_visibility_filter`` deliberately: a private
    challenge two steps ahead of you is reachable at its own URL and does not
    belong in the app-wide list of things to discover. That is precisely what
    ``unlisted`` already means here.
    """
    if user_id is None:
        return false()
    return Challenge.id.in_(
        select(RoadmapStep.challenge_id).where(
            RoadmapStep.removed_at.is_(None),
            RoadmapStep.roadmap_id.in_(my_roadmap_ids(user_id)),
        )
    )


def roadmap_visibility_filter(user_id: int | None, *, listing: bool = False):
    """Which roadmaps a caller may reach at all.

    The same three-way rule a challenge has -- public, mine, or one I am
    walking -- with the group gate ``AND``-ed on top when the roadmap belongs
    to one, so a group's course is that group's. ``listing=True`` drops
    ``unlisted`` for the reason ``listing_visibility_filter`` does: an
    unlisted roadmap is reachable at its own URL (which is what an invite link
    hands out) and never appears in a list.

    One function with a flag rather than two near-identical ones: the
    difference really is a single value in a single ``IN``, and two copies of
    a five-line clause is two places for the group gate to be forgotten.
    """
    if user_id is None:
        return and_(
            Roadmap.group_id.is_(None),
            Roadmap.visibility == Visibility.PUBLIC.value,
        )
    mine = my_roadmap_ids(user_id)
    open_states = (
        [Visibility.PUBLIC.value]
        if listing
        else [Visibility.PUBLIC.value, Visibility.UNLISTED.value]
    )
    return and_(
        or_(
            Roadmap.group_id.is_(None),
            Roadmap.group_id.in_(group_ids_for(user_id)),
            # Already walking it is a key of its own -- somebody who left the
            # group keeps the course they started, exactly as they keep the
            # challenges they joined (`group_scope_filter`).
            Roadmap.id.in_(mine),
        ),
        or_(
            Roadmap.visibility.in_(open_states),
            Roadmap.owner_id == user_id,
            Roadmap.id.in_(mine),
        ),
    )


async def count_non_owner_enrollments(
    db: AsyncSession, roadmap_id: int, owner_id: int
) -> int:
    """How many people other than the builder are walking this.

    The roadmap-side twin of ``count_non_owner_enrollments`` in
    ``routers/challenge.py``, and it guards the same kind of thing: once
    somebody else is mid-course, the structure they agreed to walk stops being
    the builder's to rearrange. See :func:`structure_is_locked`.
    """
    return (
        await db.execute(
            select(func.count())
            .select_from(RoadmapEnrollment)
            .where(
                RoadmapEnrollment.roadmap_id == roadmap_id,
                RoadmapEnrollment.user_id != owner_id,
            )
        )
    ).scalar_one()


async def structure_is_locked(db: AsyncSession, roadmap: Roadmap) -> bool:
    """Is the course's shape frozen?

    Yes, from the moment the first person other than the builder joins -- the
    same threshold that locks a challenge's ``cadence``/``goal_*``/
    ``identity_mode``, and for the same reason: forty people are halfway
    through a sequence they read before starting, and reordering it under them
    changes the deal. **Appending to the end stays free** (nobody has reached
    there yet), and removing a step stays free because removal is a stamp that
    can only ever *unblock* somebody.
    """
    return await count_non_owner_enrollments(db, roadmap.id, roadmap.owner_id) > 0


# ---------------------------------------------------------------------------
# The completion rule: parsing, and what a challenge can carry
# ---------------------------------------------------------------------------

_RULE_ADAPTER: TypeAdapter[CompletionUnion] = TypeAdapter(CompletionUnion)


def parse_rule(step: RoadmapStep) -> CompletionUnion:
    """The stored JSON back as its union member.

    The twin of ``parse_cadence`` in ``routers/checkin.py``: a column shaped
    by a discriminated union is read back through the union, so every branch
    downstream matches on a class rather than on a string somebody could
    misspell. An unreadable rule falls back to ``manual`` -- a step nobody can
    finish automatically is recoverable; an exception during rendering is not.
    """
    try:
        return _RULE_ADAPTER.validate_python(step.completion_rule or {})
    except (ValidationError, TypeError):
        return ManualRule()


def is_bounded(challenge: Challenge) -> bool:
    """Will this challenge ever read «تمام شده» on its own?

    A ``once`` or ``schedule`` challenge has an end in its own shape; a
    ``recurring_*`` one only has one if it carries a ``cadence.end_date``.
    A ``due_date`` bounds any of them. Everything else repeats forever, which
    is the correct behaviour for a habit and the reason a step on one may not
    use the ``challenge_finished`` rule -- see
    :class:`~app.schemas.completion.ChallengeFinishedRule`.
    """
    if challenge.due_date is not None:
        return True
    if challenge.cadence_kind in (
        CadenceKind.ONCE.value,
        CadenceKind.SCHEDULE.value,
    ):
        return True
    return bool((challenge.cadence or {}).get("end_date"))


def rule_refusal(rule: CompletionUnion, challenge: Challenge) -> str | None:
    """Why this rule cannot be used on this challenge, or ``None``.

    Both refusals are about a step that could never be left behind, and both
    are answered **422** at the write boundary rather than discovered later by
    whoever is stuck behind it. Checking here rather than in the schema is
    deliberate: the schema validates a rule, and whether a rule *fits* is a
    question about the challenge it is being attached to.
    """
    if isinstance(rule, ChallengeFinishedRule) and not is_bounded(challenge):
        return (
            "این چالش پایان مشخصی ندارد، پس «تا تمام شدن چالش» برایش شرط "
            "درستی نیست. یک عدد بگذار: تعداد دفعات، طول زنجیره، یا تعداد روز."
        )
    if isinstance(rule, AmountRule) and not challenge.goal_unit:
        return (
            "این چالش واحد اندازه‌گیری ندارد، پس مجموع مقدار برایش معنا "
            "نمی‌دهد."
        )
    return None


# ---------------------------------------------------------------------------
# The derived step state
# ---------------------------------------------------------------------------

STEP_STATES = tuple(s.value for s in RoadmapStepState)


def roadmap_step_state(progress: RoadmapStepProgress | None) -> str:
    """The Python half of the rule -- what a step's badge renders.

    Written twice on purpose, like ``challenge_status``/``status_filter``:
    this renders one row, :func:`step_state_filter` is the SQL behind the
    queries that ask "what is open for this member" across every roadmap they
    are on (the home card), and neither can page for the other. Both are
    restricted to the same single input -- the stored ``state``, with a
    missing progress row meaning ``locked`` -- so a badge and a filter can
    never disagree.
    """
    if progress is None:
        return RoadmapStepState.LOCKED.value
    return progress.state or RoadmapStepState.LOCKED.value


def step_state_filter(state: str | None):
    """The SQL half of the rule above, or ``None`` when it does not apply.

    A missing progress row is ``locked``; every query that uses this joins
    ``RoadmapStepProgress``, so "no row" and "locked" fall out the same way.
    An unrecognised value is ignored rather than matched, exactly as
    ``status_filter`` ignores one.
    """
    if state is None or state not in STEP_STATES:
        return None
    return RoadmapStepProgress.state == state


def step_is_open(roadmap: Roadmap, state: str) -> bool:
    """May the member act on this step right now?

    **The whole of ``strict``, in one function**, asked by the page deciding
    whether to draw a live button and by the route that refuses. With
    ``strict`` off -- the default -- the order is a *suggestion*: a locked
    step still says «هنوز نوبتش نیست» but nothing stops somebody who is ready.
    With it on, a locked step is genuinely closed, which is what a taught
    course needs.
    """
    if state != RoadmapStepState.LOCKED.value:
        return True
    return not roadmap.strict


# ---------------------------------------------------------------------------
# Evaluating one step
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ChallengeFacts:
    """Everything the rules need about one member on one challenge.

    Read live, in two queries for a whole roadmap, and never stored: the
    counts here are the same figures the leaderboard reads off ``CheckIns``
    and for the same reason ``ChallengeStats`` cannot answer them.
    """

    enrolled: bool = False
    streak: int = 0
    #: ``(when it was recorded, how much)`` per completed check-in.
    checkins: tuple[tuple[datetime, Decimal | None], ...] = ()

    def since(self, moment: datetime | None):
        if moment is None:
            return self.checkins
        return tuple(c for c in self.checkins if c[0] >= moment)


def _aware(value: datetime | None) -> datetime | None:
    """SQLite hands datetimes back naive (CLAUDE.md, Dates). Read them as UTC."""
    if value is None or value.tzinfo is not None:
        return value
    return value.replace(tzinfo=UTC)


def _challenge_is_finished(challenge: Challenge, now: datetime) -> bool:
    """``challenge_status(...) == finished``, imported at call time.

    ``app/routers/challenge.py`` imports :func:`roadmap_scope_filter` from
    this module to build its visibility clause, so that import has to run one
    way at module load; the derived status is needed the other way, once, from
    inside a function. One deferred import beats moving a rule that CLAUDE.md
    puts in the challenge router on purpose.
    """
    from app.routers.challenge import STATUS_FINISHED, challenge_status

    return challenge_status(challenge, now) == STATUS_FINISHED


def evaluate_rule(
    rule: CompletionUnion,
    *,
    challenge: Challenge,
    facts: ChallengeFacts,
    progress: RoadmapStepProgress,
    now: datetime,
) -> bool:
    """Is this step finished? One branch per union member, and no state.

    Everything is read from live rows each time. ``count`` and ``amount`` are
    measured from ``unlocked_at`` rather than from the beginning of time,
    which is ``Roadmap.count_prior_progress = False`` made concrete: somebody
    who joined that challenge months ago starts this step at zero, so the
    course means the same thing for everybody walking it.
    """
    unlocked = _aware(progress.unlocked_at)

    if isinstance(rule, ManualRule):
        # The only rule the engine cannot see happen. The member's own
        # «تمام شد» writes the state directly, so all this does is not undo it.
        return progress.state == RoadmapStepState.COMPLETED.value
    if isinstance(rule, ChallengeFinishedRule):
        return _challenge_is_finished(challenge, now)
    if isinstance(rule, StreakRule):
        return facts.streak >= rule.n
    if isinstance(rule, DurationRule):
        return unlocked is not None and now >= unlocked + timedelta(days=rule.days)
    if isinstance(rule, CountRule):
        return len(facts.since(unlocked)) >= rule.n
    if isinstance(rule, AmountRule):
        total = sum(
            (amount for _at, amount in facts.since(unlocked) if amount is not None),
            Decimal(0),
        )
        return total >= rule.target
    return False


def rule_progress(
    rule: CompletionUnion,
    *,
    facts: ChallengeFacts,
    progress: RoadmapStepProgress,
    now: datetime,
) -> dict | None:
    """«۷ از ۱۲» -- the countable rules only, or ``None``.

    A share, not a percentage the template has to compute: the ring and the
    bar both read ``done``/``total`` and the caller never divides. Rules with
    nothing to count answer ``None`` rather than a fake 0/1, because a
    progress bar that only ever shows empty or full is a worse answer than no
    bar.
    """
    unlocked = _aware(progress.unlocked_at)
    if isinstance(rule, CountRule):
        done, total = len(facts.since(unlocked)), rule.n
    elif isinstance(rule, StreakRule):
        done, total = facts.streak, rule.n
    elif isinstance(rule, AmountRule):
        done = sum(
            (amount for _at, amount in facts.since(unlocked) if amount is not None),
            Decimal(0),
        )
        total = rule.target
    elif isinstance(rule, DurationRule):
        if unlocked is None:
            return None
        done, total = (now - unlocked).days, rule.days
    else:
        return None
    done = min(max(float(done), 0.0), float(total))
    total = float(total)
    return {
        "done": _clean_number(done),
        "total": _clean_number(total),
        "pct": round(done / total * 100) if total else 0,
    }


def _clean_number(value: float) -> str:
    """``12.0`` -> ``12``, ``2.50`` -> ``2.5``. Same call ``views/challenge.py``
    makes for a goal amount: a whole number should not wear a decimal point."""
    text = f"{value:.2f}".rstrip("0").rstrip(".")
    return text or "0"


# ---------------------------------------------------------------------------
# The engine
# ---------------------------------------------------------------------------


@dataclass
class RefreshOutcome:
    """What one recomputation actually changed.

    Returned rather than notified from inside, so the caller decides -- and so
    a refresh triggered by a page load and one triggered by a check-in can
    raise different things without the engine growing a mode.
    """

    unlocked: list[RoadmapStep] = field(default_factory=list)
    completed: list[RoadmapStep] = field(default_factory=list)
    finished: bool = False


async def active_steps(db: AsyncSession, roadmap_id: int) -> list[RoadmapStep]:
    """The course as it stands: removed steps are not part of it.

    Ordered by ``(stage_index, order_in_stage)`` -- the reading order, and the
    order every screen draws.
    """
    return list(
        (
            await db.execute(
                select(RoadmapStep)
                .where(
                    RoadmapStep.roadmap_id == roadmap_id,
                    RoadmapStep.removed_at.is_(None),
                )
                .options(selectinload(RoadmapStep.challenge))
                .order_by(RoadmapStep.stage_index, RoadmapStep.order_in_stage, RoadmapStep.id)
            )
        )
        .scalars()
        .all()
    )


async def member_facts(
    db: AsyncSession, *, user_id: int, challenge_ids: list[int]
) -> dict[int, ChallengeFacts]:
    """Two queries for a whole course, not two per step.

    The same trade ``_mute_map`` makes in ``notify_many`` and
    ``social_context`` makes for a page of cards: a roadmap with twelve steps
    asks about twelve challenges once.
    """
    if not challenge_ids:
        return {}
    rows = (
        await db.execute(
            select(
                Enrollment.id, Enrollment.challenge_id, Enrollment.current_streak
            ).where(
                Enrollment.user_id == user_id,
                Enrollment.challenge_id.in_(challenge_ids),
            )
        )
    ).all()
    by_enrollment = {row[0]: row[1] for row in rows}
    facts: dict[int, ChallengeFacts] = {
        challenge_id: ChallengeFacts(enrolled=True, streak=streak or 0)
        for _eid, challenge_id, streak in rows
    }
    if not by_enrollment:
        return facts

    checks = (
        await db.execute(
            select(CheckIn.enrollment_id, CheckIn.created_at, CheckIn.amount).where(
                CheckIn.enrollment_id.in_(list(by_enrollment)),
                CheckIn.state == COMPLETED_STATE,
            )
        )
    ).all()
    collected: dict[int, list[tuple[datetime, Decimal | None]]] = {}
    for enrollment_id, created_at, amount in checks:
        collected.setdefault(by_enrollment[enrollment_id], []).append(
            (_aware(created_at) or datetime.now(UTC), amount)
        )
    for challenge_id, entries in collected.items():
        facts[challenge_id] = ChallengeFacts(
            enrolled=True,
            streak=facts[challenge_id].streak,
            checkins=tuple(sorted(entries)),
        )
    return facts


async def refresh_progress(
    db: AsyncSession,
    *,
    roadmap: Roadmap,
    enrollment: RoadmapEnrollment,
    timezone: str,
    now: datetime | None = None,
) -> RefreshOutcome:
    """Recompute one member's whole course, and open whatever that opens.

    Idempotent by construction: nothing here is incremented, every answer is
    re-derived from live rows, and running it twice in a row changes nothing
    the second time. It is called from every place the answer could have
    moved -- opening the roadmap page, joining, recording a manual step, a
    challenge being archived -- rather than from a job, because this app has
    no job runner (see ``purge_expired_codes``) and because a course that is
    only right after a nightly run is a course nobody trusts.

    Nothing is committed. The caller commits, exactly as ``notify`` requires,
    so an opened step and the enrollment it wrote land together or not at all.
    """
    # Truncated to the second, and that is load-bearing rather than tidy:
    # `CheckIn.created_at` is `server_default=func.now()`, which on SQLite is
    # `CURRENT_TIMESTAMP` and has second granularity (the same fact
    # `newest_first`'s id tie-break exists for). A check-in recorded in the
    # very second a step opened would otherwise read as *earlier* than the
    # unlock and not count towards a `count` or `amount` rule -- which is
    # exactly the first check-in somebody makes on a step that just opened.
    # Widening the window by under a second is the harmless direction.
    now = (now or datetime.now(UTC)).replace(microsecond=0)
    outcome = RefreshOutcome()
    steps = await active_steps(db, roadmap.id)
    if not steps:
        return outcome

    rows = list(
        (
            await db.execute(
                select(RoadmapStepProgress).where(
                    RoadmapStepProgress.roadmap_enrollment_id == enrollment.id
                )
            )
        )
        .scalars()
        .all()
    )
    by_step = {row.step_id: row for row in rows}
    for step in steps:
        if step.id not in by_step:
            row = RoadmapStepProgress(
                roadmap_enrollment_id=enrollment.id,
                step_id=step.id,
                state=RoadmapStepState.LOCKED.value,
                last_modifier_user_id=enrollment.user_id,
            )
            db.add(row)
            by_step[step.id] = row
    await db.flush()

    facts = await member_facts(
        db,
        user_id=enrollment.user_id,
        challenge_ids=[s.challenge_id for s in steps],
    )

    # Stages in order. A stage opens only when the one before it is settled,
    # so the walk stops at the first unsettled stage and everything after it
    # stays locked -- which is exactly what "locked" has to mean for the
    # absence of an enrollment to be the gate.
    stages: dict[int, list[RoadmapStep]] = {}
    for step in steps:
        stages.setdefault(step.stage_index, []).append(step)

    for _stage, stage_steps in sorted(stages.items()):
        for step in stage_steps:
            progress = by_step[step.id]
            if progress.unlocked_at is None:
                progress.unlocked_at = now
                progress.state = RoadmapStepState.AVAILABLE.value
                progress.last_modifier_user_id = enrollment.user_id
                await _open_step(
                    db,
                    roadmap=roadmap,
                    step=step,
                    user_id=enrollment.user_id,
                    timezone=timezone,
                )
                outcome.unlocked.append(step)

        for step in stage_steps:
            progress = by_step[step.id]
            if progress.state == RoadmapStepState.SKIPPED.value:
                continue
            was_completed = progress.state == RoadmapStepState.COMPLETED.value
            rule = parse_rule(step)
            fact = facts.get(step.challenge_id, ChallengeFacts())
            done = evaluate_rule(
                rule,
                challenge=step.challenge,
                facts=fact,
                progress=progress,
                now=now,
            )
            if done:
                progress.state = RoadmapStepState.COMPLETED.value
                if progress.completed_at is None:
                    progress.completed_at = now
                if not was_completed:
                    outcome.completed.append(step)
            else:
                # Falling back out of `completed` is possible and correct: a
                # check-in deleted inside the backfill window really does undo
                # a `count` rule, and a stored answer that could only ever go
                # forwards would be the incrementing counter this engine is
                # built to avoid.
                progress.completed_at = None
                progress.state = (
                    RoadmapStepState.IN_PROGRESS.value
                    if fact.since(_aware(progress.unlocked_at))
                    else RoadmapStepState.AVAILABLE.value
                )

        settled = all(
            by_step[s.id].state
            in (RoadmapStepState.COMPLETED.value, RoadmapStepState.SKIPPED.value)
            for s in stage_steps
            if s.required
        )
        if not settled:
            break

    everything_settled = all(
        by_step[s.id].state
        in (RoadmapStepState.COMPLETED.value, RoadmapStepState.SKIPPED.value)
        for s in steps
        if s.required
    )
    status = (
        RoadmapEnrollmentStatus.COMPLETED.value
        if everything_settled
        else RoadmapEnrollmentStatus.ACTIVE.value
    )
    if enrollment.status != status:
        enrollment.status = status
        enrollment.updated_at = now
        outcome.finished = everything_settled
    return outcome


async def _open_step(
    db: AsyncSession,
    *,
    roadmap: Roadmap,
    step: RoadmapStep,
    user_id: int,
    timezone: str,
) -> None:
    """Reaching a step is an ordinary enrolment being written, nothing more.

    :func:`app.groups.assign_participants` is that write -- the same
    function a group challenge is handed out with, so anonymity resolution
    (``resolve_assigned_anonymity``: somebody who was never asked is not
    named) and the ``ChallengeStats`` counter cannot drift between the two
    ways somebody is put into a challenge. It is idempotent, so a member
    already enrolled in this challenge keeps the enrollment and the history
    they have.

    It writes the enrollment and says nothing: telling the member is
    :func:`announce_outcome`'s job, in one place, because the same unlock is
    worth announcing when a check-in caused it and not worth announcing when
    the member is looking straight at it after joining. Two notifiers would be
    two chances to send the same sentence twice.
    """
    await assign_participants(
        db,
        challenge=step.challenge,
        user_ids=[user_id],
        actor_user_id=user_id,
        timezone=timezone,
        notify=False,
    )
    # `roadmap` is not read here -- it is in the signature because every other
    # step operation is keyed on the pair, and a helper that silently did not
    # need one of them would be a helper somebody calls with the wrong step.
    _ = roadmap


async def join_roadmap(
    db: AsyncSession,
    *,
    roadmap: Roadmap,
    user_id: int,
    timezone: str,
) -> tuple[RoadmapEnrollment, bool]:
    """Start walking. Idempotent -- opening the link twice is joining once.

    Returns the enrollment and whether it is new, so the caller can tell 201
    from 200 without re-reading the invariant. Nothing is committed here.
    """
    existing = (
        await db.execute(
            select(RoadmapEnrollment).where(
                RoadmapEnrollment.roadmap_id == roadmap.id,
                RoadmapEnrollment.user_id == user_id,
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        await refresh_progress(
            db, roadmap=roadmap, enrollment=existing, timezone=timezone
        )
        return existing, False

    now = datetime.now(UTC)
    enrollment = RoadmapEnrollment(
        roadmap_id=roadmap.id,
        user_id=user_id,
        status=RoadmapEnrollmentStatus.ACTIVE.value,
        started_at=now,
        last_modifier_user_id=user_id,
    )
    db.add(enrollment)
    await db.flush()
    await refresh_progress(
        db, roadmap=roadmap, enrollment=enrollment, timezone=timezone, now=now
    )
    # The builder hears about it; `notify` drops this when they are the one
    # joining their own course.
    await notify(
        db,
        user_id=roadmap.owner_id,
        kind=NotificationKind.ROADMAP_JOINED,
        actor_user_id=user_id,
        roadmap_id=roadmap.id,
    )
    return enrollment, True


async def announce_outcome(
    db: AsyncSession,
    *,
    roadmap: Roadmap,
    enrollment: RoadmapEnrollment,
    outcome: RefreshOutcome,
    announce_unlocks: bool = True,
) -> None:
    """Say what the recomputation did, to the one person it happened to.

    Separate from :func:`refresh_progress` because the two have different
    audiences at different moments. ``announce_unlocks=False`` is what joining
    passes: the first stage opens as part of the tap, and the member is
    looking straight at it -- a feed that reports your own tap back to you is
    the noise ``notify``'s first invariant exists to suppress, and this is the
    one case that invariant cannot catch on its own.

    Both kinds are raised with **no actor** -- nobody did this to anybody, the
    engine recomputed -- which is the case ``Notification.actor_user_id`` is
    nullable for.
    """
    for step in outcome.unlocked if announce_unlocks else ():
        await notify(
            db,
            user_id=enrollment.user_id,
            kind=NotificationKind.ROADMAP_STEP_UNLOCKED,
            roadmap_id=roadmap.id,
            challenge_id=step.challenge_id,
        )
    if outcome.finished:
        await notify(
            db,
            user_id=enrollment.user_id,
            kind=NotificationKind.ROADMAP_COMPLETED,
            roadmap_id=roadmap.id,
        )


async def advance_after_checkin(
    db: AsyncSession,
    *,
    user_id: int,
    challenge_id: int,
    timezone: str,
) -> None:
    """Recompute every course of this member that this challenge is a step of.

    **This is where a step actually opens.** A member records a check-in on a
    challenge page and never goes near the roadmap; if the engine only ran
    when somebody opened the roadmap, the next step's enrollment would not
    exist and «امروز» would be silently one step behind for as long as they
    did not think to look. There is no job runner in this app (see
    ``purge_expired_codes``), so the work happens on the event that could have
    changed the answer.

    It costs **one indexed query** for a member with no roadmaps -- which is
    almost everybody, almost always -- because ``ix_roadmap_steps_challenge``
    answers "is this challenge a step of anything I am walking" and the loop
    below does not run. Nothing is committed; the check-in routes commit once.
    """
    roadmaps = list(
        (
            await db.execute(
                select(Roadmap, RoadmapEnrollment)
                .join(
                    RoadmapEnrollment, RoadmapEnrollment.roadmap_id == Roadmap.id
                )
                .join(RoadmapStep, RoadmapStep.roadmap_id == Roadmap.id)
                .where(
                    RoadmapStep.challenge_id == challenge_id,
                    RoadmapStep.removed_at.is_(None),
                    RoadmapEnrollment.user_id == user_id,
                )
                .distinct()
            )
        ).all()
    )
    for roadmap, enrollment in roadmaps:
        outcome = await refresh_progress(
            db, roadmap=roadmap, enrollment=enrollment, timezone=timezone
        )
        await announce_outcome(
            db, roadmap=roadmap, enrollment=enrollment, outcome=outcome
        )


async def skip_steps_for_challenge(
    db: AsyncSession,
    *,
    challenge: Challenge,
    actor_user_id: int,
    timezone: str,
) -> list[Roadmap]:
    """A challenge was archived: pass its steps over, do not stall the course.

    An archived challenge produces no more occurrences, so every rule that
    counts one becomes unsatisfiable -- and a ``required`` step nobody can
    finish would leave every person behind it stuck forever, for a decision
    taken by somebody who may not even know the roadmap exists. So the step is
    marked ``skipped``, which counts as settled for opening the next stage,
    and the roadmap's **builder** is told: their course has a hole in it and
    only they can decide what goes there instead.

    Called from ``update_challenge``, beside ``notify_lifecycle_change`` --
    the one place a lifecycle transition is already known to have happened.
    Nothing is committed; that route commits once.
    """
    steps = list(
        (
            await db.execute(
                select(RoadmapStep)
                .where(
                    RoadmapStep.challenge_id == challenge.id,
                    RoadmapStep.removed_at.is_(None),
                )
                .options(
                    selectinload(RoadmapStep.challenge),
                    selectinload(RoadmapStep.roadmap),
                )
            )
        )
        .scalars()
        .all()
    )
    touched: list[Roadmap] = []
    for step in steps:
        roadmap = step.roadmap
        if roadmap is None:
            continue
        touched.append(roadmap)
        rows = list(
            (
                await db.execute(
                    select(RoadmapStepProgress).where(
                        RoadmapStepProgress.step_id == step.id,
                        RoadmapStepProgress.state.notin_(
                            [
                                RoadmapStepState.COMPLETED.value,
                                RoadmapStepState.SKIPPED.value,
                            ]
                        ),
                    )
                )
            )
            .scalars()
            .all()
        )
        for row in rows:
            row.state = RoadmapStepState.SKIPPED.value
            row.updated_at = datetime.now(UTC)
        await db.flush()
        # Everyone the skip just unblocked. Bounded by the people actually
        # halfway through this step, and archiving is rare -- the alternative
        # is a course that only moves the next time each member happens to
        # open its page.
        for row in rows:
            enrollment = await db.get(RoadmapEnrollment, row.roadmap_enrollment_id)
            if enrollment is None:
                continue
            outcome = await refresh_progress(
                db, roadmap=roadmap, enrollment=enrollment, timezone=timezone
            )
            await announce_outcome(
                db, roadmap=roadmap, enrollment=enrollment, outcome=outcome
            )
        await notify(
            db,
            user_id=roadmap.owner_id,
            kind=NotificationKind.ROADMAP_STEP_SKIPPED,
            actor_user_id=actor_user_id,
            roadmap_id=roadmap.id,
            challenge_id=challenge.id,
        )
    return touched


# ---------------------------------------------------------------------------
# What a roadmap's English-coded values are called in Farsi
# ---------------------------------------------------------------------------
#
# These live here, in the domain module, rather than in one views router --
# the same call `NOTIFICATION_META` and `app/groups.py` make, and for their
# reason: more than one surface renders them (the roadmap screens *and* the
# home dashboard's «قدم فعلی تو» card), so a second copy in the second router
# is a second thing to keep in agreement. `register_roadmap_filters` is the
# same registration contract `register_icon_filters` has -- a views router
# that renders a roadmap must call it, and forgetting is a render-time error.

STEP_STATE_LABELS = {
    RoadmapStepState.LOCKED.value: "هنوز نوبتش نیست",
    RoadmapStepState.AVAILABLE.value: "آمادهٔ شروع",
    RoadmapStepState.IN_PROGRESS.value: "در حال انجام",
    RoadmapStepState.COMPLETED.value: "تمام شد",
    RoadmapStepState.SKIPPED.value: "رد شد",
}

STEP_STATE_ICONS = {
    RoadmapStepState.LOCKED.value: "lock",
    RoadmapStepState.AVAILABLE.value: "unlock",
    RoadmapStepState.IN_PROGRESS.value: "flame",
    RoadmapStepState.COMPLETED.value: "check",
    RoadmapStepState.SKIPPED.value: "seal",
}

#: The glyph beside a rule wherever a step is drawn. **Only the icons**: the
#: *chip wording* the builder picks from is built client-side, in the sheet
#: that asks «این قدم کِی تمام می‌شود؟», so a Farsi map for it here would be a
#: map with no reader -- what the server renders is the whole sentence, and
#: that is `describe_rule` below. Adding a rule means a member in
#: `schemas/completion.py`, a branch in `evaluate_rule`, an entry here, and
#: the wizard's own chip.
RULE_ICONS = {
    "challenge_finished": "seal",
    "count": "check",
    "streak": "flame",
    "amount": "chart",
    "duration": "clock",
    "manual": "tap",
}


def describe_rule(step: RoadmapStep) -> str:
    """One Farsi sentence saying when this step is left behind.

    The roadmap-side twin of ``describe_cadence``: the one place a stored rule
    becomes something a person reads, so no template ever branches on a rule's
    ``kind``.
    """
    rule = parse_rule(step)
    if isinstance(rule, ChallengeFinishedRule):
        return "وقتی خود چالش تمام شود"
    if isinstance(rule, CountRule):
        return f"{rule.n} بار ثبت کنی"
    if isinstance(rule, StreakRule):
        return f"{rule.n} وعده پشت‌سرهم ثبت کنی"
    if isinstance(rule, AmountRule):
        unit = (step.challenge.goal_unit if step.challenge else None) or ""
        return f"مجموع {_clean_number(float(rule.target))} {unit}".strip()
    if isinstance(rule, DurationRule):
        return f"{rule.days} روز از باز شدن این قدم بگذرد"
    return "خودت اعلام کنی تمام شده"


def register_roadmap_filters(env) -> None:
    """Expose the label maps and the rule sentence to one Jinja environment."""
    env.globals["step_state_labels"] = STEP_STATE_LABELS
    env.globals["step_state_icons"] = STEP_STATE_ICONS
    env.globals["rule_icons"] = RULE_ICONS
    env.filters["describe_rule"] = describe_rule
