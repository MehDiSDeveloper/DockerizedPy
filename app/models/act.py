"""The Act layer: the three tables that turn a challenge into a commitment.

A **Challenge** in this app was always one shape of a more general thing: a
promise somebody makes, recorded against a clock. What it could not express
was *who vouches for it*, *what counts as evidence*, and *what became of a
report*. Those are three independent axes, and this module carries the
storage for the two of them that needed tables of their own:

* **roles** -- :class:`ChallengeReferee`. The doer is an ``Enrollment`` and
  the owner is ``Challenge.owner_id``; both already existed. The referee is
  the axis that was missing, and it is a join table rather than a third
  value on ``Enrollment.role`` because an enrollment carries a *doer's*
  record -- a timezone, a start date, two streaks, an anonymity answer --
  and a referee has none of that and must never appear in a participant
  count, a leaderboard or «امروز».
* **proof** -- :class:`ProofAsset`. What a check-in must carry lives on the
  challenge (``proof_kind`` + ``proof``, the same pair ``cadence_kind`` +
  ``cadence`` is); this is the ledger for the one proof kind that is a file.
* the **log** -- :class:`ActEvent`, which is not an axis at all but the
  record that the other two were exercised.

The third axis, **outcome**, needed no table: it is ``CheckIns.verdict``
beside the ``state`` that was always there. See ``app/verification.py``.

**A challenge with none of this set behaves exactly as it did.** Every
column added here and in ``app/models/challenge.py`` carries the old
behaviour as its default, and the tables are simply empty.
"""

from __future__ import annotations

import enum

