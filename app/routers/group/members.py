# app/routers/group/members.py
"""The group itself, and membership: creating, joining directly, roles,
leaving, removal and ownership transfer."""

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_user
from app.database import get_db
from app.groups import apply_standing_audience, leave_group_challenges, member_counts
from app.models.challenge import Challenge
from app.models.group import Group, GroupMembership, GroupRole
from app.models.notification import NotificationKind
from app.models.user import User
from app.notifications import notify
from app.permissions import Perm
from app.routers.challenge import resolve_timezone
from app.routers.group.queries import (
    DEFAULT_GROUP_PAGE_SIZE,
    MAX_GROUP_PAGE_SIZE,
    _membership_of,
    _user_by_credential,
    fetch_group_member_page,
    fetch_group_page,
    group_read,
    load_group,
    member_rows,
    require,
)
from app.routers.user import DEFAULT_MEMBER_PAGE_SIZE
from app.schemas.group import (
    GroupCreate,
    GroupLeaveResult,
    GroupMemberRead,
    GroupRead,
    GroupRoleUpdate,
    GroupTransfer,
    GroupUpdate,
    MemberAdd,
)

router = APIRouter(prefix="/groups", tags=["groups"])


# ---------------------------------------------------------------------------
# The group itself
# ---------------------------------------------------------------------------


@router.post("/", response_model=GroupRead, status_code=201)
async def create_group(
    payload: GroupCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Anyone signed in may open a group; the creator is its owner.

    Both records are written -- ``owner_id`` on the group *and* an ``owner``
    membership row -- because they answer different questions and
    ``group_role`` resolves them together: the column is the authority, the
    row is what makes the owner appear on their own roster.
    """
    group = Group(
        **payload.model_dump(),
        owner_id=current_user.id,
        last_modifier_user_id=current_user.id,
    )
    db.add(group)
    await db.flush()
    db.add(
        GroupMembership(
            group_id=group.id,
            user_id=current_user.id,
            role=GroupRole.OWNER.value,
            last_modifier_user_id=current_user.id,
        )
    )
    await db.commit()
    await db.refresh(group)
    return await group_read(db, group, current_user.id, None)


@router.get("/", response_model=list[GroupRead])
async def list_my_groups(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=DEFAULT_GROUP_PAGE_SIZE, ge=1, le=MAX_GROUP_PAGE_SIZE),
):
    groups, _ = await fetch_group_page(
        db, user_id=current_user.id, offset=offset, limit=limit
    )
    counts = await member_counts(db, [g.id for g in groups])
    roles = dict(
        (
            await db.execute(
                select(GroupMembership.group_id, GroupMembership.role).where(
                    GroupMembership.user_id == current_user.id,
                    GroupMembership.group_id.in_([g.id for g in groups] or [0]),
                )
            )
        ).all()
    )
    return [
        GroupRead(
            id=g.id,
            name=g.name,
            description=g.description,
            kind=g.kind,
            emblem=g.emblem,
            owner_id=g.owner_id,
            created_at=g.created_at,
            member_count=counts.get(g.id, 0),
            my_role=(
                GroupRole.OWNER.value
                if g.owner_id == current_user.id
                else roles.get(g.id)
            ),
        )
        for g in groups
    ]


@router.get("/{group_id}", response_model=GroupRead)
async def get_group(
    group_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    group, membership = await load_group(db, group_id, current_user)
    return await group_read(db, group, current_user.id, membership)


@router.patch("/{group_id}", response_model=GroupRead)
async def update_group(
    group_id: int,
    payload: GroupUpdate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    group, membership = await load_group(db, group_id, current_user)
    require(current_user, Perm.GROUP_EDIT, group, membership)
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(group, field, value)
    group.updated_at = datetime.now(UTC)
    group.last_modifier_user_id = current_user.id
    await db.commit()
    await db.refresh(group)
    return await group_read(db, group, current_user.id, membership)


@router.delete("/{group_id}", status_code=204)
async def delete_group(
    group_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Owner-only, and refused once the group has run anything.

    Memberships, invites and requests cascade -- none of them is history
    anybody logged -- but challenges deliberately do not, so a group with any
    challenge answers **409**. The alternative would destroy every check-in
    its members recorded, which is the exact harm
    ``DELETE /challenges/{id}``'s own 409 exists to prevent; archiving the
    challenges first is the way out, same as there.
    """
    group, membership = await load_group(db, group_id, current_user)
    require(current_user, Perm.GROUP_DELETE, group, membership)
    has_challenge = (
        await db.execute(
            select(Challenge.id).where(Challenge.group_id == group_id).limit(1)
        )
    ).scalar_one_or_none()
    if has_challenge is not None:
        raise HTTPException(
            status_code=409,
            detail="Cannot delete a group that still has challenges",
        )
    await db.delete(group)
    await db.commit()


# ---------------------------------------------------------------------------
# Members
# ---------------------------------------------------------------------------


@router.get("/{group_id}/members", response_model=list[GroupMemberRead])
async def list_group_members(
    group_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    q: str | None = Query(default=None, max_length=100),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=DEFAULT_MEMBER_PAGE_SIZE, ge=1, le=100),
):
    """Who is in this group -- readable by everyone in it.

    A group roster is not the thing ``profile_visibility_filter`` withholds:
    these people are in a room together and know it. What the roster carries
    is deliberately only a name, a picture and a role
    (:class:`GroupMemberRead`), and no row here links to a profile -- being
    an administrator buys reach over the *group*, never over an account.
    """
    group, membership = await load_group(db, group_id, current_user)
    require(current_user, Perm.GROUP_VIEW, group, membership)
    rows, _ = await fetch_group_member_page(
        db, group_id=group_id, q=q, offset=offset, limit=limit
    )
    return member_rows(rows)


