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

import secrets
from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.identity import resolve_assigned_anonymity
from app.models.challenge import (
    Challenge,
    GroupAudience,
    ParticipationMode,
)
from app.models.enrollment import Enrollment
from app.models.group import (
    Group,
    GroupInvite,
    GroupKind,
    GroupMembership,
    GroupRole,
)
from app.models.notification import NotificationKind
from app.models.stats import ChallengeStats
from app.notifications import membership_is_announced, notify, notify_many
from app.occurrences import local_today

# Length of the random part of an invite code. 32 bytes of urlsafe base64 is
# 43 characters and ~256 bits -- an invite code is the *only* key that
# reaches a group from outside, so it is sized to be unguessable rather than
# to be typed.
INVITE_CODE_BYTES = 32


def new_invite_code() -> str:
    return secrets.token_urlsafe(INVITE_CODE_BYTES)[:43]


# ---------------------------------------------------------------------------
# Membership as SQL
# ---------------------------------------------------------------------------


def group_ids_for(user_id: int):
    """The groups this member is in, as a subquery.

    A subquery and not a loaded list: every caller needs it *inside* a
    ``WHERE``, and a Python-side list would turn each visibility check into a
    second round trip and, worse, into something a caller could forget to
    refresh.
    """
    return select(GroupMembership.group_id).where(
        GroupMembership.user_id == user_id
    )


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
    """Still in the group? The question ``ParticipationMode`` turns on."""
    found = await db.execute(
        select(GroupMembership.id).where(
            GroupMembership.group_id == group_id,
            GroupMembership.user_id == user_id,
        )
    )
    return found.scalar_one_or_none() is not None


async def trusted_member_ids(db: AsyncSession, group_id: int) -> set[int]:
    """Who in this group may be swept into its standing challenges.

    The *second* answer an administrator gives about a new arrival, and the
    only place `GroupMembership.is_trusted` is read on the way to enrolling
    somebody. Being in the group is the first answer and is what the
    membership row means; this one is «محرمیت» -- permission to be put into
    the challenges nobody picked them for by name.

    Both `seed_group_participants` (creating an «همه اعضا» challenge) and
    :func:`apply_standing_audience` (a new arrival picking those challenges
    up) read it, so the two halves of "for everyone" agree about who everyone
    is. Naming somebody explicitly is deliberately **not** filtered by it:
    an administrator who picked a person by hand has given exactly the
    approval this column stands for.
    """
    return set(
        (
            await db.execute(
                select(GroupMembership.user_id).where(
                    GroupMembership.group_id == group_id,
                    GroupMembership.is_trusted.is_(True),
                )
            )
        )
        .scalars()
        .all()
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
# Invites
# ---------------------------------------------------------------------------

INVITE_ACTIVE = "active"
INVITE_REVOKED = "revoked"
INVITE_EXPIRED = "expired"
INVITE_FULL = "full"


def invite_state(invite: GroupInvite, now: datetime | None = None) -> str:
    """What this link is doing right now -- derived, never stored.

    The same rule the challenge status follows: a stored column would need a
    job to rewrite it the minute a link expired or filled, and a link whose
    stored state disagrees with whether it actually works is worse than no
    state at all. The four values are ordered by how final they are, so a
    revoked link that also expired reads as revoked -- the admin's own act,
    which is the more useful of the two answers.
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

    **A member who has not been approved for the group's challenges picks up
    nothing here**, and the check is inside this function rather than at its
    call sites for the reason every invariant in this app is inside its one
    door: there are three paths that create a membership (a link, an approved
    request, an administrator adding somebody by number) and the one that
    forgot would not be a visible error -- it would be somebody quietly
    enrolled in obligations they were never approved for. Being trusted later
    calls this same function, which is what makes the approval *do* something
    rather than merely record a fact.
    """
    trusted = (
        await db.execute(
            select(GroupMembership.is_trusted).where(
                GroupMembership.group_id == group_id,
                GroupMembership.user_id == user_id,
            )
        )
    ).scalar_one_or_none()
    if not trusted:
        return []

    challenges = list(
        (
            await db.execute(
                select(Challenge).where(
                    Challenge.group_id == group_id,
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

# What each *derived* invite state is called -- see `invite_state` on why the
# state is derived rather than stored.
INVITE_STATE_LABELS = {
    INVITE_ACTIVE: "فعال",
    INVITE_REVOKED: "باطل‌شده",
    INVITE_EXPIRED: "منقضی",
    INVITE_FULL: "ظرفیت پر",
}

# The two answers to «محرمیت» -- see `GroupMembership.is_trusted`. A label
# per state rather than one word plus a negation, because both halves are
# shown on a roster and «تاییدنشده» beside «تاییدشده» reads as a state while
# an absent pill reads as a bug.
TRUST_LABELS = {
    True: "تاییدشده",
    False: "در انتظار تایید",
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
    env.globals["invite_state_labels"] = INVITE_STATE_LABELS
    env.globals["trust_labels"] = TRUST_LABELS
    env.globals["audience_labels"] = AUDIENCE_LABELS
    env.globals["participation_labels"] = PARTICIPATION_LABELS
