"""The notification feed's JSON door, and the queries the SSR page reuses.

Ownership here is the app's usual rule applied to a table that is *entirely*
per-member: every statement in this module filters on the session user, and
there is no client-supplied recipient anywhere -- the same pattern
`routers/enrollment.py` follows, for the same reason. There is no visibility
filter to compose, because there is no notion of a notification someone else
may read: `Notification.user_id == current_user_id` **is** the whole access
rule, and it is written into every query rather than checked after the fact.

Three endpoints, and each has a caller -- a JSON endpoint no page can reach
is the gap CLAUDE.md names, so nothing speculative is exposed:

* `GET /notifications/` backs the API consumer's feed; the SSR page and its
  `/fragment` share `fetch_notification_page` with it.
* `GET /notifications/unread-count` backs the bell in every page's header.
* `GET`/`PUT `/notifications/prefs` back the «اعلان‌ها» section of the
  settings screen -- which kinds the member wants to be told about. The
  preference is enforced in `notify()`, not here; this is only its door.
* `POST /notifications/read` is what the feed page fires once it has
  rendered -- see the view router for why marking read is a mutation the
  client sends and not a side effect of the GET that shows the list.

There is deliberately **no** per-row `PATCH` and no `DELETE`. Nothing in the
app needs to mark one row read while leaving another unread (the feed is
informational, not a task list), and a member cannot delete history that was
written *for* them -- the read mark is the only state a notification has.
"""

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.auth import get_current_user, get_current_user_id
from app.database import get_db
from app.models.audit_base import newest_first
from app.models.notification import Notification
from app.models.user import User
from app.notifications import (
    muted_kinds,
    notification_settings,
    set_notification_enabled,
)
from app.schemas.notification import (
    MarkedRead,
    NotificationPref,
    NotificationPrefUpdate,
    NotificationRead,
    UnreadCount,
)

router = APIRouter(prefix="/notifications", tags=["notifications"])

DEFAULT_PAGE_SIZE = 20
MAX_PAGE_SIZE = 50

# All three relationships are read by the renderer -- the sentence can name
# the actor, the challenge and the group -- and the session is async, so a
# lazy load during template rendering surfaces as `MissingGreenlet`
# (CLAUDE.md). Loaded here, once, rather than at each call site that could
# forget.
ROW_OPTIONS = (
    selectinload(Notification.actor),
    selectinload(Notification.challenge),
    selectinload(Notification.group),
)


async def fetch_notification_page(
    db: AsyncSession,
    *,
    user_id: int,
    offset: int = 0,
    limit: int = DEFAULT_PAGE_SIZE,
) -> tuple[list[Notification], bool]:
    """One page of this member's feed, plus whether more remain.

    Query logic in the API router, presentation in ``views/`` -- the split
    ``fetch_challenge_page`` and ``fetch_member_page`` follow, so the page
    and the JSON endpoint can never disagree about what the feed contains.

    ``limit + 1`` is fetched and trimmed so "is there another page" costs no
    second COUNT, and the order is ``newest_first(Notification)`` like every
    other paginated list. The id tie-break earns its place here more than
    anywhere: a lifecycle change broadcasts to every participant at once, so
    a whole block of rows shares one ``created_at`` second by construction.
    """
    stmt = (
        select(Notification)
        .where(Notification.user_id == user_id)
        .order_by(*newest_first(Notification))
        .offset(offset)
        .limit(limit + 1)
        .options(*ROW_OPTIONS)
    )
    rows = list((await db.execute(stmt)).scalars().all())
    has_more = len(rows) > limit
    return rows[:limit], has_more


async def count_unread(db: AsyncSession, user_id: int) -> int:
    """How many of this member's notifications are still unread."""
    return (
        await db.execute(
            select(func.count())
            .select_from(Notification)
            .where(Notification.user_id == user_id, Notification.read_at.is_(None))
        )
    ).scalar_one()


async def mark_all_read(db: AsyncSession, user_id: int) -> int:
    """Stamp every unread row of this member's, and return how many changed.

    One UPDATE rather than a load-and-loop: the feed can be long, and the
    only thing that matters is that the set it touches is scoped by
    ``user_id`` in the statement itself. ``read_at.is_(None)`` is not just an
    optimisation -- re-reading a feed must not rewrite the timestamp that
    records when it was *first* seen.
    """
    result = await db.execute(
        update(Notification)
        .where(Notification.user_id == user_id, Notification.read_at.is_(None))
        .values(read_at=datetime.now(UTC))
    )
    return result.rowcount or 0


@router.get("/", response_model=list[NotificationRead])
async def list_notifications(
    current_user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE),
):
    """This member's feed, newest first. Never anyone else's."""
    rows, _ = await fetch_notification_page(
        db, user_id=current_user_id, offset=offset, limit=limit
    )
    return [NotificationRead.of(row) for row in rows]


@router.get("/unread-count", response_model=UnreadCount)
async def get_unread_count(
    current_user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    """The number behind the bell's dot. One row, one column, one index."""
    return UnreadCount(unread=await count_unread(db, current_user_id))


@router.post("/read", response_model=MarkedRead)
async def mark_notifications_read(
    current_user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    """Mark this member's whole feed read.

    A POST and not a side effect of the feed's GET, deliberately: a GET that
    changes state cannot be retried, prefetched, or cached, and "showing the
    list" and "clearing the badge" are two different things that happen to
    usually go together. The feed page fires this once it has rendered, so
    the rows the member is looking at keep their "new" mark for that view
    while the bell goes quiet.
    """
    updated = await mark_all_read(db, current_user_id)
    await db.commit()
    return MarkedRead(updated=updated)


@router.get("/prefs", response_model=list[NotificationPref])
async def list_notification_prefs(
    current_user: User = Depends(get_current_user),
):
    """Which kinds reach this member. Always the full set, never a subset.

    Every kind is answered, muted or not, because the caller is drawing one
    switch per kind: a response that listed only the mutes would leave the
    client to reconstruct "everything else is on" from a source of truth it
    does not have.
    """
    return [
        NotificationPref(kind=row["kind"], enabled=row["enabled"])
        for row in notification_settings(current_user)
    ]


@router.put("/prefs", response_model=list[NotificationPref])
async def update_notification_pref(
    payload: NotificationPrefUpdate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Flip one switch on the session user's own row.

    The recipient is the session user, never a body field -- the same rule
    every other statement in this module follows. The full set comes back so
    the caller repaints from the server's answer rather than from what it
    assumed it wrote.
    """
    before = muted_kinds(current_user)
    set_notification_enabled(current_user, payload.kind, payload.enabled)
    if muted_kinds(current_user) != before:
        current_user.updated_at = datetime.now(UTC)
        current_user.last_modifier_user_id = current_user.id
        await db.commit()
    return [
        NotificationPref(kind=row["kind"], enabled=row["enabled"])
        for row in notification_settings(current_user)
    ]
