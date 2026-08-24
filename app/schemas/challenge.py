from datetime import datetime
from typing import Annotated, Literal, Union

from pydantic import BaseModel, ConfigDict, Field

from app.models.challenge import ChallengeCategory, ChallengeType, RecurringType


class ChallengeBase(BaseModel):
    title: str = Field(min_length=3, max_length=50)
    description: str | None = Field(max_length=500, default=None)
    due_date: datetime | None = Field(default=None)
    rules: str | None = Field(max_length=1000, default=None)
    category: ChallengeCategory = Field(default=ChallengeCategory.OTHER)


class ChallengeReadBase(ChallengeBase):
    model_config = ConfigDict(from_attributes=True)
    id: int = Field(description="The unique identifier of the challenge")
    owner_id: int = Field(
        description="The unique identifier of the owner of the challenge"
    )


class OneTimeChallengeCreate(ChallengeBase):
    challenge_type: Literal[ChallengeType.OneTimeChallenge]


class RecurringChallengeCreate(ChallengeBase):
    challenge_type: Literal[ChallengeType.RecurringChallenge]
    recurrence_pattern: RecurringType = RecurringType.Daily
    interval: int = 1
    end_date: datetime | None = None


class OneTimeChallengeRead(ChallengeReadBase):
    challenge_type: Literal[ChallengeType.OneTimeChallenge]


class RecurringChallengeRead(ChallengeReadBase):
    challenge_type: Literal[ChallengeType.RecurringChallenge]
    recurrence_pattern: RecurringType = RecurringType.Daily
    interval: int = 1
    end_date: datetime | None = None


class ChallengeUpdate(ChallengeBase):
    pass


ChallengeCreateUnion = Annotated[
    Union[OneTimeChallengeCreate, RecurringChallengeCreate],
    Field(discriminator="challenge_type"),
]

ChallengeReadUnion = Annotated[
    Union[OneTimeChallengeRead, RecurringChallengeRead],
    Field(discriminator="challenge_type"),
]
