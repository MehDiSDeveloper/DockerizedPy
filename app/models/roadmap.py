"""«مسیر» -- an ordered course somebody builds out of challenges.

A roadmap is a *reading order* over challenges that already exist: «کتاب
بخوان» then «پادکست گوش کن» then «نهال بکار». Five tables, and each one is
here because what it holds cannot be derived from the others:

* :class:`Roadmap` -- the identity (title, description, pictures), the record
  of ownership, and the two rules that shape the whole run (``strict`` and
  ``count_prior_progress``).
* :class:`RoadmapStep` -- one challenge's place in the course, and **its own
  exit condition**.
* :class:`RoadmapEnrollment` -- somebody walking it.
* :class:`RoadmapStepProgress` -- where that person got to on one step.
* :class:`RoadmapInvite` -- the link that brings somebody in, with a real
  capacity (``app/invites.py``).

Three decisions carry the design, and each is the answer to a problem the app
already had:

**The exit condition belongs to the step, not to the challenge.** A habit
challenge is endlessly repeating and that is *correct* -- there is no
app-wide notion of "I finished this challenge" (``EnrollmentStatus.COMPLETED``
is written by nothing), and inventing one would put a full stop on something
designed not to have one. So the same challenge is a step "do it 12 times"
in one roadmap and "keep a 4-week streak" in another, and the challenge
itself is never touched. ``completion_rule`` is a discriminated JSON union
shaped by ``app/schemas/completion.py``, exactly as ``Challenge.cadence`` is
shaped by ``CadenceUnion`` -- one column, one union, branches in one module.

**A challenge is referenced, never copied.** ``RoadmapStep.challenge_id``
points at the real row, and reaching a step writes an ordinary
:class:`~app.models.enrollment.Enrollment`. Everything downstream -- the
occurrence engine, streaks, «امروز», the leaderboard, the backfill window,
every notification -- then works untouched, because there is nothing new for
it to know about. A step nobody has reached has *no enrollment*, which is why
a locked step cannot appear in «امروز»: not a filter in ``routers/today.py``,
an absence.

**Structure is stages, presentation is a line.** ``stage_index`` +
``order_in_stage`` means "these two together, then that one" costs no
migration later, while today's builder simply puts each step in a stage of
its own. A stage opens when every ``required`` step of the one before it is
finished *or* skipped -- skipped counts, or one archived challenge would
strand everybody behind it forever.
"""

import enum

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import relationship

from app.media import MEDIA_KEY_MAX_LENGTH
from app.models.audit_base import AuditBase
from app.models.challenge import JSONVariant


class RoadmapStepState(str, enum.Enum):
    """Where one person is on one step. English codes (D7).

    Stored on :class:`RoadmapStepProgress` rather than derived from scratch,
    for the one fact that has no other source: *when* a step opened. Whether
    it is **finished**, though, is recomputed on every refresh off live
    ``CheckIns`` -- like a streak, never incremented -- so this column is the
    engine's own answer, not a counter anybody adds to.
    """

    #: Its stage has not opened yet. No enrollment exists, so it cannot appear
    #: in «امروز» and cannot be checked into.
    LOCKED = "locked"
    #: Open, enrollment written, nothing recorded against it yet.
    AVAILABLE = "available"
    #: Open and under way -- at least one check-in since it opened.
    IN_PROGRESS = "in_progress"
    #: Its own ``completion_rule`` is satisfied.
    COMPLETED = "completed"
    #: Its challenge was archived out from under the run. Counts as settled
    #: for the purpose of opening the next stage: the roadmap must not stop
    #: because somebody else retired a challenge.
    SKIPPED = "skipped"


class RoadmapEnrollmentStatus(str, enum.Enum):
    ACTIVE = "active"
    COMPLETED = "completed"


