from datetime import datetime

from sqlalchemy import Column, DateTime, Integer, String
from sqlalchemy.orm import Mapped, relationship

from app.models.auditBase import AuditBase
from app.models.enrollment import Enrollment


class User(AuditBase):
    __tablename__ = "Users"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(50), nullable=False)
    email = Column(String(100), unique=True, nullable=True)
    password = Column(String(150), nullable=False)
    member_since = Column(DateTime(timezone=True), default=datetime.now)

    owned_challenges = relationship("Challenge", back_populates="owner")
    enrollments: Mapped[list["Enrollment"]] = relationship(
        "Enrollment", back_populates="user"
    )
