# app/routers/group/invites.py
"""Invite links, the public link, and the join-request queue behind them."""

import logging
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_user
from app.database import get_db
from app.groups import (
    INVITE_ACTIVE,
    apply_standing_audience,
    invite_state,
    member_counts,
    new_invite_code,
)
from app.logging_config import log_event
from app.models.group import (
    Group,
    GroupInvite,
    GroupJoinRequest,
    GroupMembership,
    GroupRole,
    JoinRequestStatus,
)
from app.models.notification import NotificationKind
from app.models.user import User
from app.notifications import notify, notify_many
from app.permissions import Perm
from app.routers.challenge import resolve_timezone
from app.routers.group.queries import (
    DEFAULT_REQUEST_PAGE_SIZE,
    MAX_REQUEST_PAGE_SIZE,
    PUBLIC_INVITE_LABEL,
    REQUEST_STATES,
    fetch_request_page,
    group_admin_ids,
    group_invites,
    group_read,
    invite_by_code,
    invite_rows,
    load_group,
    require,
)
from app.schemas.group import (
    GroupRead,
    InviteCreate,
    InvitePreview,
    InviteRead,
    JoinRequestRead,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/groups", tags=["groups"])

# The invite landing lives on its own prefix rather than under `/groups/`,
# because the whole point of a code is that the person holding it does not
# know -- and must not need to know -- the group's id.
invite_router = APIRouter(prefix="/invites", tags=["groups"])


# ---------------------------------------------------------------------------
# Invites
# ---------------------------------------------------------------------------


@router.post("/{group_id}/invites", response_model=InviteRead, status_code=201)
async def create_invite(
    group_id: int,
    payload: InviteCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    group, membership = await load_group(db, group_id, current_user)
    require(current_user, Perm.GROUP_MANAGE_MEMBERS, group, membership)
    invite = GroupInvite(
        group_id=group_id,
        code=new_invite_code(),
        label=payload.label,
        max_uses=payload.max_uses,
        expires_at=payload.expires_at,
        requires_approval=payload.requires_approval,
        uses=0,
        created_by_user_id=current_user.id,
        last_modifier_user_id=current_user.id,
    )
    db.add(invite)
    await db.commit()
    await db.refresh(invite)
    return invite_rows([invite])[0]


@router.post("/{group_id}/invites/public", response_model=InviteRead)
async def public_invite(
    group_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """The group's own link, the one that goes in a signature or on a wall.

    Get-or-create, and that is the whole design: a *standing* public link is
    one address that keeps meaning "this group", so asking for it twice must
    answer the same code twice. A route that minted a new one per tap would
    leave every previously-handed-out copy alive and unaccounted for, which
    is the opposite of what a public link is for.

    It is an ordinary :class:`GroupInvite` with ``requires_approval`` set and
    no capacity, rather than a column on ``Group``: everything a public link
    needs -- an unguessable code, a landing page, revocation -- already
    exists on that table, and a second mechanism would be a second place to
    keep those rules true. Revoking it is the same route every other link
    uses; the next call here mints a fresh one, so "rotate the public link"
    needs no code of its own.
    """
    group, membership = await load_group(db, group_id, current_user)
    require(current_user, Perm.GROUP_MANAGE_MEMBERS, group, membership)
    now = datetime.now(UTC)
    existing = [
        i
        for i in await group_invites(db, group_id)
        if i.requires_approval and invite_state(i, now) == INVITE_ACTIVE
    ]
    if existing:
        # `group_invites` is newest-first, so the most recently minted one
        # wins if an older generation is somehow still open.
        return invite_rows([existing[0]])[0]
    invite = GroupInvite(
        group_id=group_id,
        code=new_invite_code(),
        label=PUBLIC_INVITE_LABEL,
        max_uses=None,
        expires_at=None,
        requires_approval=True,
        uses=0,
        created_by_user_id=current_user.id,
        last_modifier_user_id=current_user.id,
    )
    db.add(invite)
    await db.commit()
    await db.refresh(invite)
    return invite_rows([invite])[0]


@router.get("/{group_id}/invites", response_model=list[InviteRead])
async def list_invites(
    group_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    group, membership = await load_group(db, group_id, current_user)
    require(current_user, Perm.GROUP_MANAGE_MEMBERS, group, membership)
    return invite_rows(await group_invites(db, group_id))


@router.delete("/{group_id}/invites/{invite_id}", status_code=204)
async def revoke_invite(
    group_id: int,
    invite_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Kill a link. A stamp, not a DELETE -- see ``GroupInvite.revoked_at``."""
    group, membership = await load_group(db, group_id, current_user)
    require(current_user, Perm.GROUP_MANAGE_MEMBERS, group, membership)
    invite = (
        await db.execute(
            select(GroupInvite).where(
                GroupInvite.id == invite_id, GroupInvite.group_id == group_id
            )
        )
    ).scalar_one_or_none()
    if invite is None:
        raise HTTPException(status_code=404, detail="Invite not found")
    if invite.revoked_at is None:
        invite.revoked_at = datetime.now(UTC)
        invite.last_modifier_user_id = current_user.id
        await db.commit()


@invite_router.get("/{code}", response_model=InvitePreview)
async def preview_invite(
    code: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """What the link says about the group, before it is used.

    The name, the emblem, the kind and the size -- and nothing else. Whoever
    holds the code is not a member yet, so a preview that listed the
    challenges or the people would turn an unused invite into a way to read
    the group. The state comes back too, so a full or expired link can say so
    and offer the other door (a join request) instead of failing on tap.
    """
    invite = await invite_by_code(db, code)
    group = invite.group
    counts = await member_counts(db, [group.id])
    already = (
        await db.execute(
            select(GroupMembership.id).where(
                GroupMembership.group_id == group.id,
                GroupMembership.user_id == current_user.id,
            )
        )
    ).scalar_one_or_none() is not None
    pending = (
        await db.execute(
            select(GroupJoinRequest.id).where(
                GroupJoinRequest.group_id == group.id,
                GroupJoinRequest.user_id == current_user.id,
                GroupJoinRequest.status == JoinRequestStatus.PENDING.value,
            )
        )
    ).scalar_one_or_none() is not None
    return InvitePreview(
        group_id=group.id,
        name=group.name,
        description=group.description,
        kind=group.kind,
        emblem=group.emblem,
        member_count=counts.get(group.id, 0),
        state=invite_state(invite),
        requires_approval=bool(invite.requires_approval),
        already_member=already,
        request_pending=pending,
    )


@invite_router.post("/{code}/accept", response_model=GroupRead, status_code=201)
async def accept_invite(
    code: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    timezone: str | None = Query(default=None, max_length=64),
):
    """Use a link and be in the group. No approval, by design.

    **The seat is taken with a conditional UPDATE, not a read followed by a
    write.** ``uses = uses + 1 WHERE uses < max_uses`` and a check of
    ``rowcount`` is the only version of this that survives two people tapping
    a one-seat link at the same instant; reading ``uses``, deciding, and then
    writing is precisely the race that lets a capacity be exceeded. Revocation
    and expiry are in the same ``WHERE`` so there is one statement to reason
    about rather than three checks and a window between them.

    Everything is one transaction, so a membership that fails to insert takes
    the consumed seat back with it.
    """
    invite = await invite_by_code(db, code)
    group = invite.group
    if invite.requires_approval:
        # A link that asks rather than admits. The landing page never offers
        # this button for one, so reaching here means a hand-made request --
        # and a code that let somebody in past the queue its owner set up
        # would make the flag decorative.
        raise HTTPException(
            status_code=409, detail="This link needs an administrator's approval"
        )

    already = (
        await db.execute(
            select(GroupMembership).where(
                GroupMembership.group_id == group.id,
                GroupMembership.user_id == current_user.id,
            )
        )
    ).scalar_one_or_none()
    if already is not None:
        # Idempotent, and it does not spend a seat: somebody re-opening the
        # link they joined with has not joined twice.
        return await group_read(db, group, current_user.id, already)

    now = datetime.now(UTC)
    consumed = await db.execute(
        update(GroupInvite)
        .where(
            GroupInvite.id == invite.id,
            GroupInvite.revoked_at.is_(None),
            or_(GroupInvite.expires_at.is_(None), GroupInvite.expires_at > now),
            or_(
                GroupInvite.max_uses.is_(None),
                GroupInvite.uses < GroupInvite.max_uses,
            ),
        )
        .values(uses=GroupInvite.uses + 1)
        # `synchronize_session=False` because this UPDATE is the authority and
        # the ORM's in-Python evaluation of the same WHERE is not: SQLite
        # hands `expires_at` back naive, so evaluating `expires_at > now`
        # against a mapped instance raises on the tz comparison. The row is
        # re-read from the database wherever it is needed after this.
        .execution_options(synchronize_session=False)
    )
    if consumed.rowcount == 0:
        await db.rollback()
        raise HTTPException(
            status_code=409, detail="This invite link can no longer be used"
        )

    membership = GroupMembership(
        group_id=group.id,
        user_id=current_user.id,
        role=GroupRole.MEMBER.value,
        last_modifier_user_id=current_user.id,
    )
    db.add(membership)
    try:
        await db.flush()
    except IntegrityError:
        # Lost a race against another tab. The rollback also returns the seat.
        await db.rollback()
        raise HTTPException(status_code=409, detail="Already a member") from None

    # A standing «همه اعضا» challenge is what the group already asks of
    # everyone, so an arrival picks it up on the way in -- see
    # `apply_standing_audience` on why that is re-read rather than snapshotted.
    await apply_standing_audience(
        db,
        group_id=group.id,
        user_id=current_user.id,
        timezone=resolve_timezone(timezone),
        actor_user_id=current_user.id,
    )
    await db.commit()
    log_event(logger, "group.invite_accepted", group_id=group.id, invite_id=invite.id)
    return await group_read(db, group, current_user.id, membership)


# ---------------------------------------------------------------------------
# Join requests
# ---------------------------------------------------------------------------


@invite_router.post("/{code}/request", response_model=JoinRequestRead, status_code=201)
async def request_to_join(
    code: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Ask to be let in. Keyed on the invite code, not on the group id.

    Deliberately: a route taking a group id would let anybody walk the
    sequential ids and learn which groups exist, and spam every one of them.
    The code is the same thing that makes the group reachable in the first
    place, so a request is only possible for somebody who was given a link --
    including one that has expired or filled up, which is exactly the case
    this door exists for.
    """
    invite = await invite_by_code(db, code)
    group = invite.group
    member = (
        await db.execute(
            select(GroupMembership.id).where(
                GroupMembership.group_id == group.id,
                GroupMembership.user_id == current_user.id,
            )
        )
    ).scalar_one_or_none()
    if member is not None:
        raise HTTPException(status_code=409, detail="Already a member")

    existing = (
        await db.execute(
            select(GroupJoinRequest).where(
                GroupJoinRequest.group_id == group.id,
                GroupJoinRequest.user_id == current_user.id,
                GroupJoinRequest.status == JoinRequestStatus.PENDING.value,
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        # Asking twice is the same request. Answering 409 would be honest but
        # useless -- the caller wants to be in the queue, and they are.
        return JoinRequestRead(
            id=existing.id,
            group_id=group.id,
            user_id=current_user.id,
            name=current_user.name,
            avatar=current_user.avatar,
            status=existing.status,
            created_at=existing.created_at,
        )

    request = GroupJoinRequest(
        group_id=group.id,
        user_id=current_user.id,
        status=JoinRequestStatus.PENDING.value,
        last_modifier_user_id=current_user.id,
    )
    db.add(request)
    await notify_many(
        db,
        user_ids=await group_admin_ids(db, group),
        kind=NotificationKind.GROUP_JOIN_REQUESTED,
        actor_user_id=current_user.id,
        group_id=group.id,
    )
    await db.commit()
    await db.refresh(request)
    return JoinRequestRead(
        id=request.id,
        group_id=group.id,
        user_id=current_user.id,
        name=current_user.name,
        avatar=current_user.avatar,
        status=request.status,
        created_at=request.created_at,
    )


@router.get("/{group_id}/requests", response_model=list[JoinRequestRead])
async def list_join_requests(
    group_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    status: str = Query(default=JoinRequestStatus.PENDING.value),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(
        default=DEFAULT_REQUEST_PAGE_SIZE, ge=1, le=MAX_REQUEST_PAGE_SIZE
    ),
):
    """This group's requests in one state, newest page first.

    Pending by default, because that is the only state anybody has to *act*
    on; the two terminal states are the record of what was already decided.
    An unrecognised state is a 422 rather than a silent fallback to the
    queue -- see :data:`REQUEST_STATES`.
    """
    group, membership = await load_group(db, group_id, current_user)
    require(current_user, Perm.GROUP_MANAGE_MEMBERS, group, membership)
    if status not in REQUEST_STATES:
        raise HTTPException(status_code=422, detail="Unknown status")
    rows, _ = await fetch_request_page(
        db, group_id=group_id, status=status, offset=offset, limit=limit
    )
    return [
        JoinRequestRead(
            id=r.id,
            group_id=r.group_id,
            user_id=r.user_id,
            name=r.user.name if r.user else "—",
            avatar=r.user.avatar if r.user else None,
            status=r.status,
            created_at=r.created_at,
            decided_at=r.decided_at,
            decided_by_name=r.decided_by.name if r.decided_by else None,
        )
        for r in rows
    ]


async def _decide_request(
    db: AsyncSession,
    *,
    group: Group,
    request_id: int,
    approve: bool,
    actor: User,
    timezone: str | None,
) -> GroupJoinRequest:
    request = (
        await db.execute(
            select(GroupJoinRequest).where(
                GroupJoinRequest.id == request_id,
                GroupJoinRequest.group_id == group.id,
            )
        )
    ).scalar_one_or_none()
    if request is None:
        raise HTTPException(status_code=404, detail="Request not found")
    if request.status != JoinRequestStatus.PENDING.value:
        # Two administrators tapping the same row: the second is told the
        # decision was already made rather than overwriting it.
        raise HTTPException(status_code=409, detail="Already decided")

    request.status = (
        JoinRequestStatus.APPROVED.value if approve else JoinRequestStatus.REJECTED.value
    )
    request.decided_by_user_id = actor.id
    request.decided_at = datetime.now(UTC)
    request.updated_at = request.decided_at
    request.last_modifier_user_id = actor.id

    if approve:
        db.add(
            GroupMembership(
                group_id=group.id,
                user_id=request.user_id,
                role=GroupRole.MEMBER.value,
                last_modifier_user_id=actor.id,
            )
        )
        await db.flush()
        await apply_standing_audience(
            db,
            group_id=group.id,
            user_id=request.user_id,
            timezone=resolve_timezone(timezone),
            actor_user_id=actor.id,
        )

    await notify(
        db,
        user_id=request.user_id,
        kind=(
            NotificationKind.GROUP_JOIN_APPROVED
            if approve
            else NotificationKind.GROUP_JOIN_REJECTED
        ),
        actor_user_id=actor.id,
        group_id=group.id,
    )
    await db.commit()
    await db.refresh(request)
    log_event(
        logger,
        "group.request_decided",
        group_id=group.id,
        request_id=request.id,
        member_id=request.user_id,
        approved=approve,
    )
    return request


@router.post(
    "/{group_id}/requests/{request_id}/approve", response_model=JoinRequestRead
)
async def approve_request(
    group_id: int,
    request_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    timezone: str | None = Query(default=None, max_length=64),
):
    group, membership = await load_group(db, group_id, current_user)
    require(current_user, Perm.GROUP_MANAGE_MEMBERS, group, membership)
    request = await _decide_request(
        db,
        group=group,
        request_id=request_id,
        approve=True,
        actor=current_user,
        timezone=timezone,
    )
    user = (
        await db.execute(select(User).where(User.id == request.user_id))
    ).scalar_one()
    return JoinRequestRead(
        id=request.id,
        group_id=group_id,
        user_id=request.user_id,
        name=user.name,
        avatar=user.avatar,
        status=request.status,
        created_at=request.created_at,
    )


@router.post(
    "/{group_id}/requests/{request_id}/reject", response_model=JoinRequestRead
)
async def reject_request(
    group_id: int,
    request_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    group, membership = await load_group(db, group_id, current_user)
    require(current_user, Perm.GROUP_MANAGE_MEMBERS, group, membership)
    request = await _decide_request(
        db,
        group=group,
        request_id=request_id,
        approve=False,
        actor=current_user,
        timezone=None,
    )
    user = (
        await db.execute(select(User).where(User.id == request.user_id))
    ).scalar_one()
    return JoinRequestRead(
        id=request.id,
        group_id=group_id,
        user_id=request.user_id,
        name=user.name,
        avatar=user.avatar,
        status=request.status,
        created_at=request.created_at,
    )
