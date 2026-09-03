"""Fixtures the group tests share.

A module rather than a growing ``conftest.py``: these builders are about one
subsystem, and the rest of the suite has no use for them.
"""

from __future__ import annotations

from datetime import date

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import create_session_cookie, hash_password
from app.models.challenge import (
    Challenge,
    ChallengeCategory,
    GroupAudience,
    IdentityMode,
    ParticipationMode,
)
from app.models.enrollment import ChallengeRole, Enrollment
from app.models.group import Group, GroupMembership, GroupRole
from app.models.stats import ChallengeStats
from app.models.user import User, UserRole

_seq = 0


async def make_user(
    db: AsyncSession, name: str, *, role: str = UserRole.MEMBER.value
) -> User:
    global _seq
    _seq += 1
    user = User(
        name=name,
        email=f"{name.lower().replace(' ', '.')}.{_seq}@example.com",
        password_hash=hash_password("password123"),
        role=role,
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


def sign_in(client: AsyncClient, user: User) -> None:
    client.cookies.set("session", create_session_cookie(user.id))


async def make_group(
    db: AsyncSession,
    owner: User,
    *,
    name: str = "شرکت آبی",
    parent: Group | None = None,
) -> Group:
    """A group with its owner's membership row written, exactly as
    ``POST /groups/`` writes it -- both records, because ``group_role``
    resolves the two together."""
    group = Group(
        name=name,
        kind="company",
        owner_id=owner.id,
        parent_id=parent.id if parent else None,
    )
    db.add(group)
    await db.flush()
    db.add(
        GroupMembership(
            group_id=group.id,
            user_id=owner.id,
            role=GroupRole.OWNER.value,
        )
    )
    await db.commit()
    await db.refresh(group)
    return group


async def add_member(
    db: AsyncSession,
    group: Group,
    user: User,
    *,
    role: str = GroupRole.MEMBER.value,
) -> GroupMembership:
    """An established member of the group."""
    membership = GroupMembership(group_id=group.id, user_id=user.id, role=role)
    db.add(membership)
    await db.commit()
    await db.refresh(membership)
    return membership


async def make_challenge(
    db: AsyncSession,
    owner: User,
    *,
    group: Group | None = None,
    title: str = "چالش",
    visibility: str = "public",
    identity_mode: str = IdentityMode.NAMED.value,
    audience: str = GroupAudience.SELECTED.value,
    participation: str = ParticipationMode.OPTIONAL.value,
) -> Challenge:
    challenge = Challenge(
        title=title,
        owner_id=owner.id,
        category=ChallengeCategory.OTHER,
        cadence_kind="once",
        cadence={"kind": "once"},
        visibility=visibility,
        lifecycle_status="active",
        identity_mode=identity_mode,
        group_id=group.id if group else None,
        group_audience=audience,
        participation_mode=participation,
    )
    db.add(challenge)
    await db.flush()
    db.add(
        Enrollment(
            challenge_id=challenge.id,
            user_id=owner.id,
            start_date=date(2026, 1, 1),
            role=ChallengeRole.OWNER.value,
        )
    )
    db.add(ChallengeStats(challenge_id=challenge.id, participant_count=1))
    await db.commit()
    await db.refresh(challenge)
    return challenge
