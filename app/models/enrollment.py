import enum

from sqlalchemy import Column, Date, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import relationship

from app.models.audit_base import AuditBase


class ChallengeRole(str, enum.Enum):
    """What a member may do *inside one challenge*.

    Lives on the enrollment rather than on `Users` because it is scoped to a
    row: the same person is `owner` of their own challenge and
    `participant` in everyone else's. This is the axis that grows -- a
    `coach` who may edit the content of a challenge they do not own, or a
    second owner after a hand-over -- which is why it is a column instead of
    the `Challenge.owner_id == user_id` comparison it replaces at the call
    sites.

    `Challenge.owner_id` stays the record of ownership and remains the
    authority: an owner may unenrol, which deletes the only row that could
    carry `owner`. `app.permissions.challenge_role` resolves the two, so
    neither can drift out from under the other.
    """

    PARTICIPANT = "participant"
    OWNER = "owner"


class EnrollmentStatus(str, enum.Enum):
    ACTIVE = "active"
    COMPLETED = "completed"
    ABANDONED = "abandoned"


class Enrollment(AuditBase):
    __tablename__ = "Enrollments"
    __table_args__ = (
        UniqueConstraint("user_id", "challenge_id", name="uq_user_challenge"),
    )
    id = Column(Integer, primary_key=True, index=True)
    challenge_id = Column(Integer, ForeignKey("Challenges.id"), nullable=False)
    user_id = Column(Integer, ForeignKey("Users.id"), nullable=False)
    status = Column(
        String(16), nullable=False, default=EnrollmentStatus.ACTIVE.value
    )
    timezone = Column(String(64), nullable=False, default="Asia/Tehran")
    start_date = Column(Date, nullable=False)
    current_streak = Column(Integer, nullable=False, default=0)
    longest_streak = Column(Integer, nullable=False, default=0)
    last_checkin_local_date = Column(Date, nullable=True)
    legacy_completed_count = Column(Integer, nullable=False, default=0)
    # This member's role *in this challenge* -- see `ChallengeRole`. Written
    # at enrolment (the creator's auto-enrolment gets `owner`, everyone else
    # `participant`) and backfilled for pre-existing rows by
    # `bootstrap_db.backfill_enrollment_roles`. Read only through
    # `app.permissions`.
    role = Column(String(16), nullable=False, default=ChallengeRole.PARTICIPANT.value)

    user = relationship("User", back_populates="enrollments")
    challenge = relationship("Challenge", back_populates="enrollments")
    checkins = relationship(
        "CheckIn", back_populates="enrollment", cascade="all, delete-orphan"
    )
