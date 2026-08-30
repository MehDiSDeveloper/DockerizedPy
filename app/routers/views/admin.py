# routers/views/admin.py
from fastapi import APIRouter, Depends, Query, Request
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.auth import get_admin_page_user, get_admin_user
from app.avatars import register_avatar_filters
from app.config import BASE_DIR
from app.database import get_db
from app.icons import register_icon_filters
from app.models.challenge import Challenge, LifecycleStatus, Visibility
from app.models.enrollment import Enrollment
from app.models.user import User, UserRole
from app.routers.challenge import (
    ALL_CATEGORIES,
    DEFAULT_PAGE_SIZE,
    MAX_PAGE_SIZE,
    STATUS_ALL,
    STATUS_VALUES,
    challenge_status,
    fetch_challenge_page,
)
from app.routers.user import (
    DEFAULT_MEMBER_PAGE_SIZE,
    MAX_MEMBER_PAGE_SIZE,
    ROLE_ALL,
    ROLE_PATTERN,
    fetch_member_page,
)

# The Farsi label/icon for each derived status lives with the public list, so
# a status means the same word and the same glyph on both screens.
from app.routers.views.challenge import STATUS_META

router = APIRouter(prefix="/views/admin", tags=["admin-views"])
templates = Jinja2Templates(directory=BASE_DIR / "templates")
register_avatar_filters(templates.env)
# The challenge roster renders category icons and the derived status badge, so
# this environment needs the same filters the public list registers -- every
# views router builds its own `Jinja2Templates`, and a missing one is a
# template error at render time, not at import.
register_icon_filters(templates.env)
templates.env.filters["challenge_status"] = challenge_status
templates.env.globals["status_meta"] = STATUS_META

# Farsi labels for `UserRole`, inlined here rather than in a shared display
# map -- the same per-surface pattern the rest of the app follows for the
# English-coded enums (D7).
ROLE_LABELS = {
    UserRole.MEMBER.value: "عضو",
    UserRole.ADMIN.value: "مدیر",
}

# Farsi for the two English-coded state enums, inlined here for the same
# reason ROLE_LABELS is (D7). These are the only two fields moderation can
# write, and the sheet is built from these maps, so a new state is one entry.
LIFECYCLE_LABELS = {
    LifecycleStatus.DRAFT.value: "پیش‌نویس",
    LifecycleStatus.ACTIVE.value: "فعال",
    LifecycleStatus.ARCHIVED.value: "آرشیو",
}

VISIBILITY_LABELS = {
    Visibility.PUBLIC.value: "عمومی",
    Visibility.UNLISTED.value: "با لینک",
    Visibility.PRIVATE.value: "خصوصی",
}

# The status filter's buttons: «همه» first, then the three derived statuses in
# the order the public challenge list shows them, so one control means one
# thing across the app.
STATUS_FILTER_OPTIONS = [{"value": STATUS_ALL, "label": "همه", "icon": None}] + [
    {"value": code, "label": meta["label"], "icon": meta["icon"]}
    for code, meta in STATUS_META.items()
]

STATUS_QUERY_PATTERN = "^({})$".format("|".join(STATUS_VALUES))

# Loaded on every challenge row: the owner's name is the one thing a roster of
# other people's challenges must show, and the count of participants is what
# decides whether deleting is even offered.
CHALLENGE_ROW_OPTIONS = (
    selectinload(Challenge.owner),
    selectinload(Challenge.enrollments),
)


async def _challenge_page(db: AsyncSession, **kwargs):
    """One page of the moderation roster.

    Thin on purpose: the query itself is `fetch_challenge_page` in the API
    router, shared verbatim with the public list -- `scope_all=True` is the
    only difference, and it is the same "query logic in the API router,
    presentation in views/" split the member roster follows.
    """
    return await fetch_challenge_page(
        db,
        current_user_id=None,
        category=ALL_CATEGORIES,
        scope_all=True,
        options=CHALLENGE_ROW_OPTIONS,
        **kwargs,
    )


# The role filter's buttons, in the order they are shown. «همه» is first
# because it is the state the page opens in; the two real roles follow in
# the order of `UserRole`, so the segmented control reads the same way the
# enum does.
ROLE_FILTER_OPTIONS = [
    {"value": ROLE_ALL, "label": "همه"},
    {"value": UserRole.MEMBER.value, "label": ROLE_LABELS[UserRole.MEMBER.value]},
    {"value": UserRole.ADMIN.value, "label": ROLE_LABELS[UserRole.ADMIN.value]},
]


