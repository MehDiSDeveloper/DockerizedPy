"""Group membership as a *predicate*, and the two writes that depend on it.

The shape of this module is the shape of ``app/identity.py`` and
``app/permissions.py``: domain logic both front doors read, with no router
and no template in it, so that the rules below are stated once and imported
rather than restated at each call site.

**Membership is a gate composed into the query, never a check after the
load.** :func:`group_scope_filter` is one extra conjunct on the two challenge
visibility filters, and that is the *whole* mechanism by which a group's
challenges exist for its members and do not exist for anybody else. It is a
conjunct rather than a replacement on purpose, and the consequence is worth
spelling out because it is what the feature was asked to do: inside a group
the app's existing three-way rule (public / mine / enrolled) still applies
untouched, so a group challenge marked ``public`` is public *to the group*
and one marked ``private`` is visible only to the people actually in it. One
rule, scoped -- not a second rule that has to be kept in agreement with the
first.

**The gate lets an existing enrollment through.** Somebody who has left the
group, or was removed from it, keeps the challenges they were already
enrolled in -- they still have logged history there, the app never destroys
that (the 409 on hard-deleting a challenge is the same principle), and a
member who could neither see nor leave a challenge they are still enrolled
in would be stuck. It leaks nothing: they were in the room, and the rows are
theirs. Somebody who was *never* in the group has no enrollment and no
membership, so for them a group challenge does not exist in any listing, at
any URL, through either front door.

**Leaving is the third half, and it is the member's own act.** The gate
above is about somebody an administrator *removed*: their enrollments are
left alone, because destroying what another person logged is not a power
running a group buys. Somebody who walks out themselves is the opposite
case -- :func:`leave_group_challenges` gives up every enrollment the group
holds, once from the group page («انصراف از همهٔ چالش‌ها») and once as part
of leaving the group, where it is what the confirmation promises. It is one
function so the two cannot come to mean different things, and it is the same
act ``DELETE /enrollments/{id}`` performs, repeated -- every owner still
hears about the departure, every counter still comes down.

**Assignment is the other half.** A group challenge is not joined, it is
*handed out*: :func:`assign_participants` writes the enrollments an
administrator picked, and :func:`apply_standing_audience` re-reads the
``audience = all`` challenges when somebody new arrives, so "for everyone"
keeps meaning everyone. Both go through :func:`resolve_assigned_anonymity`
rather than :func:`resolve_anonymity`, because somebody who was put into a
challenge was never asked -- see ``app/identity.py``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from functools import lru_cache

from sqlalchemy import func, or_, select, union
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.identity import resolve_assigned_anonymity

# An invite link means the same thing here and on a roadmap -- an unguessable
# code, a real capacity, a derived state -- so the rules live in `app/invites.py`
# and are re-exported from here for the call sites (and the tests) that have
# always imported them from this module. One definition, two subsystems.
from app.invites import (  # noqa: F401  -- re-exported, see above
    INVITE_ACTIVE,
    INVITE_CODE_BYTES,
    INVITE_EXPIRED,
    INVITE_FULL,
    INVITE_REVOKED,
    INVITE_STATE_LABELS,
    consume_seat,
    invite_state,
    new_invite_code,
    register_invite_filters,
)
from app.models.challenge import (
    Challenge,
    GroupAudience,
    ParticipationMode,
)
from app.models.enrollment import Enrollment
from app.models.group import (
    Group,
    GroupKind,
    GroupMembership,
    GroupRole,
)
from app.models.notification import NotificationKind
from app.models.stats import ChallengeStats
from app.notifications import membership_is_announced, notify, notify_many
from app.occurrences import local_today
from app.permissions import GROUP_ROLE_RANK, EffectiveMembership, inherited_role

# ---------------------------------------------------------------------------
# Membership as SQL
# ---------------------------------------------------------------------------


# How deep the tree may go, counted in groups: a top-level group is 1, so
# ``A > B > D`` is 3 and the cap allows one more level under that. A limit and
# not "unbounded" because every ancestry walk below is bounded by it in
# practice, and because an organisation that needs a fifth level needs a
# second group, not a deeper one.
MAX_GROUP_DEPTH = 4


@lru_cache(maxsize=1)
def group_tree():
    """Every (group, one of its ancestors-or-itself) pair, as a recursive CTE.

    **One CTE answers both directions**, which is why nesting costs the rest
    of this module so little: read it by ``id`` and it gives a group's own
    ancestry; read it by ``ancestor_id`` and it gives a group's whole subtree.
    Parameterless on purpose, and built **once**: the whole tree is the
    answer, and the seed is a ``WHERE`` on top of it. Two differently-seeded
    CTEs of the same name in one statement is a compile error in SQLAlchemy,
    and two *named differently* would be the same walk written twice -- so
    there is one object, shared by every caller and composed into as many
    statements as ask for it.

    Termination rests on ``Group.parent_id`` being create-only and pointing at
    a row that already exists: no group can become its own ancestor, so the
    walk always reaches a NULL parent.
    """
    base = (
        select(Group.id.label("id"), Group.id.label("ancestor_id"))
        .cte("group_tree", recursive=True)
    )
    child = aliased(Group)
    return base.union_all(
        select(child.id, base.c.ancestor_id).join(
            base, base.c.id == child.parent_id
        )
    )


def ancestor_ids(group_ids):
    """These groups and everything they sit inside."""
    tree = group_tree()
    return select(tree.c.ancestor_id).where(tree.c.id.in_(group_ids))


def descendant_ids(group_ids):
    """These groups and everything inside them."""
    tree = group_tree()
    return select(tree.c.id).where(tree.c.ancestor_id.in_(group_ids))


async def group_depth(db: AsyncSession, group_id: int) -> int:
    """How many groups are in this one's ancestry, itself included."""
    tree = group_tree()
    return (
        await db.execute(
            select(func.count()).select_from(tree).where(tree.c.id == group_id)
        )
    ).scalar_one()


