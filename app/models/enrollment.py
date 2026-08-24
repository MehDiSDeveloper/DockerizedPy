import enum

from sqlalchemy import Column, Enum, ForeignKey, Integer, UniqueConstraint
from sqlalchemy.orm import relationship
from app.database import Base


class EnrollmentStatus(str, enum.Enum):
    ENROLLED = "enrolled"
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    DONE = "done"


class Enrollment(Base):
    __tablename__ = "Enrollments"
    __table_args__ = (
        UniqueConstraint("user_id", "challenge_id", name="uq_user_challenge"),
    )
    id = Column(Integer, primary_key=True, index=True)
    challenge_id = Column(Integer, ForeignKey("Challenges.id"), nullable=False)
    user_id = Column(Integer, ForeignKey("Users.id"), nullable=False)
    status = Column(
        Enum(EnrollmentStatus), nullable=False, default=EnrollmentStatus.ENROLLED
    )

    user = relationship("User", back_populates="enrollments")
    challenge = relationship("Challenge", back_populates="enrollments")
    completed_count = Column(
        Integer, nullable=False, default=0
    )  # New column to track completion count
