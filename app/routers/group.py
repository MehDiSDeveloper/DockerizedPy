"""The group subsystem's JSON door, and the queries the SSR pages reuse.

Three rules from the rest of the app decide almost everything in this file,
and they are worth naming because between them they *are* the access control:

1. **Reachability is a composed clause.** Every statement that touches a
   group goes through :func:`load_group`, which selects the row with
   ``group_visibility_filter`` already in the ``WHERE``. A group you are not
   in is not a 403, it is a **404** -- the third such filter in the app,
   following ``challenge_visibility_filter`` and
   ``profile_visibility_filter`` (CLAUDE.md).
2. **Callers ask for a permission, never a role.** ``can(user, Perm.GROUP_*,
   group=…, membership=…)``. Which of ``member``/``admin``/``owner`` may do
   what is data in ``app/permissions.py``, so a fourth group role would be a
   row in a map rather than a sweep through this file.
3. **The acting user comes from the session.** There is no client-supplied
   actor anywhere here. The one place a *subject* is named in a path -- the
   two member routes -- is guarded by a permission and by the rules below,
   never by trusting the id.

**404 vs 403, inside a group.** Once you are in, the group is not a secret
from you, so refusing an action you may not take is an honest 403 and leaks
nothing: everybody in a group can already see who administers it. The 404 is
reserved for the group itself, and for one other case -- a member id that is
not in this group -- because there the id is a bare sequential integer and a
403 would confirm the account exists.

**What an operator cannot do here.** Nothing in this module consults
``User.role``. An app-wide admin moderates *challenges*, which are published
content; a group is somebody's organisation, and an operator who could
administer it would be indistinguishable from the person who runs it. That is
the same line the module docstring of ``app/permissions.py`` draws twice
already, and ``tests/test_groups.py`` pins it.
"""

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import and_, case, func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.auth import get_current_user
from app.database import get_db
from app.groups import (
    INVITE_ACTIVE,
    apply_standing_audience,
    assign_participants,
    group_visibility_filter,
    invite_state,
    leave_group_challenges,
    member_counts,
    new_invite_code,
)
from app.models.audit_base import newest_first
from app.models.challenge import Challenge
from app.models.enrollment import Enrollment
from app.models.group import (
    Group,
    GroupInvite,
    GroupJoinRequest,
    GroupMembership,
    GroupRole,
    JoinRequestStatus,
)
from app.models.notification import NotificationKind
from app.models.stats import ChallengeStats
from app.models.user import User
from app.notifications import notify, notify_many
from app.permissions import Perm, can, group_role
from app.phone import normalize_mobile
from app.routers.challenge import resolve_timezone
from app.routers.user import DEFAULT_MEMBER_PAGE_SIZE, apply_member_filters
from app.schemas.group import (
    GroupCreate,
    GroupLeaveResult,
    GroupMemberRead,
    GroupRead,
    GroupRoleUpdate,
    GroupTransfer,
    GroupUpdate,
    InviteCreate,
    InvitePreview,
    InviteRead,
    JoinRequestRead,
    MemberAdd,
    ParticipantsAdd,
)

router = APIRouter(prefix="/groups", tags=["groups"])

# The invite landing lives on its own prefix rather than under `/groups/`,
# because the whole point of a code is that the person holding it does not
# know -- and must not need to know -- the group's id.
invite_router = APIRouter(prefix="/invites", tags=["groups"])

DEFAULT_GROUP_PAGE_SIZE = 20
MAX_GROUP_PAGE_SIZE = 50


# ---------------------------------------------------------------------------
# Loading, and the permission gate
# ---------------------------------------------------------------------------


async def load_group(
    db: AsyncSession, group_id: int, user: User
) -> tuple[Group, GroupMembership | None]:
    """The group and the caller's own membership in it, or **404**.

    Both come back on one read because every caller needs both: the group to
    act on, and the membership ``can()`` resolves the role from. Fetching the
    membership separately is how a route ends up asking ``can()`` with
    ``membership=None`` and quietly denying an administrator.

    The owner is admitted even with no membership row. That cannot happen
    through any route here -- an owner may not leave -- but ``group_role``
    resolves ``owner_id`` first for exactly this reason, and a gate that
    disagreed with it would be a gate that locks the owner out of their own
    group after a hand-edited database.
    """
    row = (
        await db.execute(
            select(Group, GroupMembership)
            .outerjoin(
                GroupMembership,
                and_(
                    GroupMembership.group_id == Group.id,
                    GroupMembership.user_id == user.id,
                ),
            )
            .where(
                Group.id == group_id,
                or_(group_visibility_filter(user.id), Group.owner_id == user.id),
            )
        )
    ).one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="Group not found")
    return row[0], row[1]


