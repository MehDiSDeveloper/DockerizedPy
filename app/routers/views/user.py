# routers/views/user.py
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_page_user
from app.avatars import AVATAR_IDS, avatar_url, register_avatar_filters
from app.config import BASE_DIR
from app.database import get_db
from app.models.enrollment import Enrollment, EnrollmentStatus
from app.models.user import User
from app.routers.user import profile_visibility_filter

router = APIRouter(prefix="/views/users", tags=["user-views"])
templates = Jinja2Templates(directory=BASE_DIR / "templates")
register_avatar_filters(templates.env)


@router.get("/{user_id}")
async def user_detail(
    user_id: int,
    request: Request,
    viewer: User = Depends(get_page_user),
    db: AsyncSession = Depends(get_db),
):
    """Your own profile. Anyone else's is a 404.

    The rule is ``profile_visibility_filter`` in ``routers/user.py``, shared
    with the JSON ``GET /users/{id}`` so the page and the API it mirrors can
    never disagree -- see that docstring for why own-only is the right rule
    for today's app.

    404 rather than 403: the path id is a bare sequential integer, so a 403
    would tell a walker of ``1..n`` exactly which ids are real members, which
    is the entire prize. An id the viewer may not see is indistinguishable
    from one that does not exist. (Being *signed out* is still the 303 to
    login that ``get_page_user`` raises -- authentication and authorization
    fail differently on purpose.)

    ``is_own_profile`` is therefore always true here, but it stays: the
    template branches on it for the owner-only affordances, and it is the
    flag a looser rule would start flipping.
    """
    current_user_id = viewer.id
    result = await db.execute(
        select(User).where(
            User.id == user_id, profile_visibility_filter(current_user_id)
        )
    )
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
            # The whole catalogue, id + path, for the "change picture" sheet.
            # Built here rather than in the template so the picker never has
            # to know where avatar files live -- `avatar_url` stays the one
            # id-to-path function, same as the `| avatar_url` filter above.
            "avatar_options": [
                {"id": avatar_id, "url": avatar_url(avatar_id)}
                for avatar_id in AVATAR_IDS
            ],
            "default_avatar_url": avatar_url(None),
            "current_user_id": current_user_id,
            "active_nav": "profile",
        },
    )
