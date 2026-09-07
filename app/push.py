"""Waking a phone for a notification that was already stored.

This is the second half of `app/notifications.py`, and it is deliberately a
*consequence* of that module rather than a second way to reach a member. The
rules that decide whether somebody hears about an event -- you are never told
about your own action, a muted kind is dropped, an anonymous actor loses their
name -- are all in ``notify()``, and they are enforced once. What reaches this
module is a ``Notification`` row that already passed every one of them, so a
push can never say something the bell does not.

**Three things this module holds, and they are the whole design.**

*One: the sentence is derived here too.* A push carries a title and a body,
and both come from ``NOTIFICATION_META`` through the same
``notification_title``/``notification_text`` the feed renders with. Nothing
about the wording is stored on a row, and nothing about it is written twice --
rewording a kind moves the bell, the settings switch and the phone's lock
screen together.

*Two: nothing is sent before the transaction commits.* ``notify()`` adds a row
and does not commit; the caller commits once. A push sent from inside that
window would announce an enrolment that a later rollback un-did, and there is
no un-sending a notification on somebody's lock screen. So the row is only
*queued* here, and the queue is drained after the response has been produced
-- which is also what keeps a push service's latency out of the request the
member is waiting on.

The queue is a plain list in a ``ContextVar``, put there by
``RequestLogMiddleware`` before the request runs. That indirection is not
decoration: Starlette's ``BaseHTTPMiddleware`` runs the endpoint in a task of
its own, so a ``ContextVar`` *rebound* downstream is invisible upstream --
but a list the middleware created and the endpoint appended to is the same
object on both sides. Outside a request (a script, a test, a boot task) the
var is unset, ``queue()`` does nothing, and nothing anywhere has to branch on
"are we in a request".

Whether a queued row really committed is read from SQLAlchemy's own identity
map (``inspect(obj).identity``), never by touching an attribute: a rolled-back
pending object goes back to being transient and loses its identity, while a
committed one keeps it with no refresh and no I/O. That is the check, and it
is why a rollback silently sends nothing.

*Three: a dead subscription is deleted.* A push service answers 404 or 410 for
a browser that is gone -- permission revoked, site data cleared, app
uninstalled -- and that is the only garbage collection this table gets. There
is no sweep to schedule (this app has no job runner; see
``purge_expired_codes``) because every send is itself the sweep.

**Where it grows.** The dispatcher is one background task doing bounded-
concurrency HTTP; a broadcast to a few hundred members is a few hundred small
POSTs and finishes in a second or two. The day that is not enough, the change
is this module's ``deliver()`` writing to a queue instead of to the push
services, and nothing else in the app moves -- ``notify()`` does not know this
file exists, and this file does not know what raised the row.
"""

from __future__ import annotations

import asyncio
import json
import logging
from contextvars import ContextVar
from datetime import UTC, datetime

import httpx
from sqlalchemy import delete, inspect, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app import webpush
from app.config import settings
from app.database import AsyncSessionLocal
from app.logging_config import log_event
from app.models.notification import Notification
from app.models.push import PushSubscription

logger = logging.getLogger("app.push")

#: How many push services are talked to at once. High enough that a broadcast
#: is not serial, low enough that one archived challenge with four hundred
#: participants cannot open four hundred sockets at once on a small dyno.
MAX_CONCURRENT_SENDS = 16

#: A push service that does not answer in this long is treated as a transient
#: failure: the row is kept and the member reads the bell instead. Nothing
#: waits on this -- it runs after the response is out.
SEND_TIMEOUT = 10.0

#: The rows queued by this request, waiting for its transaction to end. The
#: list object is created by the middleware; see the module docstring for why
#: it is a mutable value rather than a var each caller rebinds.
_queued: ContextVar[list[Notification] | None] = ContextVar("push_queued", default=None)


# ---------------------------------------------------------------------------
# The queue: filled by notify(), drained by the middleware
# ---------------------------------------------------------------------------


def open_batch() -> list[Notification]:
    """Start collecting for this request. Called once, by the middleware."""
    batch: list[Notification] = []
    _queued.set(batch)
    return batch


def queue(notification: Notification) -> None:
    """Remember a raised notification so it can be pushed after the commit.

    Called from ``notifications._add`` -- the one door every notification
    already goes through -- so a new emitting site gets push for free and
    cannot forget it, exactly as it cannot forget the mute list.
    """
    batch = _queued.get()
    if batch is not None:
        batch.append(notification)


