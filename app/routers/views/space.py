# routers/views/space.py
"""«فضای من» -- the fifth bottom-nav destination.

Groups and roadmaps are *content*: things a member joins, builds and comes
back to. They used to be reachable only from the settings screen, next to
«تغییر رمز عبور», which is where preferences live -- so two whole subsystems
sat behind a door nobody opens twice. This is the door they get instead: one
destination with two segments, and the four existing screens (a group, a
course, and the two management pages) hang off it exactly as they did.

**One page, three URLs, one template.** ``/views/space/`` is the destination;
``/views/groups/`` and ``/views/roadmaps/`` are the same page opened on their
own segment, because they were already linked from the profile, from the empty
states and from the redirect after a group is deleted. Two separate listing
screens rendering the same rows is the parallel-source mistake the plan's
first principle names -- so the two index templates were merged into
``space/index.html`` and there is no second one to keep in step.

Both lists page through the *existing* fragment routes
(``/views/groups/fragment``, ``/views/roadmaps/fragment``), which still live in
the routers that own their queries -- presentation moved, the queries did not.
"""

from fastapi import APIRouter, Depends, Query, Request
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_page_user
from app.avatars import register_avatar_filters
from app.config import BASE_DIR
from app.database import get_db
from app.date_filters import register_date_filters
from app.explainers import register_explainer_filters
from app.groups import register_group_filters
from app.icons import register_icon_filters
from app.media import register_media_filters
from app.models.user import User
from app.roadmaps import register_roadmap_filters
from app.routers.group import (
    DEFAULT_GROUP_PAGE_SIZE,
    fetch_group_page,
    member_counts,
    parent_names,
)
from app.routers.roadmap import DEFAULT_ROADMAP_PAGE_SIZE
from app.routers.views.roadmap import list_context, resolve_scope

router = APIRouter(tags=["space-views"])

templates = Jinja2Templates(directory=BASE_DIR / "templates")
# This page renders both subsystems' cards, so it owes both their
# registrations on top of the shell's own (CLAUDE.md: every views router
# builds its own environment and a forgotten call is a render-time error).
register_date_filters(templates.env)
register_explainer_filters(templates.env)
register_icon_filters(templates.env)
register_media_filters(templates.env)
register_avatar_filters(templates.env)
register_group_filters(templates.env)
register_roadmap_filters(templates.env)

GROUPS_TAB = "groups"
ROADMAPS_TAB = "roadmaps"


async def _render(
    request: Request,
    viewer: User,
    db: AsyncSession,
    *,
    tab: str,
    scope: str | None,
    q: str | None,
):
    """Both lists' first page, plus which segment opens.

    The segment is a *rendering* decision and nothing more -- both panels are
    server-rendered either way, so switching is a class toggle rather than a
    refetch, the same trade «ریل»/«لیست» makes on the challenge list.
    """
    groups, groups_has_more = await fetch_group_page(
        db, user_id=viewer.id, offset=0, limit=DEFAULT_GROUP_PAGE_SIZE
    )
    active_scope = await resolve_scope(db, viewer.id, scope)
    roadmaps = await list_context(
        db,
        user_id=viewer.id,
        scope=active_scope,
        q=q,
        offset=0,
        limit=DEFAULT_ROADMAP_PAGE_SIZE,
    )
    return templates.TemplateResponse(
        "space/index.html",
        {
            "title": "فضای من",
            "request": request,
            "current_user_id": viewer.id,
            "active_nav": "space",
            "active_tab": tab,
            # Groups.
            "groups": groups,
            # Renamed away from `member_counts`: the two card partials both
            # ask for that key and they are rendered into one context here.
            "group_member_counts": await member_counts(db, [g.id for g in groups]),
            "parent_names": await parent_names(db, groups),
            "groups_has_more": groups_has_more,
            "group_page_size": DEFAULT_GROUP_PAGE_SIZE,
            # Roadmaps.
            "roadmaps": roadmaps["roadmaps"],
            "step_counts": roadmaps["step_counts"],
            "roadmap_member_counts": roadmaps["member_counts"],
            "walking_ids": roadmaps["walking_ids"],
            "roadmaps_has_more": roadmaps["has_more"],
            "roadmap_page_size": DEFAULT_ROADMAP_PAGE_SIZE,
            "active_scope": active_scope,
            "query": q or "",
        },
    )


@router.get("/views/space/")
async def space_page(
    request: Request,
    viewer: User = Depends(get_page_user),
    db: AsyncSession = Depends(get_db),
    tab: str | None = Query(default=None),
    scope: str | None = Query(default=None),
    q: str | None = Query(default=None, max_length=100),
):
    return await _render(
        request,
        viewer,
        db,
        tab=ROADMAPS_TAB if tab == ROADMAPS_TAB else GROUPS_TAB,
        scope=scope,
        q=q,
    )


@router.get("/views/groups/")
async def groups_page(
    request: Request,
    viewer: User = Depends(get_page_user),
    db: AsyncSession = Depends(get_db),
):
    """The old address of «گروه‌های من», now the same page opened on its
    segment -- every link that pointed here still lands on the groups list."""
    return await _render(request, viewer, db, tab=GROUPS_TAB, scope=None, q=None)


@router.get("/views/roadmaps/")
async def roadmaps_page(
    request: Request,
    viewer: User = Depends(get_page_user),
    db: AsyncSession = Depends(get_db),
    scope: str | None = Query(default=None),
    q: str | None = Query(default=None, max_length=100),
):
    """Likewise for «مسیرها»."""
    return await _render(request, viewer, db, tab=ROADMAPS_TAB, scope=scope, q=q)
