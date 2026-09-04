# routers/views/group.py
"""The group screens.

Presentation only: every query in here comes from ``app/routers/group.py``
or ``app/routers/challenge.py``, the same "query logic in the API router,
presentation in views/" split ``fetch_challenge_page`` and
``fetch_member_page`` follow. In particular the group page's challenge list
is ``fetch_challenge_page(group_id=…)`` -- the app-wide list's own query with
one more narrowing -- so «چالش‌های این گروه» and «چالش‌ها» can never disagree
about what a search matches or what a status filter means.

**Which screens exist, and why these.** A group answers three questions, and
CLAUDE.md's rule about lists is what splits them across two pages rather than
one or four:

* ``/views/groups/{id}`` -- «چالش‌ها» / «اعضا» / «درباره» as three tab
  panels, the same shape challenge-detail uses for the same reason: three
  questions about one thing, one screen, the selected panel in the URL hash
  so a shared link lands where it was shared from. Only *one* of the three
  is lazily paged (the challenges), because two paged lists on one screen
  would fight over one URL and neither would stay linkable -- so «اعضا» is a
  screenful with a link to the roster.
* ``/views/groups/{id}/members`` -- the roster, searched and paged.
* ``/views/groups/{id}/manage`` -- requests, links and settings, gated on
  ``GROUP_MANAGE_MEMBERS``. A page of its own for the reason the moderation
  roster is one: it is where an administrator goes to *act*, and a member has
  no business reading it.
* ``/views/groups/{id}/challenges/{cid}/participants`` -- who is in one group
  challenge, and the only screen that adds or removes them.
* ``/views/invites/{code}`` -- the landing page, which is the one screen here
  a non-member ever sees.

**The gate is the same one the JSON API uses**, ``load_group``, so a page and
a ``fetch()`` can never disagree about who is in a group; the only difference
is the dependency in front of it (``get_page_user`` vs
``get_current_user_id``) -- the 303/401 split, for the reason every other
pair in the app has it.
"""

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.auth import get_current_user, get_page_user
from app.avatars import AVATAR_IDS, avatar_url, register_avatar_filters
from app.config import BASE_DIR
from app.database import get_db
from app.date_filters import register_date_filters
from app.explainers import register_explainer_filters
from app.groups import (
    INVITE_ACTIVE,
    invite_state,
    leaving_is_allowed,
    my_group_enrollments,
    register_group_filters,
)
from app.icons import register_icon_filters
from app.media import register_media_filters
from app.models.challenge import Challenge
from app.models.enrollment import Enrollment
from app.models.group import (
    Group,
    GroupJoinRequest,
    GroupMembership,
    JoinRequestStatus,
)
from app.models.user import User
from app.permissions import Perm, can, group_role, register_role_filters
from app.routers.challenge import (
    DEFAULT_PAGE_SIZE,
    MAX_PAGE_SIZE,
    STATUS_ALL,
    challenge_status,
    fetch_challenge_page,
)
from app.routers.group import (
    DEFAULT_GROUP_PAGE_SIZE,
    MAX_GROUP_PAGE_SIZE,
    count_pending_requests,
    fetch_child_groups,
    fetch_group_member_page,
    fetch_group_page,
    fetch_request_page,
    group_invites,
    invite_by_code,
    invite_rows,
    load_group,
    member_counts,
    member_rows,
    parent_names,
)
from app.routers.user import DEFAULT_MEMBER_PAGE_SIZE, MAX_MEMBER_PAGE_SIZE
from app.routers.views.challenge import STATUS_META, social_context

router = APIRouter(prefix="/views/groups", tags=["group-views"])
invite_router = APIRouter(prefix="/views/invites", tags=["group-views"])

templates = Jinja2Templates(directory=BASE_DIR / "templates")
register_date_filters(templates.env)
register_explainer_filters(templates.env)
# Every views router builds its own environment and owes the registrations
# for what it renders: pictures (a group's emblem is an avatar id -- one
# catalogue, one filter), the shell's icons, and the derived challenge status
# the group's own challenge cards wear.
register_avatar_filters(templates.env)
register_role_filters(templates.env)
register_media_filters(templates.env)
register_icon_filters(templates.env)
templates.env.filters["challenge_status"] = challenge_status
templates.env.globals["status_meta"] = STATUS_META