def committed_ids(batch: list[Notification]) -> list[int]:
    """The ids of the rows in ``batch`` that a commit actually persisted.

    Read from the identity map rather than from ``notification.id``: the
    object may be detached by now, and a pending row that a rollback threw
    away would either raise or -- worse -- answer with the id it briefly had.
    """
    ids = []
    for notification in batch:
        identity = inspect(notification).identity
        if identity:
            ids.append(identity[0])
    return ids


def dispatch(batch: list[Notification]) -> None:
    """Hand this request's committed notifications to a background send.

    Fire-and-forget on purpose: the member is waiting for a page, not for
    Google's push endpoint. A failure here is logged and costs a notification
    on a lock screen -- never the request that raised it.
    """
    if not settings.push_enabled or not batch:
        return
    ids = committed_ids(batch)
    if not ids:
        return
    task = asyncio.create_task(deliver(ids))
    # Without a reference the loop is free to garbage-collect a pending task
    # mid-flight, which shows up as pushes that arrive only sometimes.
    _in_flight.add(task)
    task.add_done_callback(_in_flight.discard)


#: Strong references to the tasks above, for the reason named there.
_in_flight: set[asyncio.Task] = set()


# ---------------------------------------------------------------------------
# The payload: the same words the bell shows
# ---------------------------------------------------------------------------


def target_url(notification: Notification) -> str:
    """Where tapping this notification should land.

    The most specific thing the row names, falling back to the feed -- which
    is always a real page, so the phone's notification never opens nothing.
    """
    if notification.challenge_id:
        return f"/views/challenges/{notification.challenge_id}"
    if notification.group_id:
        return f"/views/groups/{notification.group_id}"
    if notification.roadmap_id:
        return f"/views/roadmaps/{notification.roadmap_id}"
    return "/views/notifications/"


def build_payload(notification: Notification) -> bytes:
    """The bytes one device receives, worded by ``NOTIFICATION_META``.

    Imported inside the function because ``app.notifications`` imports this
    module for ``queue()``; the two halves of one feature genuinely do refer
    to each other, and the alternative is a third module holding one dict.
    """
    from app.notifications import notification_text, notification_title

    return json.dumps(
        {
            "title": notification_title(notification),
            "body": notification_text(notification),
            "url": target_url(notification),
            # The kind is the tag, so a second comment on the same challenge
            # replaces the first on the lock screen instead of stacking --
            # the feed is the place to read a list, a phone is not.
            "tag": f"{notification.kind}:{notification.challenge_id or 0}",
        },
        ensure_ascii=False,
    ).encode("utf-8")


# ---------------------------------------------------------------------------
# The send
# ---------------------------------------------------------------------------


