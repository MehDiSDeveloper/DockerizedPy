from datetime import datetime  # noqa: F401
from typing import Annotated, Literal, Union  # noqa: F401

from pydantic import BaseModel, ConfigDict, Field


class UserBase(BaseModel):
    name: str = Field(min_length=3, max_length=50)
    email: str | None = Field(max_length=100, default=None)


class UserCreate(UserBase):
    password: str = Field(min_length=8, max_length=150)
    confirm_password: str = Field(min_length=8, max_length=150)


class UserUpdate(UserBase):
    pass


class UserReadBase(UserBase):
    model_config = ConfigDict(from_attributes=True)
    id: int = Field(description="The unique identifier of the user")


class UserRead(UserReadBase):
    pass
