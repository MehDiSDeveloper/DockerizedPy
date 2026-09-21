"""The JSON door onto an act's referees.

Four routes and no page of its own -- the screens that use these are the
act's manage screen (the owner's half) and the notification the invitee
gets (theirs). Query and policy live in ``app/referees.py``; this file is
the HTTP shape of it, the same split every other router in the app makes.

**The 404/403 split is the app's.** A challenge the caller cannot see is a
404 from ``challenge_visibility_filter`` -- composed into the query, never
checked afterwards -- so a referee route cannot confirm that a private act
exists. Once the row is in hand, a refusal is an honest 403.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.actlog import record
from app.auth import get_current_user, get_current_user_id
from app.database import get_db
from app.logging_config import log_event
from app.models.act import ActEventKind, RefereeState
from app.models.challenge import Challenge
from app.models.notification import NotificationKind
from app.models.user import User
from app.notifications import notify
from app.permissions import Perm, can
from app.referees import (
    RefereeRefused,
    fetch_referees,
    invite_referee,
    referee_row,
    remove,
    resolve_member,
    respond,
)
from app.routers.challenge import challenge_visibility_filter
from app.schemas.act import RefereeInvite, RefereeRead, RefereeResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/challenges", tags=["referees"])


async def _visible_challenge(
    db: AsyncSession, challenge_id: int, user_id: int
) -> Challenge:
    challenge = (
        await db.execute(
            select(Challenge).where(
                Challenge.id == challenge_id,
                challenge_visibility_filter(user_id),
            )
        )
    ).scalar_one_or_none()
    if challenge is None:
        raise HTTPException(status_code=404, detail="Challenge not found")
    return challenge


def _managed_challenge(challenge: Challenge, user: User) -> None:
    if not can(user, Perm.CHALLENGE_MANAGE_REFEREES, challenge=challenge):
        raise HTTPException(
            status_code=403, detail="ناظران این تعهد را تو تعیین نمی‌کنی."
        )


@router.get("/{challenge_id}/referees", response_model=list[RefereeRead])
async def list_referees(
    challenge_id: int,
    current_user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    """Who vouches for this act.

    Open to anyone who can see the act, and deliberately so: whether somebody
    is being watched, and by whom, is a term of the act rather than a detail
    of its administration -- a doer who cannot see who rules on their reports
    is being judged by nobody in particular. The answer carries ids and
    states and no profile, exactly as ``GroupMemberRead`` does.
    """
    await _visible_challenge(db, challenge_id, current_user_id)
    return await fetch_referees(db, challenge_id)


@router.post("/{challenge_id}/referees", response_model=RefereeRead, status_code=201)
async def add_referee(
    challenge_id: int,
    payload: RefereeInvite,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Ask somebody to vouch. Named by credential -- see ``app/referees.py``."""
    challenge = await _visible_challenge(db, challenge_id, current_user.id)
    _managed_challenge(challenge, current_user)

    try:
        member = await resolve_member(db, payload.identifier)
        row = await invite_referee(
            db, challenge=challenge, user=member, actor_user_id=current_user.id
        )
    except RefereeRefused as exc:
        raise HTTPException(status_code=exc.status, detail=exc.detail) from exc

    await db.flush()
    record(
        db,
        challenge_id=challenge.id,
        kind=ActEventKind.REFEREE_INVITED,
        actor_user_id=current_user.id,
        subject_user_id=member.id,
    )
    await notify(
        db,
        user_id=member.id,
        kind=NotificationKind.REFEREE_INVITED,
        actor_user_id=current_user.id,
        challenge_id=challenge.id,
    )
    await db.commit()
    await db.refresh(row)
    log_event(
        logger,
        "referee.invited",
        challenge_id=challenge.id,
        referee_user_id=member.id,
    )
    return row


@router.patch("/{challenge_id}/referees/me", response_model=RefereeRead)
async def answer_invitation(
    challenge_id: int,
    payload: RefereeResponse,
    current_user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    """Accept or decline. The invitee's own route, and the only one they have.

    ``/me`` rather than ``/{user_id}``: the acting member comes from the
    session and never from the path -- the rule every router in this app
    follows, and the one that makes "answer somebody else's invitation"
    unexpressible rather than merely refused.
    """
    await _visible_challenge(db, challenge_id, current_user_id)
    row = await referee_row(db, challenge_id=challenge_id, user_id=current_user_id)
    if row is None:
        raise HTTPException(status_code=404, detail="دعوتی برای تو نیست.")

    try:
        respond(row, accept=payload.accept, actor_user_id=current_user_id)
    except RefereeRefused as exc:
        raise HTTPException(status_code=exc.status, detail=exc.detail) from exc

    record(
        db,
        challenge_id=challenge_id,
        kind=(
            ActEventKind.REFEREE_ACCEPTED
            if payload.accept
            else ActEventKind.REFEREE_DECLINED
        ),
        actor_user_id=current_user_id,
    )
    # The person who asked hears either way. One kind, not two -- an owner
    # who wanted only the yeses would be asking for a feed that flatters.
    await notify(
        db,
        user_id=row.invited_by_user_id,
        kind=NotificationKind.REFEREE_RESPONDED,
        actor_user_id=current_user_id,
        challenge_id=challenge_id,
    )
    await db.commit()
    await db.refresh(row)
    log_event(
        logger,
        "referee.responded",
        challenge_id=challenge_id,
        accepted=payload.accept,
    )
    return row


@router.delete("/{challenge_id}/referees/{user_id}", status_code=204)
async def remove_referee(
    challenge_id: int,
    user_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Take somebody off. A state, never a delete -- see ``app/referees.py``.

    A member id that is not a referee of this act is a **404**, the call the
    group router makes for the same reason: a 403 would confirm the account
    exists.

    What this does *not* do is disturb anything already ruled on. A verdict
    is stored, so reports that referee approved stay approved -- the same
    trade ``Enrollment.is_anonymous`` makes about a mode that changed later.
    Reports still pending simply wait for whoever is left.
    """
    challenge = await _visible_challenge(db, challenge_id, current_user.id)
    _managed_challenge(challenge, current_user)

    row = await referee_row(db, challenge_id=challenge_id, user_id=user_id)
    if row is None or row.state == RefereeState.REMOVED.value:
        raise HTTPException(status_code=404, detail="ناظری با این مشخصات نیست.")

    try:
        remove(row, actor_user_id=current_user.id)
    except RefereeRefused as exc:
        raise HTTPException(status_code=exc.status, detail=exc.detail) from exc

    record(
        db,
        challenge_id=challenge_id,
        kind=ActEventKind.REFEREE_REMOVED,
        actor_user_id=current_user.id,
        subject_user_id=user_id,
    )
    await db.commit()
    log_event(
        logger,
        "referee.removed",
        level=logging.WARNING,
        challenge_id=challenge_id,
        referee_user_id=user_id,
    )
