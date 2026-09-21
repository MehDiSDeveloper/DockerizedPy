"""«تأییدها» -- the referee's queue, and the one screen the loop needs.

A page of its own rather than a panel on challenge-detail, for the reason
the notification feed is a page: the question it answers is *cross-act*.
Somebody refereeing four acts would otherwise have to open four screens to
find out whether anybody is waiting on them -- and a queue nobody can see in
one place is a queue that silently stops being worked.

**It is not a fifth bottom-nav tab.** The entrance is a row on «فضای من»,
rendered only when there is something in it, plus the notification each
report raises. A member who referees nothing pays one indexed count and sees
no pixels -- the trade «قدم فعلی تو» makes on the home dashboard.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_user_id, get_page_user
from app.avatars import register_avatar_filters
from app.config import BASE_DIR
from app.database import get_db
from app.date_filters import register_date_filters
from app.explainers import register_explainer_filters
from app.icons import register_icon_filters
from app.identity import participant_display
from app.media import media_url, register_media_filters
from app.models.user import User
from app.proof import register_proof_filters
from app.referees import pending_invites_for, register_referee_filters
from app.routers.verification import (
    DEFAULT_QUEUE_PAGE_SIZE,
    MAX_QUEUE_PAGE_SIZE,
)
from app.verification import fetch_review_queue, register_verification_filters

router = APIRouter(prefix="/views/verifications", tags=["verification-views"])
templates = Jinja2Templates(directory=BASE_DIR / "templates")
# Every views router owes the registrations for what it renders -- the same
# contract `register_icon_filters` has. This screen draws a date, an icon, an
# explainer, a picture, an outcome pill and a referee state, so it owes six.
register_date_filters(templates.env)
register_explainer_filters(templates.env)
register_icon_filters(templates.env)
register_avatar_filters(templates.env)
register_media_filters(templates.env)
register_proof_filters(templates.env)
register_referee_filters(templates.env)
register_verification_filters(templates.env)


def build_queue_rows(rows, viewer_id: int) -> list[dict]:
    """Shape one page of the queue for the template.

    The rows are *built here*, not handed to the template raw, for the reason
    `build_leaderboard_rows` is: the name and the picture of a participant go
    through `participant_display`, which needs the viewer, and a Jinja filter
    taking a second argument is a filter every surface has to remember to
    pass it to. One place resolves it; the template just prints.

    **Anonymity is honoured here too.** Refereeing is not an exemption from
    it -- an anonymous participant's report is ruled on by its contents, and
    the contents are what this row carries.
    """
    out = []
    for r in rows:
        who = participant_display(r.enrollment, viewer_id)
        out.append(
            {
                "id": r.id,
                "name": who["name"],
                "avatar": who["avatar"],
                "challenge_id": r.challenge_id,
                "challenge_title": r.challenge.title if r.challenge else "",
                "occurred_at_utc": r.occurred_at_utc,
                "timezone": r.timezone,
                "note": r.note,
                "amount": r.amount,
                "unit": r.unit,
                "proof_url": (
                    media_url(r.proof_asset.media_key) if r.proof_asset else None
                ),
            }
        )
    return out


@router.get("/")
async def verifications_page(
    request: Request,
    viewer: User = Depends(get_page_user),
    db: AsyncSession = Depends(get_db),
):
    """What is waiting on me, oldest first -- plus the invitations I owe an
    answer to.

    Two lists on one screen and only one of them pages, the rule
    `/views/groups/{id}` follows: two paged lists would fight over one URL.
    The invitations are a handful by nature (you are asked by name, one act
    at a time), so they are rendered whole, above the queue -- answering them
    is what *fills* the queue, so a screen that hid them would have an empty
    list and no way to explain why.
    """
    rows, has_more = await fetch_review_queue(
        db, referee_user_id=viewer.id, offset=0, limit=DEFAULT_QUEUE_PAGE_SIZE
    )
    invites = await pending_invites_for(db, viewer.id)
    return templates.TemplateResponse(
        "verification/index.html",
        {
            "title": "تأییدها",
            "request": request,
            "current_user_id": viewer.id,
            "active_nav": None,
            "rows": build_queue_rows(rows, viewer.id),
            "has_more": has_more,
            "page_size": DEFAULT_QUEUE_PAGE_SIZE,
            "invites": invites,
        },
    )


@router.get("/fragment")
async def verifications_fragment(
    request: Request,
    current_user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=DEFAULT_QUEUE_PAGE_SIZE, ge=1, le=MAX_QUEUE_PAGE_SIZE),
):
    """The next page of the queue.

    `get_current_user_id`, not the page dependency -- every `/fragment` in
    the app takes it, because `createInfiniteScroller()` reads a real 401 to
    redirect and would silently append a login page's markup to a 303.
    """
    rows, has_more = await fetch_review_queue(
        db, referee_user_id=current_user_id, offset=offset, limit=limit
    )
    response = templates.TemplateResponse(
        "verification/_queue_rows.html",
        {"request": request, "rows": build_queue_rows(rows, current_user_id)},
    )
    response.headers["X-Has-More"] = "true" if has_more else "false"
    return response
