import enum

from sqlalchemy import (
    JSON,
    Column,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    Numeric,
    String,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import relationship

from app.models.audit_base import AuditBase

JSONVariant = JSON().with_variant(JSONB, "postgresql")


class ChallengeCategory(str, enum.Enum):
    FITNESS = "سلامت جسمانی"
    NUTRITION = "تغذیه"
    MENTAL_HEALTH = "سلامت ذهنی"
    PRODUCTIVITY = "بهره‌وری"
    SOCIAL_RESPONSIBILITY = "مسئولیت اجتماعی"
    OTHER = "سایر"


class Visibility(str, enum.Enum):
    PRIVATE = "private"
    UNLISTED = "unlisted"
    PUBLIC = "public"


class LifecycleStatus(str, enum.Enum):
    DRAFT = "draft"
    ACTIVE = "active"
    ARCHIVED = "archived"


class CadenceKind(str, enum.Enum):
    ONCE = "once"
    SCHEDULE = "schedule"
    RECURRING_DAYS = "recurring_days"
    RECURRING_QUOTA = "recurring_quota"


class Challenge(AuditBase):
    __tablename__ = "Challenges"

    id = Column(Integer, primary_key=True, index=True)
    title = Column(String(50), nullable=False)
    description = Column(String, nullable=True)
    rules = Column(String, nullable=True)
    due_date = Column(DateTime(timezone=True), nullable=True)
    owner_id = Column(Integer, ForeignKey("Users.id"), nullable=False)
    category = Column(
        Enum(ChallengeCategory), nullable=False, default=ChallengeCategory.OTHER
    )

    cadence_kind = Column(String(32), nullable=False, default=CadenceKind.ONCE.value)
    cadence = Column(JSONVariant, nullable=False, default=dict)
    visibility = Column(String(16), nullable=False, default=Visibility.PUBLIC.value)
    lifecycle_status = Column(
        String(16), nullable=False, default=LifecycleStatus.ACTIVE.value
    )
    goal_amount = Column(Numeric(12, 2), nullable=True)
    goal_unit = Column(String(32), nullable=True)
    legacy_cadence = Column(JSONVariant, nullable=True)

    owner = relationship(
        "User", foreign_keys=[owner_id], back_populates="owned_challenges"
    )
    participants = relationship("User", secondary="Enrollments", viewonly=True)
    # Cascades are required, not an optimisation: every child FK here is
    # NOT NULL (ChallengeStats.challenge_id is even the PK), so SQLAlchemy's
    # default de-association on parent delete tries to NULL them out and
    # raises before it ever reaches the database. Without these, deleting any
    # challenge is an unconditional 500 -- and every challenge has both a
    # stats row and the owner's auto-enrollment from the moment it is created.
    enrollments = relationship(
        "Enrollment", back_populates="challenge", cascade="all, delete-orphan"
    )
    stats = relationship(
        "ChallengeStats",
        back_populates="challenge",
        uselist=False,
        cascade="all, delete-orphan",
    )
    checkins = relationship(
        "CheckIn", back_populates="challenge", cascade="all, delete-orphan"
    )
