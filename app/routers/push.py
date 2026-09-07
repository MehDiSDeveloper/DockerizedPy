"""The door a device uses to say "wake me", and to take it back.

Three routes and no page: the notification settings screen drives all of
them from JavaScript, because everything they do is a fact only the browser
holds -- whether it can subscribe at all, whether the member granted
permission, and what endpoint its push service minted.

Ownership follows this app's usual rule and there is no client-supplied
recipient anywhere: a subscription belongs to the session user, and the
delete is scoped to it. What is *not* a secret is the VAPID public key --
it is handed to every browser at subscribe time by definition -- so
``GET /push/key`` is open, like the manifest.

There is deliberately no route to list a member's devices and none to push
on demand. A roster of devices answers no question the member is asking
(the switch is about *this* one, and it reads its own state from the
browser), and a "send me a test" endpoint is a way to make this server POST
to an arbitrary https URL on request.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Response, status
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import Request

from app.auth import get_current_user_id
from app.config import settings
from app.database import get_db
from app.logging_config import log_event
from app.push import delete_subscription, save_subscription
from app.schemas.push import PushKeyRead, PushSubscriptionCreate, PushSubscriptionDelete

router = APIRouter(prefix="/push", tags=["push"])
logger = logging.getLogger("app.push")

#: Kept only so the settings screen can name the device a row belongs to.
#: Truncated rather than parsed -- see the model.
MAX_USER_AGENT = 255


@router.get("/key", response_model=PushKeyRead)
async def push_key() -> PushKeyRead:
    """The application server key, or an honest "this deploy cannot push".

    Answered before permission is ever asked for: a browser needs the key to
    call ``subscribe()``, and a page that asked for notification permission
    and *then* discovered there was nowhere to send them would have spent the
    one prompt a member ever gives it.
    """
    if not settings.push_enabled:
        return PushKeyRead(enabled=False)
    return PushKeyRead(enabled=True, public_key=settings.vapid_public_key)


@router.post("/subscriptions", status_code=status.HTTP_204_NO_CONTENT)
async def subscribe(
    payload: PushSubscriptionCreate,
    request: Request,
    current_user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
) -> Response:
    """Remember this device for this member.

    Idempotent by construction: the endpoint is the device's identity, so a
    browser that re-subscribes -- because permission was re-granted, or
    because it silently refreshed its own subscription -- updates the row it
    already has. The page is free to send this on every load without
    accumulating anything, which is what makes "did my subscription expire"
    a question nobody has to answer.
    """
    if not settings.push_enabled:
        # A deploy with no keys stores nothing: a row that could never be sent
        # to is a row that will look like a working switch until somebody
        # wonders why nothing arrives.
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    subscription = await save_subscription(
        db,
        user_id=current_user_id,
        endpoint=payload.endpoint,
        p256dh=payload.keys.p256dh,
        auth=payload.keys.auth,
        user_agent=(request.headers.get("user-agent") or "")[:MAX_USER_AGENT] or None,
    )
    log_event(logger, "push.subscribed", subscription_id=subscription.id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.delete("/subscriptions", status_code=status.HTTP_204_NO_CONTENT)
async def unsubscribe(
    payload: PushSubscriptionDelete,
    current_user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
) -> Response:
    """Forget this device. 204 whether a row went or not.

    Turning off something already off is not an error, and a device whose row
    has already been dropped -- by the sender, after its push service said it
    was gone -- is in exactly the state the caller is asking for. Answering
    404 there would make the switch report a failure for doing its job.
    """
    removed = await delete_subscription(
        db, user_id=current_user_id, endpoint=payload.endpoint
    )
    if removed:
        log_event(logger, "push.unsubscribed")
    return Response(status_code=status.HTTP_204_NO_CONTENT)
