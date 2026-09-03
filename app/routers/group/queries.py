# app/routers/group/queries.py
"""Shared queries and helpers for the group subsystem.

Nothing here is a route. This is the "query logic in the API router,
presentation in views/" layer CLAUDE.md describes for challenges and users,
reused by ``members.py``/``invites.py``/``participants.py`` in this package
and by the SSR views in ``app/routers/views/group.py`` and
``app/routers/views/challenge.py``. It must not import from those route
modules -- the dependency runs one way.
"""

from datetime import UTC, datetime

from fastapi import HTTPException
from sqlalchemy import case, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.groups import (
    group_visibility_filter,
    invite_state,
    member_counts,
    standing_in,
)
from app.models.audit_base import newest_first
from app.models.challenge import Challenge
from app.models.group import (
    Group,
    GroupInvite,
    GroupJoinRequest,
    GroupMembership,
    GroupRole,
    JoinRequestStatus,
)
from app.models.user import User
from app.permissions import EffectiveMembership, can, group_role
from app.phone import normalize_mobile
from app.routers.user import DEFAULT_MEMBER_PAGE_SIZE, apply_member_filters
from app.schemas.group import GroupMemberRead, GroupRead, InviteRead

DEFAULT_GROUP_PAGE_SIZE = 20
MAX_GROUP_PAGE_SIZE = 50


# ---------------------------------------------------------------------------
# Loading, and the permission gate
# ---------------------------------------------------------------------------


async def load_group(
    db: AsyncSession, group_id: int, user: User
) -> tuple[Group, EffectiveMembership | None]:
    """The group and the caller's standing in it, or **404**.

    Both come back together because every caller needs both: the group to act
    on, and the standing ``can()`` resolves the role from. Resolving the
    standing separately is how a route ends up asking ``can()`` with
    ``membership=None`` and quietly denying an administrator.

    The standing is :func:`standing_in`, not a row -- with nested groups the
    row that answers "what may you do here" can be one on a group this one
    sits inside.

    The owner is admitted even with no membership row. That cannot happen
    through any route here -- an owner may not leave -- but ``group_role``
    resolves ``owner_id`` first for exactly this reason, and a gate that
    disagreed with it would be a gate that locks the owner out of their own
    group after a hand-edited database.
    """
    group = (
        await db.execute(
            select(Group).where(
                Group.id == group_id,
                or_(group_visibility_filter(user.id), Group.owner_id == user.id),
            )
        )
    ).scalar_one_or_none()
    if group is None:
        raise HTTPException(status_code=404, detail="Group not found")
    return group, await standing_in(db, group, user.id)


def require(
    user: User,
    permission: str,
    group: Group,
    membership: EffectiveMembership | None,
    detail: str = "Not allowed in this group",
) -> None:
    """403 unless the caller holds ``permission`` here. See the module note
    on why this is a 403 and the group itself is a 404."""
    if not can(user, permission, group=group, membership=membership):
        raise HTTPException(status_code=403, detail=detail)


async def group_read(
    db: AsyncSession,
    group: Group,
    viewer_id: int,
    membership: EffectiveMembership | None = None,
) -> GroupRead:
    counts = await member_counts(db, [group.id])
    parent_name = None
    if group.parent_id is not None:
        parent_name = (
            await db.execute(select(Group.name).where(Group.id == group.parent_id))
        ).scalar_one_or_none()
    child_count = (
        await db.execute(
            select(func.count())
            .select_from(Group)
            .where(Group.parent_id == group.id)
        )
    ).scalar_one()
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
        parent_id=group.parent_id,
        parent_name=parent_name,
        child_count=child_count,
    )


async def parent_names(db: AsyncSession, groups: list[Group]) -> dict[int, str]:
    """``{group id: the name of the group it sits inside}``, in one query.

    The list of a member's groups is flat -- it has to be, because somebody
    can be in two organisations that know nothing about each other -- so a
    subgroup that did not say where it belongs would read as a second
    top-level group with a confusingly narrow name. One read for the page,
    like ``member_counts``, not one per card.
    """
    wanted = {g.parent_id for g in groups if g.parent_id is not None}
    if not wanted:
        return {}
    names = dict(
        (
            await db.execute(
                select(Group.id, Group.name).where(Group.id.in_(wanted))
            )
        ).all()
    )
    return {
        g.id: names[g.parent_id]
        for g in groups
        if g.parent_id in names
    }


async def fetch_child_groups(db: AsyncSession, group_id: int) -> list[Group]:
    """The groups directly inside this one, newest first.

    Direct children only, and unpaged: a group's structure is a handful of
    rows a reader takes in at once, and anything below the children is read
    on the child's own page -- a whole tree flattened onto one screen is a
    picture of the organisation, not a way to get anywhere in it.
    """
    return list(
        (
            await db.execute(
                select(Group)
                .where(Group.parent_id == group_id)
                .order_by(*newest_first(Group))
            )
        )
        .scalars()
        .all()
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

    **Oldest first, always.** Pending rows are a *queue* -- the person who
    has been waiting longest is the one an administrator owes an answer to,
    so this is the one list in the app that deliberately does not read
    newest-first. The manage page only ever renders the pending state now;
    the id tie-break still applies for the reason every paginated list in
    this app has one -- ``server_default=now()`` has second granularity on
    SQLite, so a burst of rows shares a timestamp and an offset-paged
    scroller over a non-total order drops one row and repeats another.
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
        .order_by(GroupJoinRequest.created_at.asc(), GroupJoinRequest.id.asc())
        .offset(offset)
        .limit(limit + 1)
    )
    rows = list((await db.execute(stmt)).scalars().all())
    return rows[:limit], len(rows) > limit


async def count_pending_requests(db: AsyncSession, group_id: int) -> int:
    """How many requests are still waiting -- a count, not the rows.

    The two surfaces that ask want different things (the group page wants a
    badge, the manage row wants a figure beside a link), and neither wants
    the people, which now live one screen further in.
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


PUBLIC_INVITE_LABEL = "لینک عمومی گروه"


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
