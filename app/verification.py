"""The outcome axis: what became of a report, and the one predicate for it.

``CheckIns.state`` says what the **doer reported** -- ``completed`` or
``skipped``. ``CheckIns.verdict`` says what **became of** that report. They
are two columns because they answer two questions, and because widening
``state`` would have been silently wrong: a dozen places in this app compare
it to ``"completed"``, and a third value there would have been counted as a
success by every one of them.

The two together derive what a member actually reads:

============  ==========  ====================================
``state``     ``verdict`` outcome
============  ==========  ====================================
completed     auto        **موفق** -- no referee, settled at once
completed     approved    **موفق** -- a referee said so
completed     pending     **در انتظار تأیید**
completed     rejected    **ناموفق**
skipped       (auto)      **انجام نشد** -- reported as missed
============  ==========  ====================================

**One predicate, composed everywhere.** :func:`counted_clause` (SQL) and
:func:`counts` (Python) are the same rule written for the two places it is
asked, exactly as ``challenge_status`` and ``status_filter`` are. Streaks,
``ChallengeStats``, the leaderboard, the heatmap, the twelve-week grid and
every roadmap completion rule go through them, so "what counts as done"
changes in one place or not at all.

**A pending report does not count, and that is not a bug in the streak.**
``compute_streaks`` recomputes from scratch on every write
(``app/occurrences.py``), so an approval restores the streak the report was
holding open. That is the whole reason streaks were never incremented.

**A referee cannot rule on their own report.** The rule that makes a pact
possible: two people who each hold an ``Enrollment`` and a
``ChallengeReferee`` row are each other's referee, and without this they
would simply approve themselves. Enforced in :func:`load_reviewable` at the
write, not by a constraint -- the same person legitimately holds both rows.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.act import ChallengeReferee, RefereeState
from app.models.challenge import Challenge, ReviewMode
from app.models.checkin import CheckIn
from app.models.enrollment import Enrollment

#: What the doer reported. Unchanged, and deliberately still only two values.
DONE_STATE = "completed"
SKIPPED_STATE = "skipped"

#: No referee was ever involved -- the report was final when it was made.
#: The default, and what every row written before this column meant.
VERDICT_AUTO = "auto"
#: Waiting on a referee.
VERDICT_PENDING = "pending"
#: A referee said yes.
VERDICT_APPROVED = "approved"
#: A referee said no.
VERDICT_REJECTED = "rejected"

VERDICTS = (VERDICT_AUTO, VERDICT_PENDING, VERDICT_APPROVED, VERDICT_REJECTED)

#: The verdicts a completed report needs to *count*. `auto` is in here
#: because an act with no referee has always counted its reports, and that
#: must not change for anybody.
COUNTED_VERDICTS = (VERDICT_AUTO, VERDICT_APPROVED)

#: The verdicts that are **settled** -- a decision has been reached, one way
#: or the other. The denominator of the commitment score, and the thing a
#: pending report is deliberately not in.
SETTLED_VERDICTS = (VERDICT_AUTO, VERDICT_APPROVED, VERDICT_REJECTED)


# --- the one predicate, written twice -------------------------------------


def counted_clause():
    """SQL for "this check-in counts as done". Compose with ``.where()``."""
    return and_(
        CheckIn.state == DONE_STATE, CheckIn.verdict.in_(COUNTED_VERDICTS)
    )


def counts(checkin) -> bool:
    """The same rule, for a row already in hand."""
    return (
        checkin.state == DONE_STATE
        and (checkin.verdict or VERDICT_AUTO) in COUNTED_VERDICTS
    )


def settled_clause():
    """SQL for "a decision has been reached about this check-in".

    A skip is settled by definition -- the member decided, and reported it.
    """
    return or_(
        CheckIn.state == SKIPPED_STATE,
        and_(CheckIn.state == DONE_STATE, CheckIn.verdict.in_(SETTLED_VERDICTS)),
    )


# --- the derived outcome ---------------------------------------------------

OUTCOME_SUCCESS = "success"
OUTCOME_PENDING = "pending"
OUTCOME_FAILED = "failed"
OUTCOME_SKIPPED = "skipped"


def checkin_outcome(checkin) -> str:
    """The one word a member reads about one report. Derived, never stored.

    The same trade ``challenge_status`` makes: two stored facts, one derived
    answer, so nothing can be written that disagrees with what is shown.
    """
    if checkin.state == SKIPPED_STATE:
        return OUTCOME_SKIPPED
    verdict = checkin.verdict or VERDICT_AUTO
    if verdict == VERDICT_PENDING:
        return OUTCOME_PENDING
    if verdict == VERDICT_REJECTED:
        return OUTCOME_FAILED
    return OUTCOME_SUCCESS


#: Per-surface Farsi (D7): a short label for one screen family, not a
#: promise about behaviour. The explainer registry is where the *rule* is
#: written down; this is what a pill says.
OUTCOME_META: dict[str, dict[str, str]] = {
    OUTCOME_SUCCESS: {"label": "موفق", "icon": "check", "tone": "ok"},
    OUTCOME_PENDING: {"label": "در انتظار تأیید", "icon": "clock", "tone": "wait"},
    OUTCOME_FAILED: {"label": "تأیید نشد", "icon": "x", "tone": "bad"},
    OUTCOME_SKIPPED: {"label": "انجام نشد", "icon": "minus", "tone": "mute"},
}


def outcome_meta(checkin) -> dict[str, str]:
    return OUTCOME_META[checkin_outcome(checkin)]


def register_verification_filters(env) -> None:
    """Expose the outcome renderer to one Jinja environment."""
    env.filters["checkin_outcome"] = checkin_outcome
    env.filters["outcome_meta"] = outcome_meta
    env.globals["outcome_meta_map"] = OUTCOME_META


# --- the write boundary ----------------------------------------------------


def verdict_for_report(challenge: Challenge, state: str) -> str:
    """What ``verdict`` a fresh report on this act starts life with.

    ``auto`` unless the act asks for a referee, which is why an act with no
    review -- every act that existed before this -- behaves exactly as it
    did. A *skip* is always ``auto``: there is nothing to vouch for in
    somebody saying they did not do it, and a queue of skips waiting on a
    referee would be a queue nobody should have to work through.
    """
    if state != DONE_STATE:
        return VERDICT_AUTO
    if challenge.review_mode == ReviewMode.REFEREE.value:
        return VERDICT_PENDING
    return VERDICT_AUTO


class NotReviewable(Exception):
    """This caller may not rule on this report. ``status`` is the HTTP code."""

    def __init__(self, status: int, detail: str):
        super().__init__(detail)
        self.status = status
        self.detail = detail


async def load_reviewable(
    db: AsyncSession, *, checkin_id: int, referee_user_id: int
) -> tuple[CheckIn, Enrollment, Challenge]:
    """The report this referee may rule on, or a refusal saying why.

    The gate, in the order it fails -- and the order is the 404-vs-403 split
    CLAUDE.md draws:

    * **the report does not exist, or you are not a referee of its act**:
      404. Check-in ids are sequential and nothing links somebody to
      another member's report, so a 403 here would map every member's logged
      history -- exactly what ``_load_enrollment_for_checkin`` refuses to do.
    * **it is your own report**: 403. You could already see it, and being
      told plainly is the only way a pact's two halves make sense.
    * **it is already settled**: 409. A second ruling is not a correction,
      it is two referees disagreeing after the fact.
    """
    checkin = (
        await db.execute(
            select(CheckIn)
            .where(CheckIn.id == checkin_id)
            .options(selectinload(CheckIn.proof_asset))
        )
    ).scalar_one_or_none()
    if checkin is None:
        raise NotReviewable(404, "گزارش پیدا نشد.")

    referee = (
        await db.execute(
            select(ChallengeReferee).where(
                ChallengeReferee.challenge_id == checkin.challenge_id,
                ChallengeReferee.user_id == referee_user_id,
                ChallengeReferee.state == RefereeState.ACTIVE.value,
            )
        )
    ).scalar_one_or_none()
    if referee is None:
        raise NotReviewable(404, "گزارش پیدا نشد.")

    enrollment = (
        await db.execute(
            select(Enrollment).where(Enrollment.id == checkin.enrollment_id)
        )
    ).scalar_one_or_none()
    if enrollment is None:
        raise NotReviewable(404, "گزارش پیدا نشد.")

    if enrollment.user_id == referee_user_id:
        raise NotReviewable(403, "گزارش خودت را نمی‌توانی تأیید کنی.")

    if (checkin.verdict or VERDICT_AUTO) != VERDICT_PENDING:
        raise NotReviewable(409, "این گزارش قبلاً تعیین تکلیف شده است.")

    challenge = (
        await db.execute(
            select(Challenge).where(Challenge.id == checkin.challenge_id)
        )
    ).scalar_one_or_none()
    if challenge is None:
        raise NotReviewable(404, "گزارش پیدا نشد.")

    return checkin, enrollment, challenge


def apply_verdict(
    checkin: CheckIn,
    *,
    verdict: str,
    by_user_id: int,
    note: str | None,
    now_utc: datetime | None = None,
) -> None:
    """Write a ruling onto a report. Nothing else -- no commit, no counters.

    Separated from the route so the caller can re-run streaks and stats in
    the same transaction, which is the part that has to happen *after* this
    and *before* the commit.
    """
    checkin.verdict = verdict
    checkin.verdict_by_user_id = by_user_id
    checkin.verdict_at = now_utc or datetime.now(UTC)
    checkin.verdict_note = note


# --- the queue -------------------------------------------------------------


async def fetch_review_queue(
    db: AsyncSession,
    *,
    referee_user_id: int,
    offset: int = 0,
    limit: int = 20,
) -> tuple[list[CheckIn], bool]:
    """One page of reports waiting on this referee, plus whether more remain.

    **Oldest first**, unlike every other paginated list in the app and for
    the reason the group's pending-request queue is: this is a queue of work,
    and the person who reported first has been waiting longest. Falls through
    to the id tie-break like everything else.

    Own reports are excluded in SQL rather than filtered afterwards, or a
    pact's two halves would each see a page half full of their own rows --
    and a scroller's ``offset`` counts server-returned rows, so dropping one
    in Python skips a report.
    """
    mine = (
        select(Enrollment.id)
        .where(Enrollment.user_id == referee_user_id)
        .scalar_subquery()
    )
    active_acts = (
        select(ChallengeReferee.challenge_id)
        .where(
            ChallengeReferee.user_id == referee_user_id,
            ChallengeReferee.state == RefereeState.ACTIVE.value,
        )
        .scalar_subquery()
    )
    stmt = (
        select(CheckIn)
        .where(
            CheckIn.verdict == VERDICT_PENDING,
            CheckIn.challenge_id.in_(active_acts),
            CheckIn.enrollment_id.notin_(mine),
        )
        .order_by(CheckIn.created_at.asc(), CheckIn.id.asc())
        .offset(offset)
        .limit(limit + 1)
        .options(
            selectinload(CheckIn.challenge),
            selectinload(CheckIn.proof_asset),
            selectinload(CheckIn.enrollment).selectinload(Enrollment.user),
        )
    )
    rows = list((await db.execute(stmt)).scalars().all())
    return rows[:limit], len(rows) > limit


async def count_review_queue(db: AsyncSession, *, referee_user_id: int) -> int:
    """How many reports are waiting on this referee. One indexed count."""
    mine = (
        select(Enrollment.id)
        .where(Enrollment.user_id == referee_user_id)
        .scalar_subquery()
    )
    active_acts = (
        select(ChallengeReferee.challenge_id)
        .where(
            ChallengeReferee.user_id == referee_user_id,
            ChallengeReferee.state == RefereeState.ACTIVE.value,
        )
        .scalar_subquery()
    )
    return (
        await db.execute(
            select(func.count())
            .select_from(CheckIn)
            .where(
                CheckIn.verdict == VERDICT_PENDING,
                CheckIn.challenge_id.in_(active_acts),
                CheckIn.enrollment_id.notin_(mine),
            )
        )
    ).scalar_one()