def require(
    user: User,
    permission: str,
    group: Group,
    membership: GroupMembership | None,
    detail: str = "Not allowed in this group",
) -> None:
    """403 unless the caller holds ``permission`` here. See the module note
    on why this is a 403 and the group itself is a 404."""
    if not can(user, permission, group=group, membership=membership):
        raise HTTPException(status_code=403, detail=detail)


async def group_read(
    db: AsyncSession, group: Group, viewer_id: int, membership: GroupMembership | None
) -> GroupRead:
    counts = await member_counts(db, [group.id])
    return GroupRead(
        id=group.id,
        name=group.name,
        description=group.description,
        kind=group.kind,
        emblem=group.emblem,
        owner_id=group.owner_id,
        created_at=group.created_at,
        member_count=counts.get(group.id, 0),
        my_role=group_role(viewer_id, group=group, membership=membership),
    )


# ---------------------------------------------------------------------------
# Queries the SSR pages share
# ---------------------------------------------------------------------------


async def fetch_group_page(
    db: AsyncSession,
    *,
    user_id: int,
    offset: int = 0,
    limit: int = DEFAULT_GROUP_PAGE_SIZE,
) -> tuple[list[Group], bool]:
    """One page of *this member's* groups, plus whether more remain.

    There is no "all groups" version of this query anywhere in the app and
    that is deliberate: groups are not public and not searchable, so the only
    listing that exists is your own. ``limit + 1`` and ``newest_first`` like
    every other paginated list.
    """
    stmt = (
        select(Group)
        .where(group_visibility_filter(user_id))
        .order_by(*newest_first(Group))
        .offset(offset)
        .limit(limit + 1)
    )
    groups = list((await db.execute(stmt)).scalars().all())
    return groups[:limit], len(groups) > limit


async def fetch_group_member_page(
    db: AsyncSession,
    *,
    group_id: int,
    q: str | None = None,
    trusted: bool | None = None,
    offset: int = 0,
    limit: int = DEFAULT_MEMBER_PAGE_SIZE,
) -> tuple[list[GroupMembership], bool]:
    """One page of a group's roster.

    Search is ``apply_member_filters`` reused rather than rewritten, so
    "search spans name and email" stays one rule across the admin roster, the
    challenge participant roster and this one.

    Ordered by role first, then ``newest_first``: a roster is read to find
    who runs the place at least as often as to find who joined last, and the
    id tie-break underneath keeps an offset-paged scroller from dropping a
    row inside a block of memberships written in the same second.
    """
    stmt = (
        select(GroupMembership)
        .join(User, User.id == GroupMembership.user_id)
        .where(GroupMembership.group_id == group_id)
    )
    if trusted is not None:
        # One parameter on the shared query rather than a second query
        # builder for the approval queue, the same call `scope_all` makes on
        # `fetch_challenge_page`: the queue and the roster can then never
        # disagree about who is in this group.
        stmt = stmt.where(GroupMembership.is_trusted.is_(trusted))
    stmt = apply_member_filters(stmt, q, None)
    stmt = (
        stmt.order_by(
            _role_rank(), *newest_first(GroupMembership)
        )
        .offset(offset)
        .limit(limit + 1)
        .options(selectinload(GroupMembership.user))
    )
    rows = list((await db.execute(stmt)).scalars().all())
    return rows[:limit], len(rows) > limit


def _role_rank():
    """Owner, then admins, then everyone else -- as SQL, so it survives paging.

    Sorting the loaded page in Python would put the owner at the top of
    *each* page rather than at the top of the roster, which is the classic
    way a sorted list and a scroller disagree.
    """
    return case(
        (GroupMembership.role == GroupRole.OWNER.value, 0),
        (GroupMembership.role == GroupRole.ADMIN.value, 1),
        else_=2,
    )


def member_rows(memberships: list[GroupMembership]) -> list[GroupMemberRead]:
    return [
        GroupMemberRead(
            user_id=m.user_id,
            name=m.user.name if m.user else "—",
            avatar=m.user.avatar if m.user else None,
            role=m.role,
            joined_at=m.created_at,
            is_trusted=bool(m.is_trusted),
        )
        for m in memberships
    ]


