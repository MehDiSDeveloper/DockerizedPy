"""Groups: a space a real organisation gathers its people in.

A company running a step challenge for its staff, a school running a reading
challenge for a class. Four tables, and each one exists because the thing it
holds cannot be derived from the others:

* :class:`Group` -- the identity (name, description, emblem, what kind of
  organisation it is) and the record of ownership.
* :class:`GroupMembership` -- who is in it and what they are *in here*. The
  **third** role axis in the app, deliberately never merged with the other
  two (see ``app/permissions.py``).
* :class:`GroupInvite` -- a link with a capacity, which is how most people
  actually get in.
* :class:`GroupJoinRequest` -- the other way in, for someone with no link.

**A membership row means membership, full stop.** The obvious alternative --
a ``status`` column with ``pending`` on the same table -- was rejected for
the reason ``notify()`` is the one door in: every query that asks "is this
person in this group" would then have to *remember* to add
``status = 'active'``, and the one that forgets is a silent access-control
bug rather than a visible error. A pending request is a different fact about
a different moment, so it gets a table of its own and the membership table
stays a plain set.

**Groups are not public and are not searchable.** There is no listing of all
groups anywhere in the app: ``GET /groups/`` answers *your* groups, the
detail routes compose ``group_visibility_filter``, and the only way in from
outside is an invite code, which is unguessable. That is why a
``Group.visibility`` column does not exist -- there is no second state for it
to be in.
"""

import enum

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import relationship

from app.avatars import AVATAR_ID_MAX_LENGTH
from app.models.audit_base import AuditBase


class GroupKind(str, enum.Enum):
    """What sort of organisation this is.

    English codes, like every enum introduced after ``ChallengeCategory``
    (D7); the Farsi labels live in the surface that shows them. It is a
    label, not a permission -- nothing in the app branches on it, and it
    exists so a member reading a list of four groups can tell the company
    from the running club at a glance.
    """

    COMPANY = "company"
    SCHOOL = "school"
    TEAM = "team"
    OTHER = "other"


class GroupRole(str, enum.Enum):
    """What a member may do *inside one group*.

    The third role axis, and it stays separate from the other two for the
    same reason they are separate from each other: an app-wide operator is
    not the owner of everybody's company, and the owner of a challenge is not
    an administrator of the group it happens to sit in. ``app/permissions.py``
    is the only place any of the three becomes an answer.

    ``ADMIN`` sits strictly below ``OWNER``: it may run the group day to day
    (invite, approve, remove plain members, create challenges) but not touch
    who administers it. Handing out admin, taking it back, transferring the
    group and deleting it are the owner's alone -- an administrator who could
    promote themselves is not an administrator, they are an owner with a
    delay.
    """

    MEMBER = "member"
    ADMIN = "admin"
    OWNER = "owner"


class JoinRequestStatus(str, enum.Enum):
    """Where one request got to. Terminal states are kept, not deleted.

    An approved or rejected row is the record that the decision was made and
    by whom (``decided_by_user_id``), which is the only place that answers
    "who let this person in" once the membership row itself says nothing
    about how it came to exist.
    """

    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


class Group(AuditBase):
    __tablename__ = "Groups"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(60), nullable=False)
    description = Column(String(500), nullable=True)
    kind = Column(String(16), nullable=False, default=GroupKind.OTHER.value)

    # An id from the *same* catalogue `Users.avatar` draws from
    # (`app/avatars.py`), never a path or a URL -- so there is one picture
    # catalogue in the app, one validator at one write boundary, and one
    # `avatar_url` that answers the placeholder for NULL. A second catalogue
    # would mean a second asset pipeline and a second way for a stored value
    # to turn into a path on disk, for no gain: the abstract styles in the
    # set read perfectly well as an emblem.
    emblem = Column(String(AVATAR_ID_MAX_LENGTH), nullable=True)

    # The record of ownership, exactly as `Challenge.owner_id` is: NOT NULL,
    # always present, and resolved against `GroupMembership.role` by
    # `app.permissions.group_role`. Kept alongside the membership row rather
    # than only on it, because an owner's membership row is one DELETE away
    # from leaving a group with nobody who can administer it.
    owner_id = Column(Integer, ForeignKey("Users.id"), nullable=False)

    owner = relationship("User", foreign_keys=[owner_id])
    memberships = relationship(
        "GroupMembership", back_populates="group", cascade="all, delete-orphan"
    )
    invites = relationship(
        "GroupInvite", back_populates="group", cascade="all, delete-orphan"
    )
    join_requests = relationship(
        "GroupJoinRequest", back_populates="group", cascade="all, delete-orphan"
    )
    # Challenges are deliberately **not** cascaded: deleting a group must not
    # destroy the logged history of everyone who ran its challenges, which is
    # the same rule `DELETE /challenges/{id}` follows with its 409. The
    # router refuses to delete a group that still has challenges instead.
    challenges = relationship("Challenge", back_populates="group")
    notifications = relationship(
        "Notification", back_populates="group", cascade="all, delete-orphan"
    )


