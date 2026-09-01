import enum

from sqlalchemy import Column, DateTime, ForeignKey, Index, Integer, String
from sqlalchemy.orm import relationship

from app.models.audit_base import AuditBase


class NotificationKind(str, enum.Enum):
    """What happened. **Not** what the notification says.

    English codes, like every enum introduced after ``ChallengeCategory``
    (D7); the Farsi sentence each one turns into lives in
    ``app/notifications.py`` and nowhere else.

    Each member is one *event in the domain*, and there is deliberately no
    kind for "something changed" -- a kind whose meaning depends on a stored
    message is a kind that cannot be reworded, filtered, or counted. Adding
    a kind means adding a row to ``NOTIFICATION_META`` and a ``notify()``
    call at the one place that event actually happens.
    """

    #: Someone enrolled in a challenge you own.
    ENROLLMENT_JOINED = "enrollment_joined"
    #: Someone left a challenge you own.
    ENROLLMENT_LEFT = "enrollment_left"
    #: A challenge you are enrolled in was archived by its owner or a moderator.
    CHALLENGE_ARCHIVED = "challenge_archived"
    #: A challenge you are enrolled in was (re)opened -- draft/archived -> active.
    CHALLENGE_ACTIVATED = "challenge_activated"

    # ---- Groups -------------------------------------------------------
    # Each of these is a place a member could not otherwise find out. A
    # group happens *around* you: somebody else approves you, promotes you,
    # hands you the group, or puts you in a challenge -- none of which is
    # visible on a page you would have been looking at.
    #: Somebody asked to join a group you administer.
    GROUP_JOIN_REQUESTED = "group_join_requested"
    #: Your request to join a group was approved.
    GROUP_JOIN_APPROVED = "group_join_approved"
    #: Your request to join a group was turned down.
    GROUP_JOIN_REJECTED = "group_join_rejected"
    #: An administrator added you to a group directly, by your number.
    #:
    #: Distinct from ``GROUP_JOIN_APPROVED``: nothing was asked for here, so
    #: a sentence about "your request" would describe an event that never
    #: happened -- the row stores the event, so the two events are two kinds.
    GROUP_MEMBER_ADDED = "group_member_added"
    #: Your role inside a group changed.
    GROUP_ROLE_CHANGED = "group_role_changed"
    #: A group was handed over to you.
    GROUP_OWNERSHIP_TRANSFERRED = "group_ownership_transferred"
    #: You were put into an *optional* group challenge.
    GROUP_CHALLENGE_ASSIGNED = "group_challenge_assigned"
    #: You were put into a *mandatory* group challenge.
    #:
    #: Two kinds rather than one kind with a stored adjective, for the rule
    #: this whole model is built on: the row stores the event, never the
    #: sentence. It also means the two get their own switches, so a member
    #: can silence the optional invitations and still be told about the
    #: obligations -- which is the pair anybody would actually want.
    GROUP_CHALLENGE_REQUIRED = "group_challenge_required"


class Notification(AuditBase):
    """One thing that happened, addressed to one member.

    **The row stores the event, never the sentence.** There is no `title` or
    `body` column: the words are derived from ``kind`` plus the actor and the
    challenge at render time (``app/notifications.py``), so rewording a
    notification -- or translating one -- is a code change rather than a data
    migration over every row ever written. It is the same rule the derived
    challenge status follows: one fact, one place it becomes text.

    `created_at` from :class:`AuditBase` is the "when", and the feed is
    ordered by ``newest_first(Notification)`` like every other paginated list
    in the app. `updated_at` is untouched here -- the only mutation a
    notification has is `read_at`, which is its own column precisely so the
    ordering key stays immutable while rows are marked read underneath a
    scroller.
    """

    __tablename__ = "Notifications"
    __table_args__ = (
        # The feed is always "this member's, newest first", and the bell's
        # count is "this member's, unread" -- both are served by the same
        # leading column, so one index covers the two reads the app makes.
        Index("ix_notifications_user_created", "user_id", "created_at"),
    )

    id = Column(Integer, primary_key=True, index=True)

    # The recipient. Every read in the app is `WHERE user_id = <session
    # user>`; there is no client-supplied recipient anywhere, the same rule
    # `routers/enrollment.py` follows.
    user_id = Column(Integer, ForeignKey("Users.id"), nullable=False, index=True)

    kind = Column(String(32), nullable=False)

    # Who caused it. Nullable because not every event has a person behind it
    # (a deadline reminder would not), and because the renderer must degrade
    # to "someone" rather than fail. A real FK, so an account that has acted
    # on other people cannot be deleted out from under their feed -- which is
    # the behaviour `DELETE /users/{id}` already has for every other kind of
    # history it leaves behind (it catches the IntegrityError and answers 409).
    actor_user_id = Column(Integer, ForeignKey("Users.id"), nullable=True)

    # What it is about. Nullable for the same reason `actor_user_id` is, and
    # cascaded from the challenge (see `Challenge.notifications`): a
    # notification about a challenge that no longer exists cannot be read,
    # opened, or worded.
    challenge_id = Column(Integer, ForeignKey("Challenges.id"), nullable=True)

    # Which group it is about, for the kinds that are about one. Nullable and
    # cascaded exactly like `challenge_id`, and for exactly its reasons: not
    # every event has a group behind it, and a sentence about a group that no
    # longer exists is a sentence about nothing.
    group_id = Column(Integer, ForeignKey("Groups.id"), nullable=True)

    # NULL means unread. A timestamp rather than a boolean because "when did
    # you see this" is the question a support request actually asks, and it
    # costs the same column.
    read_at = Column(DateTime(timezone=True), nullable=True)

    user = relationship("User", foreign_keys=[user_id])
    group = relationship("Group", back_populates="notifications")
    actor = relationship("User", foreign_keys=[actor_user_id])
    challenge = relationship("Challenge", back_populates="notifications")
