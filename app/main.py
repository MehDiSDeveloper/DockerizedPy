from fastapi import FastAPI, Request
from fastapi.exception_handlers import http_exception_handler
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.config import BASE_DIR
from app.routers import auth, challenge, checkin, enrollment, today, user
from app.routers.views import auth as auth_views
from app.routers.views import challenge as challenge_views
from app.routers.views import home as home_views
from app.routers.views import today as today_views
from app.routers.views import user as user_views

app = FastAPI(title="Challenge Manager API")

templates = Jinja2Templates(directory=BASE_DIR / "templates")


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
app.include_router(challenge.router)
app.include_router(user.router)
app.include_router(enrollment.router)
app.include_router(checkin.router)
app.include_router(today.router)

app.include_router(auth_views.router)
app.include_router(challenge_views.router)
app.include_router(home_views.router)
app.include_router(today_views.router)
app.include_router(user_views.router)
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
# Uncommenting the line below also requires re-adding `import debugpy` above.
# debugpy.listen(("0.0.0.0", 5678))