def _my_membership_ids(user_id: int):
    return select(GroupMembership.group_id).where(
        GroupMembership.user_id == user_id
    )


def group_ids_for(user_id: int):
    """The groups this member reaches, as a subquery.

    A subquery and not a loaded list: every caller needs it *inside* a
    ``WHERE``, and a Python-side list would turn each visibility check into a
    second round trip and, worse, into something a caller could forget to
    refresh.

    **Nesting is two rules, and this is where both of them live.** A
    membership row is still the only key, but a tree gives it two directions:

    * **Membership reaches upward.** Somebody in a team is in the company the
      team is part of, so the company's own challenges reach them. That is
      what makes a group-wide challenge mean what it says once an
      organisation has departments -- without it, «همه» would quietly mean
      "the few people whose row happens to be on the parent".
    * **Authority reaches downward, and only authority.** An administrator of
      a group administers what is inside it; a plain member of the company
      does not thereby read every team's room. This is the asymmetry that
      keeps a sub-team a room of its own rather than a folder.

    Everything else in the app inherits both for free, because this is the
    one function ``group_scope_filter`` and ``group_visibility_filter`` are
    built from.
    """
    mine = _my_membership_ids(user_id)
    administered = select(GroupMembership.group_id).where(
        GroupMembership.user_id == user_id,
        GroupMembership.role.in_([GroupRole.ADMIN.value, GroupRole.OWNER.value]),
    )
    return union(ancestor_ids(mine), descendant_ids(administered))


def group_scope_filter(user_id: int | None):
    """The group gate for a challenge query -- see the module docstring.

    Composed with ``AND`` onto ``challenge_visibility_filter`` and
    ``listing_visibility_filter``, which is why it names only the *group*
    question and leaves visibility to them.
    """
    if user_id is None:
        return Challenge.group_id.is_(None)
    return or_(
        Challenge.group_id.is_(None),
        Challenge.group_id.in_(group_ids_for(user_id)),
        # An enrollment is a key of its own: see "The gate lets an existing
        # enrollment through" in the module docstring.
        Challenge.id.in_(
            select(Enrollment.challenge_id).where(Enrollment.user_id == user_id)
        ),
    )


