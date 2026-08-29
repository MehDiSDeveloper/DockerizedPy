from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.avatars import AVATAR_ID_MAX_LENGTH, is_valid_avatar


class UserBase(BaseModel):
    name: str = Field(min_length=3, max_length=50)
    email: str | None = Field(max_length=100, default=None)
    # An id from `app.avatars.AVATAR_IDS`; `None` means "no pick", which is a
    # permanent state, not a pending one.
    avatar: str | None = Field(max_length=AVATAR_ID_MAX_LENGTH, default=None)

    @field_validator("avatar")
    @classmethod
    def _known_avatar(cls, value: str | None) -> str | None:
        """The one place an avatar id is checked before it reaches the column.

        The stored value is fed straight back out as a static path, so an
        unvetted string is the whole risk here -- rejecting at the schema
        boundary keeps both front doors (`POST /users/`, `PATCH /users/{id}`)
        on the same rule without either remembering to call it.
        """
        if not is_valid_avatar(value):
            raise ValueError("Unknown avatar")
        return value


class UserCreate(UserBase):
    password: str = Field(min_length=8, max_length=150)
    confirm_password: str = Field(min_length=8, max_length=150)


class UserUpdate(UserBase):
    """A real PATCH shape: every field optional, `exclude_unset` in the router
    deciding what is written.

    `name` is required on `UserBase` (it is, at signup), which made a body of
    just `{"avatar": ...}` a 422 -- so the profile's avatar picker would have
    had to resend the name it is not editing. Overriding it here keeps the
    `avatar` validator inherited and the write boundary unchanged.
    """

    name: str | None = Field(min_length=3, max_length=50, default=None)

    @field_validator("name")
    @classmethod
    def _name_not_cleared(cls, value: str | None) -> str | None:
        """`None` here means "not in the body", never "erase it".

        The column is NOT NULL, so an explicit `{"name": null}` would reach
        the database and come back a 500. A field validator only runs on
        values that were actually supplied, which is exactly the split
        needed. (`avatar` is the opposite: null is a real value there.)
        """
        if value is None:
            raise ValueError("Name cannot be null")
        return value


class UserLogin(BaseModel):
    email: str
    password: str


class UserReadBase(UserBase):
    model_config = ConfigDict(from_attributes=True)
    id: int = Field(description="The unique identifier of the user")


class UserRead(UserReadBase):
    pass


class UserPublicRead(BaseModel):
    """Public-facing user shape -- excludes email so it's safe to return to
    any caller, not just the user themselves."""

    model_config = ConfigDict(from_attributes=True)
    id: int = Field(description="The unique identifier of the user")
    name: str
    # Kept in the public shape on purpose: an avatar is a chosen picture from
    # a fixed catalogue, so it carries nothing the member did not publish.
    avatar: str | None = None
