from sqlalchemy import Column, DateTime, Integer, func

from app.database import Base


class AuditBase(Base):
    __abstract__ = True

    created_at = Column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at = Column(DateTime(timezone=True), nullable=True, server_default=None)
    last_modifier_user_id = Column(Integer, nullable=True)