def group_visibility_filter(user_id: int | None):
    """Which groups a caller may reach at all: the ones they are in.

    The third such filter in the app, alongside ``challenge_visibility_filter``
    and ``profile_visibility_filter``, and it follows their rule -- composed
    into the statement so a miss falls out as "no such row" and answers
    **404** rather than 403. There is no operator exemption here: an app-wide
    admin moderates *challenges*, and a group is somebody's organisation, not
    content published on the app (CLAUDE.md, Roles).
    """
    if user_id is None:
        # Impossible in practice -- every group route requires a session --
        # but written rather than assumed, so a future anonymous caller fails
        # closed instead of matching everything.
        return Group.id.is_(None)
    return Group.id.in_(group_ids_for(user_id))


async def is_group_member(db: AsyncSession, group_id: int, user_id: int) -> bool:
    """Still in the group? The question ``ParticipationMode`` turns on.

    Asked over the *subtree*, because that is who the group's people are once
    it has sub-teams (:func:`group_member_ids`): somebody a company-wide
    mandatory challenge reached through their department is held by it for
    exactly as long as they are in that department.
    """
    found = await db.execute(
        select(GroupMembership.id).where(
            GroupMembership.group_id.in_(descendant_ids([group_id])),
            GroupMembership.user_id == user_id,
        )
    )
    return found.scalar_one_or_none() is not None


async def standing_in(
    db: AsyncSession, group: Group, user_id: int
) -> EffectiveMembership | None:
    """What this member is *here*, resolved across the group's ancestry.

    With nested groups the row that decides what somebody may do in a group
    is not always a row on that group: an administrator of the company
    administers its departments. So the caller's rows on the group and on
    every group it sits inside are read together and the strongest wins --
    their own row as it stands, an ancestor's capped at ``admin`` by
    :func:`inherited_role` (an owner of the parent does not become the owner
    of what somebody else opened inside it).

    One extra small query per group screen, rather than a join, because it
    reads as what it is and because ``load_group``'s answer is the input to
    every ``require`` after it -- this is not a place to be clever.
    """
    above = set(
        (await db.execute(ancestor_ids([group.id]))).scalars().all()
    )
    rows = list(
        (
            await db.execute(
                select(GroupMembership).where(
                    GroupMembership.user_id == user_id,
                    or_(
                        GroupMembership.group_id.in_(above),
                        GroupMembership.group_id.in_(descendant_ids([group.id])),
                    ),
                )
            )
        )
        .scalars()
        .all()
    )
    if not rows:
        return None
    standings = [
        EffectiveMembership(
            user_id=user_id,
            group_id=group.id,
            role=_role_here(row, group.id, above),
            inherited=row.group_id != group.id,
        )
        for row in rows
    ]
    return max(standings, key=lambda s: GROUP_ROLE_RANK.get(s.role, 0))


def _role_here(row: GroupMembership, group_id: int, above: set[int]) -> str:
    """What one membership row is worth in one group.

    The two directions of the tree, said in three lines. A row *on* the group
    is itself. A row above it is authority coming down, capped at ``admin``
    by :func:`inherited_role`. A row below it is membership coming up, and it
    is worth exactly ``member``: running a department does not make somebody
    an administrator of the company it is part of.
    """
    if row.group_id == group_id:
        return row.role or GroupRole.MEMBER.value
    if row.group_id in above:
        return inherited_role(row.role)
    return GroupRole.MEMBER.value


def group_member_ids(group_id: int):
    """Who this group's people are, as a subquery: its own rows and every row
    below it.

    **A roster and an audience are two different questions**, and this is the
    audience one. A group's roster screen lists the people added *there* --
    everyone else is listed on the sub-team they belong to, where their role
    means something and where an administrator can act on them. But «همه» on
    a challenge means everyone the group covers, so assignment reads the
    subtree. Both are honest and neither is the other.
    """
    return select(GroupMembership.user_id).where(
        GroupMembership.group_id.in_(descendant_ids([group_id]))
    )


async def member_counts(db: AsyncSession, group_ids: list[int]) -> dict[int, int]:
    """How many people are in each of these groups, as one query.

    Every surface that lists groups shows the size, so this is the group-side
    twin of ``_mute_map``: one read for the whole page rather than one per
    card.
    """
    if not group_ids:
        return {}
    rows = (
        await db.execute(
            select(GroupMembership.group_id, func.count(GroupMembership.id))
            .where(GroupMembership.group_id.in_(set(group_ids)))
            .group_by(GroupMembership.group_id)
        )
    ).all()
    return {group_id: count for group_id, count in rows}


