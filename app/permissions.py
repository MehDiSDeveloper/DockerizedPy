"""Who may do what -- the one place a role is turned into an answer.

There are **two independent role axes** and they never merge:

* ``User.role`` (:class:`~app.models.user.UserRole`) is app-wide: ``member``
  or ``admin``. It answers "may you run the place".
* ``Enrollment.role`` (:class:`~app.models.enrollment.ChallengeRole`) is
  scoped to one challenge: ``participant`` or ``owner``. It answers "what
  are you *in here*".

Keeping them apart is the point. An admin is a plain participant inside
someone else's challenge -- they get no edit button there -- and an owner has
no reach outside their own. One merged column is how an admin quietly becomes
the owner of everything.

**Moderation is the third thing, and it is not ownership.** An operator has
to be able to see every challenge and take a bad one off the floor, so the
admin role grants ``CHALLENGE_LIST_ALL`` / ``CHALLENGE_MODERATE`` /
``CHALLENGE_DELETE_ANY`` -- reach it, change *what state it is in*, remove it.
It does **not** grant ``CHALLENGE_EDIT``: rewriting someone's title, cadence
or goal is authorship, and an admin who could do it silently would be
indistinguishable from the owner in the audit trail. That line is the whole
reason moderation has names of its own instead of admin being added to
``CHALLENGE_GRANTS``, and ``tests/test_admin_challenges.py`` pins both halves.

Callers ask for a **permission**, never for a role::

    if not can(user, Perm.CHALLENGE_DELETE, challenge=c, enrollment=e):
        raise HTTPException(403, ...)

so changing policy is an edit to the two grant maps below rather than a hunt
for ``user.role == "admin"`` spread across routers. That indirection is the
whole scalability argument: a third role is a row in a map, not a sweep.

**What this is not.** It does not replace the two SQL visibility filters
(``challenge_visibility_filter``, ``profile_visibility_filter``). Those decide
whether a row is *reachable* and must stay composed into the query, because
that is what makes a miss fall out as "no such row" and keeps the deliberate
404-vs-403 split intact (see CLAUDE.md). ``can()`` runs *after* a row is in
hand, on a caller who could already see it.
"""

from __future__ import annotations

from app.models.challenge import Challenge
from app.models.enrollment import ChallengeRole, Enrollment
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
    # Moderation: the operator's reach over challenges they do not own. It is
    # deliberately three narrow permissions rather than "admin owns
    # everything" -- see the module docstring and CLAUDE.md.
    CHALLENGE_LIST_ALL = "challenge.list_all"
    CHALLENGE_MODERATE = "challenge.moderate"
    CHALLENGE_DELETE_ANY = "challenge.delete_any"

    # Per-challenge, granted by `Enrollment.role`.
    CHALLENGE_EDIT = "challenge.edit"
    CHALLENGE_DELETE = "challenge.delete"


GLOBAL_GRANTS: dict[str, frozenset[str]] = {
    UserRole.MEMBER.value: frozenset(),
    UserRole.ADMIN.value: frozenset(
        {
            Perm.USER_LIST,
            Perm.USER_SET_ROLE,
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


def can(
    user: User | None,
    permission: str,
    *,
    challenge: Challenge | None = None,
    enrollment: Enrollment | None = None,
) -> bool:
    """Does ``user`` hold ``permission``, in this challenge if one is given?

    An anonymous caller holds nothing. The two axes are checked
    independently and either can grant -- but note that no app-wide role
    grants a per-challenge permission today, which is the deliberate answer
    to "can an admin edit my challenge": no.
    """
    if user is None:
        return False
    if permission in GLOBAL_GRANTS.get(user.role, frozenset()):
        return True
    role = challenge_role(user.id, challenge=challenge, enrollment=enrollment)
    if role is None:
        return False
    return permission in CHALLENGE_GRANTS.get(role, frozenset())
