from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.media import MEDIA_KEY_MAX_LENGTH, is_media_key
from app.models.challenge import (
    ChallengeCategory,
    GroupAudience,
    IdentityMode,
    LifecycleStatus,
    ParticipationMode,
    ProofKind,
    ReviewMode,
    Visibility,
)
from app.schemas.cadence import CadenceUnion
from app.schemas.proof import ProofUnion, SelfProof


class ChallengeBase(BaseModel):
    title: str = Field(min_length=3, max_length=50)
    description: str | None = Field(max_length=500, default=None)
    due_date: datetime | None = Field(default=None)
    rules: str | None = Field(max_length=1000, default=None)
    category: ChallengeCategory = Field(default=ChallengeCategory.OTHER)
    visibility: Visibility = Field(default=Visibility.PUBLIC)
    # The default a challenge is *created* with is `member_choice`: the
    # creator has not been asked, and deferring the question to each
    # participant is the answer that decides least on their behalf. The
    # column's own default stays `named` -- that one is what a row written
    # before this feature existed means, and rewriting it would change what
    # those challenges promised.
    identity_mode: IdentityMode = Field(default=IdentityMode.MEMBER_CHOICE)
    lifecycle_status: LifecycleStatus = Field(default=LifecycleStatus.ACTIVE)
    goal_amount: Decimal | None = Field(default=None)
    goal_unit: str | None = Field(max_length=32, default=None)
    # --- the act's two other axes ---------------------------------------
    # `proof` is a discriminated union, exactly as `cadence` is, and the
    # router writes its `kind` onto `proof_kind` -- so the SQL-queryable
    # discriminator can never disagree with the JSON beside it. Both default
    # to what every challenge in the app already was: say you did it, and
    # that is the end of it.
    proof: ProofUnion = Field(default_factory=SelfProof)
    review_mode: ReviewMode = Field(default=ReviewMode.AUTO)
    # The two pictures, each a media key from `POST /media/` -- never a path,
    # and never the bytes themselves: storing a file and choosing what a row
    # points at are two acts, and keeping them apart is what lets the create
    # wizard (which has no row yet) and the edit sheet use one picker.
    image_square: str | None = Field(max_length=MEDIA_KEY_MAX_LENGTH, default=None)
    image_tall: str | None = Field(max_length=MEDIA_KEY_MAX_LENGTH, default=None)

    @field_validator("proof", mode="before")
    @classmethod
    def _proof_default(cls, value):
        """A NULL ``proof`` column reads as self-report.

        The column is nullable so the migration needs no backfill (see
        `app/models/challenge.py`), and NULL therefore means exactly one
        thing: a challenge written before the proof axis existed, which is
        self-reported by definition. Every write since sets `proof_kind` and
        `proof` from the same object, so the two cannot disagree.

        The same fallback `app.proof.parse_proof` makes, restated here rather
        than imported because this is the *serialisation* boundary and that
        one is the domain's -- and because a schema importing a service
        module to answer a default is a cycle waiting to happen.
        """
        return {"kind": "self"} if value is None else value

    @field_validator("image_square", "image_tall")
    @classmethod
    def _known_media(cls, value: str | None) -> str | None:
        """The one gate a picture id passes before it reaches the column.

        Same shape as `schemas/user.py`'s `_known_avatar`: `None` is a real
        value (no picture), and anything else has to be a key this app's own
        `app/media.py` minted -- which is what keeps the column from ever
        holding something that could be read as a path.
        """
        if value is not None and not is_media_key(value):
            raise ValueError("Unknown image")
        return value