# ---------------------------------------------------------------------------
# Assignment: putting people into a group's challenges
# ---------------------------------------------------------------------------


def _assignment_kind(challenge: Challenge) -> NotificationKind:
    """Which of the two assignment notifications this challenge raises.

    Two kinds rather than one with an adjective in it -- the row stores the
    event, never the sentence (``app/models/notification.py``) -- and the
    split is also what lets a member silence the optional invitations while
    still being told about the obligations.
    """
    if challenge.participation_mode == ParticipationMode.MANDATORY.value:
        return NotificationKind.GROUP_CHALLENGE_REQUIRED
    return NotificationKind.GROUP_CHALLENGE_ASSIGNED


async def assign_participants(
    db: AsyncSession,
    *,
    challenge: Challenge,
    user_ids: list[int],
    actor_user_id: int,
    timezone: str,
    notify: bool = True,
) -> list[Enrollment]:
    """Enrol these members in this group challenge. Idempotent.

    The one place a group challenge gains a participant, whether that is at
    creation, an administrator adding somebody later, or a new arrival
    picking up the group's standing challenges. Everyone who is already
    enrolled is skipped silently: adding somebody twice is the same request
    as adding them once, and answering 409 would make "add the new hires" a
    request an administrator has to pick apart by hand.

    Anonymity goes through :func:`resolve_assigned_anonymity` -- these people
    were not asked -- and the notification is added to the caller's
    transaction like every other, so a rolled-back assignment cannot announce
    itself.

    ``ChallengeStats.participant_count`` is bumped per row actually written,
    the same way ``enroll`` bumps it, so the counter cannot drift from the
    only path that writes these enrollments.
    """
    wanted = [uid for uid in dict.fromkeys(user_ids)]
    if not wanted:
        return []

    existing = set(
        (
            await db.execute(
                select(Enrollment.user_id).where(
                    Enrollment.challenge_id == challenge.id,
                    Enrollment.user_id.in_(wanted),
                )
            )
        )
        .scalars()
        .all()
    )
    fresh = [uid for uid in wanted if uid not in existing]
    if not fresh:
        return []

    is_anonymous = resolve_assigned_anonymity(challenge.identity_mode)
    start = local_today(timezone, datetime.now(UTC))
    created = []
    for user_id in fresh:
        enrollment = Enrollment(
            challenge_id=challenge.id,
            user_id=user_id,
            timezone=timezone,
            start_date=start,
            is_anonymous=is_anonymous,
            last_modifier_user_id=actor_user_id,
        )
        db.add(enrollment)
        created.append(enrollment)

    stats = (
        await db.execute(
            select(ChallengeStats).where(ChallengeStats.challenge_id == challenge.id)
        )
    ).scalar_one_or_none()
    if stats is None:
        db.add(
            ChallengeStats(challenge_id=challenge.id, participant_count=len(created))
        )
    else:
        stats.participant_count = (stats.participant_count or 0) + len(created)

    if notify:
        await notify_many(
            db,
            user_ids=fresh,
            kind=_assignment_kind(challenge),
            actor_user_id=actor_user_id,
            challenge_id=challenge.id,
            group_id=challenge.group_id,
        )
    return created


