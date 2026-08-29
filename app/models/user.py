from sqlalchemy import Column, Integer, String
from sqlalchemy.orm import Mapped, relationship

from app.avatars import AVATAR_ID_MAX_LENGTH
from app.models.audit_base import AuditBase
from app.models.enrollment import Enrollment


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

    owned_challenges = relationship("Challenge", back_populates="owner")
    enrollments: Mapped[list[Enrollment]] = relationship(
        "Enrollment", back_populates="user"
    )