# The Farsi label maps live in `app/groups.py` -- more than one views router
# renders them (this one, and the challenge card's group chip), so they follow
# `NOTIFICATION_META`'s call rather than the per-surface one, and arrive here
# through the same registration contract `register_icon_filters` has.
register_group_filters(templates.env)

# How many members the group page's «اعضا» panel shows before handing off to
# the roster. A screenful, not a page: the panel answers "who is here" at a
# glance, and the paged answer lives at its own URL (see the module note on
# why only one list on this screen pages).
MEMBERS_PREVIEW = 12

def emblem_options() -> list[dict]:
    """The picker's options, in the catalogue's own order.

    The same list the profile's avatar sheet renders, built the same way --
    one catalogue in the app, so an emblem and an avatar are the same kind of
    value and go through the same validator (`app/avatars.py`).
    """
    return [{"id": a, "url": avatar_url(a)} for a in AVATAR_IDS]


async def _group_context(
    db: AsyncSession, group, membership, viewer: User
) -> dict:
    """What every group screen needs about the group and the reader.

    Three flags, in the shape ``routers/views/user.py`` established for the
    profile: what the reader *is* here, and what the template may therefore
    offer. They are rendering decisions only -- every route they lead to
    gates itself on the same permission through ``load_group`` + ``require``,
    so a member who guesses a URL gets the server's answer, not the
    template's.
    """
    my_role = group_role(viewer.id, group=group, membership=membership)
    counts = await member_counts(db, [group.id])
    # Where this group sits, and what sits inside it. Both are read on every
    # group screen because both are navigation: the parent is the way up (a
    # subgroup with no way back to its organisation is a dead end), the
    # children are the way down.
    children = await fetch_child_groups(db, group.id)
    parent = (
        await db.get(Group, group.parent_id)
        if group.parent_id is not None
        else None
    )
    return {
        "group": group,
        "my_role": my_role,
        "member_count": counts.get(group.id, 0),
        "parent_group": parent,
        "children": children,
        "child_counts": await member_counts(db, [c.id for c in children]),
        "can_create_subgroup": can(
            viewer, Perm.GROUP_CREATE_SUBGROUP, group=group, membership=membership
        ),
        "can_manage": can(
            viewer, Perm.GROUP_MANAGE_MEMBERS, group=group, membership=membership
        ),
        "can_edit_group": can(
            viewer, Perm.GROUP_EDIT, group=group, membership=membership
        ),
        "can_manage_admins": can(
            viewer, Perm.GROUP_MANAGE_ADMINS, group=group, membership=membership
        ),
        "can_create_challenge": can(
            viewer, Perm.GROUP_CREATE_CHALLENGE, group=group, membership=membership
        ),
    }


# ---------------------------------------------------------------------------
# My groups -- the page itself lives in `routers/views/space.py` («فضای من»),
# which renders this list and the roadmap one as two segments of the fifth
# nav destination. Only the fragment stays here, beside the query it pages.
# ---------------------------------------------------------------------------


@router.get("/fragment")
async def groups_fragment(
    request: Request,
    viewer: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=DEFAULT_GROUP_PAGE_SIZE, ge=1, le=MAX_GROUP_PAGE_SIZE),
):
    """`get_current_user`, not the page dependency: a `fetch()` needs a real
    401 to redirect on (CLAUDE.md, the 303/401 split)."""
    groups, has_more = await fetch_group_page(
        db, user_id=viewer.id, offset=offset, limit=limit
    )
    counts = await member_counts(db, [g.id for g in groups])
    response = templates.TemplateResponse(
        "group/_group_cards.html",
        {
            "request": request,
            "groups": groups,
            "member_counts": counts,
            "parent_names": await parent_names(db, groups),
        },
    )
    response.headers["X-Has-More"] = "true" if has_more else "false"
    return response


# ---------------------------------------------------------------------------
# One group
# ---------------------------------------------------------------------------


