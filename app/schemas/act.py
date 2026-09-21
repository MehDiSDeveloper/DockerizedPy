"""Bodies and answers for the act layer: referees, and the verification loop.

One module rather than two, because the two halves are one flow read from
two ends -- somebody is asked to vouch, and then they vouch -- and splitting
four small models across two files would mean two imports at every call site
for no separation anybody benefits from.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class RefereeInvite(BaseModel):
    """Who to ask. A credential, never an id and never a name search.

    The same shape ``MemberAdd.identifier`` has and for its reasons: a search
    box here would hand every act's owner a way to walk the membership, and
    an id would let one be guessed. ``app.referees.resolve_member``
    normalises a mobile number and falls back to an exact email.
    """

    identifier: str = Field(min_length=3, max_length=120)


class RefereeResponse(BaseModel):
    """The invitee's own answer, and the only thing they may write."""

    accept: bool


class RefereeRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    challenge_id: int
    user_id: int
    state: str
    responded_at: datetime | None = None
    created_at: datetime


class VerdictWrite(BaseModel):
    """A referee's ruling on one report.

    ``note`` is **required on a rejection** and optional on an approval, and
    that asymmetry is the whole design: an approval needs no explanation
    because nothing follows from it, while a refusal is the one outcome the
    doer has to do something about -- and «رد شد» with no reason is a
    refusal nobody can act on. Validated here, at the write boundary, so
    neither front door can skip it.
    """

    verdict: Literal["approved", "rejected"]
    note: str | None = Field(default=None, max_length=300)

    @model_validator(mode="after")
    def _reason_when_refusing(self):
        # A **model** validator, not a field one: a field validator on `note`
        # does not run when the key is simply absent from the body, which is
        # exactly the request this rule exists to refuse.
        if self.verdict == "rejected" and not (self.note or "").strip():
            raise ValueError("برای رد کردن یک گزارش باید دلیلش را بنویسی.")
        return self
