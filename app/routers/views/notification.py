# routers/views/notification.py
from fastapi import APIRouter, Depends, Query, Request
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_user_id, get_page_user
from app.avatars import register_avatar_filters
from app.config import BASE_DIR
from app.database import get_db
from app.explainers import register_explainer_filters
from app.icons import register_icon_filters
from app.models.user import User
from app.notifications import register_notification_filters
from app.routers.notification import (
    DEFAULT_PAGE_SIZE,
    MAX_PAGE_SIZE,
    fetch_notification_page,
)

router = APIRouter(prefix="/views/notifications", tags=["notification-views"])
templates = Jinja2Templates(directory=BASE_DIR / "templates")
register_explainer_filters(templates.env)
# Every views router builds its own environment, so each one owes the
# registrations for what it renders: the actor's picture, the shell's icons,
# and the three renderers that turn a stored event into a sentence.
register_avatar_filters(templates.env)
register_icon_filters(templates.env)
register_notification_filters(templates.env)


@router.get("/")
async def notifications_page(
    request: Request,
    viewer: User = Depends(get_page_user),
    db: AsyncSession = Depends(get_db),
):
    """The feed -- one flat list of what happened, newest first.

    A page of its own rather than a dropdown off the bell, for the reason
    every other list in this app is a page: it is lazily paged, and a list
    inside a popover can be neither scrolled to its end nor linked to. It is
    also not a fifth bottom-nav tab -- the bell in the header is already on
    every screen, and the nav is the four things a member *does*.

    There is deliberately no filter and no «خوانده‌نشده» tab. Everything here
    is informational -- someone joined, a challenge changed state -- so
    nothing is ever kept unread as a task, and a control that partitions a
    list nobody needs partitioned is a control that has to be explained. The
    unread mark is a *visual* one on the row, cleared for the next visit by
    the `POST /notifications/read` the page fires once it has rendered (see
    that endpoint on why clearing is a mutation the client sends rather than
    a side effect of this GET).

    `get_page_user` and not `get_current_user_id`: this is a full page, so an
    expired session owes the visitor a 303 to login carrying `next`, not a
    401 they would see as a JSON error page (CLAUDE.md, the 303/401 split).
    """
    notifications, has_more = await fetch_notification_page(
        db, user_id=viewer.id, offset=0, limit=DEFAULT_PAGE_SIZE
    )
    return templates.TemplateResponse(
        "notification/index.html",
        {
            "title": "اعلان‌ها",
            "request": request,
            "current_user_id": viewer.id,
            "active_nav": None,
            "notifications": notifications,
            "has_more": has_more,
            "page_size": DEFAULT_PAGE_SIZE,
        },
    )


@router.get("/fragment")
async def notifications_fragment(
    request: Request,
    current_user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE),
):
    """The next page of rows, for infinite scroll.

    `get_current_user_id`, not the page dependency -- the rule every
    `/fragment` route in the app follows. `createInfiniteScroller()` reads a
    real **401** to send the browser to login; the 303 `get_page_user` raises
    would be followed silently by `fetch()` and the login page's markup
    appended as if it were notification rows.
    """
    notifications, has_more = await fetch_notification_page(
        db, user_id=current_user_id, offset=offset, limit=limit
    )
    response = templates.TemplateResponse(
        "notification/_notification_rows.html",
        {"request": request, "notifications": notifications},
    )
    response.headers["X-Has-More"] = "true" if has_more else "false"
    return response