DEFAULT_REQUEST_PAGE_SIZE = 20
MAX_REQUEST_PAGE_SIZE = 50

# The three states a request can be read in. Anything else is refused by the
# route's own pattern rather than silently falling back to the queue -- the
# same call `?sort=` makes on the leaderboard, and for the same reason: a
# filter that quietly means something other than what the URL says is a
# filter nobody can share.
REQUEST_STATES = (
    JoinRequestStatus.PENDING.value,
    JoinRequestStatus.APPROVED.value,
    JoinRequestStatus.REJECTED.value,
)


async def fetch_request_page(
    db: AsyncSession,
    *,
    group_id: int,
    status: str = JoinRequestStatus.PENDING.value,
    offset: int = 0,
    limit: int = DEFAULT_REQUEST_PAGE_SIZE,
) -> tuple[list[GroupJoinRequest], bool]:
    """One page of this group's requests in one state, plus "is there more".

    **The order depends on the state, and that is the point.** Pending rows
    are a *queue* -- oldest first, because the person who has been waiting
    longest is the one an administrator owes an answer to -- and this is the
    one list in the app that deliberately does not read newest-first. A
    decided row is the opposite: it is history, so it reads newest-first off
    ``decided_at``, which is when the thing the reader is looking for
    actually happened (``created_at`` would order two answers given today by
    when they were *asked*). Both fall through to an id tie-break, for the
    reason every paginated list in this app does -- ``server_default=now()``
    has second granularity on SQLite, so a burst of rows shares a timestamp
    and an offset-paged scroller over a non-total order drops one row and
    repeats another.
    """
    stmt = (
        select(GroupJoinRequest)
        .where(
            GroupJoinRequest.group_id == group_id,
            GroupJoinRequest.status == status,
        )
        .options(
            selectinload(GroupJoinRequest.user),
            selectinload(GroupJoinRequest.decided_by),
        )
        .offset(offset)
        .limit(limit + 1)
    )
    if status == JoinRequestStatus.PENDING.value:
        stmt = stmt.order_by(
            GroupJoinRequest.created_at.asc(), GroupJoinRequest.id.asc()
        )
    else:
        stmt = stmt.order_by(
            GroupJoinRequest.decided_at.desc(), GroupJoinRequest.id.desc()
        )
    rows = list((await db.execute(stmt)).scalars().all())
    return rows[:limit], len(rows) > limit


async def count_pending_requests(db: AsyncSession, group_id: int) -> int:
    """How many requests are still waiting -- a count, not the rows.

    The two surfaces that ask want different things (the group page wants a
    badge, the manage row wants a figure beside a link), and neither wants
    the people, which now live one screen further in. Same split as
    :func:`count_awaiting_trust`.
    """
    return (
        await db.execute(
            select(func.count())
            .select_from(GroupJoinRequest)
            .where(
                GroupJoinRequest.group_id == group_id,
                GroupJoinRequest.status == JoinRequestStatus.PENDING.value,
            )
        )
    ).scalar_one()


async def count_awaiting_trust(db: AsyncSession, group_id: int) -> int:
    """How many members are waiting on the second approval.

    A count rather than the rows, because the two surfaces that ask want
    different things: the group page wants a badge, the manage page wants the
    people. One query each, sharing the one predicate that defines the
    queue -- ``is_trusted`` false -- rather than a list that has to be
    counted in two templates.
    """
    return (
        await db.execute(
            select(func.count())
            .select_from(GroupMembership)
            .where(
                GroupMembership.group_id == group_id,
                GroupMembership.is_trusted.is_(False),
            )
        )
    ).scalar_one()


async def group_invites(db: AsyncSession, group_id: int) -> list[GroupInvite]:
    return list(
        (
            await db.execute(
                select(GroupInvite)
                .where(GroupInvite.group_id == group_id)
                .order_by(*newest_first(GroupInvite))
            )
        )
        .scalars()
        .all()
    )


def invite_rows(invites: list[GroupInvite]) -> list[InviteRead]:
    now = datetime.now(UTC)
    return [
        InviteRead(
            id=i.id,
            code=i.code,
            label=i.label,
            max_uses=i.max_uses,
            uses=i.uses or 0,
            expires_at=i.expires_at,
            revoked_at=i.revoked_at,
            created_at=i.created_at,
            requires_approval=bool(i.requires_approval),
            state=invite_state(i, now),
        )
        for i in invites
    ]


