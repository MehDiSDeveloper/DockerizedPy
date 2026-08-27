from datetime import UTC, datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_user_id, get_optional_user_id
from app.config import BASE_DIR
from app.database import get_db
from app.models.user import User
from app.routers.today import get_today_items

router = APIRouter(prefix="/views/today", tags=["today-views"])
templates = Jinja2Templates(directory=BASE_DIR / "templates")

DEFAULT_PAGE_SIZE = 20
MAX_PAGE_SIZE = 100


def _local_time(dt: datetime, tz: str) -> str:
    """Wall-clock HH:MM in the enrollment's own zone.

    Only a pre-JS fallback: app.js re-renders the same instant with Persian
    digits over the top. It still has to be the *right* time, though -- printing
    the raw UTC clock here would flash a wrong hour on every scheduled card.
    """
    try:
        return dt.astimezone(ZoneInfo(tz)).strftime("%H:%M")
    except (ZoneInfoNotFoundError, ValueError):
        return dt.astimezone(UTC).strftime("%H:%M")


templates.env.filters["local_time"] = _local_time


def _redirect_to_login() -> RedirectResponse:
    return RedirectResponse(url="/views/auth/?next=/views/today/", status_code=303)


@router.get("/")
async def today_page(
    request: Request,
    current_user_id: int | None = Depends(get_optional_user_id),
    db: AsyncSession = Depends(get_db),
):
    if current_user_id is None:
        return _redirect_to_login()

    user_result = await db.execute(select(User).where(User.id == current_user_id))
    db_user = user_result.scalar_one_or_none()
    if db_user is None:
        return _redirect_to_login()

    items = await get_today_items(
        db, user_id=current_user_id, now_utc=datetime.now(UTC)
    )
    page = items[:DEFAULT_PAGE_SIZE]
    has_more = len(items) > DEFAULT_PAGE_SIZE

    return templates.TemplateResponse(
        "today/index.html",
        {
            "title": "امروز",
            "request": request,
            "user": db_user,
            "items": page,
            "total_count": len(items),
            "has_more": has_more,
            "page_size": DEFAULT_PAGE_SIZE,
            "current_user_id": current_user_id,
            "active_nav": "today",
        },
    )


@router.get("/fragment")
async def today_fragment(
    request: Request,
    current_user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE),
):
    """Returns just the next page of today's occurrence cards, for infinite scroll."""
    items = await get_today_items(
        db, user_id=current_user_id, now_utc=datetime.now(UTC)
    )
    page = items[offset : offset + limit]
    has_more = offset + limit < len(items)
    response = templates.TemplateResponse(
        "today/_today_cards.html", {"request": request, "items": page}
    )
    response.headers["X-Has-More"] = "true" if has_more else "false"
    return response
