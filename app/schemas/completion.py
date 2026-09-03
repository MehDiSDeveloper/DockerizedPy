"""What it means to be *done* with one step of a roadmap.

The same shape ``app/schemas/cadence.py`` has, for the same reason: a
discriminated union in one JSON column, keyed on ``Field(discriminator=…)``,
rather than six nullable columns on a flat table where five of them are always
NULL and nothing states which one matters. Adding a rule is a member here plus
a branch in :func:`app.roadmaps.evaluate_rule` -- two places, both of which
fail loudly if the other is forgotten.

**Why the rule lives on the step and not on the challenge.** This app has no
notion of finishing a challenge, and it should not grow one: a
``recurring_days`` challenge is meant to repeat for as long as somebody keeps
it up, and ``EnrollmentStatus.COMPLETED`` is written by no route in the
codebase. So the *course* says what leaving a step behind means. «کتاب بخوان»
is "twelve times" in one roadmap and "four weeks running" in another, and
neither of them edits the challenge.

**The member never sees the word ``completion_rule``.** The builder asks «این
قدم کِی تمام می‌شود؟» and offers «۲۱ بار انجامش بدهد» / «۴ هفته پشت‌سرهم» /
«۳۰ روز بگذرد». These classes are what those chips mean.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field


class ChallengeFinishedRule(BaseModel):
    """Done when the challenge itself reads «تمام شده».

    The one rule with no number in it, and the one that cannot be put on
    every challenge: a ``recurring_days`` or ``recurring_quota`` challenge
    with no ``end_date`` and no ``due_date`` never reaches that status, so a
    step carrying this rule on one would be a step nobody could ever leave.
    :func:`app.roadmaps.is_bounded` is the check, and it answers **422** at
    the write boundary rather than letting the step be written and discovered
    later by whoever is stuck behind it.
    """

    model_config = ConfigDict(from_attributes=True)
    kind: Literal["challenge_finished"] = "challenge_finished"


class CountRule(BaseModel):
    """Done after ``n`` recorded occurrences -- «۲۱ بار انجامش بدهد»."""

    model_config = ConfigDict(from_attributes=True)
    kind: Literal["count"] = "count"
    n: int = Field(ge=1, le=1000)


class StreakRule(BaseModel):
    """Done at a current streak of ``n`` -- «۴ هفته پشت‌سرهم».

    Read off ``Enrollment.current_streak``, which is legitimate precisely
    because ``compute_streaks`` recomputes that column rather than
    incrementing it (CLAUDE.md, Streaks). ``MAX_STREAK_WALK`` bounds it at
    400, so anything past that could never be reached.
    """

    model_config = ConfigDict(from_attributes=True)
    kind: Literal["streak"] = "streak"
    n: int = Field(ge=1, le=400)


class AmountRule(BaseModel):
    """Done once the recorded amounts add up to ``target``.

    Only meaningful on a challenge with a ``goal_unit`` -- a check-in's
    ``amount`` is required exactly when the challenge has one and is NULL
    otherwise, so this rule on a unit-less challenge would sum nothing
    forever. Refused at the write boundary, like ``challenge_finished`` on an
    endless cadence.
    """

    model_config = ConfigDict(from_attributes=True)
    kind: Literal["amount"] = "amount"
    target: Decimal = Field(gt=0, max_digits=12, decimal_places=2)


class DurationRule(BaseModel):
    """Done ``days`` after the step opened -- «۳۰ روز بگذرد».

    Counted from ``RoadmapStepProgress.unlocked_at``, which is the one fact
    the progress row stores rather than derives, and which is why it is
    stored: nothing else in the app records when a course reached a step.
    """

    model_config = ConfigDict(from_attributes=True)
    kind: Literal["duration"] = "duration"
    days: int = Field(ge=1, le=365)


class ManualRule(BaseModel):
    """Done when the member says so.

    The escape hatch, and an honest one: some steps are «برو ثبت‌نام کن» and
    no counter in this app can see that happen. It is the only rule the
    engine cannot evaluate on its own, so it is the only one with a route
    (``POST /roadmaps/{id}/steps/{step_id}/complete``) -- and that route
    refuses every other kind, or «تمام شد» would be a way past a rule the
    builder set.
    """

    model_config = ConfigDict(from_attributes=True)
    kind: Literal["manual"] = "manual"


CompletionUnion = Annotated[
    ChallengeFinishedRule
    | CountRule
    | StreakRule
    | AmountRule
    | DurationRule
    | ManualRule,
    Field(discriminator="kind"),
]
