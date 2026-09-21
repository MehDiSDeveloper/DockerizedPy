"""The act's immutable log: one function that writes, and nothing that does not.

An act with a referee is an act people can disagree about. «من ثبت کردم» /
«من چیزی ندیدم» / «تو حذفش کردی» are all answerable, but only if something
kept the order of events -- and neither ``AuditBase`` nor the request log
does. ``AuditBase`` records *who touched a row last*, which the next touch
overwrites; the request log records that a request happened, and is on
stdout in a different system with its own retention.

So: :class:`~app.models.act.ActEvent`, and this module.

**Append-only is enforced by what does not exist.** There is one writer,
:func:`record`. There is no update, no delete, no route that reaches a row,
and the model is deliberately not an ``AuditBase`` subclass -- with no
``updated_at`` and no ``last_modifier_user_id``, an edit is not expressible
in the schema at all. That is a weaker guarantee than an append-only store
and a stronger one than a comment, and it is honest about which it is: a
Postgres role with UPDATE on the table can still change a row, and the answer
to *that* is a database grant, not application code.

**It stores the event, never the sentence** -- the rule
``app/notifications.py`` is built on. ``kind`` plus a small ``data`` object,
worded at render time by :data:`ACT_EVENT_META`, so re-wording an entry never
touches a row and a log written a year ago still reads in today's Farsi.

**It lands in the caller's transaction.** :func:`record` adds and never
commits, exactly as ``notify`` does: an approval that rolls back must not
leave a line saying it happened.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.act import ActEvent, ActEventKind


def record(
    db: AsyncSession,
    *,
    challenge_id: int,
    kind: ActEventKind,
    actor_user_id: int | None = None,
    subject_user_id: int | None = None,
    checkin_id: int | None = None,
    **data,
) -> ActEvent:
    """Append one line to an act's log.

    Synchronous and returns the (unflushed) row, because it neither reads nor
    decides anything -- unlike ``notify``, which has a mute list to consult.
    Keyword extras become ``data``; an empty one is stored as NULL rather
    than ``{}`` so "nothing to say" and "an empty object" are not two shapes
    a reader has to handle.
    """
    event = ActEvent(
        challenge_id=challenge_id,
        kind=kind.value,
        actor_user_id=actor_user_id,
        subject_user_id=subject_user_id,
        checkin_id=checkin_id,
        data=data or None,
    )
    db.add(event)
    return event


async def fetch_log(
    db: AsyncSession, *, challenge_id: int, limit: int = 50, offset: int = 0
) -> tuple[list[ActEvent], bool]:
    """One page of an act's log, **oldest first**, plus whether more remain.

    The one list in the app that does not read ``newest_first``, and for the
    reason the group's request queue and a comment thread do not: a log is
    read forwards, because the order events happened in *is* the thing it
    exists to preserve. Ordered by ``id`` and not ``created_at`` --
    ``server_default=func.now()`` is second-granular on SQLite, and a
    report and the approval that followed it can land in the same second.
    """
    stmt = (
        select(ActEvent)
        .where(ActEvent.challenge_id == challenge_id)
        .order_by(ActEvent.id.asc())
        .offset(offset)
        .limit(limit + 1)
        .options(
            selectinload(ActEvent.actor),
            selectinload(ActEvent.subject),
        )
    )
    rows = list((await db.execute(stmt)).scalars().all())
    return rows[:limit], len(rows) > limit


#: One entry per kind: the sentence, and the glyph. The single place a logged
#: event becomes Farsi -- ``{actor}`` and ``{subject}`` are the only
#: placeholders, and a kind that names neither simply does not use them.
ACT_EVENT_META: dict[str, dict[str, str]] = {
    ActEventKind.ACT_CREATED.value: {
        "text": "{actor} این تعهد را ساخت.",
        "icon": "sparkles",
    },
    ActEventKind.ACT_LIFECYCLE_CHANGED.value: {
        "text": "{actor} وضعیت تعهد را عوض کرد.",
        "icon": "seal",
    },
    ActEventKind.DOER_JOINED.value: {
        "text": "{actor} به این تعهد پیوست.",
        "icon": "userPlus",
    },
    ActEventKind.DOER_LEFT.value: {
        "text": "{actor} این تعهد را ترک کرد.",
        "icon": "logout",
    },
    ActEventKind.DOER_ASSIGNED.value: {
        "text": "{actor} «{subject}» را به این تعهد اضافه کرد.",
        "icon": "userPlus",
    },
    ActEventKind.REFEREE_INVITED.value: {
        "text": "{actor} «{subject}» را به ناظری دعوت کرد.",
        "icon": "shield",
    },
    ActEventKind.REFEREE_ACCEPTED.value: {
        "text": "{actor} ناظری این تعهد را پذیرفت.",
        "icon": "shieldCheck",
    },
    ActEventKind.REFEREE_DECLINED.value: {
        "text": "{actor} ناظری این تعهد را نپذیرفت.",
        "icon": "x",
    },
    ActEventKind.REFEREE_REMOVED.value: {
        "text": "{actor} «{subject}» را از ناظری برداشت.",
        "icon": "x",
    },
    ActEventKind.CHECKIN_REPORTED.value: {
        "text": "{actor} یک وعده را ثبت کرد.",
        "icon": "check",
    },
    ActEventKind.CHECKIN_AMENDED.value: {
        "text": "{actor} گزارش خود را اصلاح کرد.",
        "icon": "edit",
    },
    ActEventKind.CHECKIN_WITHDRAWN.value: {
        "text": "{actor} گزارش خود را پس گرفت.",
        "icon": "trash",
    },
    ActEventKind.CHECKIN_APPROVED.value: {
        "text": "{actor} گزارش «{subject}» را تأیید کرد.",
        "icon": "shieldCheck",
    },
    ActEventKind.CHECKIN_REJECTED.value: {
        "text": "{actor} گزارش «{subject}» را رد کرد.",
        "icon": "shieldX",
    },
}

#: What an actor with no row left is called. The same stand-in the
#: notification feed and the leaderboard use, spelled here for the reason
#: ``ANONYMOUS_NAME`` is spelled there: the three are deliberately
#: indistinguishable, and importing one would make them move together.
UNKNOWN_ACTOR = "یکی از اعضا"


def _name(user) -> str:
    return (user.name if user is not None else None) or UNKNOWN_ACTOR


def act_event_text(event: ActEvent) -> str:
    """The Farsi sentence for one logged event."""
    meta = ACT_EVENT_META.get(event.kind)
    if meta is None:
        return UNKNOWN_ACTOR
    return meta["text"].format(
        actor=_name(getattr(event, "actor", None)),
        subject=_name(getattr(event, "subject", None)),
    )


def act_event_icon(event: ActEvent) -> str:
    meta = ACT_EVENT_META.get(event.kind)
    return meta["icon"] if meta else "info"


def register_actlog_filters(env) -> None:
    """Expose the log's renderer to one Jinja environment."""
    env.filters["act_event_text"] = act_event_text
    env.filters["act_event_icon"] = act_event_icon