async def apply_standing_audience(
    db: AsyncSession,
    *,
    group_id: int,
    user_id: int,
    timezone: str,
    actor_user_id: int,
) -> list[Challenge]:
    """Put a new arrival into the group's ``audience = all`` challenges.

    This is what makes ``ALL`` a *standing* answer rather than a snapshot of
    who happened to be in the group on the afternoon the challenge was
    written. Called from every path that creates a membership -- an invite
    accepted, a request approved -- so a member cannot arrive by one door and
    miss what everybody else has.

    Archived challenges are skipped: they produce no occurrences, so enrolling
    somebody in one gives them a row that can never be acted on and a
    notification about something that already ended.

    **Whether the arrival is told depends on who acted, and that falls out of
    `notify`'s first invariant rather than being decided here.** Somebody who
    used an invite link is their own actor, so the assignment notifications
    are dropped -- they tapped "join" a second ago and are looking at the
    group page that lists these challenges, and a feed that reports your own
    taps back to you is the noise that invariant exists to suppress. Somebody
    an administrator *approved* has the administrator as their actor, so they
    are told: they asked to join, somebody else decided when, and the
    obligations they acquired are news to them. The asymmetry is right and it
    costs no branch.
    """
    challenges = list(
        (
            await db.execute(
                select(Challenge).where(
                    # This group *and every group it is inside*: joining a
                    # department is joining the company, so the company's
                    # standing challenges are the new arrival's too.
                    Challenge.group_id.in_(ancestor_ids([group_id])),
                    Challenge.group_audience == GroupAudience.ALL.value,
                    Challenge.lifecycle_status != "archived",
                )
            )
        )
        .scalars()
        .all()
    )
    for challenge in challenges:
        await assign_participants(
            db,
            challenge=challenge,
            user_ids=[user_id],
            actor_user_id=actor_user_id,
            timezone=timezone,
        )
    return challenges


@dataclass(frozen=True)
class GroupLeaveOutcome:
    """What giving up a group's challenges actually did.

    Titles rather than counts, because both halves of the answer have to be
    *said* to the member: which challenges they let go of, and -- when they
    are still in the group -- which ones the group does not let them let go
    of. A pair of integers would leave the second half as "۲ چالش باقی ماند",
    which names nothing the member could act on.
    """

    left: list[str] = field(default_factory=list)
    kept: list[str] = field(default_factory=list)

    @property
    def total(self) -> int:
        return len(self.left) + len(self.kept)


async def my_group_enrollments(
    db: AsyncSession, *, group_id: int, user_id: int
) -> list[tuple[Enrollment, Challenge]]:
    """This member's own enrollments in this group's challenges.

    No visibility clause: an enrollment is the strongest proof there is that
    the challenge is reachable (the same reasoning ``unenroll`` gives for
    reading the challenge without one), and this only ever answers about the
    caller's own rows.
    """
    rows = await db.execute(
        select(Enrollment, Challenge)
        .join(Challenge, Challenge.id == Enrollment.challenge_id)
        .where(Challenge.group_id == group_id, Enrollment.user_id == user_id)
    )
    return [(enrollment, challenge) for enrollment, challenge in rows.all()]


async def leave_group_challenges(
    db: AsyncSession,
    *,
    group_id: int,
    user_id: int,
    still_in_group: bool,
) -> GroupLeaveOutcome:
    """Give up every challenge of this group at once. Idempotent.

    The bulk twin of ``DELETE /enrollments/{challenge_id}``, and deliberately
    the *same* act repeated rather than a shortcut around it: each row is
    refused or released by :func:`leaving_is_allowed`, each release decrements
    ``ChallengeStats.participant_count`` the way ``unenroll`` does, and each
    one still tells the challenge's owner through
    :func:`membership_is_announced`. A member leaving twenty challenges is
    twenty departures, not one -- the owners are twenty different people, and
    a bulk action that quietly stopped notifying them would make "leave them
    all" a way to slip out unannounced.

    ``still_in_group`` is a parameter and not a lookup, for the reason
    :func:`leaving_is_allowed` takes it too: the two callers know the answer
    and know it about *different moments*. Asked from the group page it is
    ``True``, so a mandatory challenge is kept and named back to the member;
    asked while leaving the group it is ``False``, because the obligation
    belongs to the membership that is ending, and everything goes.

    Nothing is committed here -- the caller commits, exactly as ``notify``
    does, so leaving the group and giving up its challenges are one
    transaction and a failure cannot half-happen.
    """
    pairs = await my_group_enrollments(db, group_id=group_id, user_id=user_id)
    outcome = GroupLeaveOutcome()
    releasing = []
    for enrollment, challenge in pairs:
        if leaving_is_allowed(challenge, still_in_group):
            releasing.append((enrollment, challenge))
            outcome.left.append(challenge.title)
        else:
            outcome.kept.append(challenge.title)
    if not releasing:
        return outcome

    # One read for every counter this touches, rather than one per challenge:
    # the group-wide twin of `member_counts`.
    challenge_ids = [c.id for _e, c in releasing]
    stats_rows = (
        await db.execute(
            select(ChallengeStats).where(
                ChallengeStats.challenge_id.in_(challenge_ids)
            )
        )
    ).scalars().all()
    stats_by_challenge = {row.challenge_id: row for row in stats_rows}

    for enrollment, challenge in releasing:
        # Read before the delete: the member's anonymity choice lives on the
        # row that is about to go, and `ENROLLMENT_LEFT` outlives it. This is
        # the case that makes anonymising a notification a write-time
        # decision rather than a render-time one (CLAUDE.md, Anonymous
        # participation).
        was_anonymous = enrollment.is_anonymous
        await db.delete(enrollment)
        stats = stats_by_challenge.get(challenge.id)
        if stats is not None and stats.participant_count:
            stats.participant_count = max(stats.participant_count - 1, 0)
        if challenge.owner_id is not None and membership_is_announced(
            challenge.visibility
        ):
            # `notify` drops this when the owner is the one leaving.
            await notify(
                db,
                user_id=challenge.owner_id,
                kind=NotificationKind.ENROLLMENT_LEFT,
                actor_user_id=user_id,
                challenge_id=challenge.id,
                group_id=group_id,
                anonymous_actor=was_anonymous,
            )
    return outcome


