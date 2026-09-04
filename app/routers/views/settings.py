# routers/views/settings.py
from fastapi import APIRouter, Depends, Request
from fastapi.templating import Jinja2Templates

from app.auth import get_page_user
from app.config import BASE_DIR
from app.date_filters import register_date_filters
from app.explainers import register_explainer_filters
from app.models.user import User
from app.notifications import notification_settings
from app.permissions import is_admin

router = APIRouter(prefix="/views/settings", tags=["settings-views"])
templates = Jinja2Templates(directory=BASE_DIR / "templates")
register_date_filters(templates.env)
register_explainer_filters(templates.env)


@router.get("/")
async def settings_page(
    request: Request,
    viewer: User = Depends(get_page_user),
):
    """The one place every app-level preference lives.

    Preferences are *app* state, not *account* state: the theme is a
    localStorage value. Mixing them into the profile page -- which is a
    member's identity and their numbers -- meant the profile answered two
    unrelated questions at once, and left every future preference with no
    obvious home. This page is that home; the profile keeps only one row
    pointing here.

    The theme is still a device preference, but the notification switches
    are the first per-account one -- which is exactly the moment this page
    was already gated for. They live on :func:`notification_settings_page`
    rather than in a section here: this index is a list of *questions*, and
    one of them ("which events may reach me") has as many answers as there
    are kinds, so it grows a row for every kind added while every other
    question keeps one. The index shows how many are on, which is the
    current value a row owes its reader, and the sub-page holds the
    switches. Both read straight off ``viewer`` (the column lives on the row
    ``get_page_user`` has already loaded), so neither needs a ``db``.

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
            # Not the switches themselves -- just how many are on, so the
            # row states its current value without the reader opening it.
            "notification_prefs": notification_settings(viewer),
        },
    )


@router.get("/notifications")
async def notification_settings_page(
    request: Request,
    viewer: User = Depends(get_page_user),
):
    """The notification switches, on a screen of their own.

    They are a page rather than a section of the index for the reason the
    moderation roster is a page rather than a tab: this is the one setting
    whose row count is not fixed. Every kind added to ``NOTIFICATION_META``
    gets a switch for free, so as the set grows the section would have
    pushed «حساب کاربری» and «مدیریت» off the first screen -- and a settings
    index is scanned, not read. The index keeps one row saying how many are
    on; everything else about them is here.

    Same dependency, same absence of ``db``: the preference is a column on
    the row ``get_page_user`` already loaded.
    """
    return templates.TemplateResponse(
        "settings/notifications.html",
        {
            "title": "اعلان‌ها",
            "request": request,
            "current_user_id": viewer.id,
            "active_nav": "profile",
            # One entry per NotificationKind, wording included -- built in
            # `app/notifications.py` so a kind cannot ship without a switch,
            # and so the switch and the notification are worded by one map.
            "notification_prefs": notification_settings(viewer),
        },
    )
