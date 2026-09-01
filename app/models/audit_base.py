from sqlalchemy import Column, DateTime, Integer, func

from app.database import Base


class AuditBase(Base):
    __abstract__ = True

    created_at = Column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at = Column(DateTime(timezone=True), nullable=True, server_default=None)
    last_modifier_user_id = Column(Integer, nullable=True)


def newest_first(model: type[AuditBase]):
    """Ordering every paginated list in the app sorts by: newest row first.

    `created_at` is the sort key rather than `id` because it is what the
    ordering actually *means* -- a list reads newest-first because that is
    the order things were created, not because ids happen to grow. It is
    written once at insert and never touched again (routers only ever set
    `updated_at`), so it is safe for a scroller to page through: a mutable
    key would let a row the reader already saw reappear on the next page.

    `id` is the tie-break and is not optional -- `server_default=func.now()`
    has second granularity on SQLite, so a seed run or a burst of signups
    produces whole blocks of rows sharing one timestamp, and without it
    their relative order is undefined per query and a page boundary can
    drop or repeat one.
    """
    return (model.created_at.desc(), model.id.desc())
