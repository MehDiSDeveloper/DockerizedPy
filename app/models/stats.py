from sqlalchemy import Column, DateTime, ForeignKey, Integer, Numeric
from sqlalchemy.orm import relationship

from app.database import Base


class ChallengeStats(Base):
    __tablename__ = "ChallengeStats"

    challenge_id = Column(
        Integer, ForeignKey("Challenges.id"), primary_key=True
    )
    participant_count = Column(Integer, nullable=False, default=0)
    total_completions = Column(Integer, nullable=False, default=0)
    total_amount = Column(Numeric(14, 2), nullable=False, default=0)
    last_checkin_at = Column(DateTime(timezone=True), nullable=True)
    updated_at = Column(DateTime(timezone=True), nullable=True)

    challenge = relationship("Challenge", back_populates="stats")