@router.get("/{group_id}")
async def group_detail(
    group_id: int,
    request: Request,
    viewer: User = Depends(get_page_user),
    db: AsyncSession = Depends(get_db),
    status: str = Query(default=STATUS_ALL, pattern="^(all|upcoming|active|finished)$"),
    q: str | None = Query(default=None, max_length=100),
):
    """The group: what is running here, who is here, and what this is."""
    group, membership = await load_group(db, group_id, viewer)
    context = await _group_context(db, group, membership, viewer)

    challenges, has_more = await fetch_challenge_page(
        db,
        current_user_id=viewer.id,
        category=None,
        q=q,
        offset=0,
        limit=DEFAULT_PAGE_SIZE,
        status=status,
        group_id=group_id,
        options=(selectinload(Challenge.enrollments),),
    )
    members, _ = await fetch_group_member_page(
        db, group_id=group_id, offset=0, limit=MEMBERS_PREVIEW
    )
    # The badge on the manage button is how many requests are waiting.
    pending = (
        await count_pending_requests(db, group_id) if context["can_manage"] else 0
    )
    # What leaving would actually cost the reader, counted server-side so the
    # confirmation can name it instead of asking them to agree to a number
    # nobody has shown them. `leaving_is_allowed(…, True)` is the same
    # question the bulk route asks, so the panel and the server cannot
    # disagree about which challenges are the member's to give up.
    mine = await my_group_enrollments(db, group_id=group_id, user_id=viewer.id)
    my_optional = [c.title for _e, c in mine if leaving_is_allowed(c, True)]
    my_mandatory = [c.title for _e, c in mine if not leaving_is_allowed(c, True)]
    return templates.TemplateResponse(
        "group/detail.html",
        {
            "title": group.name,
            "request": request,
            "current_user_id": viewer.id,
            "active_nav": "profile",
            **context,
            "challenges": challenges,
            **await social_context(db, viewer.id, challenges),
            "has_more": has_more,
            "page_size": DEFAULT_PAGE_SIZE,
            "active_status": status,
            "query": q or "",
            "status_options": STATUS_META,
            "members": member_rows(members),
            "members_preview": MEMBERS_PREVIEW,
            "pending_count": pending,
            "my_optional": my_optional,
            "my_mandatory": my_mandatory,
            "emblem_options": emblem_options(),
        },
    )


@router.get("/{group_id}/challenges/fragment")
async def group_challenges_fragment(
    group_id: int,
    request: Request,
    viewer: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE),
    status: str = Query(default=STATUS_ALL, pattern="^(all|upcoming|active|finished)$"),
    q: str | None = Query(default=None, max_length=100),
):
    """The group's challenge cards, page two onward.

    `load_group` still runs: the challenge query is already narrowed by the
    visibility filter, so a non-member would get an empty list either way,
    but answering **404** for the group itself is the same answer every other
    route gives and costs one read.
    """
    group, _membership = await load_group(db, group_id, viewer)
    challenges, has_more = await fetch_challenge_page(
        db,
        current_user_id=viewer.id,
        category=None,
        q=q,
        offset=offset,
        limit=limit,
        status=status,
        group_id=group.id,
        options=(selectinload(Challenge.enrollments),),
    )
    response = templates.TemplateResponse(
        "challenge/_challenge_cards.html",
        {
            "request": request,
            "challenges": challenges,
            "hide_group_chip": True,
            **await social_context(db, viewer.id, challenges),
        },
    )
    response.headers["X-Has-More"] = "true" if has_more else "false"
    return response


# ---------------------------------------------------------------------------
# The roster
# ---------------------------------------------------------------------------


@router.get("/{group_id}/members")
async def group_members_page(
    group_id: int,
    request: Request,
    viewer: User = Depends(get_page_user),
    db: AsyncSession = Depends(get_db),
    q: str | None = Query(default=None, max_length=100),
):
    """Everybody in the group, searched and paged.

    Open to every member, because a group roster is not what
    ``profile_visibility_filter`` withholds -- these people are in a room
    together. A row carries a name, a picture and a role and **is not a
    link**: reaching somebody's profile from a list they appear on is a
    second step, and CLAUDE.md is explicit that it belongs to that one
    function and nothing else. Role controls render only for whoever may
    actually use them, and the route they call checks again.
    """
    group, membership = await load_group(db, group_id, viewer)
    context = await _group_context(db, group, membership, viewer)
    rows, has_more = await fetch_group_member_page(
        db, group_id=group_id, q=q, offset=0, limit=DEFAULT_MEMBER_PAGE_SIZE
    )
    return templates.TemplateResponse(
        "group/members.html",
        {
            "title": f"اعضای {group.name}",
            "request": request,
            "current_user_id": viewer.id,
            "active_nav": "profile",
            **context,
            "members": member_rows(rows),
            "has_more": has_more,
            "page_size": DEFAULT_MEMBER_PAGE_SIZE,
            "query": q or "",
        },
    )


