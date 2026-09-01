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
    is_anonymous: bool
    legacy_completed_count: int
    created_at: datetime
    updated_at: datetime | None = None


class EnrollmentCreate(BaseModel):
    timezone: str | None = Field(max_length=64, default=None)
    # What the joiner *asked for*, not what they get: only a `member_choice`
    # challenge consults it, and `app.identity.resolve_anonymity` at the
    # write boundary decides. A body asking to hide in a challenge that
    # names everyone is answered with a named enrolment, not a 422 -- the
    # request is simply not the question that challenge asks.
    is_anonymous: bool = Field(default=False)


class EnrollmentAnonymityUpdate(BaseModel):
    """The whole body of ``PATCH /enrollments/{challenge_id}/anonymity``.

    A door of its own rather than a field on :class:`EnrollmentUpdate`, for
    precisely the reason that class says it does not carry one: that handler
    writes its body onto the row with a blind ``setattr``, so a field there
    would be a way *around* ``resolve_anonymity``. This route runs the answer
    back through it, so the challenge's own ``identity_mode`` still decides
    and a challenge that names everyone still names everyone.

    It exists because of group challenges: somebody an administrator enrolled
    was never asked, is stored anonymous for that reason
    (``resolve_assigned_anonymity``), and needs a way to say otherwise.
    """

    is_anonymous: bool


class EnrollmentUpdate(BaseModel):
    # Deliberately *not* carrying `is_anonymous`: `update_my_enrollment`
    # writes whatever this shape holds straight onto the row, which would
    # make the PATCH a way around `resolve_anonymity` and let anyone unmask
    # themselves inside a challenge that promised the others otherwise.
    # Changing one's mind is a feature that belongs behind that same
    # resolution, not behind a blind setattr.
    status: EnrollmentStatus | None = Field(default=None)
