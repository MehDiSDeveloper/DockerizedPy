# routers/views/home.py
from typing import Literal

from fastapi import APIRouter, Depends, Query, Request
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.auth import get_current_user_id, get_page_user
from app.config import BASE_DIR
from app.database import get_db
from app.icons import register_icon_filters
from app.models.challenge import Challenge
from app.models.enrollment import Enrollment, EnrollmentStatus
from app.models.user import User

router = APIRouter(prefix="/views/home", tags=["home-views"])
templates = Jinja2Templates(directory=BASE_DIR / "templates")
register_icon_filters(templates.env)

DEFAULT_PAGE_SIZE = 20
MAX_PAGE_SIZE = 100


async def _fetch_enrollment_page(
    db: AsyncSession,
    *,
    user_id: int,
    status: str,
    offset: int,
    limit: int,
) -> tuple[list[Enrollment], bool]:
    stmt = (
        select(Enrollment)
        .options(selectinload(Enrollment.challenge).selectinload(Challenge.enrollments))
        .where(Enrollment.user_id == user_id)
    )
    if status == "completed":
        stmt = stmt.where(Enrollment.status == EnrollmentStatus.COMPLETED)
    else:
        stmt = stmt.where(Enrollment.status != EnrollmentStatus.COMPLETED)
    stmt = stmt.order_by(Enrollment.id.desc()).offset(offset).limit(limit + 1)
    result = await db.execute(stmt)
    enrollments = list(result.scalars().all())
    has_more = len(enrollments) > limit
    return enrollments[:limit], has_more


@router.get("/")
async def home(
    request: Request,
    db_user: User = Depends(get_page_user),
    db: AsyncSession = Depends(get_db),
):
    current_user_id = db_user.id

    stat_active = (
        await db.execute(
            select(func.count())
            .select_from(Enrollment)
            .where(
                Enrollment.user_id == current_user_id,
                Enrollment.status != EnrollmentStatus.COMPLETED,
            )
        )
    ).scalar_one()
    stat_done = (
        await db.execute(
            select(func.count())
            .select_from(Enrollment)
            .where(
                Enrollment.user_id == current_user_id,
                Enrollment.status == EnrollmentStatus.COMPLETED,
            )
        )
    ).scalar_one()

    enrollments, has_more = await _fetch_enrollment_page(
        db, user_id=current_user_id, status="active", offset=0, limit=DEFAULT_PAGE_SIZE
    )

    return templates.TemplateResponse(
        "home/index.html",
        {
            "title": "خانه",
            "request": request,
            "user": db_user,
            "enrollments": enrollments,
            "stat_active": stat_active,
            "stat_done": stat_done,
            "has_more": has_more,
            "page_size": DEFAULT_PAGE_SIZE,
            "current_user_id": current_user_id,
            "active_nav": "home",
        },
    )


@router.get("/fragment")
async def home_fragment(
    request: Request,
    current_user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
    status: Literal["active", "completed"] = "active",
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE),
):
    """Returns just the next page of enrollment cards, for infinite scroll."""
    enrollments, has_more = await _fetch_enrollment_page(
        db, user_id=current_user_id, status=status, offset=offset, limit=limit
    )
    response = templates.TemplateResponse(
        "home/_enrollment_cards.html",
        {"request": request, "enrollments": enrollments},
    )
    response.headers["X-Has-More"] = "true" if has_more else "false"
    return response
