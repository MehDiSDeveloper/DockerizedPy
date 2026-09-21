# routers/views/user.py
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, not_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_page_user
from app.avatars import AVATAR_IDS, avatar_url, register_avatar_filters
from app.commitment import commitment_score
from app.config import BASE_DIR
from app.database import get_db
from app.date_filters import register_date_filters
from app.explainers import register_explainer_filters
from app.models.challenge import Challenge
from app.models.enrollment import Enrollment
from app.models.user import User
from app.permissions import Perm, can, is_admin, register_role_filters
from app.phone import national_mobile
from app.routers.challenge import STATUS_FINISHED, status_filter
from app.routers.user import profile_visibility_filter

# The Farsi for `UserRole`, taken from the panel rather than restated here:
# the role pill on the roster and the role row on this page name the same
# two values, and two copies of that map is one rename away from disagreeing.
from app.routers.views.admin import ROLE_LABELS

router = APIRouter(prefix="/views/users", tags=["user-views"])
templates = Jinja2Templates(directory=BASE_DIR / "templates")
register_date_filters(templates.env)
register_explainer_filters(templates.env)
register_avatar_filters(templates.env)
register_role_filters(templates.env)


@router.get("/{user_id}")
async def user_detail(
    user_id: int,
    request: Request,
    viewer: User = Depends(get_page_user),
    db: AsyncSession = Depends(get_db),
):
    """Your own profile -- or, for an operator, any member's. Otherwise a 404.

    The rule is ``profile_visibility_filter`` in ``routers/user.py``, shared
    with the JSON ``GET /users/{id}`` so the page and the API it mirrors can
    never disagree. Widening reach for admins was an edit to *that function*
    and nothing here: this route composes the same clause it always did.

    404 rather than 403 for a member: the path id is a bare sequential
    integer, so a 403 would tell a walker of ``1..n`` exactly which ids are
    real members, which is the entire prize. An id the viewer may not see is
    indistinguishable from one that does not exist. (Being *signed out* is
    still the 303 to login that ``get_page_user`` raises -- authentication
    and authorization fail differently on purpose.)

    Three flags reach the template, and each answers a different question:

    * ``is_own_profile`` -- is this me? Gates the things that are only ever
      mine: logout, settings, the history link, the admin-panel row.
    * ``can_administer`` -- am I here as an operator? Adds the banner that
      says so and the role row, and is false on my own profile even though I
      hold the permission, because "administering myself" is not a thing this
      screen should offer (the server refuses self-demotion anyway).
    * ``can_edit`` -- may I change what this page shows? The union of the
      two, and the only one the editing affordances read, so the sheets and
      the ``PATCH /users/{id}`` behind them stay one code path rather than
      an owner copy and an operator copy.
    """
    current_user_id = viewer.id
    result = await db.execute(
        select(User).where(User.id == user_id, profile_visibility_filter(viewer))
    )
    db_user = result.scalar_one_or_none()

    if not db_user:
        raise HTTPException(status_code=404, detail="User not found")

    # "Done" is the challenge's own derived status (D-status), never
    # `Enrollments.status` -- no route ever writes anything but `active` onto
    # that column, so a branch keyed on it was always empty. See
    # `routers/views/home.py::_fetch_enrollment_page`.
    finished = status_filter(STATUS_FINISHED)
    stat_active = (
        await db.execute(
            select(func.count())
            .select_from(Enrollment)
            .join(Challenge, Enrollment.challenge_id == Challenge.id)
            .where(Enrollment.user_id == user_id, not_(finished))
        )
    ).scalar_one()
    stat_done = (
        await db.execute(
            select(func.count())
            .select_from(Enrollment)
            .join(Challenge, Enrollment.challenge_id == Challenge.id)
            .where(Enrollment.user_id == user_id, finished)
        )
    ).scalar_one()
    is_own_profile = current_user_id == user_id
    # Reaching this row already proved the *viewer* half of the permission;
    # this asks the editing half, which is granted together but checked
    # separately so the two stay independently revocable in `permissions.py`.
    can_administer = not is_own_profile and can(viewer, Perm.USER_EDIT_ANY)

    return templates.TemplateResponse(
        "user/profile.html",
        {
            "title": "پروفایل کاربر",
            "request": request,
            "user": db_user,
            "stat_active": stat_active,
            "stat_done": stat_done,
            # «نمرهٔ تعهد» -- derived live off `CheckIns`, never stored (see
            # `app/commitment.py`). Rendered on any profile this viewer can
            # already open, which is their own plus, for an operator, anybody
            # they are supporting: it is a fact about the account's record,
            # the same kind of thing the two counts above it are.
            "score": await commitment_score(db, user_id),
            "is_own_profile": is_own_profile,
            "can_administer": can_administer,
            "can_edit": is_own_profile or can_administer,
            "role_labels": ROLE_LABELS,
            # The verified number this account signs in with, in the form
            # Iranians read (`app.phone.national_mobile`) rather than the
            # canonical `+98…` it is stored as -- storage stays canonical and
            # presentation converts, the same split Jalali dates already make.
            # `None` for every account that signs in with a password, which is
            # why the row is conditional rather than rendering «ثبت نشده»:
            # this one is a credential, and there is no box to fill it in.
            "login_mobile": (
                national_mobile(db_user.mobile) if db_user.mobile else None
            ),
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
            # The admin row is the one thing in the menu below that depends
            # on who is asking, and it is a *rendering* decision only: the
            # route it points at gates itself (get_admin_page_user), so a
            # member who guesses the URL gets the same 404 either way. Read
            # off `viewer`, never off `db_user` -- on someone else's profile
            # (the day one becomes reachable) those differ, and the menu
            # belongs to the person looking, not the person looked at.
            "viewer_is_admin": is_admin(viewer),
        },
    )
