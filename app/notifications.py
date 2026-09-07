"""The notification subsystem: what an event is called, and how one is raised.

Two halves, both small, and the whole point of the module is that they are
the *only* two:

1. :data:`NOTIFICATION_META` turns a :class:`~app.models.notification.
   NotificationKind` into the words and the glyph a member reads. Nothing is
   stored as text (see the model's docstring), so this map is the single
   place a notification is worded -- rewording one, or translating the set,
   never touches a row.
2. :func:`notify` / :func:`notify_many` are the only way a notification is
   created. They enforce the four invariants that a call site would
   otherwise have to remember, and would eventually forget.

**Invariant one: you are never told about your own action.** ``notify``
silently drops a notification whose recipient is its actor. Every emitting
call site has a case where the two coincide -- the creator's auto-enrolment
in their own challenge, an owner archiving it, a moderator acting on
something they happen to be enrolled in -- and a feed that reports your own
taps back to you is noise that trains people to ignore the bell.

**Invariant two: a notification lands in the same transaction as its
event.** These functions ``db.add()`` and never ``commit()``. The caller
commits once, so a rolled-back enrolment cannot leave behind a notification
saying it happened, and a successful one cannot fail to announce itself.

**Invariant three: a member who switched a kind off is not sent it.** The
preference is a mute list on the recipient (``User.muted_notification_kinds``)
and it is read *here*, in the same place the actor rule is, rather than at
each emitting site: a setting that only the call sites that remembered it
honour is a setting that silently stops working the first time a new event
is added. It is also why these two functions are ``async`` -- the check
costs one read of the recipients, and ``notify_many`` makes it one read for
the whole broadcast.

**Invariant four: an anonymous actor is not named.** ``anonymous_actor=True``
stores the row with no ``actor_user_id`` at all, so the sentence falls back
to :data:`UNKNOWN_ACTOR` -- the same stand-in a deleted account gets. The
name is dropped *here*, at the write, rather than hidden at render time, for
two reasons: ``ENROLLMENT_LEFT`` outlives the enrollment row that carried
the member's choice, so there would be nothing left to ask; and a feed that
stores the name and merely declines to print it is one template away from
printing it. The drop rule above still runs against the *real* actor, so an
owner who re-joins their own challenge is not told about themselves by a
notification that has since forgotten who they were.

**Invariant five: a member who allowed it is also woken on their device.**
The row is handed to ``app/push.py`` from the same private helper, *after*
the four rules above have run -- so a push can never say something the bell
does not, and a new emitting site gets push without knowing push exists.
Nothing is sent from here: the row is only queued, and the queue is drained
once the caller's transaction has committed.

This module is deliberately not a router, a template, or a model: the same
shape as ``app/icons.py`` and ``app/avatars.py`` -- domain logic that both
front doors read, registered onto a views router's Jinja environment with
:func:`register_notification_filters`.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import push
from app.models.challenge import Visibility
from app.models.notification import Notification, NotificationKind
from app.models.user import User


def membership_is_announced(visibility: str | None) -> bool:
    """Whether joining/leaving *this* challenge is worth telling its owner.

    Only for a challenge that is not public. A public challenge is a
    noticeboard -- anyone who finds it may join, and its owner did not invite
    any of them -- so each arrival is a number on the roster rather than an
    event, and a feed of them buries the notifications that *are* about the
    owner's own doing. A private or unlisted one is the opposite: reaching it
    at all took a link the owner handed out, so who walked through the door
    is exactly what they are waiting to hear.

    Every half asks the same question through this one function, because a
    rule honoured on the way in and forgotten on the way out gives an owner a
    feed that only ever announces departures. There are three halves now, not
    two: joining, leaving one challenge, and leaving them all at once
    (`app/groups.py`), which is why the rule lives here rather than in the
    enrollment router that used to be its only caller.
    """
    return visibility != Visibility.PUBLIC.value

# One entry per kind. `title` is the two-or-three-word label the row is
# scanned by; `text` is the sentence, formatted with the actor's name and the
# challenge's title. `icon` is a name from the set in `app/static/js/app.js`,
# chosen server-side for the same reason `category_icon` is -- so the Farsi
# wording and its glyph are decided together, in one file, instead of the
# template picking one and a JS map picking the other.
#
# `hint` is the one line the settings screen prints under the switch for this
# kind, saying *when* it fires. It lives in this map rather than in the
# template for the same reason the sentence does: a kind is worded once, in
# one file, so a reworded notification and its switch cannot describe two
# different things -- and adding a kind cannot leave a switch unlabelled.
#
# Two of the hints say «خصوصی» on purpose: the pair of membership kinds is
# raised only for a challenge that is not public (see
# `membership_is_announced` below), and a switch
# that promises more than the event delivers reads as a broken switch.
#
# The placeholders are `{actor}`, `{challenge}` and `{group}`, and all three
# are optional per kind: a kind that names none simply does not use them, and
# a kind that names one whose row is missing gets the fallback below rather
# than a KeyError in a template.
NOTIFICATION_META: dict[str, dict[str, str]] = {
    NotificationKind.ENROLLMENT_JOINED.value: {
        "title": "هم‌چالشی تازه",
        "text": "{actor} به چالش «{challenge}» پیوست.",
        "icon": "users",
        "hint": "وقتی کسی به یکی از چالش‌های خصوصی تو می‌پیوندد.",
    },
    NotificationKind.ENROLLMENT_LEFT.value: {
        "title": "خروج از چالش",
        "text": "{actor} چالش «{challenge}» را ترک کرد.",
        "icon": "logout",
        "hint": "وقتی کسی یکی از چالش‌های خصوصی تو را ترک می‌کند.",
    },
    NotificationKind.CHALLENGE_ARCHIVED.value: {
        "title": "چالش بایگانی شد",
        "text": "چالش «{challenge}» بایگانی شد و وعدهٔ تازه‌ای نخواهد داشت.",
        "icon": "seal",
        "hint": "وقتی چالشی که در آن هستی بایگانی می‌شود.",
    },
    NotificationKind.CHALLENGE_ACTIVATED.value: {
        "title": "چالش فعال شد",
        "text": "چالش «{challenge}» فعال شد؛ از حالا وعده‌هایش شمرده می‌شود.",
        "icon": "flame",
        "hint": "وقتی چالشی که در آن هستی دوباره فعال می‌شود.",
    },
    NotificationKind.CHALLENGE_COMMENTED.value: {
        "title": "نظر تازه",
        "text": "{actor} زیر چالش «{challenge}» نظری گذاشت.",
        "icon": "message",
        "hint": "وقتی کسی زیر یکی از چالش‌های تو نظر می‌گذارد.",
    },
    NotificationKind.COMMENT_REPLIED.value: {
        "title": "پاسخ به نظر تو",
        "text": "{actor} به نظر تو در چالش «{challenge}» پاسخ داد.",
        "icon": "reply",
        "hint": "وقتی کسی به نظر تو پاسخ می‌دهد.",
    },
    # ---- Groups ---------------------------------------------------------
    # Every one of these is something that happens *to* a member because
    # somebody else acted, on a screen they were not looking at. That is the
    # bar this feed is held to, and it is why joining a group by invite link
    # -- which the joiner does themselves, and sees the result of -- raises
    # nothing at all.
    NotificationKind.GROUP_JOIN_REQUESTED.value: {
        "title": "درخواست عضویت",
        "text": "{actor} خواست به گروه «{group}» بپیوندد.",
        "icon": "userPlus",
        "hint": "وقتی کسی برای گروهی که مدیرش هستی درخواست عضویت می‌فرستد.",
    },
    NotificationKind.GROUP_JOIN_APPROVED.value: {
        "title": "به گروه اضافه شدی",
        "text": "درخواستت برای عضویت در گروه «{group}» پذیرفته شد.",
        "icon": "check",
        "hint": "وقتی درخواست عضویتت در گروهی پذیرفته می‌شود.",
    },
    NotificationKind.GROUP_JOIN_REJECTED.value: {
        "title": "درخواست پذیرفته نشد",
        "text": "درخواستت برای عضویت در گروه «{group}» پذیرفته نشد.",
        "icon": "x",
        "hint": "وقتی درخواست عضویتت در گروهی رد می‌شود.",
    },
    NotificationKind.GROUP_MEMBER_ADDED.value: {
        "title": "به گروه اضافه شدی",
        "text": "{actor} تو را به گروه «{group}» اضافه کرد.",
        "icon": "userPlus",
        "hint": "وقتی مدیر گروهی تو را مستقیم به آن اضافه می‌کند.",
    },
    NotificationKind.GROUP_ROLE_CHANGED.value: {
        "title": "نقشت در گروه",
        "text": "نقش تو در گروه «{group}» تغییر کرد.",
        "icon": "shield",
        # Deliberately does not name the new role: the sentence is derived
        # from the row, and the row stores the event -- what the role *is*
        # now is on the group page, which is one tap away and always right.
        "hint": "وقتی مدیر گروه نقش تو را در آن گروه عوض می‌کند.",
    },
    NotificationKind.GROUP_OWNERSHIP_TRANSFERRED.value: {
        "title": "مالکیت گروه",
        "text": "مالکیت گروه «{group}» به تو منتقل شد.",
        "icon": "seal",
        "hint": "وقتی گروهی به تو واگذار می‌شود.",
    },
    NotificationKind.GROUP_CHALLENGE_ASSIGNED.value: {
        "title": "چالش تازهٔ گروه",
        "text": "چالش «{challenge}» در گروه «{group}» برای تو گذاشته شد؛ شرکت در آن اختیاری است.",
        "icon": "target",
        "hint": "وقتی در یک چالش اختیاری گروه ثبت می‌شوی.",
    },
    NotificationKind.GROUP_CHALLENGE_REQUIRED.value: {
        "title": "چالش اجباری گروه",
        "text": "چالش «{challenge}» در گروه «{group}» برای تو گذاشته شد و تا وقتی عضو گروهی نمی‌توانی از آن خارج شوی.",
        "icon": "lock",
        "hint": "وقتی در یک چالش اجباری گروه ثبت می‌شوی.",
    },
    # ---- Roadmaps -------------------------------------------------------
    # A step opening is the one event in this app that happens with nobody
    # touching anything: the member finished the step before it and the engine
    # opened the next. It is therefore the kind this feed exists for.
    NotificationKind.ROADMAP_JOINED.value: {
        "title": "هم‌مسیر تازه",
        "text": "{actor} مسیر «{roadmap}» را شروع کرد.",
        "icon": "route",
        "hint": "وقتی کسی یکی از مسیرهای تو را شروع می‌کند.",
    },
    NotificationKind.ROADMAP_STEP_UNLOCKED.value: {
        "title": "قدم تازه باز شد",
        "text": "قدم بعدی مسیر «{roadmap}» باز شد: چالش «{challenge}».",
        "icon": "unlock",
        "hint": "وقتی قدم بعدی یکی از مسیرهایت باز می‌شود.",
    },
    NotificationKind.ROADMAP_COMPLETED.value: {
        "title": "مسیر تمام شد",
        "text": "همهٔ قدم‌های مسیر «{roadmap}» را تمام کردی.",
        "icon": "trophy",
        "hint": "وقتی آخرین قدم یکی از مسیرهایت را تمام می‌کنی.",
    },
    NotificationKind.ROADMAP_STEP_SKIPPED.value: {
        "title": "قدمی از مسیرت رد شد",
        "text": "چالش «{challenge}» بایگانی شد، پس قدمش در مسیر «{roadmap}» رد شد تا مسیر متوقف نماند.",
        "icon": "seal",
        "hint": "وقتی چالشِ یکی از قدم‌های مسیر تو بایگانی می‌شود.",
    },
}

# What an unknown kind renders as. A row written by a newer version of the
# app -- or by a kind someone removed from the map -- must still render as
# *something*: a feed that raises on one bad row shows the member nothing at
# all, which is strictly worse than one vague line among many.
FALLBACK_META = {
    "title": "اعلان",
    "text": "رویداد تازه‌ای ثبت شد.",
    "icon": "info",
    "hint": "رویدادهای دیگر برنامه.",
}

# Stand-ins for a relationship that is absent (`actor_user_id` is nullable)
# or was not eagerly loaded. `UNKNOWN_ACTOR` is also, deliberately, what an
# *anonymous* participant reads as: the two cases are indistinguishable to
# the reader on purpose -- a wording reserved for anonymity would announce
# that someone chose it.
UNKNOWN_ACTOR = "یکی از اعضا"
UNKNOWN_CHALLENGE = "یک چالش"
UNKNOWN_GROUP = "یک گروه"
UNKNOWN_ROADMAP = "یک مسیر"


def notification_meta(notification: Notification) -> dict[str, str]:
    return NOTIFICATION_META.get(notification.kind, FALLBACK_META)


def notification_title(notification: Notification) -> str:
    return notification_meta(notification)["title"]


def notification_icon(notification: Notification) -> str:
    return notification_meta(notification)["icon"]


def notification_text(notification: Notification) -> str:
    """The sentence a member reads, built from the row's own relationships.

    ``actor`` and ``challenge`` must be eagerly loaded before this is
    reached -- the session is async, so a lazy load during template
    rendering surfaces as ``MissingGreenlet`` rather than as a query error
    (see CLAUDE.md). ``fetch_notification_page`` loads all three; a caller that
    builds its own query owes the same ``selectinload``.
    """
    actor = notification.actor.name if notification.actor else UNKNOWN_ACTOR
    challenge = (
        notification.challenge.title if notification.challenge else UNKNOWN_CHALLENGE
    )
    group = notification.group.name if notification.group else UNKNOWN_GROUP
    roadmap = (
        notification.roadmap.title if notification.roadmap else UNKNOWN_ROADMAP
    )
    return notification_meta(notification)["text"].format(
        actor=actor, challenge=challenge, group=group, roadmap=roadmap
    )


def register_notification_filters(env) -> None:
    """Expose the three renderers to a Jinja environment.

    Every views router builds its own ``Jinja2Templates``, so a router that
    renders a notification must call this -- exactly like
    ``register_icon_filters``. Forgetting it is a template error at render
    time, not at import.
    """
    env.filters["notification_title"] = notification_title
    env.filters["notification_text"] = notification_text
    env.filters["notification_icon"] = notification_icon


def muted_kinds(user: User) -> set[str]:
    """The kinds this member has switched off.

    ``NULL`` and ``[]`` mean the same thing -- nothing muted -- and unknown
    values are kept rather than dropped, so a kind removed and later restored
    does not silently un-mute itself. Reading goes through this function so
    that "NULL means everything is on" is stated once.
    """
    stored = user.muted_notification_kinds or []
    return {str(kind) for kind in stored}


def is_muted(user: User, kind: NotificationKind) -> bool:
    return kind.value in muted_kinds(user)


def notification_settings(user: User) -> list[dict[str, object]]:
    """Every switch the settings screen draws, in one list.

    Built from :data:`NOTIFICATION_META` rather than from a list in the
    template, so a kind can never ship without a switch -- and the switch is
    worded by the same map that words the notification itself.
    """
    muted = muted_kinds(user)
    return [
        {
            "kind": kind,
            "title": meta["title"],
            "hint": meta.get("hint", FALLBACK_META["hint"]),
            "icon": meta["icon"],
            "enabled": kind not in muted,
        }
        for kind, meta in NOTIFICATION_META.items()
    ]


def set_notification_enabled(user: User, kind: NotificationKind, enabled: bool) -> None:
    """Flip one switch, writing the whole list back.

    The column is JSON, so it is reassigned rather than mutated in place --
    SQLAlchemy does not track mutation inside a plain JSON value, and an
    in-place ``add()`` would be a change that never reaches the database.
    """
    muted = muted_kinds(user)
    muted.discard(kind.value) if enabled else muted.add(kind.value)
    user.muted_notification_kinds = sorted(muted)


async def _mute_map(db: AsyncSession, user_ids: list[int]) -> dict[int, set[str]]:
    """What each of these recipients has muted, as one read.

    One query for the whole broadcast rather than one per member, and the
    only place the stored column is loaded outside the settings screen.
    """
    if not user_ids:
        return {}
    rows = (
        await db.execute(
            select(User.id, User.muted_notification_kinds).where(
                User.id.in_(set(user_ids))
            )
        )
    ).all()
    return {row_id: {str(k) for k in (stored or [])} for row_id, stored in rows}


def _add(
    db: AsyncSession,
    *,
    user_id: int,
    kind: NotificationKind,
    actor_user_id: int | None,
    challenge_id: int | None,
    group_id: int | None,
    roadmap_id: int | None,
    muted: set[str],
    anonymous_actor: bool = False,
) -> Notification | None:
    """The drop rules, the anonymising, and the row itself -- the whole door.

    Kept as one private helper so :func:`notify` and :func:`notify_many`
    cannot come to disagree about when a notification is raised; the only
    thing they do differently is how many recipients they read the mute
    list for.
    """
    if actor_user_id is not None and actor_user_id == user_id:
        return None
    if kind.value in muted:
        return None
    notification = Notification(
        user_id=user_id,
        kind=kind.value,
        # Nulled *after* the self-drop above, never before: the rule is about
        # who acted, and forgetting that first is how someone starts being
        # told about their own taps.
        actor_user_id=None if anonymous_actor else actor_user_id,
        challenge_id=challenge_id,
        group_id=group_id,
        roadmap_id=roadmap_id,
    )
    db.add(notification)
    # Invariant five, and the only one this function does not itself enforce:
    # a member who allowed it is woken on their own device. The row is merely
    # *queued* -- nothing is sent until the caller's transaction commits, and
    # the sending happens after the response (see `app/push.py`). It is here,
    # inside the one door, for the reason the mute check is: a policy honoured
    # only by the call sites that remembered it stops working the first time
    # a new event is added.
    push.queue(notification)
    return notification


async def notify(
    db: AsyncSession,
    *,
    user_id: int,
    kind: NotificationKind,
    actor_user_id: int | None = None,
    challenge_id: int | None = None,
    group_id: int | None = None,
    roadmap_id: int | None = None,
    anonymous_actor: bool = False,
) -> Notification | None:
    """Raise one notification, unless its recipient caused it or muted it.

    Adds to the session and returns the row without flushing or committing --
    see the module docstring on why that is the contract and not an
    oversight. Returns ``None`` when the notification was dropped, so a
    caller can tell "not sent" from "sent" without re-reading the invariants.
    """
    muted = (await _mute_map(db, [user_id])).get(user_id, set())
    return _add(
        db,
        user_id=user_id,
        kind=kind,
        actor_user_id=actor_user_id,
        challenge_id=challenge_id,
        group_id=group_id,
        roadmap_id=roadmap_id,
        muted=muted,
        anonymous_actor=anonymous_actor,
    )


async def notify_many(
    db: AsyncSession,
    *,
    user_ids: list[int],
    kind: NotificationKind,
    actor_user_id: int | None = None,
    challenge_id: int | None = None,
    group_id: int | None = None,
    roadmap_id: int | None = None,
    anonymous_actor: bool = False,
) -> list[Notification]:
    """:func:`notify` for a broadcast -- every participant of a challenge.

    Duplicate ids are collapsed, because "everyone enrolled" is assembled
    from a query the caller does not otherwise have to reason about, and one
    member being told twice about one event is a bug nobody would think to
    look for.
    """
    recipients = list(dict.fromkeys(user_ids))
    mutes = await _mute_map(db, recipients)
    raised = []
    for user_id in recipients:
        notification = _add(
            db,
            user_id=user_id,
            kind=kind,
            actor_user_id=actor_user_id,
            challenge_id=challenge_id,
            group_id=group_id,
            roadmap_id=roadmap_id,
            muted=mutes.get(user_id, set()),
            anonymous_actor=anonymous_actor,
        )
        if notification is not None:
            raised.append(notification)
    return raised
