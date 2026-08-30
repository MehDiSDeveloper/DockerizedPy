import re

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.avatars import AVATAR_ID_MAX_LENGTH, is_valid_avatar
from app.models.user import UserRole

# Farsi/Arabic-Indic digits, as typed on an Iranian keyboard. A phone number
# is the one profile field that is *matched* rather than just displayed, so it
# is folded to ASCII on the way in -- otherwise ۰۹۱۲... and 0912... are two
# different strings for the same number.
_DIGIT_FOLD = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")
_PHONE_RE = re.compile(r"^\+?\d{7,15}$")


class UserBase(BaseModel):
    name: str = Field(min_length=3, max_length=50)
    email: str | None = Field(max_length=100, default=None)
    # An id from `app.avatars.AVATAR_IDS`; `None` means "no pick", which is a
    # permanent state, not a pending one.
    avatar: str | None = Field(max_length=AVATAR_ID_MAX_LENGTH, default=None)
    # Contact and where-you-are, all optional and all own-profile-only (they
    # are absent from `UserPublicRead` on purpose). `address` is the precise
    # location, `city`/`country` the coarse one; nothing in the app requires
    # any of them, so an empty box means "not given" rather than an error.
    phone: str | None = Field(max_length=20, default=None)
    country: str | None = Field(max_length=60, default=None)
    city: str | None = Field(max_length=60, default=None)
    address: str | None = Field(max_length=255, default=None)

    @field_validator("country", "city", "address", "phone", "email")
    @classmethod
    def _blank_is_absent(cls, value: str | None) -> str | None:
        """An empty box is "not given", and the column stores that as NULL.

        The profile sheet submits every field it renders, so a cleared box
        arrives as `""`. Storing that would make two spellings of "unset" --
        one that `{% if user.city %}` hides and one it does not. `email` is
        in the list for a sharper reason: that column is UNIQUE, so a second
        member saving a blank one would collide with the first.
        """
        if value is None:
            return None
        value = value.strip()
        return value or None

    @field_validator("phone")
    @classmethod
    def _plausible_phone(cls, value: str | None) -> str | None:
        """Digits (+ an optional leading `+`), normalized to ASCII.

        Deliberately not an Iran-specific pattern: the check is only here to
        keep prose out of a column meant to be dialled, and a member abroad
        has a number this app has no business rejecting. Separators are
        dropped so one number has one stored form.
        """
        if value is None:
            return None
        value = value.translate(_DIGIT_FOLD)
        value = re.sub(r"[\s\-()‌.]", "", value)
        if not _PHONE_RE.match(value):
            raise ValueError("Invalid phone number")
        return value

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


class UserRoleUpdate(BaseModel):
    """The *only* shape that can change `Users.role`.

    Deliberately a schema of its own rather than a field on `UserUpdate`:
    that one is what a member sends to `PATCH /users/{id}` about themselves,
    and a role field there would be one forgotten `exclude` away from
    self-promotion. Keeping it separate means the escalation path is a
    separate route with its own admin dependency, and `UserBase` stays the
    shape a member owns end to end.
    """

    role: UserRole


class UserLogin(BaseModel):
    email: str
    password: str


class UserReadBase(UserBase):
    model_config = ConfigDict(from_attributes=True)
    id: int = Field(description="The unique identifier of the user")


class UserRead(UserReadBase):
    # Own-profile shape, so the role is safe to include -- and the settings
    # page needs it to decide whether to offer the admin panel row.
    role: str = UserRole.MEMBER.value


class UserPublicRead(BaseModel):
    """Public-facing user shape -- excludes email so it's safe to return to
    any caller, not just the user themselves."""

    model_config = ConfigDict(from_attributes=True)
    id: int = Field(description="The unique identifier of the user")
    name: str
    # Kept in the public shape on purpose: an avatar is a chosen picture from
    # a fixed catalogue, so it carries nothing the member did not publish.
    avatar: str | None = None


class UserAdminRead(BaseModel):
    """What the admin panel lists -- a third read shape on purpose.

    `UserPublicRead` deliberately drops `email`, and widening it would widen
    every caller of it; `UserRead` is the *own*-profile shape. The panel
    needs identity plus the one field it administers, so it gets its own
    model and the wider read stays confined to the route that requires
    `Perm.USER_LIST`.
    """

    model_config = ConfigDict(from_attributes=True)
    id: int
    name: str
    email: str | None = None
    avatar: str | None = None
    role: str