@router.get("/{group_id}/members/fragment")
async def group_members_fragment(
    group_id: int,
    request: Request,
    viewer: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    q: str | None = Query(default=None, max_length=100),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=DEFAULT_MEMBER_PAGE_SIZE, ge=1, le=MAX_MEMBER_PAGE_SIZE),
):
    group, membership = await load_group(db, group_id, viewer)
    context = await _group_context(db, group, membership, viewer)
    rows, has_more = await fetch_group_member_page(
        db, group_id=group_id, q=q, offset=offset, limit=limit
    )
    response = templates.TemplateResponse(
        "group/_member_rows.html",
        {
            "request": request,
            "members": member_rows(rows),
            "can_manage": context["can_manage"],
            "can_manage_admins": context["can_manage_admins"],
            "group": group,
            "current_user_id": viewer.id,
        },
    )
    response.headers["X-Has-More"] = "true" if has_more else "false"
    return response


# ---------------------------------------------------------------------------
# Management
# ---------------------------------------------------------------------------

# How many of the open queue the manage page shows inline. It has no paging
# of its own -- a settings screen is scanned, not paged -- so this is a cap
# with a note, not a page size.
MAX_PENDING_ON_MANAGE = 50


@router.get("/{group_id}/manage")
async def group_manage_page(
    group_id: int,
    request: Request,
    viewer: User = Depends(get_page_user),
    db: AsyncSession = Depends(get_db),
):
    """Requests, invite links and the group's own settings.

    **404 for a member who is not an administrator**, not 403 -- the same
    call ``get_admin_page_user`` makes for the operator panel and for the
    same reason: a page whose whole content is other people's pending
    requests has no business confirming to a member that it exists. The JSON
    routes behind it answer 403, because those are fixed paths reached by a
    ``fetch()`` that has to tell "not allowed" from "signed out".

    The pending queue is rendered directly on this page rather than on a
    screen of its own: it has no pagination and no terminal states to filter
    between, so the split that used to justify a separate page is gone. Only
    ``MAX_PENDING_ON_MANAGE`` rows are shown, with a note if the queue runs
    longer -- a settings screen is scanned, not paged.
    """
    group, membership = await load_group(db, group_id, viewer)
    context = await _group_context(db, group, membership, viewer)
    if not context["can_manage"]:
        raise HTTPException(status_code=404, detail="Not found")

    pending = await count_pending_requests(db, group_id)
    rows, has_more_requests = await fetch_request_page(
        db,
        group_id=group_id,
        status=JoinRequestStatus.PENDING.value,
        offset=0,
        limit=MAX_PENDING_ON_MANAGE,
    )
    # The public link is pulled out of the list rather than shown in it: it
    # is the one link with a *standing* meaning, and reading it out of a
    # list of five «لینک دعوت» rows is exactly the moment somebody hands out the
    # wrong one. It is only ever *read* here -- minting is a POST, so a page
    # load cannot create one.
    all_invites = invite_rows(await group_invites(db, group_id))
    public_invite = next(
        (i for i in all_invites if i.requires_approval and i.state == INVITE_ACTIVE),
        None,
    )
    return templates.TemplateResponse(
        "group/manage.html",
        {
            "title": f"مدیریت {group.name}",
            "request": request,
            "current_user_id": viewer.id,
            "active_nav": "profile",
            **context,
            "pending_count": pending,
            "requests": request_rows(rows),
            "more_requests": has_more_requests,
            "invites": [i for i in all_invites if i is not public_invite],
            "public_invite": public_invite,
            "emblem_options": emblem_options(),
        },
    )


# ---------------------------------------------------------------------------
# The request queue
# ---------------------------------------------------------------------------


def request_rows(rows: list[GroupJoinRequest]) -> list[dict]:
    """The shape the two request templates read.

    Dicts rather than the ORM rows, for the reason every other panel builder
    in this app returns one: the template must not be the thing that decides
    what a missing user or an undecided row looks like.
    """
    return [
        {
            "id": r.id,
            "user_id": r.user_id,
            "name": r.user.name if r.user else "—",
            "avatar": r.user.avatar if r.user else None,
            "status": r.status,
            "created_at": r.created_at,
            "decided_at": r.decided_at,
            "decided_by": r.decided_by.name if r.decided_by else None,
        }
        for r in rows
    ]


