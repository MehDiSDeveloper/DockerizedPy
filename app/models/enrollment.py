import enum

from sqlalchemy import Column, Date, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import relationship

from app.models.audit_base import AuditBase


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

    user = relationship("User", back_populates="enrollments")
    challenge = relationship("Challenge", back_populates="enrollments")
    checkins = relationship(
        "CheckIn", back_populates="enrollment", cascade="all, delete-orphan"
    )
