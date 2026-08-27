from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field

from app.models.challenge import ChallengeCategory, LifecycleStatus, Visibility
from app.schemas.cadence import CadenceUnion


class ChallengeBase(BaseModel):
    title: str = Field(min_length=3, max_length=50)
    description: str | None = Field(max_length=500, default=None)
    due_date: datetime | None = Field(default=None)
    rules: str | None = Field(max_length=1000, default=None)
    category: ChallengeCategory = Field(default=ChallengeCategory.OTHER)
    visibility: Visibility = Field(default=Visibility.PUBLIC)
    lifecycle_status: LifecycleStatus = Field(default=LifecycleStatus.ACTIVE)
    goal_amount: Decimal | None = Field(default=None)
    goal_unit: str | None = Field(max_length=32, default=None)


class ChallengeCreate(ChallengeBase):
    cadence: CadenceUnion
    timezone: str | None = Field(max_length=64, default=None)


class ChallengeRead(ChallengeBase):
    model_config = ConfigDict(from_attributes=True)
    id: int = Field(description="The unique identifier of the challenge")
    owner_id: int = Field(
        description="The unique identifier of the owner of the challenge"
    )
    cadence: CadenceUnion


class ChallengeUpdate(BaseModel):
    title: str | None = Field(min_length=3, max_length=50, default=None)
    description: str | None = Field(max_length=500, default=None)
    due_date: datetime | None = Field(default=None)
    rules: str | None = Field(max_length=1000, default=None)
    category: ChallengeCategory | None = Field(default=None)
    visibility: Visibility | None = Field(default=None)
    lifecycle_status: LifecycleStatus | None = Field(default=None)
    cadence: CadenceUnion | None = Field(default=None)
    goal_amount: Decimal | None = Field(default=None)
    goal_unit: str | None = Field(max_length=32, default=None)
