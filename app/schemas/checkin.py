from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class CheckInCreate(BaseModel):
    challenge_id: int
    occurrence_key: str
    state: Literal["completed", "skipped"]
    amount: Decimal | None = None
    note: str | None = Field(default=None, max_length=500)
    photo_url: str | None = Field(default=None, max_length=500)


class CheckInUpdate(BaseModel):
    state: Literal["completed", "skipped"] | None = None
    amount: Decimal | None = None
    note: str | None = Field(default=None, max_length=500)
    photo_url: str | None = Field(default=None, max_length=500)


class CheckInRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    enrollment_id: int
    challenge_id: int
    occurrence_key: str
    state: str
    occurrence_local_date: date
    occurred_at_utc: datetime
    timezone: str
    amount: Decimal | None = None
    unit: str | None = None
    note: str | None = None
    photo_url: str | None = None
    created_at: datetime
    updated_at: datetime | None = None


class EnrollmentHistoryItem(BaseModel):
    occurrence_key: str
    local_date: date
    state: str
    amount: Decimal | None = None


class TodayItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    challenge_id: int
    challenge_title: str
    category: str
    occurrence_key: str
    opens_at_utc: datetime
    closes_at_utc: datetime | None = None
    quota_done: int | None = None
    quota_target: int | None = None
    goal_unit: str | None = None
    # `opens_at_utc`/`closes_at_utc` are absolute instants, but the window they
    # describe was derived from *this enrollment's* timezone -- so the UI has to
    # render them back in that same zone, not the viewer's, or a travelling user
    # sees a deadline that disagrees with the one their streak is scored on.
    timezone: str
    # The cadence decides what the deadline even means: end of today for
    # `recurring_days`, end of the week/month for `recurring_quota`, a specific
    # clock time for `schedule`, and nothing at all for `once`. The client can't
    # infer that from the instants alone.
    cadence_kind: Literal["once", "schedule", "recurring_days", "recurring_quota"]
    quota_period: Literal["week", "month"] | None = None


class ChallengeStatsRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    challenge_id: int
    participant_count: int
    total_completions: int
    total_amount: Decimal
    last_checkin_at: datetime | None = None
    updated_at: datetime | None = None
