"""Request and response shapes for the SMS login flow.

Two rules shape all four models:

- **Normalization happens at the schema boundary**, exactly as
  ``UserBase.avatar`` is validated at the one place a value reaches the
  column. Both bodies carry ``mobile``, both fold it through
  ``app.phone.normalize_mobile``, so by the time a router sees it there is
  one spelling of one number and no handler has to remember to convert.
- **The responses say nothing an anonymous caller has not already proved.**
  ``OtpRequestResponse`` describes the *code*, never the account, so the
  request step cannot be used to ask whether a number is registered.
  ``is_new_account`` appears only on the verify response -- by then the
  caller has proved they hold the number, so telling them whether this is
  their first visit reveals nothing about anybody else.
"""

from pydantic import BaseModel, Field, field_validator

from app.otp import CODE_LENGTH
from app.phone import DIGIT_FOLD, normalize_mobile
from app.schemas.user import UserRead


class MobileBody(BaseModel):
    """Anything carrying a phone number the app is going to text."""

    mobile: str = Field(max_length=32)

    @field_validator("mobile")
    @classmethod
    def _canonical(cls, value: str) -> str:
        normalized = normalize_mobile(value)
        if normalized is None:
            raise ValueError("شماره موبایل معتبر نیست")
        return normalized


class OtpRequestBody(MobileBody):
    """Ask for a code. Deliberately carries nothing else.

    No name, no email, no avatar: sign-up and sign-in are one flow, so this
    body is identical whether the number is known or not -- which is what
    makes the response identical too.
    """


class OtpVerifyBody(MobileBody):
    code: str = Field(min_length=CODE_LENGTH, max_length=CODE_LENGTH)

    @field_validator("code")
    @classmethod
    def _ascii_digits(cls, value: str) -> str:
        """Farsi digits are folded, and nothing else is accepted.

        A member who pastes the code straight out of the SMS on a Farsi
        keyboard sends ``۱۲۳۴۵۶``; without folding, that is a wrong code for
        a reason nothing on screen could explain.
        """
        value = value.strip().translate(DIGIT_FOLD)
        if not value.isdigit():
            raise ValueError("کد باید عدد باشد")
        return value


class OtpRequestResponse(BaseModel):
    """Everything the code-entry screen needs, and nothing about the account.

    ``mobile_masked`` is there so the screen can show *which* number the SMS
    went to -- the commonest failure in this flow is a typo in the number,
    and a member staring at an empty inbox has no other way to catch it.
    """

    mobile_masked: str
    expires_in: int
    resend_after: int


class OtpVerifyResponse(BaseModel):
    user: UserRead
    #: Whether this code created the account rather than opening an existing
    #: one. The page uses it to send a first-time member to their profile so
    #: they can put a real name to the placeholder one; safe to answer here
    #: because the caller has already proved they hold the number.
    is_new_account: bool
