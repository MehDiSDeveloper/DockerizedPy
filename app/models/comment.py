"""One thing somebody said, under something.

Polymorphic and typed for the reason `Reactions` is (see
`app/models/reaction.py`): the next thing worth commenting on -- a check-in,
a group, a whole enrollment -- is a new value of :class:`CommentSubject` and
a resolver in ``app/comments.py``, not a second table, router and piece of
page JS. There is no FK to the subject, which is exactly why every read and
write goes through that module's resolver registry, composing the subject's
*own* visibility filter.

**Threads are one level deep, on purpose.** `parent_id` is a self FK, and
``app/comments.py`` re-points a reply-to-a-reply at its root before writing.
Arbitrary depth costs a recursive query on every read and buys a shape the
UI would have to flatten anyway; one level is the shape Instagram and
Telegram already taught people to read. Undoing that decision later is a
change to one function, not to this table.

There is no counter column and no `reply_count`: counts are read live off
`ix_comments_subject`, the same trade `Reactions` makes.
"""

import enum

from sqlalchemy import Column, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import relationship

from app.models.audit_base import AuditBase


class CommentSubject(str, enum.Enum):
    """What was commented on. English codes (D7)."""

    CHALLENGE = "challenge"


class Comment(AuditBase):
    __tablename__ = "Comments"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("Users.id"), nullable=False, index=True)

    #: Plain `String`, like every other enum-shaped column here -- a native
    #: PG enum would break the SQLite test database.
    subject_type = Column(String(32), nullable=False)
    subject_id = Column(Integer, nullable=False)

    #: NULL for a root comment; a root's id for a reply. Never a reply's id.
    parent_id = Column(
        Integer, ForeignKey("Comments.id", ondelete="CASCADE"), nullable=True
    )

    #: The text, if there is any. NULL for a sticker-only comment; the
    #: "at least one of the two" rule is held at the write boundary
    #: (`schemas/comment.py`), where every other content rule in this app is.
    body = Column(Text, nullable=True)
    #: An id from the fixed catalogue in `app/stickers.py`, never a path --
    #: the same contract `Users.avatar` has.
    sticker = Column(String(32), nullable=True)

    user = relationship("User")
    #: Deleting a root takes its replies with it: a thread whose question is
    #: gone is unreadable, and the alternative (a tombstone row) is a second
    #: state every query would have to remember to exclude.
    #: Read-only here: deleting a thread is `delete_comment` in
    #: `app/comments.py` issuing one DELETE over the children, not an ORM
    #: cascade -- SQLite runs with foreign keys off, so a database-level
    #: `ON DELETE CASCADE` would be enforced on Postgres and silently
    #: orphan every reply in dev, test and the deploy (CLAUDE.md: every
    #: environment is SQLite).
    replies = relationship("Comment", back_populates="parent")
    parent = relationship("Comment", back_populates="replies", remote_side=[id])

    __table_args__ = (
        # Every read is "the roots of this subject, newest first" or "the
        # replies of these roots" -- one index covers both, because
        # `parent_id` is the second column and IS NULL is a range on it.
        Index(
            "ix_comments_subject",
            "subject_type",
            "subject_id",
            "parent_id",
            "created_at",
        ),
    )
