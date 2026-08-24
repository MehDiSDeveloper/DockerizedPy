# routers/views/user.py
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import BASE_DIR
from app.database import get_db
from app.models.challenge import Challenge
from app.models.enrollment import Enrollment
from app.models.user import User

router = APIRouter(prefix="/views/users", tags=["user-views"])
templates = Jinja2Templates(directory=BASE_DIR / "templates")


@router.get("/{user_id}")
async def user_detail(
    user_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),  # noqa: B008
):
    result = await db.execute(
        select(User)
        .options(selectinload(User.enrollments).selectinload(Enrollment.user))
        .where(User.id == user_id)
    )

    db_user = result.scalar_one_or_none()

    if not db_user:
        raise HTTPException(status_code=404, detail="User not found")

    is_enrolled = any(db_user.enrollments)

    return templates.TemplateResponse(
        "user/profile.html",
        {"request": request, "user": db_user, "is_enrolled": is_enrolled},
    )