@router.patch("/{group_id}/members/{user_id}", response_model=GroupMemberRead)
async def set_member_role(
    group_id: int,
    user_id: int,
    payload: GroupRoleUpdate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Promote a member to admin, or take it back. Owner-only.

    ``GROUP_MANAGE_ADMINS`` is granted to ``owner`` alone -- an administrator
    who could hand out admin is an owner with a delay -- and the schema
    refuses ``owner`` as a target, so this route can never produce a second
    owner contradicting ``Group.owner_id``. Handing the group over is
    ``POST /groups/{id}/transfer``, which moves both records at once.

    The owner's own row is refused rather than silently ignored: an owner
    demoting themselves would leave a group whose ``owner_id`` says one thing
    and whose roster says another, which is the group-shaped version of the
    self-demotion 400 on ``PATCH /users/{id}/role``.
    """
    group, membership = await load_group(db, group_id, current_user)
    require(current_user, Perm.GROUP_MANAGE_ADMINS, group, membership)
    target = await _membership_of(db, group_id, user_id)
    if user_id == group.owner_id:
        raise HTTPException(
            status_code=400, detail="Transfer the group to change its owner"
        )
    if target.role != payload.role.value:
        target.role = payload.role.value
        target.updated_at = datetime.now(UTC)
        target.last_modifier_user_id = current_user.id
        await notify(
            db,
            user_id=user_id,
            kind=NotificationKind.GROUP_ROLE_CHANGED,
            actor_user_id=current_user.id,
            group_id=group_id,
        )
    await db.commit()
    await db.refresh(target)
    user = (
        await db.execute(select(User).where(User.id == user_id))
    ).scalar_one()
    return GroupMemberRead(
        user_id=user_id,
        name=user.name,
        avatar=user.avatar,
        role=target.role,
        joined_at=target.created_at,
    )


# ---------------------------------------------------------------------------
# Adding somebody
# ---------------------------------------------------------------------------


@router.post("/{group_id}/members", response_model=GroupMemberRead, status_code=201)
async def add_member(
    group_id: int,
    payload: MemberAdd,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    timezone: str | None = Query(default=None, max_length=64),
):
    """Put somebody in the group directly -- the third door, and the narrowest.

    The other two are the member's own act: a link they used, or a request
    they sent. This one is the administrator's, for the case both others
    handle badly -- somebody standing in front of you, or somebody who gave
    you their number and will never open a link.

    **Named by credential, never by id.** There is no member search in this
    app and there will not be one here: a route taking a user id, or matching
    a partial name, would hand every group administrator a way to walk the
    membership, which is exactly what ``profile_visibility_filter`` exists to
    prevent. A full mobile number is something the person gave you, and it is
    the identifier they sign in with. The refusal for an unknown number is an
    honest **404**, because an administrator has to be able to tell "no such
    account" from "already in" -- the oracle that costs is one whole number
    per guess, and the alternative is a screen that silently does nothing.
    """
    group, membership = await load_group(db, group_id, current_user)
    require(current_user, Perm.GROUP_MANAGE_MEMBERS, group, membership)

    user = await _user_by_credential(db, payload.identifier)
    if user is None:
        raise HTTPException(
            status_code=404, detail="No account with that number or email"
        )
    existing = (
        await db.execute(
            select(GroupMembership).where(
                GroupMembership.group_id == group_id,
                GroupMembership.user_id == user.id,
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        raise HTTPException(status_code=409, detail="Already a member")

    row = GroupMembership(
        group_id=group_id,
        user_id=user.id,
        role=GroupRole.MEMBER.value,
        last_modifier_user_id=current_user.id,
    )
    db.add(row)
    try:
        await db.flush()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(status_code=409, detail="Already a member") from None

    await apply_standing_audience(
        db,
        group_id=group_id,
        user_id=user.id,
        timezone=resolve_timezone(timezone),
        actor_user_id=current_user.id,
    )
    # Its own kind, not `GROUP_JOIN_APPROVED`: nothing was asked for here, so
    # a sentence about "your request" would describe an event that never
    # happened.
    await notify(
        db,
        user_id=user.id,
        kind=NotificationKind.GROUP_MEMBER_ADDED,
        actor_user_id=current_user.id,
        group_id=group_id,
    )
    await db.commit()
    await db.refresh(row)
    return GroupMemberRead(
        user_id=user.id,
        name=user.name,
        avatar=user.avatar,
        role=row.role,
        joined_at=row.created_at,
    )


@router.delete("/{group_id}/enrollments", response_model=GroupLeaveResult)
async def leave_all_challenges(
    group_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Give up every challenge of this group at once, staying in the group.

    **Your own enrollments and nobody else's.** There is no subject in the
    path and no id in a body: the acting user comes from the session, the
    same rule the whole of ``routers/enrollment.py`` follows, and there is
    deliberately no administrator version of this route. Deciding who is *in*
    a group challenge is running the group and has its own screen; deciding
    to stop is the member's, and an administrator who could give up somebody
    else's enrollments would be destroying what that person logged -- which
    is exactly the harm the 409 on hard-deleting a challenge exists to
    prevent.

    A mandatory challenge is **kept and named back**, not silently skipped:
    ``still_in_group=True``, because that is the truth at this moment -- the
    obligation belongs to the membership, and the member is not giving that
    up here. The answer says which ones stayed so the page can explain the
    difference rather than leave a member wondering why the list is not
    empty; leaving the group itself is the door that releases them, and it is
    one tap away on the same panel.

    Idempotent, like every other bulk write here: a member with nothing to
    give up gets an empty answer and a 200, not a 404.
    """
    group, membership = await load_group(db, group_id, current_user)
    require(current_user, Perm.GROUP_VIEW, group, membership)
    outcome = await leave_group_challenges(
        db, group_id=group.id, user_id=current_user.id, still_in_group=True
    )
    await db.commit()
    return GroupLeaveResult(left=outcome.left, kept=outcome.kept)


@router.delete("/{group_id}/members/{user_id}", status_code=204)
async def remove_member(
    group_id: int,
    user_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Leave the group, or remove somebody from it.

    One route for both, because the membership row and the owner rule are
    the same in either case and splitting them would mean two places that
    have to keep agreeing about who may not vanish. What differs is what
    happens to the enrollments -- see below. Who may do it:

    * **yourself** -- always, except the owner, who must hand the group over
      first (**409**). A group with no owner has nobody who can invite, or
      approve, or delete it, and no route anywhere could put one back.
    * **somebody else** -- needs ``GROUP_MANAGE_MEMBERS``, and an admin may
      only remove plain members. Removing a fellow admin is
      ``GROUP_MANAGE_ADMINS``, i.e. the owner, for the same reason promotion
      is: administrators are not each other's to unmake.

    **Whether the enrollments go depends on who is acting, and the split is
    the point.** Leaving is one act to the person doing it: walking out of a
    group while staying signed up to everything it was running is not a state
    anybody asks for, and a member who had to visit each challenge afterwards
    would be left doing by hand what they already said they wanted. So a
    self-leave runs `leave_group_challenges` in the *same transaction* --
    with ``still_in_group=False``, which is the truth by the end of it, so a
    mandatory challenge releases too: the obligation belonged to the
    membership that is ending, never to the person. That is exactly what the
    confirmation on the group page promises, and one function means the
    promise and the write cannot come apart.

    **Being removed is the opposite, and enrollments are left alone.**
    Destroying what another person logged is not a power running a group
    buys -- the same harm the 409 on hard-deleting a challenge exists to
    prevent -- and `group_scope_filter` lets an existing enrollment through
    so those challenges stay reachable to them afterwards. What ends either
    way is the *obligation*: a mandatory challenge becomes leavable the
    moment you are no longer in the group that set it, so somebody who was
    removed can still walk away from each one, having been given the choice
    rather than had it made for them.
    """
    group, membership = await load_group(db, group_id, current_user)
    target = await _membership_of(db, group_id, user_id)

    if user_id == group.owner_id:
        raise HTTPException(
            status_code=409,
            detail="Transfer the group before leaving it",
        )
    if user_id != current_user.id:
        needed = (
            Perm.GROUP_MANAGE_ADMINS
            if target.role == GroupRole.ADMIN.value
            else Perm.GROUP_MANAGE_MEMBERS
        )
        require(current_user, needed, group, membership)
    else:
        await leave_group_challenges(
            db, group_id=group.id, user_id=user_id, still_in_group=False
        )

    await db.delete(target)
    await db.commit()


@router.post("/{group_id}/transfer", response_model=GroupRead)
async def transfer_group(
    group_id: int,
    payload: GroupTransfer,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Hand the group to another member. Owner-only.

    Both records move in one transaction -- ``Group.owner_id`` and the two
    membership rows -- because ``group_role`` resolves ``owner_id`` first,
    so a half-applied transfer would leave the previous owner still holding
    everything while the roster claimed otherwise. The outgoing owner becomes
    an ``admin`` rather than a plain member: they were running the place a
    moment ago, and dropping them to the bottom is a demotion nobody asked
    for -- the new owner can take it away.
    """
    group, membership = await load_group(db, group_id, current_user)
    require(current_user, Perm.GROUP_TRANSFER, group, membership)
    if payload.new_owner_user_id == group.owner_id:
        raise HTTPException(status_code=400, detail="Already the owner")
    target = await _membership_of(db, group_id, payload.new_owner_user_id)

    previous = await _membership_of(db, group_id, group.owner_id)
    previous.role = GroupRole.ADMIN.value
    previous.last_modifier_user_id = current_user.id
    target.role = GroupRole.OWNER.value
    target.last_modifier_user_id = current_user.id
    group.owner_id = target.user_id
    group.updated_at = datetime.now(UTC)
    group.last_modifier_user_id = current_user.id

    await notify(
        db,
        user_id=target.user_id,
        kind=NotificationKind.GROUP_OWNERSHIP_TRANSFERRED,
        actor_user_id=current_user.id,
        group_id=group_id,
    )
    await db.commit()
    await db.refresh(group)
    return await group_read(db, group, current_user.id, previous)
