import enum

from sqlalchemy import Column, Integer, String
from sqlalchemy.orm import Mapped, relationship

from app.avatars import AVATAR_ID_MAX_LENGTH
from app.models.audit_base import AuditBase
from app.models.enrollment import Enrollment


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

    owned_challenges = relationship("Challenge", back_populates="owner")
    enrollments: Mapped[list[Enrollment]] = relationship(
        "Enrollment", back_populates="user"
    )
