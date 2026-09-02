"""Reactions -- the domain module. The only door into the `Reactions` table.

The rule this module exists to hold: **a reaction is only as visible as the
thing it is about.** A polymorphic table has no FK to its subject, so nothing
in the database can stop a body naming a challenge the caller cannot see.
`SUBJECT_RESOLVERS` is where that is stopped: one entry per
`ReactionSubject`, each composing that subject's *own* visibility filter --
the same function the rest of the app composes, never a copy of the rule --
so a subject the caller cannot see comes back as "no such row" and the route
answers 404. Adding a subject kind means adding a resolver here; there is no
path to a write that skips one.

Everything else is counting, and it is counted live (`ix_reactions_subject`),
for the reason `ChallengeStats.participant_count` cannot be the leaderboard's
number: a stored counter is a number that can drift, and this one is cheap.

`counts_for` / `liked_subject_ids` take a *list* of subject ids on purpose --
a card list asks both questions once for the whole page, not once per card.
"""

from collections.abc import Awaitable, Callable

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.challenge import Challenge
from app.models.reaction import Reaction, ReactionKind, ReactionSubject


async def _challenge_exists(db: AsyncSession, subject_id: int, user_id: int) -> bool:
    # Imported here rather than at module scope: `routers/challenge.py`
    # imports models, and a top-level import would close the cycle.
    from app.routers.challenge import challenge_visibility_filter

    row = await db.execute(
        select(Challenge.id)
        .where(Challenge.id == subject_id)
        .where(challenge_visibility_filter(user_id))
    )
    return row.scalar_one_or_none() is not None


#: subject kind -> "may this member see this subject at all"
SUBJECT_RESOLVERS: dict[
    ReactionSubject, Callable[[AsyncSession, int, int], Awaitable[bool]]
] = {
    ReactionSubject.CHALLENGE: _challenge_exists,
}


async def subject_is_visible(
    db: AsyncSession, subject: ReactionSubject, subject_id: int, user_id: int
) -> bool:
    return await SUBJECT_RESOLVERS[subject](db, subject_id, user_id)


async def set_reaction(
    db: AsyncSession,
    *,
    user_id: int,
    subject: ReactionSubject,
    subject_id: int,
    liked: bool,
    kind: ReactionKind = ReactionKind.LIKE,
) -> None:
    """Make the member's reaction *be* `liked`. Idempotent in both directions.

    Stated as a desired state rather than a toggle: a toggle over a flaky
    connection un-likes what the retry liked, and two devices disagree about
    whose tap was second. The button sends what it wants to be true.

    Does not commit -- the caller owns the transaction, like `notify()`.
    """
    existing = await db.execute(
        select(Reaction).where(
            Reaction.user_id == user_id,
            Reaction.subject_type == subject.value,
            Reaction.subject_id == subject_id,
            Reaction.kind == kind.value,
        )
    )
    row = existing.scalar_one_or_none()

    if liked and row is None:
        db.add(
            Reaction(
                user_id=user_id,
                subject_type=subject.value,
                subject_id=subject_id,
                kind=kind.value,
            )
        )
        try:
            await db.flush()
        except IntegrityError:
            # Two taps racing on one seat: the unique constraint already
            # holds the invariant, so the loser is a success, not a 409.
            await db.rollback()
    elif not liked and row is not None:
        await db.delete(row)


async def counts_for(
    db: AsyncSession,
    subject: ReactionSubject,
    subject_ids: list[int],
    kind: ReactionKind = ReactionKind.LIKE,
) -> dict[int, int]:
    """`{subject_id: count}`, one query for a whole page of cards."""
    if not subject_ids:
        return {}
    rows = await db.execute(
        select(Reaction.subject_id, func.count(Reaction.id))
        .where(
            Reaction.subject_type == subject.value,
            Reaction.kind == kind.value,
            Reaction.subject_id.in_(subject_ids),
        )
        .group_by(Reaction.subject_id)
    )
    return {sid: n for sid, n in rows.all()}


async def liked_subject_ids(
    db: AsyncSession,
    user_id: int | None,
    subject: ReactionSubject,
    subject_ids: list[int],
    kind: ReactionKind = ReactionKind.LIKE,
) -> set[int]:
    """Which of these the member has liked. A signed-out visitor: none."""
    if user_id is None or not subject_ids:
        return set()
    rows = await db.execute(
        select(Reaction.subject_id).where(
            Reaction.user_id == user_id,
            Reaction.subject_type == subject.value,
            Reaction.kind == kind.value,
            Reaction.subject_id.in_(subject_ids),
        )
    )
    return set(rows.scalars().all())


async def reaction_state(
    db: AsyncSession,
    user_id: int | None,
    subject: ReactionSubject,
    subject_id: int,
    kind: ReactionKind = ReactionKind.LIKE,
) -> tuple[int, bool]:
    """`(count, liked_by_me)` for one subject -- what the button renders."""
    counts = await counts_for(db, subject, [subject_id], kind)
    liked = await liked_subject_ids(db, user_id, subject, [subject_id], kind)
    return counts.get(subject_id, 0), subject_id in liked
