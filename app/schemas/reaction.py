from pydantic import BaseModel


class ReactionSet(BaseModel):
    """What the member wants to be true, not "flip it" -- see `set_reaction`."""

    liked: bool


class ReactionState(BaseModel):
    """What the button renders: the total, and whether I am part of it."""

    count: int
    liked: bool
