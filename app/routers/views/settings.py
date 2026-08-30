# routers/views/settings.py
from fastapi import APIRouter, Depends, Request
from fastapi.templating import Jinja2Templates

from app.auth import get_page_user
from app.config import BASE_DIR
from app.models.user import User
from app.permissions import is_admin

router = APIRouter(prefix="/views/settings", tags=["settings-views"])
templates = Jinja2Templates(directory=BASE_DIR / "templates")


@router.get("/")
async def settings_page(
    request: Request,
    viewer: User = Depends(get_page_user),
):
    """The one place every app-level preference lives.

    Preferences are *app* state, not *account* state: the theme is a
    localStorage value, notifications will be a device permission. Mixing
    them into the profile page -- which is a member's identity and their
    numbers -- meant the profile answered two unrelated questions at once,
    and left every future preference with no obvious home. This page is that
    home; the profile keeps only one row pointing here.

    Nothing on it is server state today, so it takes no ``db`` and reads
    nothing off the row -- but it is still gated by ``get_page_user`` rather
    than left open, because "my settings" is a personal surface and the
    moment one preference is persisted per-account the page would otherwise
    have to be gated retroactively. The dependency also gives the shell the
    signed-in id its bottom nav needs.

    Adding a setting is one ``.setting-row`` in the right ``<section>`` of
    ``settings/index.html`` -- see the comment at the top of that file for
    which group it belongs in and what a row owes the reader.
    """
    return templates.TemplateResponse(
        "settings/index.html",
        {
            "title": "تنظیمات",
            "request": request,
            "current_user_id": viewer.id,
            "active_nav": "profile",
            # The panel row is the one thing on this page that depends on
            # who is asking. It is a *rendering* decision only -- the route
            # it links to gates itself (get_admin_page_user), so a member
            # who guesses the URL gets the same 404 as an unknown path.
            "viewer_is_admin": is_admin(viewer),
        },
    )