async def group_admin_ids(db: AsyncSession, group: Group) -> list[int]:
    """Who to tell when something needs a decision.

    The owner plus every admin, resolved from the *rows* rather than assumed
    from ``owner_id`` alone, and de-duplicated by ``notify_many``.
    """
    ids = list(
        (
            await db.execute(
                select(GroupMembership.user_id).where(
                    GroupMembership.group_id == group.id,
                    GroupMembership.role.in_(
                        [GroupRole.OWNER.value, GroupRole.ADMIN.value]
                    ),
                )
            )
        )
        .scalars()
        .all()
    )
    return [group.owner_id, *ids]


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
            # The owner is approved for the group's own challenges by
            # construction: `is_trusted` is a decision *somebody* makes about
            # a new arrival, and there is nobody above the person who opened
            # the group to make it. Every administrator is trusted for the
            # same reason -- see `set_member_role`.
            is_trusted=True,
            trusted_at=datetime.now(UTC),
            trusted_by_user_id=current_user.id,
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


async def _membership_of(
    db: AsyncSession, group_id: int, user_id: int
) -> GroupMembership:
    row = (
        await db.execute(
            select(GroupMembership).where(
                GroupMembership.group_id == group_id,
                GroupMembership.user_id == user_id,
            )
        )
    ).scalar_one_or_none()
    if row is None:
        # 404 and not 403: the subject is a bare sequential user id, and a
        # 403 here would confirm which accounts exist.
        raise HTTPException(status_code=404, detail="Member not found")
    return row


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
        if payload.role is GroupRole.ADMIN and not target.is_trusted:
            # An administrator waiting to be approved for the group's own
            # challenges is a contradiction: they may put *other* people into
            # them. Promoting is the stronger statement, so it carries the
            # weaker one with it rather than leaving a row that has to be
            # approved by somebody it outranks.
            _grant_trust(target, current_user.id)
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
        is_trusted=bool(target.is_trusted),
    )


# ---------------------------------------------------------------------------
# Adding somebody, and approving them for the group's own challenges
# ---------------------------------------------------------------------------


def _grant_trust(membership: GroupMembership, actor_user_id: int) -> None:
    """Stamp the approval. Who and when, not just whether.

    The bare boolean would answer the access question on its own; the two
    stamps beside it answer the one support actually gets, which is *who let
    this person into the company's challenges* -- the same reason
    ``GroupJoinRequest.decided_by_user_id`` outlives the decision.
    """
    membership.is_trusted = True
    membership.trusted_at = datetime.now(UTC)
    membership.trusted_by_user_id = actor_user_id


async def _user_by_credential(db: AsyncSession, identifier: str) -> User | None:
    """The account an administrator named, by mobile or by email.

    Both, because the app has two kinds of account: one that signs in with a
    number and one with an email and a password. The number goes through
    ``normalize_mobile`` rather than being compared as typed -- ``0912…``,
    ``+98912…`` and ``۰۹۱۲…`` are one account, and a raw string comparison
    would fail to find somebody who is plainly there (``app/phone.py``).
    """
    mobile = normalize_mobile(identifier)
    if mobile:
        return (
            await db.execute(select(User).where(User.mobile == mobile))
        ).scalar_one_or_none()
    email = identifier.strip().lower()
    if "@" not in email:
        return None
    return (
        await db.execute(select(User).where(func.lower(User.email) == email))
    ).scalar_one_or_none()


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

    A new member arrives **untrusted** -- see ``GroupMembership.is_trusted``.
    ``trusted: true`` is the administrator saying both things at once, which
    is the common case for somebody they added by hand; the default keeps the
    two decisions two decisions.
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
    if payload.trusted:
        _grant_trust(row, current_user.id)
    db.add(row)
    try:
        await db.flush()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(status_code=409, detail="Already a member") from None

    # Only does anything when the approval came with them: the gate is inside
    # `apply_standing_audience`, so this call is unconditional and the rule
    # stays in one place.
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
        is_trusted=bool(row.is_trusted),
    )


