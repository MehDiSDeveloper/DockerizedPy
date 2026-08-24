from pydantic import BaseModel, ConfigDict

from app.models.enrollment import EnrollmentStatus


class EnrollmentRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    challenge_id: int
    user_id: int
    status: EnrollmentStatus
    completed_count: int