class Roadmap(AuditBase):
    __tablename__ = "Roadmaps"
    __table_args__ = (Index("ix_roadmaps_owner", "owner_id"),)

    id = Column(Integer, primary_key=True, index=True)
    title = Column(String(60), nullable=False)
    description = Column(String(1000), nullable=True)

    # One picture, holding a media key and never a path -- the same contract
    # `Challenge.image_square` has. **One and not two**: a challenge carries a
    # second, portrait one because the full-screen rail reader is a surface
    # shaped like a phone, and a roadmap has no such surface. A column nothing
    # renders and no route writes is scaffolding pretending to be a feature;
    # when a roadmap grows a reader, the column is one line and one frame in
    # `app/media.py`. NULL is permanent and legitimate.
    image_square = Column(String(MEDIA_KEY_MAX_LENGTH), nullable=True)

    owner_id = Column(Integer, ForeignKey("Users.id"), nullable=False)

    # The same three values `Challenge.visibility` takes, read by the same
    # kind of composed clause (`roadmap_visibility_filter`). A roadmap you
    # cannot see is a 404 through both front doors.
    visibility = Column(
        String(16), nullable=False, default="public", server_default="public"
    )
    lifecycle_status = Column(
        String(16), nullable=False, default="active", server_default="active"
    )

    # The group this roadmap belongs to, or NULL for a personal one.
    # **Create-only**, exactly like `Challenge.group_id` and for its reason:
    # membership decides who can see it, so moving it later would
    # retroactively change that for everyone who has already walked it.
    group_id = Column(Integer, ForeignKey("Groups.id"), nullable=True, index=True)

    # **Is the order a rule or a suggestion?** Default `False`, which is the
    # whole argument: a roadmap somebody builds for a friend is abandoned the
    # first time a hard lock refuses something they were ready to do. With
    # `strict` off the next step is still reachable and merely marked «هنوز
    # نوبتش نیست»; with it on the button is genuinely disabled, which is what
    # a taught course needs. The difference in the code is one boolean the
    # page and the enrolment route both read -- nothing else branches on it.
    strict = Column(
        Boolean, nullable=False, default=False, server_default=text("0")
    )

    # **Does work done before a step opened count towards it?** No, and v1
    # offers no way to say otherwise: somebody who joined that challenge
    # months ago would otherwise walk into a roadmap with three steps already
    # green, which is neither predictable nor what the builder meant. The
    # column exists so the answer is recorded per roadmap rather than assumed
    # app-wide; it is deliberately absent from every write schema.
    count_prior_progress = Column(
        Boolean, nullable=False, default=False, server_default=text("0")
    )

    owner = relationship("User", foreign_keys=[owner_id])
    group = relationship("Group")
    steps = relationship(
        "RoadmapStep",
        back_populates="roadmap",
        cascade="all, delete-orphan",
        order_by="(RoadmapStep.stage_index, RoadmapStep.order_in_stage)",
    )
    # Cascaded, and this is the one place worth being explicit about what is
    # *not* destroyed: a roadmap enrollment and its progress rows are the
    # app's record of a walk through the course, but the `Enrollments` and
    # `CheckIns` the walk produced belong to the challenges and stay exactly
    # where they are. Deleting a roadmap ends the course, never the history.
    enrollments = relationship(
        "RoadmapEnrollment", back_populates="roadmap", cascade="all, delete-orphan"
    )
    invites = relationship(
        "RoadmapInvite", back_populates="roadmap", cascade="all, delete-orphan"
    )
    notifications = relationship(
        "Notification", back_populates="roadmap", cascade="all, delete-orphan"
    )


class RoadmapStep(AuditBase):
    """One challenge's place in one course, and how it is left behind."""

    __tablename__ = "RoadmapSteps"
    __table_args__ = (
        # One challenge appears at most once in a roadmap: two steps pointing
        # at the same challenge would share one enrollment and one set of
        # check-ins, so the second could never be told apart from the first.
        UniqueConstraint("roadmap_id", "challenge_id", name="uq_roadmap_challenge"),
        Index("ix_roadmap_steps_roadmap", "roadmap_id"),
        # "Which roadmaps is this challenge a step of" -- asked by
        # `roadmap_scope_filter` on every challenge read, and by the archive
        # hook.
        Index("ix_roadmap_steps_challenge", "challenge_id"),
    )

    id = Column(Integer, primary_key=True, index=True)
    roadmap_id = Column(Integer, ForeignKey("Roadmaps.id"), nullable=False)
    challenge_id = Column(Integer, ForeignKey("Challenges.id"), nullable=False)

    # Which stage this step is in, and where inside it. Steps sharing a stage
    # open together; the stage after opens when the required ones here are
    # settled. Today's builder writes one step per stage, so the course is a
    # line -- and «این دو را با هم انجام بده» costs no migration when it comes.
    stage_index = Column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    order_in_stage = Column(
        Integer, nullable=False, default=0, server_default=text("0")
    )

    # An optional step is offered and never blocks: the stage after it opens
    # whether or not it was done.
    required = Column(
        Boolean, nullable=False, default=True, server_default=text("1")
    )

    # The exit condition, shaped by `CompletionUnion` in
    # `app/schemas/completion.py` -- a discriminated union in a JSON column,
    # the same trade `Challenge.cadence` makes. Adding a kind means a member
    # there plus a branch in `app/roadmaps.py`, not a nullable column per rule.
    completion_rule = Column(JSONVariant, nullable=False, default=dict)

    # One line from the builder about why this step is here. Not the
    # challenge's description -- that belongs to the challenge and is shown
    # from it.
    note = Column(String(300), nullable=True)

    # **Removing a step is a stamp, not a DELETE** -- the same call
    # `GroupInvite.revoked_at` makes. Somebody halfway through has a progress
    # row for it, and a hard delete would take that with it; a removed step
    # simply stops being part of the course, stops blocking the stage after
    # it, and leaves what anybody logged exactly where it is.
    removed_at = Column(DateTime(timezone=True), nullable=True)

    roadmap = relationship("Roadmap", back_populates="steps")
    challenge = relationship("Challenge")
    progress = relationship(
        "RoadmapStepProgress", back_populates="step", cascade="all, delete-orphan"
    )