@router.get("/")
async def admin_page(
    request: Request,
    admin: User = Depends(get_admin_page_user),
    db: AsyncSession = Depends(get_db),
    q: str | None = Query(default=None, max_length=100),
    role: str = Query(default=ROLE_ALL, pattern=ROLE_PATTERN),
):
    """The member-administration panel -- deliberately a page of its own.

    What an admin does here has no home on any existing screen: the profile
    is own-only by design and the challenge list is about discovery, so
    bolting a roster onto either would mean an `is_admin` branch inside a
    member-facing template and a wider query on a route members share. One
    route, one dependency, one query is both the smaller attack surface and
    the smaller thing to reason about.

    It is reached from a row on `/views/settings/` and from a matching row on
    the member's own profile -- both rendered only for an admin. Not from the
    bottom nav: the nav is the four things every member does, and adding a
    fifth destination for a handful of accounts would cost every other member
    a tab.

    Search and the role filter are read off the query string and rendered
    into the page, so a filtered roster is a linkable URL and a reload keeps
    what the admin was looking at. Both go through `fetch_member_page` in
    `routers/user.py`, shared with the JSON `GET /users/` -- the same
    "query logic in the API router, presentation here" split the challenge
    list follows.

    `get_admin_page_user` answers 404 to a signed-in member -- see its
    docstring -- so nothing here has to guard again.
    """
    members, has_more = await fetch_member_page(
        db, q=q, role=role, offset=0, limit=DEFAULT_MEMBER_PAGE_SIZE
    )
    # The three counts are of the *whole* install, deliberately unaffected by
    # the filters below: they answer "how big is this thing", which must not
    # change because someone typed in the search box.
    total_members = (
        await db.execute(select(func.count()).select_from(User))
    ).scalar_one()
    total_challenges = (
        await db.execute(select(func.count()).select_from(Challenge))
    ).scalar_one()
    total_enrollments = (
        await db.execute(select(func.count()).select_from(Enrollment))
    ).scalar_one()

    return templates.TemplateResponse(
        "admin/index.html",
        {
            "title": "پنل مدیریت",
            "request": request,
            "current_user_id": admin.id,
            "active_nav": "profile",
            "members": members,
            "role_labels": ROLE_LABELS,
            "role_options": ROLE_FILTER_OPTIONS,
            "query": q or "",
            "active_role": role,
            "totals": {
                "members": total_members,
                "challenges": total_challenges,
                "enrollments": total_enrollments,
            },
            "has_more": has_more,
            "page_size": DEFAULT_MEMBER_PAGE_SIZE,
        },
    )


@router.get("/fragment")
async def admin_members_fragment(
    request: Request,
    _admin: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db),
    q: str | None = Query(default=None, max_length=100),
    role: str = Query(default=ROLE_ALL, pattern=ROLE_PATTERN),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=DEFAULT_MEMBER_PAGE_SIZE, ge=1, le=MAX_MEMBER_PAGE_SIZE),
):
    """The next page of member rows, for infinite scroll.

    `get_admin_user`, not `get_admin_page_user` -- the same rule every other
    `/fragment` route follows. A `fetch()` needs a real **401** when the
    session has expired, because that is the status
    `createInfiniteScroller()` reads to send the browser to login; the 303
    the page dependency raises would be followed silently and the login
    page's markup appended as if it were member rows. The member-vs-admin
    answer here is therefore a 403, which leaks nothing this caller did not
    already know: they had to be on the panel to reach this URL at all.
    """
    members, has_more = await fetch_member_page(
        db, q=q, role=role, offset=offset, limit=limit
    )
    response = templates.TemplateResponse(
        "admin/_member_rows.html",
        {"request": request, "members": members, "role_labels": ROLE_LABELS},
    )
    response.headers["X-Has-More"] = "true" if has_more else "false"
    return response


@router.get("/challenges")
async def admin_challenges_page(
    request: Request,
    admin: User = Depends(get_admin_page_user),
    db: AsyncSession = Depends(get_db),
    q: str | None = Query(default=None, max_length=100),
    status: str = Query(default=STATUS_ALL, pattern=STATUS_QUERY_PATTERN),
):
    """The challenge side of the panel -- the public list, with the roster's
    reach and a set of actions.

    It is the member list's sibling and is built the same way, deliberately:
    the same `.search-bar`, the same `.seg`, the same `createInfiniteScroller`,
    the same row opening a `createSheet` against the JSON API. What differs is
    only the two things that make it a *moderation* screen -- it sees every
    challenge, including the private ones no member could reach, and each row
    can be acted on.

    Its own page rather than a second tab on `/views/admin/`: two lists on one
    screen would mean two searches, two filters and two scrollers competing
    for one URL, and neither would be linkable.
    """
    challenges, has_more = await _challenge_page(
        db, q=q, status=status, offset=0, limit=DEFAULT_PAGE_SIZE
    )
    return templates.TemplateResponse(
        "admin/challenges.html",
        {
            "title": "مدیریت چالش‌ها",
            "request": request,
            "current_user_id": admin.id,
            "active_nav": "profile",
            "challenges": challenges,
            "query": q or "",
            "active_status": status,
            "status_options": STATUS_FILTER_OPTIONS,
            "lifecycle_labels": LIFECYCLE_LABELS,
            "visibility_labels": VISIBILITY_LABELS,
            "has_more": has_more,
            "page_size": DEFAULT_PAGE_SIZE,
        },
    )


@router.get("/challenges/fragment")
async def admin_challenges_fragment(
    request: Request,
    _admin: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db),
    q: str | None = Query(default=None, max_length=100),
    status: str = Query(default=STATUS_ALL, pattern=STATUS_QUERY_PATTERN),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE),
):
    """The next page of challenge rows. `get_admin_user` for the same reason
    the member fragment takes it: a `fetch()` needs a real 401 to redirect on,
    and the page dependency's 303 would be appended as if it were rows."""
    challenges, has_more = await _challenge_page(
        db, q=q, status=status, offset=offset, limit=limit
    )
    response = templates.TemplateResponse(
        "admin/_challenge_rows.html",
        {
            "request": request,
            "challenges": challenges,
            "lifecycle_labels": LIFECYCLE_LABELS,
            "visibility_labels": VISIBILITY_LABELS,
        },
    )
    response.headers["X-Has-More"] = "true" if has_more else "false"
    return response
