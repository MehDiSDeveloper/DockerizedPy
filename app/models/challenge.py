import enum

from sqlalchemy import Column, DateTime, Enum, ForeignKey, Integer, String
from sqlalchemy.orm import relationship

from app.models.auditBase import AuditBase


class ChallengeCategory(str, enum.Enum):
    FITNESS = "سلامت جسمانی"
    NUTRITION = "تغذیه"
    MENTAL_HEALTH = "سلامت ذهنی"
    PRODUCTIVITY = "بهره‌وری"
    SOCIAL_RESPONSIBILITY = "مسئولیت اجتماعی"
    OTHER = "سایر"


class ChallengeType(str, enum.Enum):
    RecurringChallenge = "چالش تکرارشونده"
    OneTimeChallenge = "چالش یکباره"


class RecurringType(str, enum.Enum):
    Daily = "چالش روزانه"
    Weekly = "چالش هفتگی"
    Monthly = "چالش ماهانه"
    yearly = "چالش سالانه"


class Challenge(AuditBase):
    __tablename__ = "Challenges"
    __mapper_args__ = {
        "polymorphic_on": "challenge_type",
        "polymorphic_identity": "challenge",
    }

    id = Column(Integer, primary_key=True, index=True)
    title = Column(String(150), nullable=False)
    description = Column(String, nullable=True)
    rules = Column(String, nullable=True)
    due_date = Column(DateTime(timezone=True), nullable=True)
    owner_id = Column(Integer, ForeignKey("Users.id"), nullable=False)
    category = Column(
        Enum(ChallengeCategory), nullable=False, default=ChallengeCategory.OTHER
    )
    challenge_type = Column(
        Enum(ChallengeType), nullable=False, default=ChallengeType.OneTimeChallenge
    )

    owner = relationship(
        "User", foreign_keys=[owner_id], back_populates="owned_challenges"
    )
    participants = relationship("User", secondary="Enrollments", viewonly=True)
    enrollments = relationship("Enrollment", back_populates="challenge")


class RecurringChallenge(Challenge):
    __mapper_args__ = {"polymorphic_identity": ChallengeType.RecurringChallenge}

    recurrence_pattern = Column(
        Enum(RecurringType), nullable=False, default=RecurringType.Daily
    )
    interval = Column(
        Integer, nullable=False, default=1
    )  # e.g., every 1 day/week/month/year
    end_date = Column(DateTime(timezone=True), nullable=True)


class OneTimeChallenge(Challenge):
    __mapper_args__ = {"polymorphic_identity": ChallengeType.OneTimeChallenge}
