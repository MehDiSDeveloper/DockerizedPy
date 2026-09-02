"""One member's reaction to one thing.

Deliberately **polymorphic and typed**, not a `challenge_likes` table: the
next thing worth liking (a check-in, a group, a comment) is a new value of
`ReactionSubject` and nothing else -- no second table, no second router, no
second piece of page JS. The price is that there is no FK to the subject,
which is why `app/reactions.py` resolves every subject through a registry
that composes the subject's *own* visibility filter before a row is written.

`kind` is the second axis, for the same reason: «لایک» is the only one today,
but a reaction table whose only kind is baked into its name is a table that
gets copied the first time somebody wants a second one.

There is no counter column anywhere. Counts are read live off this table
through `ix_reactions_subject`, the same call the leaderboard makes about
`CheckIns` -- a stored count is a number that can be wrong, and this table is
small and indexed for exactly this question.
"""

import enum

from sqlalchemy import Column, ForeignKey, Index, Integer, String, UniqueConstraint
from sqlalchemy.orm import relationship

from app.models.audit_base import AuditBase


class ReactionSubject(str, enum.Enum):
    """What kind of thing was reacted to. English codes (D7)."""

    CHALLENGE = "challenge"


class ReactionKind(str, enum.Enum):
    """Which reaction. One today; the axis exists so a second is a row here."""

    LIKE = "like"


class Reaction(AuditBase):
    __tablename__ = "Reactions"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("Users.id"), nullable=False, index=True)
    #: Plain `String`, like every other enum-shaped column in this schema --
    #: a native PG enum here would break the SQLite test database.
    subject_type = Column(String(32), nullable=False)
    subject_id = Column(Integer, nullable=False)
    kind = Column(String(32), nullable=False, default=ReactionKind.LIKE.value)

    user = relationship("User")

    __table_args__ = (
        # The idempotency guarantee: liking twice is one row, so a double tap
        # (or two devices) cannot inflate a count.
        UniqueConstraint(
            "user_id", "subject_type", "subject_id", "kind", name="uq_reaction_once"
        ),
        # The count query and the "did I like it" query are both this index.
        Index("ix_reactions_subject", "subject_type", "subject_id", "kind"),
    )