class ChallengeCreate(ChallengeBase):
    cadence: CadenceUnion
    timezone: str | None = Field(max_length=64, default=None)

    # --- Group challenges -------------------------------------------------
    # All four are create-only and none of them is on `ChallengeUpdate`.
    # `group_id` decides who can *ever* see this challenge, so moving it
    # later would retroactively change that -- the same objection that locks
    # `identity_mode`; the other three are the terms the participants were
    # enrolled under. Changing the terms of something people are already in
    # is what the lock rules exist to prevent.
    #
    # A caller with no groups sends none of them and nothing about their
    # request changes, which is the point: the wizard only asks these
    # questions when it was opened from inside a group.
    group_id: int | None = Field(default=None)
    group_audience: GroupAudience = Field(default=GroupAudience.SELECTED)
    participation_mode: ParticipationMode = Field(default=ParticipationMode.OPTIONAL)
    #: Who to enrol, for `group_audience = selected`. Ignored for `all`,
    #: which is read off the group's membership instead -- and re-read
    #: whenever somebody new joins it (`apply_standing_audience`).
    member_ids: list[int] = Field(default_factory=list, max_length=500)

    # --- Pact -------------------------------------------------------------
    #: The other half of a two-person pact, named by credential (a mobile
    #: number or an exact email), exactly as a referee is invited.
    #:
    #: **Not a fourth kind of challenge.** A pact is the arrangement where
    #: two people each hold an enrollment and an active referee row, and this
    #: field is nothing but the shortcut that writes both in one request --
    #: `app.referees.is_pact` *derives* the reading back from the rows, so
    #: there is no column anywhere that could say "pact" while the rows said
    #: otherwise. Setting it implies `review_mode = referee`: a pact whose
    #: reports settle themselves is two people keeping separate diaries.
    pact_with: str | None = Field(default=None, max_length=120)


class ChallengeRead(ChallengeBase):
    model_config = ConfigDict(from_attributes=True)
    id: int = Field(description="The unique identifier of the challenge")
    owner_id: int = Field(
        description="The unique identifier of the owner of the challenge"
    )
    cadence: CadenceUnion
    proof_kind: ProofKind = Field(default=ProofKind.SELF)
    group_id: int | None = Field(default=None)
    group_audience: GroupAudience = Field(default=GroupAudience.SELECTED)
    participation_mode: ParticipationMode = Field(
        default=ParticipationMode.OPTIONAL
    )


class ChallengeUpdate(BaseModel):
    title: str | None = Field(min_length=3, max_length=50, default=None)
    description: str | None = Field(max_length=500, default=None)
    due_date: datetime | None = Field(default=None)
    rules: str | None = Field(max_length=1000, default=None)
    category: ChallengeCategory | None = Field(default=None)
    visibility: Visibility | None = Field(default=None)
    identity_mode: IdentityMode | None = Field(default=None)
    lifecycle_status: LifecycleStatus | None = Field(default=None)
    cadence: CadenceUnion | None = Field(default=None)
    goal_amount: Decimal | None = Field(default=None)
    goal_unit: str | None = Field(max_length=32, default=None)
    # Both axes stay editable after creation, unlike `cadence`/`goal_*`/
    # `identity_mode`. The difference is what an edit would rewrite: changing
    # a cadence changes what everybody's past reports were *measured
    # against*, while turning review on changes only what happens next --
    # verdicts already written are stored, so nothing settled is re-decided.
    proof: ProofUnion | None = Field(default=None)
    review_mode: ReviewMode | None = Field(default=None)
    # The two pictures, each a media key from `POST /media/` -- never a path,
    # and never the bytes themselves: storing a file and choosing what a row
    # points at are two acts, and keeping them apart is what lets the create
    # wizard (which has no row yet) and the edit sheet use one picker.
    image_square: str | None = Field(max_length=MEDIA_KEY_MAX_LENGTH, default=None)
    image_tall: str | None = Field(max_length=MEDIA_KEY_MAX_LENGTH, default=None)

    @field_validator("image_square", "image_tall")
    @classmethod
    def _known_media(cls, value: str | None) -> str | None:
        """The one gate a picture id passes before it reaches the column.

        Same shape as `schemas/user.py`'s `_known_avatar`: `None` is a real
        value (no picture), and anything else has to be a key this app's own
        `app/media.py` minted -- which is what keeps the column from ever
        holding something that could be read as a path.
        """
        if value is not None and not is_media_key(value):
            raise ValueError("Unknown image")
        return value

