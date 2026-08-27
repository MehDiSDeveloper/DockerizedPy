from sqlalchemy import (
    Column,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import relationship

from app.models.audit_base import AuditBase


class CheckIn(AuditBase):
    __tablename__ = "CheckIns"
    __table_args__ = (
        UniqueConstraint(
            "enrollment_id", "occurrence_key", name="uq_enrollment_occurrence"
        ),
        Index("ix_checkins_challenge_created", "challenge_id", "created_at"),
        Index(
            "ix_checkins_enrollment_localdate",
            "enrollment_id",
            "occurrence_local_date",
        ),
    )

    id = Column(Integer, primary_key=True, index=True)
    enrollment_id = Column(Integer, ForeignKey("Enrollments.id"), nullable=False)
    challenge_id = Column(Integer, ForeignKey("Challenges.id"), nullable=False)
    occurrence_key = Column(String(64), nullable=False)
    state = Column(String(16), nullable=False)
    occurrence_local_date = Column(Date, nullable=False)
    occurred_at_utc = Column(DateTime(timezone=True), nullable=False)
    timezone = Column(String(64), nullable=False)
    amount = Column(Numeric(12, 2), nullable=True)
    unit = Column(String(32), nullable=True)
    note = Column(String(500), nullable=True)
    photo_url = Column(String(500), nullable=True)

    enrollment = relationship("Enrollment", back_populates="checkins")
    challenge = relationship("Challenge", back_populates="checkins")