@router.post("/{group_id}/members/{user_id}/trust", response_model=GroupMemberRead)
async def trust_member(
    group_id: int,
    user_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    timezone: str | None = Query(default=None, max_length=64),
):
    """The second approval: let this member into the group's own challenges.

    Being in the group and being swept into what the group asks of everyone
    are two answers given at two moments (``GroupMembership.is_trusted``), and
    this is the second. It is not a role and not a permission of its own: it
    changes nothing about what the member may *do*, only what they are put
    *into*, so it rides on ``GROUP_MANAGE_MEMBERS`` -- deciding who is in the
    group's challenges is the same job as deciding who is in the group.

    Approving is **retroactive on purpose**: the standing «همه اعضا»
    challenges are applied here and now, through the same
    ``apply_standing_audience`` an arrival goes through, so somebody approved
    a week after they joined ends up with exactly what somebody approved on
    the day has. Idempotent -- approving twice is the same request as
    approving once, and ``assign_participants`` enrols nobody twice.
    """
    group, membership = await load_group(db, group_id, current_user)
    require(current_user, Perm.GROUP_MANAGE_MEMBERS, group, membership)
    target = await _membership_of(db, group_id, user_id)
    if not target.is_trusted:
        _grant_trust(target, current_user.id)
        target.updated_at = datetime.now(UTC)
        target.last_modifier_user_id = current_user.id
        await db.flush()
        await apply_standing_audience(
            db,
            group_id=group_id,
            user_id=user_id,
            timezone=resolve_timezone(timezone),
            actor_user_id=current_user.id,
        )
        await notify(
            db,
            user_id=user_id,
            kind=NotificationKind.GROUP_MEMBER_TRUSTED,
            actor_user_id=current_user.id,
            group_id=group_id,
        )
    await db.commit()
    await db.refresh(target)
    user = (await db.execute(select(User).where(User.id == user_id))).scalar_one()
    return GroupMemberRead(
        user_id=user_id,
        name=user.name,
        avatar=user.avatar,
        role=target.role,
        joined_at=target.created_at,
        is_trusted=bool(target.is_trusted),
    )


@router.delete("/{group_id}/members/{user_id}/trust", response_model=GroupMemberRead)
async def untrust_member(
    group_id: int,
    user_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Take the approval back. **It touches no existing enrollment.**

    Exactly the call ``remove_member`` makes: what somebody already logged is
    theirs, and destroying it is not a power running a group buys. So this
    decides the *future* only -- they stop being swept into the next «همه
    اعضا» challenge -- and ending one enrollment is still the participants
    screen, one challenge at a time, where the cost is visible.

    The owner is refused: there would be nobody who could approve them again.
    """
    group, membership = await load_group(db, group_id, current_user)
    require(current_user, Perm.GROUP_MANAGE_MEMBERS, group, membership)
    if user_id == group.owner_id:
        raise HTTPException(status_code=400, detail="The owner is always approved")
    target = await _membership_of(db, group_id, user_id)
    if target.is_trusted:
        target.is_trusted = False
        target.trusted_at = None
        target.trusted_by_user_id = None
        target.updated_at = datetime.now(UTC)
        target.last_modifier_user_id = current_user.id
    await db.commit()
    await db.refresh(target)
    user = (await db.execute(select(User).where(User.id == user_id))).scalar_one()
    return GroupMemberRead(
        user_id=user_id,
        name=user.name,
        avatar=user.avatar,
        role=target.role,
        joined_at=target.created_at,
        is_trusted=bool(target.is_trusted),
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


PUBLIC_INVITE_LABEL = "لینک عمومی گروه"


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


async def invite_by_code(db: AsyncSession, code: str) -> GroupInvite:
    invite = (
        await db.execute(
            select(GroupInvite)
            .where(GroupInvite.code == code)
            .options(selectinload(GroupInvite.group))
        )
    ).scalar_one_or_none()
    if invite is None:
        raise HTTPException(status_code=404, detail="Invite not found")
    return invite


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


# ---------------------------------------------------------------------------
# Who is in one group challenge
# ---------------------------------------------------------------------------


async def _group_challenge(
    db: AsyncSession, group: Group, challenge_id: int
) -> Challenge:
    challenge = (
        await db.execute(
            select(Challenge).where(
                Challenge.id == challenge_id, Challenge.group_id == group.id
            )
        )
    ).scalar_one_or_none()
    if challenge is None:
        raise HTTPException(status_code=404, detail="Challenge not found")
    return challenge


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
