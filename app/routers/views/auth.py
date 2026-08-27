# routers/views/auth.py
from fastapi import APIRouter, Request
from fastapi.templating import Jinja2Templates

from app.config import BASE_DIR

router = APIRouter(prefix="/views/auth", tags=["auth-views"])
templates = Jinja2Templates(directory=BASE_DIR / "templates")


DEFAULT_NEXT = "/views/home/"


def _safe_next(raw: str) -> str:
    """Clamp ``?next=`` to a same-site path.

    ``next`` goes straight into ``window.location.href`` in ``auth.html``, so
    anything schemeful (``https://evil.example``) or protocol-relative
    (``//evil.example``, ``/\\evil.example`` -- browsers normalise the latter)
    would send a freshly logged-in user off-site -- a post-auth open redirect,
    and a convincing phishing hop because the victim starts on our own domain.
    Only a plain same-site path survives; everything else falls back to the
    default. This has to be server-side: a check in the page's JS is part of
    the payload the attacker already controls the entry point to.
    """
    if not raw.startswith("/") or raw.startswith(("//", "/\\")):
        return DEFAULT_NEXT
    if "://" in raw or "\n" in raw or "\r" in raw:
        return DEFAULT_NEXT
    return raw


@router.get("/")
async def auth_page(request: Request, next: str = DEFAULT_NEXT):
    return templates.TemplateResponse(
        "user/auth.html", {"request": request, "next": _safe_next(next)}
    )
