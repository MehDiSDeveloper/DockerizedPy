from urllib.parse import quote

from fastapi import FastAPI, Request
from fastapi.exception_handlers import http_exception_handler
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.auth import LoginRequired, clear_session_cookie
from app.config import BASE_DIR
from app.explainers import register_explainer_filters
from app.routers import (
    auth,
    challenge,
    checkin,
    enrollment,
    group,
    notification,
    otp,
    today,
    user,
)
from app.routers.views import admin as admin_views
from app.routers.views import auth as auth_views
from app.routers.views import challenge as challenge_views
from app.routers.views import group as group_views
from app.routers.views import home as home_views
from app.routers.views import notification as notification_views
from app.routers.views import settings as settings_views
from app.routers.views import today as today_views
from app.routers.views import user as user_views

app = FastAPI(title="Challenge Manager API")

templates = Jinja2Templates(directory=BASE_DIR / "templates")
register_explainer_filters(templates.env)


class RevalidatedStaticFiles(StaticFiles):
    """Force browsers to revalidate static assets instead of serving a
    stale copy from disk cache.

    There is no cache-busting hash in the asset URLs, so without this a
    browser can hold on to an old ``styles.css``/``app.js`` after an edit
    and render the new markup against the old stylesheet -- which looks
    exactly like a broken template rather than a caching problem. The
    ETag/Last-Modified handling StaticFiles already does makes each
    revalidation a cheap 304, so this costs a conditional request, not a
    re-download.
    """

    def file_response(self, *args, **kwargs):
        response = super().file_response(*args, **kwargs)
        response.headers["Cache-Control"] = "no-cache"
        return response


@app.exception_handler(LoginRequired)
async def login_required_page(request: Request, exc: LoginRequired):
    """Send an anonymous visitor to the login page, then back where they were.

    ``next`` carries the full path *and* query so a deep link survives the
    round trip -- landing back on a bare ``/views/home/`` after logging in
    would silently drop whatever filter or challenge they were opening.
    ``views/auth.py`` re-clamps it to a same-site path on the way out, so a
    crafted URL cannot turn this into an open redirect.

    The cookie is cleared on the way out: reaching here with one at all means
    it was expired, forged, or signed for an account that no longer exists, so
    leaving it in place would only re-fail the same way on the next page.
    """
    target = request.url.path
    if request.url.query:
        target = f"{target}?{request.url.query}"
    response = RedirectResponse(
        url=f"/views/auth/?next={quote(target, safe='')}", status_code=303
    )
    clear_session_cookie(response)
    return response


@app.exception_handler(StarletteHTTPException)
async def http_exception_page(request: Request, exc: StarletteHTTPException):
    """Render SSR errors as HTML instead of the API's JSON ``{"detail": ...}``.

    The view routers raise the same ``HTTPException`` as the JSON API, so
    without this a bad ``/views/...`` URL (or an unknown one) dumps raw JSON
    into the browser. Everything outside ``/views/`` keeps the JSON shape.
    """
    if request.url.path.startswith("/views/"):
        # exc.detail is English and internal -- show a Farsi message instead.
        message = (
            "چالش یا کاربر مورد نظر پیدا نشد."
            if exc.status_code == 404
            else "مشکلی پیش اومد."
        )
        return templates.TemplateResponse(
            "common/error.html",
            {"request": request, "title": "خطا", "error": message},
            status_code=exc.status_code,
            headers=getattr(exc, "headers", None),
        )
    return await http_exception_handler(request, exc)


@app.get("/")
async def root():
    return RedirectResponse(url="/views/today/", status_code=307)


app.include_router(auth.router)
app.include_router(otp.router)
app.include_router(challenge.router)
app.include_router(user.router)
app.include_router(enrollment.router)
app.include_router(group.router)
# The invite landing is its own prefix: whoever holds a code must not need
# the group's id, which is the whole reason a code exists.
app.include_router(group.invite_router)
app.include_router(checkin.router)
app.include_router(notification.router)
app.include_router(today.router)

app.include_router(admin_views.router)
app.include_router(auth_views.router)
app.include_router(challenge_views.router)
app.include_router(group_views.router)
app.include_router(group_views.invite_router)
app.include_router(home_views.router)
app.include_router(notification_views.router)
app.include_router(settings_views.router)
app.include_router(today_views.router)
app.include_router(user_views.router)
app.mount("/static", RevalidatedStaticFiles(directory=BASE_DIR / "static"), name="static")
# Uncommenting the line below also requires re-adding `import debugpy` above.
# debugpy.listen(("0.0.0.0", 5678))
