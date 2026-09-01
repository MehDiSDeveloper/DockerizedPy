# app/routers/group/participants.py
"""Who is in one group challenge -- adding and removing participants."""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_user
from app.database import get_db
from app.groups import assign_participants
from app.models.enrollment import Enrollment
from app.models.group import GroupMembership
from app.models.stats import ChallengeStats
from app.models.user import User
from app.permissions import Perm
from app.routers.challenge import resolve_timezone
from app.routers.group.queries import _group_challenge, load_group, require
from app.schemas.group import ParticipantsAdd

router = APIRouter(prefix="/groups", tags=["groups"])


@router.post("/{group_id}/challenges/{challenge_id}/participants", status_code=204)
async def add_participants(
    group_id: int,
    challenge_id: int,
    payload: ParticipantsAdd,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    timezone: str | None = Query(default=None, max_length=64),
):
    """Put more of the group into a challenge that already exists.

    Gated on ``GROUP_MANAGE_MEMBERS`` rather than on owning the challenge,
    and that is the line: deciding *who in this organisation is in this* is
    running the group, while changing what the challenge says is authorship
    and stays with its owner (``CHALLENGE_EDIT``) exactly as it does for a
    moderator.

    Non-members are dropped rather than refused -- the same intersection
    ``seed_group_participants`` does, for the same reason: a stranger must
    never end up enrolled in a challenge they cannot see, and it must not be
    possible to *test* whether an id is in the group by watching which bodies
    are rejected.
    """
    group, membership = await load_group(db, group_id, current_user)
    require(current_user, Perm.GROUP_MANAGE_MEMBERS, group, membership)
    challenge = await _group_challenge(db, group, challenge_id)
    members = set(
        (
            await db.execute(
                select(GroupMembership.user_id).where(
                    GroupMembership.group_id == group_id,
                    GroupMembership.user_id.in_(payload.user_ids),
                )
            )
        )
        .scalars()
        .all()
    )
    if members:
        await assign_participants(
            db,
            challenge=challenge,
            user_ids=sorted(members),
            actor_user_id=current_user.id,
            timezone=resolve_timezone(timezone),
        )
    await db.commit()


@router.delete(
    "/{group_id}/challenges/{challenge_id}/participants/{user_id}", status_code=204
)
async def remove_participant(
    group_id: int,
    challenge_id: int,
    user_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Take somebody out of a group challenge.

    The challenge's own owner is refused: their enrolment is what carries
    ``ChallengeRole.OWNER``, and removing it would strip the author of the
    thing they wrote. Everyone else's enrolment goes, along with their
    check-ins -- which is why this is the group administrator's action and
    not something a member of the group can do to another, and why the
    counter is decremented here the same way ``unenroll`` does it.
    """
    group, membership = await load_group(db, group_id, current_user)
    require(current_user, Perm.GROUP_MANAGE_MEMBERS, group, membership)
    challenge = await _group_challenge(db, group, challenge_id)
    if user_id == challenge.owner_id:
        raise HTTPException(
            status_code=409, detail="The challenge owner cannot be removed"
        )
    enrollment = (
        await db.execute(
            select(Enrollment).where(
                Enrollment.challenge_id == challenge_id,
                Enrollment.user_id == user_id,
            )
        )
    ).scalar_one_or_none()
    if enrollment is None:
        raise HTTPException(status_code=404, detail="Not a participant")
    await db.delete(enrollment)
    stats = (
        await db.execute(
            select(ChallengeStats).where(ChallengeStats.challenge_id == challenge_id)
        )
    ).scalar_one_or_none()
    if stats is not None and stats.participant_count:
        stats.participant_count = max(stats.participant_count - 1, 0)
    await db.commit()
