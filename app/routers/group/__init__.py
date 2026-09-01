# app/routers/group/__init__.py
"""The group subsystem's JSON door, and the queries the SSR pages reuse.

Three rules from the rest of the app decide almost everything in this
package, and they are worth naming because between them they *are* the
access control:

1. **Reachability is a composed clause.** Every statement that touches a
   group goes through :func:`load_group`, which selects the row with
   ``group_visibility_filter`` already in the ``WHERE``. A group you are not
   in is not a 403, it is a **404** -- the third such filter in the app,
   following ``challenge_visibility_filter`` and
   ``profile_visibility_filter`` (CLAUDE.md).
2. **Callers ask for a permission, never a role.** ``can(user, Perm.GROUP_*,
   group=…, membership=…)``. Which of ``member``/``admin``/``owner`` may do
   what is data in ``app/permissions.py``, so a fourth group role would be a
   row in a map rather than a sweep through this package.
3. **The acting user comes from the session.** There is no client-supplied
   actor anywhere here. The one place a *subject* is named in a path -- the
   two member routes -- is guarded by a permission and by the rules below,
   never by trusting the id.

**404 vs 403, inside a group.** Once you are in, the group is not a secret
from you, so refusing an action you may not take is an honest 403 and leaks
nothing: everybody in a group can already see who administers it. The 404 is
reserved for the group itself, and for one other case -- a member id that is
not in this group -- because there the id is a bare sequential integer and a
403 would confirm the account exists.

**What an operator cannot do here.** Nothing in this package consults
``User.role``. An app-wide admin moderates *challenges*, which are published
content; a group is somebody's organisation, and an operator who could
administer it would be indistinguishable from the person who runs it. That is
the same line the module docstring of ``app/permissions.py`` draws twice
already, and ``tests/test_groups.py`` pins it.

**This is a package, not a single module, for size alone.** ``queries.py``
holds the shared queries and helpers (no routes, and it must not import from
the route modules below); ``members.py`` is the group itself and its
membership routes; ``invites.py`` is invite links, the public link and the
join-request queue behind them (two routers -- one under ``/groups``, one
under ``/invites``, for the reason ``invite_router`` below explains);
``participants.py`` is who is in one group challenge. This file is the
compatibility entry point: it re-exports every name the rest of the app
imports from ``app.routers.group`` (``app/main.py``,
``app/routers/views/group.py``, ``app/routers/views/challenge.py``), so
nothing outside this package had to change when it was split.
"""

from fastapi import APIRouter

from app.groups import member_counts
from app.routers.group import invites, members, participants
from app.routers.group.queries import (
    DEFAULT_GROUP_PAGE_SIZE,
    MAX_GROUP_PAGE_SIZE,
    count_pending_requests,
    fetch_group_member_page,
    fetch_group_page,
    fetch_request_page,
    group_invites,
    invite_by_code,
    invite_rows,
    load_group,
    member_rows,
)

router = APIRouter()
router.include_router(members.router)
router.include_router(invites.router)
router.include_router(participants.router)

# The invite landing lives on its own prefix rather than under `/groups/`,
# because the whole point of a code is that the person holding it does not
# know -- and must not need to know -- the group's id. Mounted separately in
# `app/main.py` (`app.include_router(group.invite_router)`), not nested under
# `router` above.
invite_router = invites.invite_router

__all__ = [
    "DEFAULT_GROUP_PAGE_SIZE",
    "MAX_GROUP_PAGE_SIZE",
    "count_pending_requests",
    "fetch_group_member_page",
    "fetch_group_page",
    "fetch_request_page",
    "group_invites",
    "invite_by_code",
    "invite_router",
    "invite_rows",
    "load_group",
    "member_counts",
    "member_rows",
    "router",
]
