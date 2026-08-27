from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models.enrollment import EnrollmentStatus


class EnrollmentRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    challenge_id: int
    user_id: int
    status: EnrollmentStatus
    timezone: str
    start_date: date
    current_streak: int
    longest_streak: int
    last_checkin_local_date: date | None = None
    legacy_completed_count: int
    created_at: datetime
    updated_at: datetime | None = None


class EnrollmentCreate(BaseModel):
    timezone: str | None = Field(max_length=64, default=None)


class EnrollmentUpdate(BaseModel):
    status: EnrollmentStatus | None = Field(default=None)