def leaving_is_allowed(challenge: Challenge, still_in_group: bool) -> bool:
    """May this enrollment be given up?

    The whole of ``ParticipationMode``, in one function so the route that
    refuses and the template that hides the button ask the same question.
    A mandatory group challenge cannot be left *while you are still in the
    group that set it*; once you are not, the obligation is over and the
    enrollment is ordinary again -- the obligation belongs to the membership,
    not to the person.
    """
    if challenge.group_id is None:
        return True
    if challenge.participation_mode != ParticipationMode.MANDATORY.value:
        return True
    return not still_in_group


# ---------------------------------------------------------------------------
# What a group's English-coded enums are called in Farsi
# ---------------------------------------------------------------------------
#
# These live here, in the domain module, rather than in one views router --
# the same call `NOTIFICATION_META` makes and for the same reason: more than
# one surface renders them (the group screens *and* the group chip on a
# challenge card, which is rendered by the challenge views), and a second copy
# in the second router is a second thing to keep in agreement. Everything
# with exactly one surface keeps the app's usual per-surface pattern (D7).
#
# `register_group_filters` is the same registration contract
# `register_icon_filters` and `register_notification_filters` have: every
# views router builds its own `Jinja2Templates`, so one that renders a group
# must call it, and forgetting is a template error at render time.

GROUP_KIND_LABELS = {
    GroupKind.COMPANY.value: "شرکت",
    GroupKind.SCHOOL.value: "مدرسه",
    GroupKind.TEAM.value: "تیم",
    GroupKind.OTHER.value: "سایر",
}

GROUP_KIND_ICONS = {
    GroupKind.COMPANY.value: "building",
    GroupKind.SCHOOL.value: "school",
    GroupKind.TEAM.value: "users",
    GroupKind.OTHER.value: "catOther",
}

GROUP_ROLE_LABELS = {
    GroupRole.OWNER.value: "مالک",
    GroupRole.ADMIN.value: "مدیر",
    GroupRole.MEMBER.value: "عضو",
}

AUDIENCE_LABELS = {
    GroupAudience.ALL.value: "همهٔ اعضای گروه",
    GroupAudience.SELECTED.value: "افراد منتخب",
}

PARTICIPATION_LABELS = {
    ParticipationMode.OPTIONAL.value: "اختیاری",
    ParticipationMode.MANDATORY.value: "اجباری",
}


def register_group_filters(env) -> None:
    """Expose the label maps to one Jinja environment."""
    env.globals["group_kind_labels"] = GROUP_KIND_LABELS
    env.globals["group_kind_icons"] = GROUP_KIND_ICONS
    env.globals["group_role_labels"] = GROUP_ROLE_LABELS
    register_invite_filters(env)
    env.globals["audience_labels"] = AUDIENCE_LABELS
    env.globals["participation_labels"] = PARTICIPATION_LABELS
