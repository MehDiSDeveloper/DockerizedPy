"""«نمرهٔ تعهد» -- how much of what somebody promised, they kept.

Two grouped counts, read live off ``CheckIns``, and **nothing is stored**.
That is the ``compute_streaks`` rule applied to a number that is even more
tempting to cache: a stored score would have to be rewritten by every
approval, every rejection, every withdrawn report and every act that changed
its review mode, and the first path that forgot would show a member a figure
about themselves that is simply wrong.

**What the denominator is, and what it honestly is not.** A report is
*settled* when a decision has been reached about it: a completed report that
was auto-accepted, approved or rejected, or a skip -- which is settled the
moment it is made, because the member decided and said so. A report still
waiting on a referee is in neither half, so a slow referee never drags
somebody's score down.

What is **not** in the denominator is an occurrence nobody ever recorded. A
day that came and went in silence does not count against the score. That is a
deliberate limit and not an oversight: deriving every expected occurrence for
every enrollment means walking the cadence engine per enrollment per page
render, and the number this screen is trying to give somebody -- "of the
things you reported on, how many did you keep" -- is answerable with two
indexed counts. «روند فعالیت» on the home dashboard is the screen that shows
the silence, and it shows it honestly, as gaps.

**The breakdown is by how hard the proof was.** A score of 100% is worth
knowing more about, and what it is worth knowing is *under what standard*.
:func:`proof_level` folds the two stored axes -- ``proof_kind`` and
``review_mode`` -- into the three levels a member can actually tell apart:

* ``self`` -- you said so.
* ``photo`` -- you sent a picture, and nobody reviewed it.
* ``referee`` -- somebody else looked and agreed.

Referee is the top level whatever the proof kind is: a human who said yes is
a stronger claim than a file that nobody opened. Adding a proof kind does not
add a level unless it changes *who is convinced*, which is the thing the
levels are about.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.challenge import Challenge, ProofKind, ReviewMode
from app.models.checkin import CheckIn
from app.models.enrollment import Enrollment
from app.verification import (
    COUNTED_VERDICTS,
    DONE_STATE,
    SETTLED_VERDICTS,
    SKIPPED_STATE,
)

LEVEL_SELF = "self"
LEVEL_PHOTO = "photo"
LEVEL_REFEREE = "referee"

#: Weakest first -- the order the breakdown is read in, and the order the
#: bars are drawn in.
LEVELS = (LEVEL_SELF, LEVEL_PHOTO, LEVEL_REFEREE)

LEVEL_LABELS = {
    LEVEL_SELF: "خوداظهاری",
    LEVEL_PHOTO: "با عکس",
    LEVEL_REFEREE: "با تأیید ناظر",
}

LEVEL_ICONS = {
    LEVEL_SELF: "check",
    LEVEL_PHOTO: "camera",
    LEVEL_REFEREE: "shieldCheck",
}


def proof_level(proof_kind: str | None, review_mode: str | None) -> str:
    """The one word for how hard this act's evidence is to produce."""
    if review_mode == ReviewMode.REFEREE.value:
        return LEVEL_REFEREE
    if proof_kind == ProofKind.PHOTO.value:
        return LEVEL_PHOTO
    return LEVEL_SELF


def _level_expr():
    """The same fold, in SQL, so the grouping is one query and not three."""
    return case(
        (Challenge.review_mode == ReviewMode.REFEREE.value, LEVEL_REFEREE),
        (Challenge.proof_kind == ProofKind.PHOTO.value, LEVEL_PHOTO),
        else_=LEVEL_SELF,
    )


@dataclass
class LevelScore:
    """One row of the breakdown."""

    level: str
    kept: int = 0
    settled: int = 0

    @property
    def rate(self) -> int:
        """Whole percent. Rounded, because a score with a decimal in it
        invites being read as more precise than a count of a few dozen
        reports can be."""
        return round(100 * self.kept / self.settled) if self.settled else 0

    @property
    def label(self) -> str:
        return LEVEL_LABELS[self.level]

    @property
    def icon(self) -> str:
        return LEVEL_ICONS[self.level]


@dataclass
class CommitmentScore:
    """What one member kept, overall and by how it was proved."""

    kept: int = 0
    settled: int = 0
    pending: int = 0
    by_level: list[LevelScore] = field(default_factory=list)

    @property
    def rate(self) -> int:
        return round(100 * self.kept / self.settled) if self.settled else 0

    @property
    def has_history(self) -> bool:
        """Whether there is anything to show at all.

        A member with no settled reports gets no score rather than a 0%,
        which would read as a failure rather than as a beginning -- the same
        call ``avatar_url(None)`` makes about an unset picture.
        """
        return self.settled > 0


async def commitment_score(db: AsyncSession, user_id: int) -> CommitmentScore:
    """This member's kept-promise rate, overall and per proof level.

    One grouped query for the breakdown plus one count for the pending
    figure. The join to ``Challenges`` is what supplies the level, and it is
    the reason ``CheckIn.challenge_id`` is denormalised onto the row -- the
    same reason the velocity query needs no join through ``Enrollments``.
    """
    settled_case = case(
        (
            (CheckIn.state == SKIPPED_STATE)
            | (
                (CheckIn.state == DONE_STATE)
                & CheckIn.verdict.in_(SETTLED_VERDICTS)
            ),
            1,
        ),
        else_=0,
    )
    kept_case = case(
        (
            (CheckIn.state == DONE_STATE) & CheckIn.verdict.in_(COUNTED_VERDICTS),
            1,
        ),
        else_=0,
    )
    level = _level_expr()

    rows = (
        await db.execute(
            select(
                level.label("level"),
                func.sum(kept_case),
                func.sum(settled_case),
            )
            .select_from(CheckIn)
            .join(Enrollment, Enrollment.id == CheckIn.enrollment_id)
            .join(Challenge, Challenge.id == CheckIn.challenge_id)
            .where(Enrollment.user_id == user_id)
            .group_by(level)
        )
    ).all()

    per_level = {lvl: LevelScore(level=lvl) for lvl in LEVELS}
    total = CommitmentScore()
    for lvl, kept, settled in rows:
        row = per_level.setdefault(lvl, LevelScore(level=lvl))
        row.kept = int(kept or 0)
        row.settled = int(settled or 0)
        total.kept += row.kept
        total.settled += row.settled

    total.pending = (
        await db.execute(
            select(func.count())
            .select_from(CheckIn)
            .join(Enrollment, Enrollment.id == CheckIn.enrollment_id)
            .where(
                Enrollment.user_id == user_id,
                CheckIn.state == DONE_STATE,
                CheckIn.verdict == "pending",
            )
        )
    ).scalar_one()

    # Only the levels this member has actually used. A row reading «با تأیید
    # ناظر ۰٪» for somebody who has never been in such an act is a figure
    # about nothing, and it reads as a failure.
    total.by_level = [per_level[lvl] for lvl in LEVELS if per_level[lvl].settled]
    return total
