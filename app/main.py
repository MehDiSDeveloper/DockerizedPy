import debugpy  # noqa: T100
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.config import BASE_DIR
from app.routers import challenge, enrollment, user
from app.routers.views import challenge as challenge_views
from app.routers.views import user as user_views

app = FastAPI(title="Challenge Manager API")

app.include_router(challenge.router)
app.include_router(user.router)
app.include_router(enrollment.router)

app.include_router(challenge_views.router)
app.include_router(user_views.router)
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
# debugpy.listen(("0.0.0.0", 5678))
