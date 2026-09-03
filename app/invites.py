"""A link with a capacity, and the one statement that spends a seat.

Two subsystems hand out invite links -- a group's and a roadmap's -- and both
mean exactly the same thing by one: an unguessable code, an optional label, an
optional capacity, an optional expiry, revocation by stamp, and a state that is
*derived* rather than stored. The tables are separate (each carries its own
foreign key and each is cascaded from its own parent), but the rules are not,
so they live here and neither subsystem restates them.

**The capacity is real.** :func:`consume_seat` is a conditional ``UPDATE ...
SET uses = uses + 1 WHERE <still usable>`` plus a check of ``rowcount``, and it
is the only way a seat is ever spent. Reading ``uses``, deciding, and writing
back passes every sequential test and admits two people to a one-seat link the
first time two of them tap it in the same instant --
``tests/test_group_invites.py`` drives four concurrent accepts at one seat for
exactly this reason.

**The state is derived, never stored.** A stored column would need a job to
rewrite it the minute a link expired or filled, and a link whose stored state
disagrees with whether it actually works is worse than no state at all.
"""

from __future__ import annotations

import secrets
from datetime import UTC, datetime

from sqlalchemy import or_, update
from sqlalchemy.ext.asyncio import AsyncSession

# Length of the random part of an invite code. 32 bytes of urlsafe base64 is
# 43 characters and ~256 bits -- an invite code is the *only* key that reaches
# a group (or an unlisted roadmap) from outside, so it is sized to be
# unguessable rather than to be typed.
INVITE_CODE_BYTES = 32

INVITE_ACTIVE = "active"
INVITE_REVOKED = "revoked"
INVITE_EXPIRED = "expired"
INVITE_FULL = "full"


def new_invite_code() -> str:
    return secrets.token_urlsafe(INVITE_CODE_BYTES)[:43]


def invite_state(invite, now: datetime | None = None) -> str:
    """What this link is doing right now -- derived, see the module docstring.

    Duck-typed on the four columns every invite table carries, so one function
    answers for a ``GroupInvite`` and a ``RoadmapInvite`` alike. The four
    values are ordered by how final they are, so a revoked link that also
    expired reads as revoked -- the administrator's own act, which is the more
    useful of the two answers.
    """
    now = now or datetime.now(UTC)
    if invite.revoked_at is not None:
        return INVITE_REVOKED
    expires = invite.expires_at
    if expires is not None:
        if expires.tzinfo is None:
            # SQLite has no aware datetime type (CLAUDE.md, Dates).
            expires = expires.replace(tzinfo=UTC)
        if expires <= now:
            return INVITE_EXPIRED
    if invite.max_uses is not None and (invite.uses or 0) >= invite.max_uses:
        return INVITE_FULL
    return INVITE_ACTIVE


#: What each *derived* state is called -- see :func:`invite_state` on why the
#: state is derived rather than stored. Here rather than in either subsystem's
#: own label map, for the reason the mechanics are: two surfaces render the
#: same four words about the same four states, and a second copy would be a
#: second thing to keep in agreement.
INVITE_STATE_LABELS = {
    INVITE_ACTIVE: "فعال",
    INVITE_REVOKED: "غیرفعال‌شده",
    INVITE_EXPIRED: "منقضی",
    INVITE_FULL: "ظرفیت پر",
}


def register_invite_filters(env) -> None:
    """Expose the state labels to one Jinja environment.

    The same registration contract ``register_icon_filters`` has. A views
    router that renders an invite row must call it -- ``register_group_filters``
    does it for the group screens, so those are unchanged.
    """
    env.globals["invite_state_labels"] = INVITE_STATE_LABELS


async def consume_seat(db: AsyncSession, model, invite_id: int) -> bool:
    """Spend one seat of this link, or answer ``False`` if it has none left.

    Revocation, expiry and capacity are all in the same ``WHERE``, so there is
    one statement to reason about rather than three checks and a window
    between them. Nothing is committed: the caller runs this inside the same
    transaction as the membership it is about to write, so a failed insert
    takes the consumed seat back with it.

    ``synchronize_session=False`` because this UPDATE is the authority and the
    ORM's in-Python evaluation of the same WHERE is not: SQLite hands
    ``expires_at`` back naive, so evaluating ``expires_at > now`` against a
    mapped instance raises on the timezone comparison. The row is re-read from
    the database wherever it is needed afterwards.
    """
    now = datetime.now(UTC)
    result = await db.execute(
        update(model)
        .where(
            model.id == invite_id,
            model.revoked_at.is_(None),
            or_(model.expires_at.is_(None), model.expires_at > now),
            or_(model.max_uses.is_(None), model.uses < model.max_uses),
        )
        .values(uses=model.uses + 1)
        .execution_options(synchronize_session=False)
    )
    return result.rowcount > 0
