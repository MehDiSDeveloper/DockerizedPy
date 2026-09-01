from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field

from app.models.challenge import (
    ChallengeCategory,
    GroupAudience,
    IdentityMode,
    LifecycleStatus,
    ParticipationMode,
    Visibility,
)
from app.schemas.cadence import CadenceUnion


class ChallengeBase(BaseModel):
    title: str = Field(min_length=3, max_length=50)
    description: str | None = Field(max_length=500, default=None)
    due_date: datetime | None = Field(default=None)
    rules: str | None = Field(max_length=1000, default=None)
    category: ChallengeCategory = Field(default=ChallengeCategory.OTHER)
    visibility: Visibility = Field(default=Visibility.PUBLIC)
    # The default a challenge is *created* with is `member_choice`: the
    # creator has not been asked, and deferring the question to each
    # participant is the answer that decides least on their behalf. The
    # column's own default stays `named` -- that one is what a row written
    # before this feature existed means, and rewriting it would change what
    # those challenges promised.
    identity_mode: IdentityMode = Field(default=IdentityMode.MEMBER_CHOICE)
    lifecycle_status: LifecycleStatus = Field(default=LifecycleStatus.ACTIVE)
    goal_amount: Decimal | None = Field(default=None)
    goal_unit: str | None = Field(max_length=32, default=None)


class ChallengeCreate(ChallengeBase):
    cadence: CadenceUnion
    timezone: str | None = Field(max_length=64, default=None)

    # --- Group challenges -------------------------------------------------
    # All four are create-only and none of them is on `ChallengeUpdate`.
    # `group_id` decides who can *ever* see this challenge, so moving it
    # later would retroactively change that -- the same objection that locks
    # `identity_mode`; the other three are the terms the participants were
    # enrolled under. Changing the terms of something people are already in
    # is what the lock rules exist to prevent.
    #
    # A caller with no groups sends none of them and nothing about their
    # request changes, which is the point: the wizard only asks these
    # questions when it was opened from inside a group.
    group_id: int | None = Field(default=None)
    group_audience: GroupAudience = Field(default=GroupAudience.SELECTED)
    participation_mode: ParticipationMode = Field(default=ParticipationMode.OPTIONAL)
    #: Who to enrol, for `group_audience = selected`. Ignored for `all`,
    #: which is read off the group's membership instead -- and re-read
    #: whenever somebody new joins it (`apply_standing_audience`).
    member_ids: list[int] = Field(default_factory=list, max_length=500)


class ChallengeRead(ChallengeBase):
    model_config = ConfigDict(from_attributes=True)
    id: int = Field(description="The unique identifier of the challenge")
    owner_id: int = Field(
        description="The unique identifier of the owner of the challenge"
    )
    cadence: CadenceUnion
    group_id: int | None = Field(default=None)
    group_audience: GroupAudience = Field(default=GroupAudience.SELECTED)
    participation_mode: ParticipationMode = Field(
        default=ParticipationMode.OPTIONAL
    )


class ChallengeUpdate(BaseModel):
    title: str | None = Field(min_length=3, max_length=50, default=None)
    description: str | None = Field(max_length=500, default=None)
    due_date: datetime | None = Field(default=None)
    rules: str | None = Field(max_length=1000, default=None)
    category: ChallengeCategory | None = Field(default=None)
    visibility: Visibility | None = Field(default=None)
    identity_mode: IdentityMode | None = Field(default=None)
    lifecycle_status: LifecycleStatus | None = Field(default=None)
    cadence: CadenceUnion | None = Field(default=None)
    goal_amount: Decimal | None = Field(default=None)
    goal_unit: str | None = Field(max_length=32, default=None)