async def deliver(notification_ids: list[int]) -> None:
    """Push every one of these, on a session of this task's own.

    A background task cannot borrow the request's session -- it is closed by
    the time this runs -- so it opens one, reads what it needs in two queries
    (the rows with their relationships, then every subscription belonging to
    the members they are for) and closes it before a single byte goes out.
    Nothing is held open across the network.
    """
    keys = webpush.VapidKeys.load(
        settings.vapid_private_key, settings.vapid_public_key, settings.vapid_subject
    )

    async with AsyncSessionLocal() as db:
        rows = (
            (
                await db.execute(
                    select(Notification)
                    .where(Notification.id.in_(notification_ids))
                    .options(
                        # The renderer walks all four; the session is async, so
                        # a lazy load here would surface as MissingGreenlet in
                        # a background task with nobody watching.
                        selectinload(Notification.actor),
                        selectinload(Notification.challenge),
                        selectinload(Notification.group),
                        selectinload(Notification.roadmap),
                    )
                )
            )
            .scalars()
            .all()
        )
        if not rows:
            return
        subscriptions = (
            (
                await db.execute(
                    select(PushSubscription).where(
                        PushSubscription.user_id.in_({row.user_id for row in rows})
                    )
                )
            )
            .scalars()
            .all()
        )

    by_user: dict[int, list[PushSubscription]] = {}
    for subscription in subscriptions:
        by_user.setdefault(subscription.user_id, []).append(subscription)
    if not by_user:
        return

    # One (payload, subscription) pair per device per notification: a member
    # reading on a phone and a laptop is told on both.
    jobs = [
        (subscription, build_payload(row))
        for row in rows
        for subscription in by_user.get(row.user_id, ())
    ]

    gone: set[int] = set()
    semaphore = asyncio.Semaphore(MAX_CONCURRENT_SENDS)

    async with httpx.AsyncClient(timeout=SEND_TIMEOUT) as client:

        async def one(subscription: PushSubscription, payload: bytes) -> None:
            async with semaphore:
                try:
                    await webpush.send(
                        client,
                        endpoint=subscription.endpoint,
                        p256dh=subscription.p256dh,
                        auth=subscription.auth,
                        payload=payload,
                        keys=keys,
                    )
                except webpush.WebPushError as exc:
                    if exc.gone:
                        gone.add(subscription.id)
                        return
                    # A refusal a member can cause (a full quota, a service
                    # having a bad minute) is a WARNING, never an ERROR -- the
                    # notification itself is safely in the feed either way.
                    log_event(
                        logger,
                        "push.send_failed",
                        level=logging.WARNING,
                        subscription_id=subscription.id,
                        status=exc.status,
                        reason=str(exc)[:200],
                    )

        await asyncio.gather(*(one(sub, payload) for sub, payload in jobs))

    if gone:
        await forget(gone)

    log_event(
        logger,
        "push.delivered",
        notifications=len(rows),
        devices=len(jobs),
        dropped=len(gone),
    )


async def forget(subscription_ids: set[int]) -> None:
    """Delete handles their push service says no longer exist.

    Its own session and its own transaction: this runs after the sends, when
    the read session is long closed, and a failure to clean up must not lose
    the sends that succeeded.
    """
    async with AsyncSessionLocal() as db:
        await db.execute(
            delete(PushSubscription).where(PushSubscription.id.in_(subscription_ids))
        )
        await db.commit()


# ---------------------------------------------------------------------------
# The table: one row per device, only ever written by that device
# ---------------------------------------------------------------------------


async def save_subscription(
    db: AsyncSession,
    *,
    user_id: int,
    endpoint: str,
    p256dh: str,
    auth: str,
    user_agent: str | None,
) -> PushSubscription:
    """Upsert on the endpoint, which is the device's identity.

    A browser hands back the same endpoint every time it re-subscribes, so
    the common case -- the same member re-enabling the switch, or a
    subscription the browser silently refreshed -- has to update rather than
    insert. The endpoint moving to a *different* account is a real case too
    (a shared phone), and it re-points the row instead of leaving the
    previous member's notifications going to somebody else's lock screen.
    """
    existing = (
        await db.execute(
            select(PushSubscription).where(PushSubscription.endpoint == endpoint)
        )
    ).scalar_one_or_none()

    if existing is not None:
        existing.user_id = user_id
        existing.p256dh = p256dh
        existing.auth = auth
        existing.user_agent = user_agent
        existing.updated_at = datetime.now(UTC)
        existing.last_modifier_user_id = user_id
        await db.commit()
        return existing

    subscription = PushSubscription(
        user_id=user_id,
        endpoint=endpoint,
        p256dh=p256dh,
        auth=auth,
        user_agent=user_agent,
        last_modifier_user_id=user_id,
    )
    db.add(subscription)
    try:
        await db.commit()
    except IntegrityError:
        # Two tabs subscribing at the same instant. The unique constraint is
        # the referee; the loser re-reads the winner's row and both callers
        # get the same success, exactly as a duplicate check-in does.
        await db.rollback()
        subscription = (
            await db.execute(
                select(PushSubscription).where(PushSubscription.endpoint == endpoint)
            )
        ).scalar_one()
    return subscription


async def delete_subscription(db: AsyncSession, *, user_id: int, endpoint: str) -> bool:
    """Forget one device. Scoped to the session user, like every other write.

    Returns whether a row went, so the route can answer honestly -- though
    both answers are a 204: turning off something already off is not an
    error, and a device whose row is already gone is exactly the state the
    caller asked for.
    """
    result = await db.execute(
        delete(PushSubscription).where(
            PushSubscription.user_id == user_id,
            PushSubscription.endpoint == endpoint,
        )
    )
    await db.commit()
    return bool(result.rowcount)
