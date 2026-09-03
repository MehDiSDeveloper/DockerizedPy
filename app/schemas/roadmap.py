"""Wire shapes for «مسیر».

The two rules ``app/schemas/group.py`` names carry straight over, and they are
what these classes are *for*:

* **The write boundary is the only gate.** The two picture fields go through
  the same ``is_media_key`` every other picture field does, so nothing
  downstream has to check what the column holds.
* **A field that must not be settable is not on the shape.** ``RoadmapUpdate``
  carries no ``group_id`` (create-only, for ``Challenge.group_id``'s reason:
  it decides who can ever see this, so moving it would rewrite that
  retroactively), no ``owner_id``, and no ``count_prior_progress`` -- v1
  answers that one ``False`` for everybody, and the column exists so the
  answer is recorded rather than assumed.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.media import MEDIA_KEY_MAX_LENGTH, is_media_key
from app.models.challenge import LifecycleStatus, Visibility
from app.models.roadmap import RoadmapEnrollmentStatus, RoadmapStepState
from app.schemas.completion import CompletionUnion


def _known_media(value: str | None) -> str | None:
    if value is not None and not is_media_key(value):
        raise ValueError("Unknown image")
    return value


class RoadmapBase(BaseModel):
    title: str = Field(min_length=3, max_length=60)
    description: str | None = Field(max_length=1000, default=None)
    visibility: Visibility = Field(default=Visibility.PUBLIC)
    #: Is the order a rule or a suggestion? Default *off* -- see
    #: ``Roadmap.strict`` for why a course that locks by default is a course
    #: people walk away from.
    strict: bool = Field(default=False)
    image_square: str | None = Field(max_length=MEDIA_KEY_MAX_LENGTH, default=None)

    _check_media = field_validator("image_square")(_known_media)


class RoadmapCreate(RoadmapBase):
    group_id: int | None = Field(default=None)


class RoadmapUpdate(BaseModel):
    """Partial. ``title`` may be absent but never explicitly null -- the
    column is NOT NULL, the same shape (and reason) as ``UserUpdate.name``."""

    title: str | None = Field(min_length=3, max_length=60, default=None)
    description: str | None = Field(max_length=1000, default=None)
    visibility: Visibility | None = Field(default=None)
    lifecycle_status: LifecycleStatus | None = Field(default=None)
    strict: bool | None = Field(default=None)
    image_square: str | None = Field(max_length=MEDIA_KEY_MAX_LENGTH, default=None)

    _check_media = field_validator("image_square")(_known_media)

    @field_validator("title")
    @classmethod
    def _title_not_null(cls, value: str | None) -> str | None:
        if value is None:
            raise ValueError("title cannot be null")
        return value


class RoadmapRead(RoadmapBase):
    """One roadmap, with the two figures every surface showing one needs.

    ``step_count`` and ``member_count`` ride along for ``GroupRead``'s reason:
    neither is on the row, and computing them at each call site is how a card
    and a page come to disagree about the same roadmap.
    """

    model_config = ConfigDict(from_attributes=True)
    id: int
    owner_id: int
    owner_name: str | None = None
    lifecycle_status: LifecycleStatus = LifecycleStatus.ACTIVE
    group_id: int | None = None
    created_at: datetime
    step_count: int = 0
    member_count: int = 0
    #: Where the reader is: ``None`` if they are not walking it.
    my_status: RoadmapEnrollmentStatus | None = None
    is_owner: bool = False


class StepCreate(BaseModel):
    """One challenge added to the end of the course.

    ``stage_index`` is deliberately **not** on the shape: the builder appends,
    and the server puts the new step in a stage of its own after the last one.
    That is what keeps «افزودن قدم به انتها» free even once people are
    walking -- there is nobody down there yet to disturb -- while every
    rearrangement of what is above goes through the structural lock.
    """

    challenge_id: int
    completion_rule: CompletionUnion
    required: bool = True
    note: str | None = Field(max_length=300, default=None)


class StepUpdate(BaseModel):
    completion_rule: CompletionUnion | None = None
    required: bool | None = None
    note: str | None = Field(max_length=300, default=None)
    #: Where this step sits. Both are structural, so both are refused once
    #: somebody else is mid-course.
    stage_index: int | None = Field(ge=0, le=500, default=None)
    order_in_stage: int | None = Field(ge=0, le=500, default=None)


class StepRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    roadmap_id: int
    challenge_id: int
    challenge_title: str | None = None
    stage_index: int
    order_in_stage: int
    required: bool
    completion_rule: CompletionUnion
    note: str | None = None
    #: The reader's own state on this step -- ``locked`` for somebody who is
    #: not walking the roadmap, which is honest: nothing is open for them.
    state: RoadmapStepState = RoadmapStepState.LOCKED


class RoadmapInviteCreate(BaseModel):
    label: str | None = Field(max_length=60, default=None)
    max_uses: int | None = Field(default=None, ge=1, le=10_000)
    expires_at: datetime | None = Field(default=None)


class RoadmapInviteRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    code: str
    label: str | None = None
    max_uses: int | None = None
    uses: int
    expires_at: datetime | None = None
    revoked_at: datetime | None = None
    created_at: datetime
    #: Derived, never stored -- see ``app/invites.py``.
    state: str


class RoadmapInvitePreview(BaseModel):
    """What somebody holding a link is told *before* they use it.

    The title, the picture, how many steps and how many people -- and
    deliberately **not** the steps themselves. Whoever holds the code is not
    walking the course yet, so ``roadmap_scope_filter`` grants them nothing,
    and a preview that listed the challenges would be a way to read a private
    one through a link somebody forwarded.
    """

    roadmap_id: int
    title: str
    description: str | None = None
    image_square: str | None = None
    step_count: int
    member_count: int
    state: str
    already_member: bool = False
