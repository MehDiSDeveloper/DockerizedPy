import enum

from sqlalchemy import (
    JSON,
    Column,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    Numeric,
    String,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import relationship

from app.media import MEDIA_KEY_MAX_LENGTH
from app.models.audit_base import AuditBase

JSONVariant = JSON().with_variant(JSONB, "postgresql")


class ChallengeCategory(str, enum.Enum):
    FITNESS = "سلامت جسمانی"
    NUTRITION = "تغذیه"
    MENTAL_HEALTH = "سلامت ذهنی"
    PRODUCTIVITY = "بهره‌وری"
    SOCIAL_RESPONSIBILITY = "مسئولیت اجتماعی"
    OTHER = "سایر"


class Visibility(str, enum.Enum):
    PRIVATE = "private"
    UNLISTED = "unlisted"
    PUBLIC = "public"


class LifecycleStatus(str, enum.Enum):
    DRAFT = "draft"
    ACTIVE = "active"
    ARCHIVED = "archived"


class IdentityMode(str, enum.Enum):
    """How participants of one challenge are shown to each other.

    A property of the *challenge*, not of the app: "چالش ترک سیگار" and
    "چالش ۱۰ هزار قدم" are not the same social contract, and the person who
    opens one is the person who knows which it is. The mode is fixed at
    creation and locked once anyone else joins (see
    ``app/routers/challenge.py``) -- unmasking people who joined on the
    promise of anonymity is a promise broken retroactively, and it is exactly
    the change a column with no lock invites.

    ``MEMBER_CHOICE`` is the only mode that consults the joiner; the other
    two answer for everyone. ``app.identity.resolve_anonymity`` is the one
    place that resolution happens, so the stored per-enrollment flag can
    never disagree with the policy that produced it.

    **The owner is never anonymous.** Anonymity here is about *participation*
    -- authorship is a separate thing, and a challenge whose content nobody
    is accountable for is a moderation problem, not a privacy feature. The
    creator's auto-enrolment is therefore always written named, and
    challenge-detail keeps naming them as «سازنده» in every mode.
    """

    NAMED = "named"
    ANONYMOUS = "anonymous"
    MEMBER_CHOICE = "member_choice"


class GroupAudience(str, enum.Enum):
    """Who inside the group a group challenge is for.

    Two values, not three. "یک نفر خاص" is not a third kind of audience --
    it is ``SELECTED`` with one person picked, and modelling it separately
    would mean a third branch every reader has to handle for a case that
    behaves identically to the second.

    ``ALL`` is the one that carries a *standing* meaning rather than a
    one-off act: it is re-read when somebody new joins the group, so a
    challenge for everyone stays a challenge for everyone. ``SELECTED``
    names nobody by itself -- the enrollment rows are the list, which is
    what lets an administrator add and remove people afterwards with the
    machinery that already exists.
    """

    ALL = "all"
    SELECTED = "selected"


class ParticipationMode(str, enum.Enum):
    """Whether a group member may walk away from a group challenge.

    The whole difference is one refusal in ``unenroll``: a mandatory
    challenge cannot be left *while you are still in the group that set
    it*. Leaving or being removed from the group lifts the lock -- the
    obligation belongs to the membership, not to the person -- and the
    enrollment and everything logged against it survive, for the same
    reason a challenge others have joined cannot be hard-deleted.

    Meaningless on a challenge with no ``group_id``, and stored anyway:
    a nullable "sometimes this column means something" is worse than a
    default that is simply never consulted.
    """

    OPTIONAL = "optional"
    MANDATORY = "mandatory"


class CadenceKind(str, enum.Enum):
    ONCE = "once"
    SCHEDULE = "schedule"
    RECURRING_DAYS = "recurring_days"
    RECURRING_QUOTA = "recurring_quota"


class ProofKind(str, enum.Enum):
    """What a check-in on this act must *carry* -- the evidence axis.

    The discriminator of ``Challenge.proof``, exactly as
    :class:`CadenceKind` is the discriminator of ``Challenge.cadence``, and
    for exactly that reason: the shapes carry different fields (a photo has
    a liveness rule, a future GPS proof would have a radius and a point), so
    they are a discriminated union in one JSON column rather than a widening
    row of mostly-NULL columns.

    Adding a kind is a member here, a member in ``app/schemas/proof.py``, and
    a branch in ``app.proof.proof_satisfied``. Nothing else in the app
    branches on it, which is the whole point of the union.

    ``SELF`` is the default and is every challenge that existed before this:
    you say you did it, and that is the evidence.
    """

    #: Self-report. The word of the person who made the promise.
    SELF = "self"
    #: A photograph taken now. See `app/proof.py` on what "now" can mean.
    PHOTO = "photo"


class ReviewMode(str, enum.Enum):
    """Who closes the loop on a report -- the outcome axis.

    Deliberately *not* folded into :class:`ProofKind` as a third value. A
    photograph reviewed by a referee is a real and obvious combination, and
    one enum with «خوداظهاری / عکس / تأیید ناظر» in it cannot express it.
    Two small columns multiply; one column with three values does not.

    ``AUTO`` is the default and is every challenge that existed before this:
    a report is final the moment it is made.
    """

    #: The report stands on its own. `verdict` is written `auto`.
    AUTO = "auto"
    #: An active referee has to approve it. `verdict` starts `pending`.
    REFEREE = "referee"


class Challenge(AuditBase):
    __tablename__ = "Challenges"

    id = Column(Integer, primary_key=True, index=True)
    title = Column(String(50), nullable=False)
    description = Column(String, nullable=True)
    rules = Column(String, nullable=True)
    due_date = Column(DateTime(timezone=True), nullable=True)

    # The challenge's two pictures, each a media key (`app/media.py`), never a
    # path -- the same contract `Users.avatar` has. Two rather than one
    # because the surfaces have two shapes and no single crop is honest in
    # both: `image_square` is the detail hero's ground and, cover-cropped to a
    # band, the rail card's 4:3 cover; `image_tall` is the full-screen
    # reader's panel, which is the shape of the phone it fills.
    #
    # NULL is permanent and legitimate -- every challenge predating this, and
    # every one created without a picture -- so each surface keeps the
    # category-coloured placeholder it already drew and the templates ask
    # `| media_url` rather than assuming.
    image_square = Column(String(MEDIA_KEY_MAX_LENGTH), nullable=True)
    image_tall = Column(String(MEDIA_KEY_MAX_LENGTH), nullable=True)
    owner_id = Column(Integer, ForeignKey("Users.id"), nullable=False)
    category = Column(
        Enum(ChallengeCategory), nullable=False, default=ChallengeCategory.OTHER
    )

    cadence_kind = Column(String(32), nullable=False, default=CadenceKind.ONCE.value)
    cadence = Column(JSONVariant, nullable=False, default=dict)

    # --- the act's two other axes -------------------------------------
    #
    # `proof_kind` + `proof` are the same pair `cadence_kind` + `cadence` is,
    # written the same way on purpose: the string is the SQL-queryable
    # discriminator (a card paints a «عکس» badge without parsing JSON per
    # row, and a filter can reach it) and the JSON carries whatever that kind
    # needs. See `ProofKind` and `app/schemas/proof.py`.
    #
    # `proof` is **nullable** where `cadence` is not, and that is what makes
    # the migration trivial: NULL means "whatever `proof_kind` says, with its
    # defaults", which is exactly right for every row written before this
    # column existed. `app.proof.parse_proof` is the one place that is read.
    proof_kind = Column(
        String(16),
        nullable=False,
        default=ProofKind.SELF.value,
        server_default=ProofKind.SELF.value,
    )
    proof = Column(JSONVariant, nullable=True)
    # Who closes the loop -- see `ReviewMode`. Not locked by
    # `count_non_owner_enrollments`: unlike `cadence` and `identity_mode`,
    # turning review on or off changes what happens *next*, never what a
    # report that already stands means. Reports settled under the old mode
    # keep their verdict, because the verdict is stored.
    review_mode = Column(
        String(16),
        nullable=False,
        default=ReviewMode.AUTO.value,
        server_default=ReviewMode.AUTO.value,
    )
    visibility = Column(String(16), nullable=False, default=Visibility.PUBLIC.value)
    # Who the participants are to each other -- see `IdentityMode`. Default
    # `named`, so every row written before this column existed, and every
    # challenge created without thinking about it, keeps the old behaviour.
    identity_mode = Column(
        String(16), nullable=False, default=IdentityMode.NAMED.value,
        server_default=IdentityMode.NAMED.value,
    )
    lifecycle_status = Column(
        String(16), nullable=False, default=LifecycleStatus.ACTIVE.value
    )
    goal_amount = Column(Numeric(12, 2), nullable=True)
    goal_unit = Column(String(32), nullable=True)
    legacy_cadence = Column(JSONVariant, nullable=True)

    # The group this challenge belongs to, or NULL for a personal one --
    # which is every challenge that existed before this column, so nothing
    # needs backfilling and nothing changes for a member with no groups.
    #
    # **A scalar FK, on purpose, and it is also the extension point.** One
    # challenge belonging to several groups is a real future ask, and the
    # move is a join table plus an edit to `group_scope_filter` and
    # `group_ids_for` -- because every read in the app reaches a group
    # challenge through those two functions and nothing else compares this
    # column by hand.
    #
    # It is settable at creation and never after: `ChallengeUpdate` does not
    # carry it. Moving a challenge between groups would retroactively change
    # who has been able to see it, which is the same objection that locks
    # `identity_mode` once other people have joined.
    group_id = Column(Integer, ForeignKey("Groups.id"), nullable=True, index=True)

    # Who inside that group it is for, and whether they may leave. Both carry
    # a default so `sync_sqlite_schema` can add them to a populated table
    # (CLAUDE.md, Configuration), and both are simply not consulted when
    # `group_id` is NULL.
    group_audience = Column(
        String(16), nullable=False,
        default=GroupAudience.SELECTED.value,
        server_default=GroupAudience.SELECTED.value,
    )
    participation_mode = Column(
        String(16), nullable=False,
        default=ParticipationMode.OPTIONAL.value,
        server_default=ParticipationMode.OPTIONAL.value,
    )

    owner = relationship(
        "User", foreign_keys=[owner_id], back_populates="owned_challenges"
    )
    group = relationship("Group", back_populates="challenges")
    participants = relationship("User", secondary="Enrollments", viewonly=True)
    # Cascades are required, not an optimisation: every child FK here is
    # NOT NULL (ChallengeStats.challenge_id is even the PK), so SQLAlchemy's
    # default de-association on parent delete tries to NULL them out and
    # raises before it ever reaches the database. Without these, deleting any
    # challenge is an unconditional 500 -- and every challenge has both a
    # stats row and the owner's auto-enrollment from the moment it is created.
    enrollments = relationship(
        "Enrollment", back_populates="challenge", cascade="all, delete-orphan"
    )
    stats = relationship(
        "ChallengeStats",
        back_populates="challenge",
        uselist=False,
        cascade="all, delete-orphan",
    )
    checkins = relationship(
        "CheckIn", back_populates="challenge", cascade="all, delete-orphan"
    )
    # Cascaded for a different reason than the three above: `challenge_id` on
    # a notification is *nullable*, so nothing would break without this -- the
    # rows would simply survive, pointing at a challenge that no longer
    # exists, and render as a sentence about nothing that opens a 404. A
    # notification is only meaningful while its subject is, so it goes with it.
    notifications = relationship(
        "Notification", back_populates="challenge", cascade="all, delete-orphan"
    )
    # The act's referees, and its log. Cascaded for the reason the three
    # above are -- both carry a NOT NULL `challenge_id`, so without this
    # every delete of a challenge that ever had either is a 500. Deleting a
    # challenge is only possible while nobody else has joined
    # (`count_non_owner_enrollments`), so no cascade here ever destroys a
    # record of what somebody else did.
    referees = relationship(
        "ChallengeReferee", back_populates="challenge", cascade="all, delete-orphan"
    )
    events = relationship(
        "ActEvent", back_populates="challenge", cascade="all, delete-orphan"
    )
