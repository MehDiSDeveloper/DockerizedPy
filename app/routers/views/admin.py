# routers/views/admin.py
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.auth import get_admin_page_user, get_admin_user
from app.avatars import register_avatar_filters
from app.config import BASE_DIR
from app.database import get_db
from app.date_filters import register_date_filters
from app.explainers import register_explainer_filters
from app.icons import register_icon_filters
from app.models.challenge import Challenge, LifecycleStatus, Visibility
from app.models.enrollment import ChallengeRole, Enrollment, EnrollmentStatus
from app.models.user import User, UserRole
from app.permissions import register_role_filters
from app.routers.challenge import (
    ALL_CATEGORIES,
    DEFAULT_PAGE_SIZE,
    MAX_PAGE_SIZE,
    SORT_MEMBERS,
    SORT_RECENT,
    STATUS_ALL,
    STATUS_VALUES,
    challenge_status,
    fetch_challenge_page,
)
from app.routers.enrollment import fetch_participant_page
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
register_date_filters(templates.env)
register_explainer_filters(templates.env)
register_avatar_filters(templates.env)
register_role_filters(templates.env)
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
    LifecycleStatus.ARCHIVED.value: "بایگانی‌شده",
}

VISIBILITY_LABELS = {
    Visibility.PUBLIC.value: "عمومی",
    Visibility.UNLISTED.value: "فقط با لینک",
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

# How the moderation roster can be ordered. Two options, not three: the
# public list's «velocity» answers "what is hot", which is a discovery
# question and not a moderation one. «پرعضوترین» is the roster read as a
# breakdown of the install's memberships -- which is why the «عضویت» count
# on the panel index links straight here with this sort applied, instead of
# a second list of the same challenges in a different order living on its
# own screen.
SORT_LABELS = {
    SORT_RECENT: "تازه‌ترین",
    SORT_MEMBERS: "پرعضوترین",
}

SORT_FILTER_OPTIONS = [
    {"value": value, "label": label} for value, label in SORT_LABELS.items()
]

# The moderation roster only offers the two orderings above; «velocity»
# stays a discovery sort on the public list.
SORT_QUERY_PATTERN = "^({})$".format("|".join(SORT_LABELS))

# Farsi for the per-challenge role and the enrollment's own status, inlined
# here for the same reason ROLE_LABELS is (D7). The participant roster is
# the only screen that shows either.
CHALLENGE_ROLE_LABELS = {
    ChallengeRole.OWNER.value: "مالک",
    ChallengeRole.PARTICIPANT.value: "شرکت‌کننده",
}

ENROLLMENT_STATUS_LABELS = {
    EnrollmentStatus.ACTIVE.value: "فعال",
    EnrollmentStatus.COMPLETED.value: "تمام‌شده",
    EnrollmentStatus.ABANDONED.value: "رهاشده",
}

# Loaded on every challenge row: the owner's name is the one thing a roster of
# other people's challenges must show, and the count of participants is what
# decides whether deleting is even offered.
CHALLENGE_ROW_OPTIONS = (
    selectinload(Challenge.owner),
    selectinload(Challenge.enrollments),
    # The group, for the row's own hint. `scope_all=True` reaches group
    # challenges like any other, and an operator reading «عمومی» without
    # knowing it is a group challenge would read it as public to the world.
    selectinload(Challenge.group),
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
            # Paints the panel's own chrome in layout.html -- see the "Admin
            # scope" section of styles.css. Presentation only: the gate is
            # get_admin_page_user above.
            "admin_scope": True,
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
    sort: str = Query(default=SORT_RECENT, pattern=SORT_QUERY_PATTERN),
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
        db, q=q, status=status, sort=sort, offset=0, limit=DEFAULT_PAGE_SIZE
    )
    return templates.TemplateResponse(
        "admin/challenges.html",
        {
            # Paints the panel's own chrome in layout.html -- see the "Admin
            # scope" section of styles.css. Presentation only: the gate is
            # get_admin_page_user above.
            "admin_scope": True,
            "title": "مدیریت چالش‌ها",
            "request": request,
            "current_user_id": admin.id,
            "active_nav": "profile",
            "challenges": challenges,
            "query": q or "",
            "active_status": status,
            "status_options": STATUS_FILTER_OPTIONS,
            "active_sort": sort,
            "sort_options": SORT_FILTER_OPTIONS,
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
    sort: str = Query(default=SORT_RECENT, pattern=SORT_QUERY_PATTERN),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE),
):
    """The next page of challenge rows. `get_admin_user` for the same reason
    the member fragment takes it: a `fetch()` needs a real 401 to redirect on,
    and the page dependency's 303 would be appended as if it were rows."""
    challenges, has_more = await _challenge_page(
        db, q=q, status=status, sort=sort, offset=offset, limit=limit
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


async def _load_challenge(db: AsyncSession, challenge_id: int) -> Challenge:
    """The challenge a participant roster belongs to, or 404.

    No visibility clause: the caller has already been resolved to an admin,
    who holds `Perm.CHALLENGE_LIST_ALL` -- "may see every challenge" -- so
    narrowing here would only re-ask a question already answered. The 404 is
    a genuine miss, not the enumeration-proofing 404 the member-facing routes
    use.
    """
    challenge = (
        await db.execute(
            select(Challenge)
            .where(Challenge.id == challenge_id)
            .options(selectinload(Challenge.owner))
        )
    ).scalar_one_or_none()
    if challenge is None:
        raise HTTPException(status_code=404, detail="Challenge not found")
    return challenge


@router.get("/challenges/{challenge_id}/members")
async def admin_challenge_members_page(
    challenge_id: int,
    request: Request,
    admin: User = Depends(get_admin_page_user),
    db: AsyncSession = Depends(get_db),
    q: str | None = Query(default=None, max_length=100),
):
    """Who is enrolled in one challenge -- the drill-down from a roster row.

    This is the one screen in the app that answers "who is in this", and it
    exists here rather than on `challenge-detail.html` on purpose: profiles
    are own-only (`profile_visibility_filter`) and challenge-detail therefore
    shows participant *initials*, never a roster. Widening that for members
    means loosening that one function first; until then the roster is an
    operator tool, reached only from a page an admin had to be an admin to
    open, and it links to nobody's profile.

    It is a page of its own rather than a panel inside the moderation sheet
    for the reason every other list here is: it is searched and lazily paged,
    and a list behind a modal can be neither linkable nor scrolled to its
    end.
    """
    challenge = await _load_challenge(db, challenge_id)
    enrollments, has_more = await fetch_participant_page(
        db, challenge_id=challenge_id, q=q, offset=0, limit=DEFAULT_MEMBER_PAGE_SIZE
    )
    total = (
        await db.execute(
            select(func.count())
            .select_from(Enrollment)
            .where(Enrollment.challenge_id == challenge_id)
        )
    ).scalar_one()
    return templates.TemplateResponse(
        "admin/challenge_members.html",
        {
            # Paints the panel's own chrome in layout.html -- see the "Admin
            # scope" section of styles.css. Presentation only: the gate is
            # get_admin_page_user above.
            "admin_scope": True,
            "title": f"اعضای {challenge.title}",
            "request": request,
            "current_user_id": admin.id,
            "active_nav": "profile",
            "challenge": challenge,
            "enrollments": enrollments,
            "total": total,
            "query": q or "",
            "challenge_role_labels": CHALLENGE_ROLE_LABELS,
            "enrollment_status_labels": ENROLLMENT_STATUS_LABELS,
            "has_more": has_more,
            "page_size": DEFAULT_MEMBER_PAGE_SIZE,
        },
    )


@router.get("/challenges/{challenge_id}/members/fragment")
async def admin_challenge_members_fragment(
    challenge_id: int,
    request: Request,
    _admin: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db),
    q: str | None = Query(default=None, max_length=100),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=DEFAULT_MEMBER_PAGE_SIZE, ge=1, le=MAX_MEMBER_PAGE_SIZE),
):
    """The next page of participant rows. `get_admin_user` for the same
    reason every other `/fragment` route takes it: a `fetch()` needs a real
    401 to redirect on, and the page dependency's 303 would be followed
    silently and the login page appended as if it were rows."""
    enrollments, has_more = await fetch_participant_page(
        db, challenge_id=challenge_id, q=q, offset=offset, limit=limit
    )
    response = templates.TemplateResponse(
        "admin/_participant_rows.html",
        {
            "request": request,
            "enrollments": enrollments,
            "challenge_role_labels": CHALLENGE_ROLE_LABELS,
            "enrollment_status_labels": ENROLLMENT_STATUS_LABELS,
        },
    )
    response.headers["X-Has-More"] = "true" if has_more else "false"
    return response
