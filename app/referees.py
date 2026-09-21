"""The role axis: inviting somebody to vouch, and resolving who may.

Three roles exist on an act and only one of them needed a new table:

* **Owner** -- ``Challenge.owner_id``, and the ``owner`` row on
  ``Enrollments``. Already there.
* **Doer** -- an ``Enrollment``. Already there, and it is what every streak,
  counter, leaderboard and «امروز» read.
* **Referee** -- :class:`~app.models.act.ChallengeReferee`. This module.

**Named, never linked.** A referee is invited by *credential* -- a mobile
number or an exact email -- exactly as ``POST /groups/{id}/members`` adds
somebody, and through the same ``normalize_mobile``. There is no search box
and no unguessable link, for the reason the group's member-add has neither: a
search here would hand every act's owner a way to walk the whole membership,
and a link with a capacity is a mechanism this does not need. You ask a
person you already know.

An unknown credential is an honest **404**, the same call the group router
makes: the oracle costs one whole phone number per guess, and the
alternative is a screen that silently does nothing.

**Asked is not the same as agreed.** A row starts ``invited`` and holds no
power at all; only ``active`` may rule on a report. That is not ceremony --
being made accountable for somebody else's promise without being asked is
precisely the thing the notification feed exists to prevent, and a referee
who never answered must not silently become the reason a queue is stuck.

**Nothing here is deleted.** Declining and being removed are states, because
the row is the only record that somebody was asked -- and a referee taken off
an act after ruling on evidence is exactly what an argument later turns on.
Re-inviting flips the row back to ``invited``; the unique constraint makes
that one row rather than a pile.

**A pact needs no code of its own.** Two people who each hold an
``Enrollment`` and an ``active`` row here are each other's referee, and
``app.verification.load_reviewable`` refuses a self-ruling. :func:`is_pact`
is the *derived* reading of that arrangement -- there is no ``pact`` column,
because a stored flag would be a second name for a fact the rows already
state.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.act import ChallengeReferee, RefereeState
from app.models.challenge import Challenge, ReviewMode
from app.models.enrollment import Enrollment
from app.models.user import User
from app.phone import normalize_mobile


class RefereeRefused(Exception):
    """Can't do that to this referee. ``status`` is the HTTP code."""

    def __init__(self, status: int, detail: str):
        super().__init__(detail)
        self.status = status
        self.detail = detail


async def resolve_member(db: AsyncSession, identifier: str) -> User:
    """The account behind a typed mobile number or email, or a 404.

    The group router's ``MemberAdd`` rule, shared rather than restated: a
    number goes through ``normalize_mobile`` so ``09121234567`` and
    ``+98 912 123 4567`` are one account, and anything that is not a number
    is matched as an exact, case-folded email.
    """
    raw = (identifier or "").strip()
    if not raw:
        raise RefereeRefused(404, "کاربری با این مشخصات پیدا نشد.")

    mobile = normalize_mobile(raw)
    clause = (
        (User.mobile == mobile)
        if mobile
        else (func.lower(User.email) == raw.lower())
    )
    user = (await db.execute(select(User).where(clause))).scalar_one_or_none()
    if user is None:
        raise RefereeRefused(404, "کاربری با این مشخصات پیدا نشد.")
    return user


async def fetch_referees(
    db: AsyncSession, challenge_id: int, *, include_history: bool = False
) -> list[ChallengeReferee]:
    """This act's referees.

    By default the ones that still mean something -- ``invited`` and
    ``active``. ``include_history`` adds the declined and removed rows, which
    the act's own manage screen shows and nothing else does: a member reading
    a challenge does not need the list of people who said no.
    """
    stmt = (
        select(ChallengeReferee)
        .where(ChallengeReferee.challenge_id == challenge_id)
        .options(selectinload(ChallengeReferee.user))
        .order_by(ChallengeReferee.id.asc())
    )
    if not include_history:
        stmt = stmt.where(
            ChallengeReferee.state.in_(
                (RefereeState.INVITED.value, RefereeState.ACTIVE.value)
            )
        )
    return list((await db.execute(stmt)).scalars().all())


