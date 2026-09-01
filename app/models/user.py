import enum

from sqlalchemy import Column, DateTime, Integer, String
from sqlalchemy.orm import Mapped, relationship

from app.avatars import AVATAR_ID_MAX_LENGTH
from app.models.audit_base import AuditBase
from app.models.challenge import JSONVariant
from app.models.enrollment import Enrollment
from app.phone import MOBILE_MAX_LENGTH


class UserRole(str, enum.Enum):
    """What an account may do *across* the app.

    Deliberately the smaller of the two role axes -- the one that answers
    "may you run the place", not "what are you in this challenge". The
    per-challenge axis is `Enrollment.role`, and the two never merge: an
    admin is a plain participant inside someone else's challenge, and an
    owner has no reach outside their own. Collapsing them into one column is
    what makes an admin accidentally the owner of everything.

    English codes, like every enum introduced after `ChallengeCategory`
    (D7) -- the Farsi labels live in the template that shows them.
    """

    MEMBER = "member"
    ADMIN = "admin"


class User(AuditBase):
    __tablename__ = "Users"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(50), nullable=False)
    email = Column(String(100), unique=True, nullable=True)
    password_hash = Column(String(255), nullable=False)
    # Id of a picture in `app.avatars.AVATAR_IDS`, never a path or a URL.
    # NULL is permanent and legitimate -- every account predating this column
    # has it, and picking at signup is optional -- so readers go through
    # `avatar_url()`, which answers the placeholder instead of failing.
    avatar = Column(String(AVATAR_ID_MAX_LENGTH), nullable=True)

    # The verified mobile that *identifies* this account -- the OTP flow's
    # whole notion of who you are. Canonical `+989…` (`app.phone`), UNIQUE, so
    # one number is one account no matter which spelling of it was typed.
    #
    # Deliberately **not** `phone` below, which stays what CLAUDE.md always
    # described: an optional free-text contact detail a member types on their
    # profile, permissive enough for a number abroad, and editable through
    # `PATCH /users/{id}` like any other profile field. That is exactly why it
    # cannot be the credential -- a column any member may write to arbitrary
    # text is a column any member could write somebody else's login into. This
    # one has a single door (`app/routers/otp.py`, after a code was proved)
    # and is absent from every `User*` write schema.
    #
    # NULL is a permanent, legitimate state, like `avatar`'s: every account
    # that predates this column has it, and email/password signup still does
    # not ask for a number. NULLs do not collide in a UNIQUE index on either
    # backend, which is also what lets `sync_sqlite_schema` add this to a
    # populated table.
    mobile = Column(String(MOBILE_MAX_LENGTH), unique=True, nullable=True, index=True)
    # When the code proving that number was accepted. Kept beside it rather
    # than folded into a boolean because "since when" is the question support
    # actually gets, and a re-verification overwrites it honestly.
    mobile_verified_at = Column(DateTime(timezone=True), nullable=True)

    # The app-wide role. A plain `String` with a Python-side default, not a
    # native enum: the same reason `Visibility`/`LifecycleStatus` are strings
    # (SQLite cannot represent a PG enum, and the test suite runs on SQLite).
    # `member` is the default so every existing row -- and every signup --
    # lands on today's behaviour, and `bootstrap_db` can add the column to a
    # populated table. Nothing that a member does anywhere in the app reads
    # this; it is checked only by `app.permissions.can`.
    role = Column(String(16), nullable=False, default=UserRole.MEMBER.value)

    # Optional contact/where-you-are details, shown only on the member's own
    # profile (`profile_visibility_filter`) and deliberately kept out of
    # `UserPublicRead`. Every one of them is nullable and stays nullable: the
    # app works fully without any of them, so the profile section that edits
    # them is an offer, not a form to complete. `address` is the free-text
    # precise location; `city`/`country` are the coarse one.
    phone = Column(String(20), nullable=True)
    country = Column(String(60), nullable=True)
    city = Column(String(60), nullable=True)
    address = Column(String(255), nullable=True)

    # Which notification kinds this member has switched *off*. A mute list
    # rather than a subscribe list, so a kind added later is on for everyone
    # without a backfill -- the same "existing rows land on today's
    # behaviour" rule `role` follows. NULL is a legitimate, permanent state
    # meaning "nothing muted" (like `avatar`'s NULL), which is also what lets
    # `sync_sqlite_schema` add the column to a populated table.
    #
    # A JSON list of `NotificationKind` values on the recipient, not a table
    # of preference rows: the whole set is read and written together, always
    # for exactly one member, and it is `notify()` -- the one door in -- that
    # reads it. See `app/notifications.py`.
    muted_notification_kinds = Column(JSONVariant, nullable=True)

    owned_challenges = relationship("Challenge", back_populates="owner")
    # Which groups this member is in. Read through `group_ids_for` in
    # `app/routers/group.py` rather than off this relationship in a query --
    # the visibility filters need it as a SQL subquery, not a loaded list.
    group_memberships = relationship(
        "GroupMembership",
        foreign_keys="GroupMembership.user_id",
        back_populates="user",
        cascade="all, delete-orphan",
    )
    enrollments: Mapped[list[Enrollment]] = relationship(
        "Enrollment", back_populates="user"
    )
