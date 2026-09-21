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

    # --- the outcome axis ----------------------------------------------
    #
    # `state` is what the *doer reported* -- `completed` or `skipped` -- and
    # it is read by streaks, stats, the heatmap, the leaderboard and every
    # roadmap completion rule. It is deliberately **not** widened here: a
    # third value on that column would have been silently miscounted by every
    # one of those `== "completed"` comparisons.
    #
    # `verdict` is what *became of* that report, and the two together derive
    # the outcome a member reads (`app.verification.checkin_outcome`). The
    # one predicate everything counts on is
    # `state == completed AND verdict IN (auto, approved)`, written once in
    # `app/verification.py` and composed everywhere else.
    #
    # `auto` is the default and the server_default, so every row written
    # before this column existed means exactly what it meant then: settled,
    # by nobody, at the moment it was made.
    verdict = Column(
        String(16), nullable=False, default="auto", server_default="auto"
    )
    #: The evidence, where the act asked for a file. Nullable: most acts do
    #: not. Unique, because a proof asset is single-use -- see `ProofAsset`.
    proof_asset_id = Column(
        Integer, ForeignKey("ProofAssets.id"), nullable=True, unique=True
    )
    #: Who ruled, and when, and why -- NULL while `auto` or `pending`.
    #: `verdict_note` is required on a rejection (`app/schemas/verification.py`):
    #: a refusal with no reason is a refusal nobody can act on.
    verdict_by_user_id = Column(Integer, ForeignKey("Users.id"), nullable=True)
    verdict_at = Column(DateTime(timezone=True), nullable=True)
    verdict_note = Column(String(300), nullable=True)

    enrollment = relationship("Enrollment", back_populates="checkins")
    challenge = relationship("Challenge", back_populates="checkins")
    proof_asset = relationship("ProofAsset", foreign_keys=[proof_asset_id])
    verdict_by = relationship("User", foreign_keys=[verdict_by_user_id])
