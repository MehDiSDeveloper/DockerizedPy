# routers/views/user.py
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_optional_user_id
from app.config import BASE_DIR
from app.database import get_db
from app.models.enrollment import Enrollment, EnrollmentStatus
from app.models.user import User

router = APIRouter(prefix="/views/users", tags=["user-views"])
templates = Jinja2Templates(directory=BASE_DIR / "templates")


@router.get("/{user_id}")
async def user_detail(
    user_id: int,
    request: Request,
    current_user_id: int | None = Depends(get_optional_user_id),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(User).where(User.id == user_id))
    db_user = result.scalar_one_or_none()

    if not db_user:
        raise HTTPException(status_code=404, detail="User not found")

    stat_active = (
        await db.execute(
            select(func.count())
            .select_from(Enrollment)
            .where(
                Enrollment.user_id == user_id,
                Enrollment.status != EnrollmentStatus.COMPLETED,
            )
        )
    ).scalar_one()
    stat_done = (
        await db.execute(
            select(func.count())
            .select_from(Enrollment)
            .where(
                Enrollment.user_id == user_id,
                Enrollment.status == EnrollmentStatus.COMPLETED,
            )
        )
    ).scalar_one()
    is_own_profile = current_user_id == user_id

    return templates.TemplateResponse(
        "user/profile.html",
        {
            "title": "پروفایل کاربر",
            "request": request,
            "user": db_user,
            "stat_active": stat_active,
            "stat_done": stat_done,
            "is_own_profile": is_own_profile,
            "current_user_id": current_user_id,
            "active_nav": "profile",
        },
    )