@router.get("/{group_id}/challenges/{challenge_id}/participants")
async def group_challenge_participants_page(
    group_id: int,
    challenge_id: int,
    request: Request,
    viewer: User = Depends(get_page_user),
    db: AsyncSession = Depends(get_db),
):
    """Who is in this group challenge, and the only place that changes.

    Administrator-only (**404** otherwise, like the manage page). It shows
    the group's whole roster with a mark on whoever is already enrolled,
    because "add the three people who are missing" is the actual task and a
    list of only the enrolled would make an administrator flip between two
    screens to do it.

    **It shows no scores and no per-person progress.** Running a group buys
    the right to decide who is in a challenge; it does not buy a file on
    anybody. The aggregate the group needs is on the challenge itself, and
    the leaderboard honours anonymity there like everywhere else.
    """
    group, membership = await load_group(db, group_id, viewer)
    context = await _group_context(db, group, membership, viewer)
    if not context["can_manage"]:
        raise HTTPException(status_code=404, detail="Not found")

    challenge = (
        await db.execute(
            select(Challenge).where(
                Challenge.id == challenge_id, Challenge.group_id == group_id
            )
        )
    ).scalar_one_or_none()
    if challenge is None:
        raise HTTPException(status_code=404, detail="Challenge not found")

    enrolled = set(
        (
            await db.execute(
                select(Enrollment.user_id).where(
                    Enrollment.challenge_id == challenge_id
                )
            )
        )
        .scalars()
        .all()
    )
    roster, _ = await fetch_group_member_page(
        db, group_id=group_id, offset=0, limit=MAX_MEMBER_PAGE_SIZE
    )
    return templates.TemplateResponse(
        "group/challenge_participants.html",
        {
            "title": f"شرکت‌کنندگان {challenge.title}",
            "request": request,
            "current_user_id": viewer.id,
            "active_nav": "profile",
            **context,
            "challenge": challenge,
            "members": [
                {**row.model_dump(), "enrolled": row.user_id in enrolled}
                for row in member_rows(roster)
            ],
            "enrolled_count": len(enrolled),
        },
    )


# ---------------------------------------------------------------------------
# The invite landing
# ---------------------------------------------------------------------------


@invite_router.get("/{code}")
async def invite_landing(
    code: str,
    request: Request,
    viewer: User = Depends(get_page_user),
    db: AsyncSession = Depends(get_db),
):
    """The one group screen somebody outside the group ever sees.

    A signed-out visitor is handled by machinery that already exists:
    ``get_page_user`` raises ``LoginRequired``, ``app.main`` turns it into a
    303 to the login page carrying ``?next=`` -- so they sign up, land back
    here, and the link still works. That is the whole answer to "what happens
    when somebody not signed in taps an invite", and it needed no code.

    What the page shows is :class:`InvitePreview`'s answer and nothing more:
    the group's name, emblem, kind and size. Whoever holds the code is not a
    member yet, and a preview listing the challenges or the people would make
    an unused invite a way to read the group.

    A dead link is not a dead end. A revoked, expired or full one says which,
    and offers the other door -- a join request, keyed on this same code, so
    the person who was handed a link that filled up is not simply stuck.
    """
    invite = await invite_by_code(db, code)
    group = invite.group
    state = invite_state(invite)
    counts = await member_counts(db, [group.id])
    membership = (
        await db.execute(
            select(GroupMembership).where(
                GroupMembership.group_id == group.id,
                GroupMembership.user_id == viewer.id,
            )
        )
    ).scalar_one_or_none()
    pending = (
        await db.execute(
            select(func.count())
            .select_from(GroupJoinRequest)
            .where(
                GroupJoinRequest.group_id == group.id,
                GroupJoinRequest.user_id == viewer.id,
                GroupJoinRequest.status == JoinRequestStatus.PENDING.value,
            )
        )
    ).scalar_one()
    return templates.TemplateResponse(
        "group/invite.html",
        {
            "title": "دعوت به گروه",
            "request": request,
            "current_user_id": viewer.id,
            "active_nav": None,
            "group": group,
            "code": code,
            "state": state,
            "requires_approval": bool(invite.requires_approval),
            # "Usable" means «پیوستن با همین لینک», so a link that only lets
            # somebody *ask* is not usable in that sense even while it is
            # perfectly alive -- the page draws one button or the other from
            # this, rather than offering «پیوستن» and discovering the
            # refusal on tap.
            "is_usable": state == INVITE_ACTIVE and not invite.requires_approval,
            "member_count": counts.get(group.id, 0),
            "already_member": membership is not None,
            "request_pending": bool(pending),
        },
    )