class RoadmapEnrollment(AuditBase):
    """One person walking one roadmap."""

    __tablename__ = "RoadmapEnrollments"
    __table_args__ = (
        UniqueConstraint("user_id", "roadmap_id", name="uq_user_roadmap"),
        Index("ix_roadmap_enrollments_user", "user_id"),
        Index("ix_roadmap_enrollments_roadmap", "roadmap_id"),
    )

    id = Column(Integer, primary_key=True, index=True)
    roadmap_id = Column(Integer, ForeignKey("Roadmaps.id"), nullable=False)
    user_id = Column(Integer, ForeignKey("Users.id"), nullable=False)
    status = Column(
        String(16),
        nullable=False,
        default=RoadmapEnrollmentStatus.ACTIVE.value,
        server_default=RoadmapEnrollmentStatus.ACTIVE.value,
    )
    # When the walk began. `created_at` says the same thing today, and this
    # says it as the domain fact rather than as an audit stamp -- the first
    # step's clock (`duration` rules) is read from `unlocked_at` below, never
    # from here, so the two never have to agree.
    started_at = Column(DateTime(timezone=True), nullable=True)

    roadmap = relationship("Roadmap", back_populates="enrollments")
    user = relationship("User", foreign_keys=[user_id])
    progress = relationship(
        "RoadmapStepProgress",
        back_populates="roadmap_enrollment",
        cascade="all, delete-orphan",
    )


class RoadmapStepProgress(AuditBase):
    """Where one person got to on one step.

    **What is stored is when the step opened; what is recomputed is whether it
    is finished.** ``unlocked_at`` has no other source -- nothing else in the
    app records the moment a course reached a step, and it is the clock a
    ``duration`` rule counts from and the line a ``count``/``amount`` rule
    counts *after* (because ``count_prior_progress`` is False). Completion,
    by contrast, is re-derived from live ``CheckIns`` on every refresh, the
    same rule ``compute_streaks`` follows: there is no ``+= 1`` anywhere here.

    **There is deliberately no ``enrollment_id`` column.** The enrollment this
    step produced is already uniquely addressed by ``(user_id, challenge_id)``
    -- ``Enrollments`` carries a UNIQUE constraint on exactly that pair -- so
    a copy of the id here would be a second name for a fact the database
    already guarantees, and one that a plain ``DELETE /enrollments/{id}``
    would leave dangling, since SQLite does not enforce foreign keys in this
    app. ``unlocked_at IS NOT NULL`` is what says the enrollment was written,
    so somebody who unenrols mid-course is never silently re-enrolled: the
    step falls back to ``in_progress`` and waits for them.
    """

    __tablename__ = "RoadmapStepProgress"
    __table_args__ = (
        UniqueConstraint(
            "roadmap_enrollment_id", "step_id", name="uq_roadmap_progress"
        ),
        # "What is open for this member right now" -- the home card's question,
        # asked across every roadmap they are on.
        Index("ix_roadmap_progress_state", "roadmap_enrollment_id", "state"),
    )

    id = Column(Integer, primary_key=True, index=True)
    roadmap_enrollment_id = Column(
        Integer, ForeignKey("RoadmapEnrollments.id"), nullable=False
    )
    step_id = Column(Integer, ForeignKey("RoadmapSteps.id"), nullable=False)
    state = Column(
        String(16),
        nullable=False,
        default=RoadmapStepState.LOCKED.value,
        server_default=RoadmapStepState.LOCKED.value,
    )
    unlocked_at = Column(DateTime(timezone=True), nullable=True)
    completed_at = Column(DateTime(timezone=True), nullable=True)

    roadmap_enrollment = relationship("RoadmapEnrollment", back_populates="progress")
    step = relationship("RoadmapStep", back_populates="progress")


class RoadmapInvite(AuditBase):
    """A link into a roadmap. Same table shape, same rules, as a group's.

    Everything about a capacity, an expiry, revocation and the derived state
    lives in ``app/invites.py`` and is shared with
    :class:`~app.models.group.GroupInvite` -- see that module on why the seat
    is spent by a conditional UPDATE and never by a read followed by a write.
    """

    __tablename__ = "RoadmapInvites"
    __table_args__ = (Index("ix_roadmap_invites_roadmap", "roadmap_id"),)

    id = Column(Integer, primary_key=True, index=True)
    roadmap_id = Column(Integer, ForeignKey("Roadmaps.id"), nullable=False)
    code = Column(String(43), nullable=False, unique=True, index=True)
    label = Column(String(60), nullable=True)
    max_uses = Column(Integer, nullable=True)
    uses = Column(Integer, nullable=False, default=0, server_default=text("0"))
    expires_at = Column(DateTime(timezone=True), nullable=True)
    revoked_at = Column(DateTime(timezone=True), nullable=True)
    created_by_user_id = Column(Integer, ForeignKey("Users.id"), nullable=False)

    roadmap = relationship("Roadmap", back_populates="invites")
    created_by = relationship("User", foreign_keys=[created_by_user_id])
