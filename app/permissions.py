"""Who may do what -- the one place a role is turned into an answer.

There are **four independent role axes** and they never merge:

* ``User.role`` (:class:`~app.models.user.UserRole`) is app-wide: ``member``
  or ``admin``. It answers "may you run the place".
* ``Enrollment.role`` (:class:`~app.models.enrollment.ChallengeRole`) is
  scoped to one challenge: ``participant`` or ``owner``. It answers "what
  are you *in here*".
* ``GroupMembership.role`` (:class:`~app.models.group.GroupRole`) is scoped
  to one group: ``member``, ``admin`` or ``owner``. It answers "what are you
  *in this organisation*".
* ``Roadmap.owner_id`` is scoped to one roadmap: you built this course, or you
  are walking it. It answers "whose course is this". The narrowest axis --
  one role and no membership table of roles to reconcile -- and it is an axis
  anyway, because an ``owner_id ==`` comparison spread across a router is
  precisely what :func:`can` replaces.

Keeping them apart is the point. An admin is a plain participant inside
someone else's challenge -- they get no edit button there -- and an owner has
no reach outside their own. One merged column is how an admin quietly becomes
the owner of everything.

**The group axis is the newest, and the rule holds twice over.** No app-wide
role grants a single ``GROUP_*`` permission -- an operator does not become
the administrator of a company that happens to use this app -- and no group
role grants a single ``CHALLENGE_*`` one. A group administrator gets
``GROUP_CREATE_CHALLENGE``, which is checked *at the moment a challenge is
created under their group*; from then on that challenge has an owner like
any other and its edit rights come from the challenge axis alone. Both lines
are drawn in the same place and for the same reason: whoever wrote a thing
and whoever runs the room it sits in are not interchangeable in the record.

**Moderation is the third thing, and it is not ownership.** An operator has
to be able to see every challenge and take a bad one off the floor, so the
admin role grants ``CHALLENGE_LIST_ALL`` / ``CHALLENGE_MODERATE`` /
``CHALLENGE_DELETE_ANY`` -- reach it, change *what state it is in*, remove it.
It does **not** grant ``CHALLENGE_EDIT``: rewriting someone's title, cadence
or goal is authorship, and an admin who could do it silently would be
indistinguishable from the owner in the audit trail. That line is the whole
reason moderation has names of its own instead of admin being added to
``CHALLENGE_GRANTS``, and ``tests/test_admin_challenges.py`` pins both halves.

**Support is the fourth thing, and it is why an admin may edit an account
but not a challenge.** ``USER_VIEW_ANY`` / ``USER_EDIT_ANY`` let an operator
open a member's profile and correct what it holds -- a mistyped email, a
phone number that will not receive anything, a city. That is not the same
grant ``CHALLENGE_EDIT`` would be: a challenge is *authored* work published
under someone's name, so rewriting its title or cadence would make the
operator indistinguishable from the author, while an account's contact
fields are the member's own record, held by the app on their behalf, and
correcting one on request is the whole job of a support desk. Every such
write still passes through ``update_user``, which stamps
``last_modifier_user_id`` with the operator, so the edit is attributable
rather than anonymous.

Three things stay out of it on purpose, and each has its own door or none at
all: the **password** (no route can set someone else's -- the seed script is
the only reset path, and it is an operator-console action, not an in-app
one), the **role** (``PATCH /users/{id}/role``, so escalation has exactly one
audited entrance), and **deletion** (own-only, and refused outright once any
history exists).

Callers ask for a **permission**, never for a role::

    if not can(user, Perm.CHALLENGE_DELETE, challenge=c, enrollment=e):
        raise HTTPException(403, ...)

so changing policy is an edit to the four grant maps below rather than a hunt
for ``user.role == "admin"`` spread across routers. That indirection is the
whole scalability argument: a new role is a row in a map, not a sweep.

**What this is not.** It does not replace the two SQL visibility filters
(``challenge_visibility_filter``, ``profile_visibility_filter``). Those decide
whether a row is *reachable* and must stay composed into the query, because
that is what makes a miss fall out as "no such row" and keeps the deliberate
404-vs-403 split intact (see CLAUDE.md). ``can()`` runs *after* a row is in
hand, on a caller who could already see it.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.models.challenge import Challenge
from app.models.enrollment import ChallengeRole, Enrollment
from app.models.group import Group, GroupMembership, GroupRole
from app.models.roadmap import Roadmap
from app.models.user import User, UserRole


class Perm:
    """Permission names. Constants, not an enum, so a grant map reads as data.

    Add a name here, grant it below, and check it at the call site -- in that
    order. A permission nothing checks is documentation pretending to be a
    gate.
    """

    # App-wide, granted by `User.role`.
    USER_LIST = "user.list"
    USER_SET_ROLE = "user.set_role"
    # Support: reach a member's own profile, and correct what it holds. The
    # deliberate counterpart to CHALLENGE_EDIT staying per-challenge -- see
    # the module docstring for why the two lines fall in different places.
    USER_VIEW_ANY = "user.view_any"
    USER_EDIT_ANY = "user.edit_any"
    # Moderation: the operator's reach over challenges they do not own. It is
    # deliberately three narrow permissions rather than "admin owns
    # everything" -- see the module docstring and CLAUDE.md.
    CHALLENGE_LIST_ALL = "challenge.list_all"
    CHALLENGE_MODERATE = "challenge.moderate"
    CHALLENGE_DELETE_ANY = "challenge.delete_any"

    # Per-challenge, granted by `Enrollment.role`.
    CHALLENGE_EDIT = "challenge.edit"
    CHALLENGE_DELETE = "challenge.delete"

    # Per-group, granted by `GroupMembership.role`. Split finely rather than
    # given one "manage" name, because the split is exactly what separates an
    # administrator from an owner: running the place day to day is shared,
    # and deciding *who runs it* is not.
    GROUP_VIEW = "group.view"
    GROUP_EDIT = "group.edit"
    #: Invite, approve/reject requests, remove plain members.
    GROUP_MANAGE_MEMBERS = "group.manage_members"
    #: Hand out and take back `admin`. Owner-only, deliberately: an
    #: administrator who could promote themselves is an owner with a delay.
    GROUP_MANAGE_ADMINS = "group.manage_admins"
    GROUP_CREATE_CHALLENGE = "group.create_challenge"
    #: Open a group *inside* this one. An administrator's, like creating a
    #: challenge here: both are running the group day to day, and neither
    #: touches who administers it. The creator owns the subgroup they made --
    #: a new group is created the one way groups are created -- so authority
    #: over it comes from `GROUP_GRANTS` reaching down the tree
    #: (`group_ids_for`), not from a second kind of ownership.
    GROUP_CREATE_SUBGROUP = "group.create_subgroup"
    GROUP_TRANSFER = "group.transfer"
    GROUP_DELETE = "group.delete"

    # Per-roadmap, granted by ownership of the roadmap. The fourth axis, and
    # the narrowest: a roadmap has a builder and everybody else is walking it,
    # so there is one role and no membership table of roles to resolve. It is
    # still an axis rather than an `owner_id ==` at each call site, because
    # that comparison spread across a router is exactly what `can()` exists to
    # replace -- and because "a co-author of a course" is the obvious next
    # thing to want, and it should be a row in a map.
    ROADMAP_EDIT = "roadmap.edit"
    ROADMAP_DELETE = "roadmap.delete"
    #: Add, reorder and remove steps; mint and revoke invite links.
    ROADMAP_MANAGE = "roadmap.manage"


GLOBAL_GRANTS: dict[str, frozenset[str]] = {
    UserRole.MEMBER.value: frozenset(),
    UserRole.ADMIN.value: frozenset(
        {
            Perm.USER_LIST,
            Perm.USER_SET_ROLE,
            Perm.USER_VIEW_ANY,
            Perm.USER_EDIT_ANY,
            Perm.CHALLENGE_LIST_ALL,
            Perm.CHALLENGE_MODERATE,
            Perm.CHALLENGE_DELETE_ANY,
        }
    ),
}

CHALLENGE_GRANTS: dict[str, frozenset[str]] = {
    ChallengeRole.PARTICIPANT.value: frozenset(),
    ChallengeRole.OWNER.value: frozenset({Perm.CHALLENGE_EDIT, Perm.CHALLENGE_DELETE}),
}

# The group axis. Cumulative, because the roles genuinely nest here -- an
# owner can do everything an admin can -- but written out as unions rather
# than resolved by a lookup chain, so "what may an admin do" is still one
# expression you can read, the same as the two maps above.
_GROUP_MEMBER_GRANTS = frozenset({Perm.GROUP_VIEW})
_GROUP_ADMIN_GRANTS = _GROUP_MEMBER_GRANTS | {
    Perm.GROUP_EDIT,
    Perm.GROUP_MANAGE_MEMBERS,
    Perm.GROUP_CREATE_CHALLENGE,
    Perm.GROUP_CREATE_SUBGROUP,
}

GROUP_GRANTS: dict[str, frozenset[str]] = {
    GroupRole.MEMBER.value: _GROUP_MEMBER_GRANTS,
    GroupRole.ADMIN.value: _GROUP_ADMIN_GRANTS,
    GroupRole.OWNER.value: _GROUP_ADMIN_GRANTS
    | {Perm.GROUP_MANAGE_ADMINS, Perm.GROUP_TRANSFER, Perm.GROUP_DELETE},
}


# The roadmap axis. One role today, written as a map for the reason the other
# three are: a second role is a row here rather than a sweep through the
# router. **No app-wide role grants a ROADMAP_* permission** -- the same line
# drawn twice already for groups and challenges: an operator moderates
# published *challenges*, and a course somebody assembled is authored work,
# not a challenge they own.
ROADMAP_OWNER = "owner"

ROADMAP_GRANTS: dict[str, frozenset[str]] = {
    ROADMAP_OWNER: frozenset(
        {Perm.ROADMAP_EDIT, Perm.ROADMAP_DELETE, Perm.ROADMAP_MANAGE}
    ),
}


def roadmap_role(user_id: int | None, roadmap: Roadmap | None) -> str | None:
    """This member's role in this roadmap, or ``None``.

    The third of the ``*_role`` resolvers, and the simplest: ``owner_id`` is
    NOT NULL and there is no second record to reconcile it with, so unlike
    :func:`challenge_role` and :func:`group_role` there is nothing here that
    could drift. It is written anyway so that every axis is asked the same way
    and a future co-author role has one place to land.
    """
    if user_id is None or roadmap is None:
        return None
    return ROADMAP_OWNER if roadmap.owner_id == user_id else None


def is_admin(user: User | None) -> bool:
    return user is not None and user.role == UserRole.ADMIN.value


def challenge_role(
    user_id: int | None,
    challenge: Challenge | None = None,
    enrollment: Enrollment | None = None,
) -> str | None:
    """This member's role in this challenge, or ``None`` if they have none.

    Two sources, resolved in one place because either alone is wrong:

    * ``Challenge.owner_id`` is NOT NULL and always present, but cannot
      express a second owner or a coach.
    * ``Enrollment.role`` can, but the row is not guaranteed to exist -- an
      owner may unenrol from their own challenge, and doing so must not
      strip them of the challenge they still own.

    So ownership recorded on the challenge wins, and the enrollment supplies
    the role for everyone else. When a hand-over ships, it writes *both*, and
    this function keeps answering from one place either way.
    """
    if user_id is None:
        return None
    if challenge is not None and challenge.owner_id == user_id:
        return ChallengeRole.OWNER.value
    if enrollment is not None and enrollment.user_id == user_id:
        return enrollment.role or ChallengeRole.PARTICIPANT.value
    return None


def group_role(
    user_id: int | None,
    group: Group | None = None,
    membership: GroupMembership | None = None,
) -> str | None:
    """This member's role in this group, or ``None`` if they are not in it.

    The group-side twin of :func:`challenge_role`, resolved the same way and
    for the same reason: ``Group.owner_id`` is the record of ownership and is
    always present, while the membership row carries the role for everyone
    else. A group owner cannot leave without handing the group over first --
    but the resolution is written this way regardless, so that a membership
    row saying ``member`` can never be read as the owner having lost their
    own group.

    The membership is checked to belong to the asker: a row handed in for
    somebody else grants nothing, exactly as in :func:`challenge_role`.
    """
    if user_id is None:
        return None
    if group is not None and group.owner_id == user_id:
        return GroupRole.OWNER.value
    if membership is not None and membership.user_id == user_id:
        return membership.role or GroupRole.MEMBER.value
    return None


@dataclass(frozen=True)
class EffectiveMembership:
    """A standing in one group, resolved across the group tree.

    ``load_group`` hands this to :func:`can` in place of a ``GroupMembership``
    row, because with nested groups the row that decides what somebody may do
    here is not always a row *on* here: an administrator of the company
    administers its departments. It duck-types the two attributes
    :func:`group_role` reads and carries nothing else -- it is an answer, not
    a record, and it is deliberately not a row anybody can write through.

    **Inherited authority stops at ``admin``.** An owner of the parent runs
    what is inside it, but does not become the owner of a group somebody else
    opened: ownership is transferred and deleted from, and both of those are
    decisions that belong to the person whose group it is. You own what you
    were given, and you administer what is under it.
    """

    user_id: int
    group_id: int
    role: str
    inherited: bool = False


def inherited_role(role: str | None) -> str:
    """What a role on an ancestor is worth further down: at most ``admin``."""
    if role in (GroupRole.OWNER.value, GroupRole.ADMIN.value):
        return GroupRole.ADMIN.value
    return GroupRole.MEMBER.value


#: Strongest first, for picking between the rows somebody holds up the tree.
GROUP_ROLE_RANK = {
    GroupRole.OWNER.value: 2,
    GroupRole.ADMIN.value: 1,
    GroupRole.MEMBER.value: 0,
}


def can(
    user: User | None,
    permission: str,
    *,
    challenge: Challenge | None = None,
    enrollment: Enrollment | None = None,
    group: Group | None = None,
    membership: GroupMembership | None = None,
    roadmap: Roadmap | None = None,
) -> bool:
    """Does ``user`` hold ``permission`` -- here, in this challenge, group or
    roadmap?

    An anonymous caller holds nothing. The four axes are checked
    independently and any one of them can grant, but note that no app-wide
    role grants a per-challenge, per-group or per-roadmap permission today. That is the
    deliberate answer to both "can an operator edit my challenge" and "can an
    operator run my company's group": no. ``tests/test_user_roles.py`` and
    ``tests/test_groups.py`` parametrize the assertion over both global
    roles, so a future grant cannot quietly cross either line.
    """
    if user is None:
        return False
    if permission in GLOBAL_GRANTS.get(user.role, frozenset()):
        return True
    role = challenge_role(user.id, challenge=challenge, enrollment=enrollment)
    if role is not None and permission in CHALLENGE_GRANTS.get(role, frozenset()):
        return True
    g_role = group_role(user.id, group=group, membership=membership)
    if g_role is not None and permission in GROUP_GRANTS.get(g_role, frozenset()):
        return True
    r_role = roadmap_role(user.id, roadmap)
    if r_role is None:
        return False
    return permission in ROADMAP_GRANTS.get(r_role, frozenset())


# --- presentation: which roles a roster paints as elevated ---------------
# The four axes never merge as *permissions* -- that is the whole point of
# this module. But every roster in the app draws the same accent on the role
# pill of somebody who runs the thing being listed, and that one presentation
# decision had been written four different ways (`== 'admin'` on the member
# roster, `== 'owner'` on the participant roster, `in ('owner','admin')` on
# the group roster, a hardcoded class on the profile). One set, asked by one
# filter, so a new elevated role is a row here rather than a sweep.
#
# It is deliberately *not* `can()`: a pill is handed a role string and no
# object, so there is no permission to ask about -- and a filter that looked
# like a permission check would invite being used as one.
ELEVATED_ROLES = frozenset(
    {
        UserRole.ADMIN.value,
        ChallengeRole.OWNER.value,
        GroupRole.OWNER.value,
        GroupRole.ADMIN.value,
    }
)


def role_is_elevated(role: str | None) -> bool:
    """True for a role that runs the thing it is scoped to, on any axis."""
    return role in ELEVATED_ROLES


def register_role_filters(env) -> None:
    """Expose the role-pill helper to one Jinja environment."""
    env.filters["role_is_elevated"] = role_is_elevated
