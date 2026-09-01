# routers/views/auth.py
from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates

from app.auth import get_optional_user_id
from app.avatars import AVATAR_IDS, register_avatar_filters
from app.config import BASE_DIR
from app.explainers import register_explainer_filters

router = APIRouter(prefix="/views/auth", tags=["auth-views"])
templates = Jinja2Templates(directory=BASE_DIR / "templates")
register_explainer_filters(templates.env)
register_avatar_filters(templates.env)


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
async def auth_page(
    request: Request,
    next: str = DEFAULT_NEXT,
    current_user_id: int | None = Depends(get_optional_user_id),
):
    target = _safe_next(next)
    # Already signed in: go straight where they were headed. Without this the
    # bottom nav's profile tab and any stale bookmark of this URL park a
    # logged-in user on a login form, and submitting it just re-issues the
    # cookie they already hold. ``get_optional_user_id`` only checks the
    # signature, which is enough here -- the destination page runs
    # ``get_page_user`` and bounces a cookie whose account is gone right back.
    if current_user_id is not None:
        return RedirectResponse(url=target, status_code=303)
    return templates.TemplateResponse(
        "user/auth.html",
        {"request": request, "next": target, "avatars": AVATAR_IDS},
    )
