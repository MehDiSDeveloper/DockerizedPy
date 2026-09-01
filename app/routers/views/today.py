from datetime import UTC, datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, Depends, Query, Request
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_user_id, get_page_user
from app.config import BASE_DIR
from app.database import get_db
from app.explainers import register_explainer_filters
from app.icons import register_icon_filters
from app.models.user import User
from app.routers.challenge import DEFAULT_TIMEZONE
from app.routers.today import get_today_items

router = APIRouter(prefix="/views/today", tags=["today-views"])
templates = Jinja2Templates(directory=BASE_DIR / "templates")
register_explainer_filters(templates.env)

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
register_icon_filters(templates.env)


@router.get("/")
async def today_page(
    request: Request,
    db_user: User = Depends(get_page_user),
    db: AsyncSession = Depends(get_db),
):
    current_user_id = db_user.id
    now_utc = datetime.now(UTC)
    items = await get_today_items(db, user_id=current_user_id, now_utc=now_utc)
    page = items[:DEFAULT_PAGE_SIZE]
    has_more = len(items) > DEFAULT_PAGE_SIZE
    # The hero's date line. Each *card* still judges its own deadline in its
    # enrollment's zone; this is a challenge-level "what day is it" label, so
    # it takes the app default the same way due_date/created_at do.
    local_today = now_utc.astimezone(ZoneInfo(DEFAULT_TIMEZONE)).date().isoformat()

    return templates.TemplateResponse(
        "today/index.html",
        {
            "title": "امروز",
            "local_today": local_today,
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
