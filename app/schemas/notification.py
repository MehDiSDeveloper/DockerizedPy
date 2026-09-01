from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict

from app.models.notification import Notification, NotificationKind


class NotificationRead(BaseModel):
    """One notification, as the JSON API answers it.

    Deliberately **structured, not worded**. The row stores an event rather
    than a sentence (see `app/models/notification.py`), and this shape keeps
    that promise outward: a consumer gets the kind, who did it and what it
    was about, and words it for its own surface. The Farsi wording the SSR
    feed uses lives in `app/notifications.py` and reaches the template as a
    filter -- the same split as `category_icon`, and the same reason: one
    fact, one place it becomes text.

    `actor_name` and `challenge_title` are flattened off the relationships
    rather than nested reads, because that is all any renderer needs and a
    nested `UserPublicRead` here would be a second, unlooked-for door onto
    other members' rows -- exactly what `profile_visibility_filter` exists
    to keep shut.
    """

    model_config = ConfigDict(from_attributes=True)

    id: int
    kind: str
    challenge_id: int | None = None
    challenge_title: str | None = None
    group_id: int | None = None
    group_name: str | None = None
    actor_user_id: int | None = None
    actor_name: str | None = None
    read_at: datetime | None = None
    created_at: datetime

    @classmethod
    def of(cls, notification: Notification) -> NotificationRead:
        """Build from a row whose `actor`/`challenge`/`group` are loaded."""
        return cls(
            id=notification.id,
            kind=notification.kind,
            challenge_id=notification.challenge_id,
            challenge_title=(
                notification.challenge.title if notification.challenge else None
            ),
            group_id=notification.group_id,
            group_name=notification.group.name if notification.group else None,
            actor_user_id=notification.actor_user_id,
            actor_name=notification.actor.name if notification.actor else None,
            read_at=notification.read_at,
            created_at=notification.created_at,
        )


class NotificationPrefUpdate(BaseModel):
    """One switch, flipped. The whole body of `PUT /notifications/prefs`.

    One kind at a time rather than the whole map, because that is what the
    settings screen actually does -- a member taps one switch -- and a
    whole-map write would silently undo a change made on another device
    between the page loading and the tap. `kind` is the enum, so an unknown
    value is a 422 at the boundary instead of a mute nothing will ever read.
    """

    kind: NotificationKind
    enabled: bool


class NotificationPref(BaseModel):
    """One switch as the API answers it: the event, and whether it reaches me.

    Unworded, like `NotificationRead` and for the same reason -- the Farsi
    label and hint live in `app/notifications.py`, and the SSR settings page
    reads them there rather than through this shape.
    """

    kind: str
    enabled: bool


class UnreadCount(BaseModel):
    """What the bell asks for on every page load: one number.

    Its own tiny endpoint rather than a field on some larger read, because
    the caller is the app shell on *every* page and it must stay the
    cheapest request the app makes.
    """

    unread: int


class MarkedRead(BaseModel):
    """How many rows `POST /notifications/read` actually changed."""

    updated: int