class GroupMembership(AuditBase):
    """One person's place in one group. The row's existence *is* membership."""

    __tablename__ = "GroupMemberships"
    __table_args__ = (
        UniqueConstraint("user_id", "group_id", name="uq_user_group"),
        # Every access check in the group subsystem is "which groups is this
        # member in" (`group_ids_for`), and every roster read is "who is in
        # this group" -- one index per direction.
        Index("ix_group_memberships_user", "user_id"),
        Index("ix_group_memberships_group", "group_id"),
    )

    id = Column(Integer, primary_key=True, index=True)
    group_id = Column(Integer, ForeignKey("Groups.id"), nullable=False)
    user_id = Column(Integer, ForeignKey("Users.id"), nullable=False)
    role = Column(String(16), nullable=False, default=GroupRole.MEMBER.value)

    # **Being in the group and being let into its standing challenges are two
    # different answers, given at two different moments.** Somebody who
    # arrives by a link or an approved request is a member immediately -- they
    # can read the group, and an administrator may put them into a challenge
    # by name -- but they are not swept into the «همه اعضا» challenges until
    # an administrator says so a second time. That is what this column holds,
    # and it is a column rather than a second membership state for the reason
    # the join request is a table of its own: a membership row must keep
    # meaning membership, full stop, so nothing that asks "is this person in
    # this group" has to remember a second condition. Only
    # `apply_standing_audience` reads it.
    #
    # ``server_default`` is **1** while the ORM default is ``False``, and the
    # asymmetry is deliberate: the column is added to a populated table by
    # `sync_sqlite_schema`, and every membership that predates it was already
    # picking up the group's standing challenges -- flipping them all to
    # "waiting for approval" would be a silent retroactive demotion. New rows
    # are written by code that states the answer.
    is_trusted = Column(
        Boolean, nullable=False, default=False, server_default=text("1")
    )
    trusted_at = Column(DateTime(timezone=True), nullable=True)
    trusted_by_user_id = Column(Integer, ForeignKey("Users.id"), nullable=True)

    group = relationship("Group", back_populates="memberships")
    user = relationship("User", foreign_keys=[user_id])
    trusted_by = relationship("User", foreign_keys=[trusted_by_user_id])


class GroupInvite(AuditBase):
    """A link that lets a bounded number of people in without approval.

    **The capacity is real.** ``max_uses`` NULL means unlimited; any other
    value is enforced by a conditional UPDATE at the moment of use
    (``consume_invite`` in ``app/routers/group.py``), never by reading
    ``uses`` and then writing it -- two people tapping a one-seat link at the
    same instant is exactly the race a read-then-write loses, and a capacity
    that can be exceeded is not a capacity.
    """

    __tablename__ = "GroupInvites"
    __table_args__ = (Index("ix_group_invites_group", "group_id"),)

    id = Column(Integer, primary_key=True, index=True)
    group_id = Column(Integer, ForeignKey("Groups.id"), nullable=False)

    # The whole secret. Unguessable and unique, because it is the only key
    # that reaches a group from outside -- see the module docstring on why
    # groups have no public listing for it to be an alternative to.
    code = Column(String(43), nullable=False, unique=True, index=True)

    # Optional, so a link can be introduced ("دعوت تیم فروش") without
    # becoming a second name for the group.
    label = Column(String(60), nullable=True)

    # NULL = unlimited. A number = that many joins and no more.
    max_uses = Column(Integer, nullable=True)
    uses = Column(Integer, nullable=False, default=0)

    # **A link that asks rather than admits.** With this set the code still
    # reaches the group's landing page, but the only button on it is «درخواست
    # عضویت» -- the group's *public* link, the one an administrator is happy
    # to put in a signature or on a noticeboard, where the door is the
    # request queue rather than the link itself. It is a flag on the same
    # table rather than a second kind of link because everything else about
    # it is identical (a code, a label, a capacity, revocation, one landing
    # page), and a second table would be a second place to keep those rules
    # true.
    requires_approval = Column(
        Boolean, nullable=False, default=False, server_default=text("0")
    )

    expires_at = Column(DateTime(timezone=True), nullable=True)
    # Revoking is a stamp rather than a DELETE: an admin looking at the list
    # needs to see that the link they handed out is dead, and a row that
    # vanishes reads as one that never existed.
    revoked_at = Column(DateTime(timezone=True), nullable=True)

    created_by_user_id = Column(Integer, ForeignKey("Users.id"), nullable=False)

    group = relationship("Group", back_populates="invites")
    created_by = relationship("User", foreign_keys=[created_by_user_id])


class GroupJoinRequest(AuditBase):
    """Someone asking to be let in, and what was decided."""

    __tablename__ = "GroupJoinRequests"
    __table_args__ = (
        # One *open* request per person per group is the rule the router
        # enforces; the constraint cannot express "one pending" portably, so
        # the index is here to make the check cheap rather than to be it.
        Index("ix_group_requests_group_status", "group_id", "status"),
        Index("ix_group_requests_user", "user_id"),
    )

    id = Column(Integer, primary_key=True, index=True)
    group_id = Column(Integer, ForeignKey("Groups.id"), nullable=False)
    user_id = Column(Integer, ForeignKey("Users.id"), nullable=False)
    status = Column(
        String(16), nullable=False, default=JoinRequestStatus.PENDING.value
    )
    # Who answered it. NULL while pending -- and kept afterwards, because
    # "who let this person in" has no other answer once the membership row
    # exists (it says nothing about how it came to).
    decided_by_user_id = Column(Integer, ForeignKey("Users.id"), nullable=True)
    decided_at = Column(DateTime(timezone=True), nullable=True)

    group = relationship("Group", back_populates="join_requests")
    user = relationship("User", foreign_keys=[user_id])
    decided_by = relationship("User", foreign_keys=[decided_by_user_id])
