from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models.task import TaskStatus


class TaskBase(BaseModel):
    title: str = Field(min_length=3, max_length=50)
    description: str | None = Field(max_length=500, default=None)
    status: TaskStatus = TaskStatus.PENDING
    due_date: datetime | None = Field(default=None)


class TaskCreate(TaskBase):
    pass


class TaskUpdate(TaskBase):
    pass


class TaskRead(TaskBase):
    model_config = ConfigDict(from_attributes=True)
    id: int = Field(description="The unique identifier of the task")