async def active_referee_count(db: AsyncSession, challenge_id: int) -> int:
    """How many people can actually rule on this act's reports."""
    return (
        await db.execute(
            select(func.count())
            .select_from(ChallengeReferee)
            .where(
                ChallengeReferee.challenge_id == challenge_id,
                ChallengeReferee.state == RefereeState.ACTIVE.value,
            )
        )
    ).scalar_one()


async def active_referee_ids(
    db: AsyncSession, challenge_id: int, *, excluding: int | None = None
) -> list[int]:
    """Who to tell that a report is waiting. ``excluding`` drops the reporter.

    The exclusion is here rather than left to ``notify``'s own actor rule,
    because that rule drops a notification whose *recipient* is its actor --
    which is the same thing in a pact and not in an act where the owner
    referees somebody else. Naming it is cheaper than relying on the two
    coinciding.
    """
    stmt = select(ChallengeReferee.user_id).where(
        ChallengeReferee.challenge_id == challenge_id,
        ChallengeReferee.state == RefereeState.ACTIVE.value,
    )
    if excluding is not None:
        stmt = stmt.where(ChallengeReferee.user_id != excluding)
    return list((await db.execute(stmt)).scalars().all())


async def referee_row(
    db: AsyncSession, *, challenge_id: int, user_id: int
) -> ChallengeReferee | None:
    return (
        await db.execute(
            select(ChallengeReferee).where(
                ChallengeReferee.challenge_id == challenge_id,
                ChallengeReferee.user_id == user_id,
            )
        )
    ).scalar_one_or_none()


async def is_active_referee(
    db: AsyncSession, *, challenge_id: int, user_id: int | None
) -> bool:
    if user_id is None:
        return False
    row = await referee_row(db, challenge_id=challenge_id, user_id=user_id)
    return row is not None and row.state == RefereeState.ACTIVE.value


async def invite_referee(
    db: AsyncSession,
    *,
    challenge: Challenge,
    user: User,
    actor_user_id: int,
) -> ChallengeReferee:
    """Ask somebody to vouch for this act. Idempotent on the person.

    Re-inviting somebody who declined or was removed puts the *same* row back
    to ``invited`` -- the unique constraint says one row per person per act,
    and a second row would be a second history of the same relationship.
    Re-inviting somebody who is already ``invited`` or ``active`` is a
    no-change success, the call ``assign_participants`` makes: asking twice
    is the same request as asking once.

    **The owner may be a referee, and a doer may be one too.** The rule is
    not "a referee is somebody else" -- it is "nobody rules on their own
    report", and that is enforced once, at the ruling
    (``verification.load_reviewable``). Forbidding it here instead would ban
    the two most ordinary arrangements there are: an owner who watches the
    people they set the act for, and a pact, where each of two doers rules on
    the other's reports and on none of their own.
    """
    existing = await referee_row(
        db, challenge_id=challenge.id, user_id=user.id
    )
    if existing is not None:
        if existing.state in (RefereeState.INVITED.value, RefereeState.ACTIVE.value):
            return existing
        existing.state = RefereeState.INVITED.value
        existing.responded_at = None
        existing.invited_by_user_id = actor_user_id
        existing.updated_at = datetime.now(UTC)
        existing.last_modifier_user_id = actor_user_id
        return existing

    row = ChallengeReferee(
        challenge_id=challenge.id,
        user_id=user.id,
        state=RefereeState.INVITED.value,
        invited_by_user_id=actor_user_id,
        last_modifier_user_id=actor_user_id,
    )
    db.add(row)
    return row


def respond(row: ChallengeReferee, *, accept: bool, actor_user_id: int) -> None:
    """The invitee's own answer. Only from ``invited``.

    An ``active`` referee who wants out is *removed* rather than un-accepting,
    and that is the owner's act -- so there is one door out and it is the one
    the log records.
    """
    if row.state != RefereeState.INVITED.value:
        raise RefereeRefused(409, "این دعوت دیگر باز نیست.")
    now = datetime.now(UTC)
    row.state = (
        RefereeState.ACTIVE.value if accept else RefereeState.DECLINED.value
    )
    row.responded_at = now
    row.updated_at = now
    row.last_modifier_user_id = actor_user_id