from sqlalchemy import (
    JSON,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import relationship

from app.database import Base
from app.media import MEDIA_KEY_MAX_LENGTH
from app.models.audit_base import AuditBase

JSONVariant = JSON().with_variant(JSONB, "postgresql")


class RefereeState(str, enum.Enum):
    """Where one referee invitation stands.

    Four values and no deletion. ``removed`` and ``declined`` are kept rather
    than deleted for the reason terminal ``GroupJoinRequest`` rows are: the
    row is the only record that somebody was asked, and a referee who was
    taken off an act after ruling on somebody's evidence is exactly the thing
    an argument later turns on. Re-inviting somebody flips the row back to
    ``invited``; the unique constraint is what makes that one row rather than
    a pile.
    """

    #: Asked, has not answered. Holds no power yet.
    INVITED = "invited"
    #: Accepted. This is the only state that may rule on a report.
    ACTIVE = "active"
    #: Said no.
    DECLINED = "declined"
    #: The owner took them off.
    REMOVED = "removed"


class ChallengeReferee(AuditBase):
    """One person asked to vouch for what happens inside one act.

    **A referee is not a participant.** Nothing about this row feeds a
    streak, a leaderboard, «امروز», ``ChallengeStats.participant_count`` or
    the ``count_non_owner_enrollments`` threshold that locks a cadence --
    those all read ``Enrollments``, and this is a different table precisely
    so none of them has to learn to exclude a role.

    **A referee cannot rule on their own report.** The one rule that makes a
    pact work at all: a pact is two people who each hold an ``Enrollment``
    *and* a row here, so without that rule each of them would simply approve
    themselves. It is enforced in ``app/verification.py`` at the write, not
    here -- a constraint cannot express it, since the same person legitimately
    holds both rows.
    """

    __tablename__ = "ChallengeReferees"
    __table_args__ = (
        # One row per person per act; re-inviting updates it in place.
        UniqueConstraint("challenge_id", "user_id", name="uq_referee_challenge_user"),
        # "What is waiting for me" -- the queue screen's only read.
        Index("ix_referees_user_state", "user_id", "state"),
    )

    id = Column(Integer, primary_key=True, index=True)
    challenge_id = Column(
        Integer, ForeignKey("Challenges.id"), nullable=False, index=True
    )
    user_id = Column(Integer, ForeignKey("Users.id"), nullable=False)
    state = Column(
        String(16),
        nullable=False,
        default=RefereeState.INVITED.value,
        server_default=RefereeState.INVITED.value,
    )
    #: Who asked. A real FK, so the record of who staffed an act survives.
    invited_by_user_id = Column(Integer, ForeignKey("Users.id"), nullable=False)
    #: When they accepted or declined. NULL while ``invited``.
    responded_at = Column(DateTime(timezone=True), nullable=True)

    challenge = relationship("Challenge", back_populates="referees")
    user = relationship("User", foreign_keys=[user_id])
    invited_by = relationship("User", foreign_keys=[invited_by_user_id])


class ProofAsset(AuditBase):
    """One uploaded picture, held to the standard evidence is held to.

    ``app/media.py`` deliberately has **no** table behind a media key: a
    picture is a small opaque string in the column that wanted one, and a row
    of metadata beside it would answer no question the app asks. Proof asks
    three that decoration does not, and each of them is a column here:

    * **when** -- ``captured_at`` is the *server's* clock at the moment the
      bytes arrived. Never the device's, never EXIF (which ``store_image``
      strips before anything else touches the file). A photograph's own
      metadata is a claim by whoever is making the claim.
    * **whose** -- ``user_id``. Somebody else's asset is a 404 at the attach.
    * **used already?** -- ``claimed_at``. An asset is single-use, so
      yesterday's photograph cannot be re-submitted for today, and the same
      photograph cannot cover two acts at once.

    ``sha256`` is the file's fingerprint and is stored because it is the only
    thing that survives the bytes: two identical submissions are visibly
    identical afterwards, and a dispute about *which* picture was sent has an
    answer that does not depend on the file still being on the disk.

    The bytes themselves live exactly where every other picture lives -- the
    key is a normal ``app/media.py`` key, re-encoded through Pillow like all
    the rest -- so nothing about serving, caching or deleting them is new.
    """

    __tablename__ = "ProofAssets"
    __table_args__ = (
        UniqueConstraint("media_key", name="uq_proof_media_key"),
        Index("ix_proof_assets_user_created", "user_id", "created_at"),
    )

    id = Column(Integer, primary_key=True, index=True)
    media_key = Column(String(MEDIA_KEY_MAX_LENGTH), nullable=False)
    user_id = Column(Integer, ForeignKey("Users.id"), nullable=False)
    #: Hex digest of the stored (re-encoded) bytes -- ours, not the client's.
    sha256 = Column(String(64), nullable=False)
    byte_size = Column(Integer, nullable=False, default=0)
    #: Server clock at upload. The freshness window is measured from here.
    captured_at = Column(DateTime(timezone=True), nullable=False)
    #: When a check-in claimed it. NULL means unused; set once, never cleared.
    claimed_at = Column(DateTime(timezone=True), nullable=True)

    user = relationship("User", foreign_keys=[user_id])


class ActEventKind(str, enum.Enum):
    """What the log records. English codes, like every enum after D7.

    One member per thing that *happened*, never per thing that was shown --
    the same rule :class:`~app.models.notification.NotificationKind` follows,
    and for the same reason: a line whose meaning depends on a stored
    sentence cannot be filtered, counted or re-worded.
    """

    ACT_CREATED = "act.created"
    ACT_LIFECYCLE_CHANGED = "act.lifecycle_changed"

    DOER_JOINED = "doer.joined"
    DOER_LEFT = "doer.left"
    DOER_ASSIGNED = "doer.assigned"

    REFEREE_INVITED = "referee.invited"
    REFEREE_ACCEPTED = "referee.accepted"
    REFEREE_DECLINED = "referee.declined"
    REFEREE_REMOVED = "referee.removed"

    #: A doer recorded an occurrence. `data` carries state and proof presence.
    CHECKIN_REPORTED = "checkin.reported"
    CHECKIN_AMENDED = "checkin.amended"
    CHECKIN_WITHDRAWN = "checkin.withdrawn"
    CHECKIN_APPROVED = "checkin.approved"
    CHECKIN_REJECTED = "checkin.rejected"


class ActEvent(Base):
    """One line of an act's immutable log: who, when, what.

    **Append-only, and the shape of the row is how that is said.** This is
    the one model in the app that is *not* an
    :class:`~app.models.audit_base.AuditBase` subclass, and the omission is
    the point: there is no ``updated_at`` and no ``last_modifier_user_id``,
    so an edit is not expressible in the schema at all. There is one writer
    (``app.actlog.record``) and no route in the app that updates or deletes a
    row here.

    ``checkin_id`` is a plain ``Integer`` and deliberately **not** a foreign
    key. A check-in can be withdrawn inside the backfill window; the record
    that it was made, and ruled on, must not go with it -- which is exactly
    what an FK (and the cascade every other child of ``Challenges`` carries)
    would do. The id stays as a join key for the rows that still exist and as
    a bare number for the ones that do not.

    It *is* cascaded from the challenge, because an act's log is part of the
    act: deleting a challenge is only possible while nobody else has joined
    (``count_non_owner_enrollments``), so nothing anyone else did is ever
    destroyed by that path.
    """

    __tablename__ = "ActEvents"
    __table_args__ = (
        # The whole log of one act, in order. `id` and not `created_at`:
        # `server_default=func.now()` is second-granular on SQLite, and this
        # is the one list in the app where two rows in the same second must
        # still read in the order they were written.
        Index("ix_act_events_challenge_id", "challenge_id", "id"),
    )

    id = Column(Integer, primary_key=True, index=True)
    challenge_id = Column(
        Integer, ForeignKey("Challenges.id"), nullable=False, index=True
    )
    kind = Column(String(32), nullable=False)
    #: Who did it. NULL where the engine did (nothing does yet, and the
    #: column is nullable for the reason `Notification.actor_user_id` is).
    actor_user_id = Column(Integer, ForeignKey("Users.id"), nullable=True)
    #: Who it was done *to*, where that is a different person from the actor.
    subject_user_id = Column(Integer, ForeignKey("Users.id"), nullable=True)
    checkin_id = Column(Integer, nullable=True)
    #: Whatever the kind needs to be legible later -- an occurrence key, a
    #: rejection reason, a state pair. Small and structured, never a sentence.
    data = Column(JSONVariant, nullable=True)
    created_at = Column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    challenge = relationship("Challenge", back_populates="events")
    actor = relationship("User", foreign_keys=[actor_user_id])
    subject = relationship("User", foreign_keys=[subject_user_id])
