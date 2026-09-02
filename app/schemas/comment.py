"""The comment write boundary -- where "what may be said" is decided.

Two rules, both here rather than in the router, so both front doors get them
from one place: a comment must carry *something* (text, a sticker, or both),
and a sticker must be an id from the fixed catalogue -- exactly the contract
`schemas/user.py` holds for `avatar`.
"""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.stickers import is_valid_sticker

MAX_BODY = 1000


class CommentCreate(BaseModel):
    body: str | None = Field(default=None, max_length=MAX_BODY)
    sticker: str | None = None
    #: The comment being answered. A reply to a reply is re-pointed at its
    #: root by `create_comment`, so the client never has to know the shape.
    parent_id: int | None = Field(default=None, ge=1)

    @field_validator("body")
    @classmethod
    def _strip(cls, value: str | None) -> str | None:
        value = (value or "").strip()
        return value or None

    @field_validator("sticker")
    @classmethod
    def _known_sticker(cls, value: str | None) -> str | None:
        if value and not is_valid_sticker(value):
            raise ValueError("چنین استیکری وجود ندارد")
        return value

    @model_validator(mode="after")
    def _not_empty(self):
        if not self.body and not self.sticker:
            raise ValueError("متن یا استیکر لازم است")
        return self


class CommentAuthor(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    avatar: str | None = None


class CommentRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    parent_id: int | None
    body: str | None
    sticker: str | None
    created_at: datetime | None
    user: CommentAuthor