def remove(row: ChallengeReferee, *, actor_user_id: int) -> None:
    """Take somebody off. A state, never a delete -- see the module docstring."""
    if row.state == RefereeState.REMOVED.value:
        raise RefereeRefused(409, "این ناظر قبلاً برداشته شده است.")
    now = datetime.now(UTC)
    row.state = RefereeState.REMOVED.value
    row.updated_at = now
    row.last_modifier_user_id = actor_user_id


# --- the derived shapes ----------------------------------------------------


async def is_pact(db: AsyncSession, challenge: Challenge) -> bool:
    """Is this act two people vouching for each other?

    Derived, not stored, for the rule the whole app follows: a ``pact``
    column would be a second name for what the rows already say, and the two
    could disagree the moment somebody was removed. A pact is exactly *two*
    doers, each of whom is an ``active`` referee -- so it stops being a pact
    the instant a third person joins or one of them steps back, which is the
    honest answer.
    """
    if challenge.review_mode != ReviewMode.REFEREE.value:
        return False
    doers = set(
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
    if len(doers) != 2:
        return False
    referees = set(
        (
            await db.execute(
                select(ChallengeReferee.user_id).where(
                    ChallengeReferee.challenge_id == challenge.id,
                    ChallengeReferee.state == RefereeState.ACTIVE.value,
                )
            )
        )
        .scalars()
        .all()
    )
    return doers == referees


async def pending_invites_for(
    db: AsyncSession, user_id: int
) -> list[ChallengeReferee]:
    """Acts waiting on this member to say whether they will vouch."""
    stmt = (
        select(ChallengeReferee)
        .where(
            ChallengeReferee.user_id == user_id,
            ChallengeReferee.state == RefereeState.INVITED.value,
        )
        .options(selectinload(ChallengeReferee.challenge))
        .order_by(ChallengeReferee.id.asc())
    )
    return list((await db.execute(stmt)).scalars().all())


def referee_scope_filter(user_id: int | None):
    """One more ``OR`` leg for ``challenge_visibility_filter``.

    A referee has to be able to *open* the act they were asked to vouch for
    -- otherwise the invitation is a notification about a 404. It is a leg of
    the disjunction rather than a conjunct for the reason
    ``roadmap_scope_filter`` is: a group *narrows* what somebody may see,
    while being asked to referee *widens* it, for one person, by one act.

    An ``invited`` row is enough. Deciding whether to accept means reading
    what you would be vouching for, and refusing that until after the
    acceptance is the wrong order.
    """
    if user_id is None:
        return None
    return Challenge.id.in_(
        select(ChallengeReferee.challenge_id).where(
            ChallengeReferee.user_id == user_id,
            ChallengeReferee.state.in_(
                (RefereeState.INVITED.value, RefereeState.ACTIVE.value)
            ),
        )
    )


#: Per-surface Farsi (D7) for the four states.
REFEREE_STATE_LABELS = {
    RefereeState.INVITED.value: "در انتظار پاسخ",
    RefereeState.ACTIVE.value: "ناظر فعال",
    RefereeState.DECLINED.value: "نپذیرفت",
    RefereeState.REMOVED.value: "برداشته شد",
}


def register_referee_filters(env) -> None:
    """Expose the referee labels to one Jinja environment."""
    env.globals["referee_state_labels"] = REFEREE_STATE_LABELS


__all__ = [
    "REFEREE_STATE_LABELS",
    "RefereeRefused",
    "active_referee_count",
    "active_referee_ids",
    "fetch_referees",
    "invite_referee",
    "is_active_referee",
    "is_pact",
    "pending_invites_for",
    "referee_row",
    "referee_scope_filter",
    "register_referee_filters",
    "remove",
    "resolve_member",
    "respond",
]
