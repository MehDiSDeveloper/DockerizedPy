"""Wire shapes for the group subsystem.

Two rules from the rest of the app carry straight over and are worth naming
because they are what these classes are *for*:

* **The write boundary is the only gate.** ``emblem`` is validated here with
  the same ``is_valid_avatar`` the ``avatar`` field uses, for the same
  reason (``app/avatars.py``): the stored value is fed back out as a static
  path, so nothing downstream needs to check it and nothing downstream does.
* **A field that must not be settable is not on the shape.** ``GroupUpdate``
  carries no ``owner_id`` and no ``role``: ownership moves through
  ``POST /groups/{id}/transfer`` and a role through
  ``PATCH /groups/{id}/members/{user_id}``, each with its own permission, so
  a blind ``setattr`` loop over an update body can never be a way around
  either. It is the same reason ``UserUpdate`` does not carry ``role`` and
  ``EnrollmentUpdate`` does not carry ``is_anonymous``.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.avatars import is_valid_avatar
from app.models.group import GroupKind, GroupRole, JoinRequestStatus


def _validate_emblem(value: str | None) -> str | None:
    if not is_valid_avatar(value):
        raise ValueError("Unknown emblem id")
    return value


class GroupBase(BaseModel):
    name: str = Field(min_length=2, max_length=60)
    description: str | None = Field(max_length=500, default=None)
    kind: GroupKind = Field(default=GroupKind.OTHER)
    emblem: str | None = Field(max_length=60, default=None)

    _check_emblem = field_validator("emblem")(_validate_emblem)


class GroupCreate(GroupBase):
    """A new group, optionally inside one that already exists.

    ``parent_id`` is on *create* and nowhere else: it is the only field of a
    group that decides who can see it, so moving a group later would
    retroactively change that for everything ever published in it. It is
    absent from ``GroupUpdate`` for the reason ``ChallengeUpdate`` carries no
    ``group_id``.
    """

    parent_id: int | None = Field(default=None)


class GroupUpdate(BaseModel):
    """Partial. ``name`` is optional but may not be explicitly null -- the
    column is NOT NULL, the same shape (and the same reason) as
    ``UserUpdate.name``."""

    name: str | None = Field(min_length=2, max_length=60, default=None)
    description: str | None = Field(max_length=500, default=None)
    kind: GroupKind | None = Field(default=None)
    emblem: str | None = Field(max_length=60, default=None)

    _check_emblem = field_validator("emblem")(_validate_emblem)

    @field_validator("name")
    @classmethod
    def _name_not_null(cls, value: str | None) -> str | None:
        if value is None:
            raise ValueError("name cannot be null")
        return value


class GroupRead(GroupBase):
    """One group, as its members see it.

    Carries ``member_count`` and the reader's own ``my_role`` because both
    are what every surface showing a group needs and neither is on the row --
    computing them at each call site is how a card and a page come to
    disagree about the same group.
    """

    model_config = ConfigDict(from_attributes=True)
    id: int
    owner_id: int
    created_at: datetime
    member_count: int = 0
    my_role: GroupRole | None = None
    #: Where this group sits. The name rides along because every surface that
    #: shows the parent shows it by name, and a second request per card to
    #: turn an id into a word is the thing ``member_count`` is here to avoid.
    parent_id: int | None = None
    parent_name: str | None = None
    #: How many groups are directly inside this one.
    child_count: int = 0


class GroupMemberRead(BaseModel):
    """One person on a group roster.

    Deliberately **not** a nested ``UserPublicRead`` or anything wider: name,
    picture and role are what a roster is, and a group administrator gets no
    reach into a member's account through the fact that they administer the
    group. That is the same line ``profile_visibility_filter`` draws, and
    this shape is where it would leak if it leaked anywhere.
    """

    model_config = ConfigDict(from_attributes=True)
    user_id: int
    name: str
    avatar: str | None = None
    role: GroupRole
    joined_at: datetime


class GroupRoleUpdate(BaseModel):
    """The whole body of ``PATCH /groups/{id}/members/{user_id}``.

    One field, on a route of its own, for the reason ``UserRoleUpdate`` is:
    a role is the one thing on a membership that decides what somebody may
    do, so it gets an entrance that can be guarded on its own permission
    rather than riding along inside a general update.

    ``owner`` is not accepted -- handing the group over is
    ``POST /groups/{id}/transfer``, which has to move ``Group.owner_id`` and
    demote the previous owner in the same transaction, and a role PATCH that
    could produce a second owner would leave the two records contradicting
    each other.
    """

    role: GroupRole

    @field_validator("role")
    @classmethod
    def _not_owner(cls, value: GroupRole) -> GroupRole:
        if value is GroupRole.OWNER:
            raise ValueError("Use the transfer endpoint to change the owner")
        return value


class MemberAdd(BaseModel):
    """Add one person to the group by the credential they sign in with.

    A phone number (or an email, for an account that has one) rather than a
    user id, because an id is not something an administrator can *know*:
    there is no member search in this app, and adding one would hand every
    group administrator a way to walk the whole membership -- exactly what
    ``profile_visibility_filter`` exists to prevent. An exact number is
    something the person themselves gave you.
    """

    identifier: str = Field(min_length=3, max_length=120)


class GroupTransfer(BaseModel):
    new_owner_user_id: int


class InviteCreate(BaseModel):
    """``max_uses`` NULL means an unlimited link, which is a real and common
    choice (a company's own staff channel), so it is the field's default
    rather than something to be talked out of."""

    label: str | None = Field(max_length=60, default=None)
    max_uses: int | None = Field(default=None, ge=1, le=10_000)
    expires_at: datetime | None = Field(default=None)
    #: A link that asks instead of admitting -- see
    #: ``GroupInvite.requires_approval``. ``False`` is the default because a
    #: link handed to one person is still the common case; the group's
    #: standing public link is the one that sets it.
    requires_approval: bool = False


class InviteRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    code: str
    label: str | None = None
    max_uses: int | None = None
    uses: int
    expires_at: datetime | None = None
    revoked_at: datetime | None = None
    created_at: datetime
    requires_approval: bool = False
    #: Derived, never stored -- see ``invite_state`` in
    #: ``app/routers/group.py``. A stored status column would have to be
    #: rewritten by a job the moment a link expired.
    state: str


class InvitePreview(BaseModel):
    """What somebody holding a link is told *before* they use it.

    The group's name, emblem and size and nothing else: whoever has the code
    is not a member yet, and a preview that listed the challenges or the
    people would make an unused invite a way to read the group.
    """

    group_id: int
    name: str
    description: str | None = None
    kind: GroupKind
    emblem: str | None = None
    member_count: int
    state: str
    #: Does this link admit, or only let somebody ask? The landing page draws
    #: one button or the other from it, rather than offering «پیوستن» and
    #: discovering the refusal on tap.
    requires_approval: bool = False
    #: Already in? Then the landing page sends them straight in instead of
    #: offering to add them twice.
    already_member: bool = False
    #: Already asked to join and waiting? The page says so rather than
    #: offering a button that answers 409.
    request_pending: bool = False


class JoinRequestRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    group_id: int
    user_id: int
    name: str
    avatar: str | None = None
    status: JoinRequestStatus
    created_at: datetime
    # A terminal row is kept rather than deleted, and these two are the whole
    # reason: they are the only place that answers *when* the decision was
    # made and *who* made it -- the membership row itself says nothing about
    # how it came to exist. Both are NULL while the request is pending.
    decided_at: datetime | None = None
    decided_by_name: str | None = None


class ParticipantsAdd(BaseModel):
    """Who to add to an existing group challenge.

    A list, not one id per request: an administrator adding the new hires
    means one action to them, and a route that made it five would be five
    chances for a partial result.
    """

    user_ids: list[int] = Field(min_length=1, max_length=200)


class GroupLeaveResult(BaseModel):
    """What giving up a group's challenges did -- see `app/groups.py`.

    Titles rather than counts, because the caller has to *say* both halves:
    what was given up, and what the group would not let go of. `left_group`
    distinguishes the two routes that answer this shape -- the bulk unenrol,
    which leaves the member in the group, and leaving the group, which does
    both -- so the page can send the reader onward in one case and not the
    other without inferring it from a count.
    """

    left: list[str] = []
    kept: list[str] = []
    left_group: bool = False
