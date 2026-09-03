# app/routers/roadmap.py
"""«مسیر» -- the JSON door, and the queries the SSR pages reuse.

The same three rules that decide almost everything in ``app/routers/group``
decide everything here, and between them they *are* the access control:

1. **Reachability is a composed clause.** Every statement that touches a
   roadmap goes through :func:`load_roadmap`, which selects the row with
   ``roadmap_visibility_filter`` already in the ``WHERE``. A roadmap you
   cannot see is not a 403, it is a **404** -- the fourth such filter in the
   app.
2. **Callers ask for a permission, never a role.** ``can(user,
   Perm.ROADMAP_MANAGE, roadmap=…)``. Who may do what is data in
   ``app/permissions.py``, so a future co-author role is a row in a map rather
   than a sweep through this module.
3. **The acting user comes from the session.** There is no client-supplied
   actor and no client-supplied recipient anywhere here.

**404 vs 403.** Once you can see a roadmap it is not a secret from you, so
refusing an action you may not take is an honest 403 -- everybody looking at a
course can already see whose it is. The 404 is reserved for the roadmap
itself, and for a step id that does not belong to the roadmap in the path.

This module holds both the routes and the shared queries, unlike the group
subsystem, which was split into a package for size alone. If it grows the same
way, the split is the same split: ``queries.py`` first.
"""

import logging
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.auth import get_current_user, get_optional_user_id
from app.database import get_db
from app.invites import consume_seat, invite_state, new_invite_code
from app.logging_config import log_event
from app.media import discard, discard_replaced
from app.models.audit_base import newest_first
from app.models.challenge import Challenge, LifecycleStatus
from app.models.roadmap import (
    Roadmap,
    RoadmapEnrollment,
    RoadmapInvite,
    RoadmapStep,
    RoadmapStepProgress,
    RoadmapStepState,
)
from app.models.user import User
from app.permissions import Perm, can
from app.roadmaps import (
    announce_outcome,
    count_non_owner_enrollments,
    join_roadmap,
    parse_rule,
    refresh_progress,
    roadmap_visibility_filter,
    rule_refusal,
    step_is_open,
    structure_is_locked,
)
from app.routers.challenge import (
    challenge_visibility_filter,
    resolve_group_for_create,
    resolve_timezone,
)
from app.schemas.completion import ManualRule
from app.schemas.roadmap import (
    RoadmapCreate,
    RoadmapInviteCreate,
    RoadmapInvitePreview,
    RoadmapInviteRead,
    RoadmapRead,
    RoadmapUpdate,
    StepCreate,
    StepRead,
    StepUpdate,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/roadmaps", tags=["roadmaps"])

# The invite landing lives on its own prefix, for the reason a group's does:
# whoever holds a code must not need -- and must not learn -- the roadmap's id.
invite_router = APIRouter(prefix="/roadmap-invites", tags=["roadmaps"])

DEFAULT_ROADMAP_PAGE_SIZE = 20
MAX_ROADMAP_PAGE_SIZE = 50

#: The two ways the list is read. «مال من» is what somebody with courses
#: wants; «همه» is discovery. An unrecognised value is refused by the route's
#: own pattern rather than silently defaulting, the same call `?sort=` makes on
#: the leaderboard.
SCOPE_MINE = "mine"
SCOPE_ALL = "all"
SCOPE_PATTERN = "^(mine|all)$"


# ---------------------------------------------------------------------------
# Loading, and the permission gate
# ---------------------------------------------------------------------------


async def load_roadmap(
    db: AsyncSession, roadmap_id: int, user_id: int | None
) -> Roadmap:
    """The roadmap, or **404**. The one door every route here goes through."""
    roadmap = (
        await db.execute(
            select(Roadmap).where(
                Roadmap.id == roadmap_id, roadmap_visibility_filter(user_id)
            )
        )
    ).scalar_one_or_none()
    if roadmap is None:
        raise HTTPException(status_code=404, detail="Roadmap not found")
    return roadmap


def require(user: User, permission: str, roadmap: Roadmap, detail: str) -> None:
    """403 unless the caller holds ``permission`` on this roadmap."""
    if not can(user, permission, roadmap=roadmap):
        raise HTTPException(status_code=403, detail=detail)


async def my_enrollment(
    db: AsyncSession, roadmap_id: int, user_id: int | None
) -> RoadmapEnrollment | None:
    if user_id is None:
        return None
    return (
        await db.execute(
            select(RoadmapEnrollment).where(
                RoadmapEnrollment.roadmap_id == roadmap_id,
                RoadmapEnrollment.user_id == user_id,
            )
        )
    ).scalar_one_or_none()


# ---------------------------------------------------------------------------
# Queries the SSR pages share
# ---------------------------------------------------------------------------


async def roadmap_counts(
    db: AsyncSession, roadmap_ids: list[int]
) -> tuple[dict[int, int], dict[int, int]]:
    """``(steps per roadmap, people per roadmap)`` -- two queries for a page.

    The same trade ``member_counts`` makes for groups: every surface listing a
    roadmap shows both figures, so they are read once for the whole page
    rather than once per card.
    """
    if not roadmap_ids:
        return {}, {}
    ids = set(roadmap_ids)
    steps = dict(
        (
            await db.execute(
                select(RoadmapStep.roadmap_id, func.count(RoadmapStep.id))
                .where(
                    RoadmapStep.roadmap_id.in_(ids),
                    RoadmapStep.removed_at.is_(None),
                )
                .group_by(RoadmapStep.roadmap_id)
            )
        ).all()
    )
    members = dict(
        (
            await db.execute(
                select(
                    RoadmapEnrollment.roadmap_id, func.count(RoadmapEnrollment.id)
                )
                .where(RoadmapEnrollment.roadmap_id.in_(ids))
                .group_by(RoadmapEnrollment.roadmap_id)
            )
        ).all()
    )
    return steps, members


async def fetch_roadmap_page(
    db: AsyncSession,
    *,
    user_id: int | None,
    scope: str = SCOPE_ALL,
    q: str | None = None,
    offset: int = 0,
    limit: int = DEFAULT_ROADMAP_PAGE_SIZE,
) -> tuple[list[Roadmap], bool]:
    """One page of visible roadmaps, plus whether more remain.

    ``scope="mine"`` narrows to what the reader built or is walking; it is a
    narrowing *on top of* the visibility clause, never instead of it, exactly
    as ``fetch_challenge_page``'s ``group_id`` is. Ordered by
    ``newest_first`` like every other paginated list in the app, and archived
    roadmaps are left out of every listing for the reason an archived
    challenge reads «تمام شده»: the list is what is running.
    """
    stmt = select(Roadmap).where(
        roadmap_visibility_filter(user_id, listing=True),
        Roadmap.lifecycle_status != LifecycleStatus.ARCHIVED.value,
    )
    if scope == SCOPE_MINE and user_id is not None:
        stmt = stmt.where(
            or_(
                Roadmap.owner_id == user_id,
                Roadmap.id.in_(
                    select(RoadmapEnrollment.roadmap_id).where(
                        RoadmapEnrollment.user_id == user_id
                    )
                ),
            )
        )
    q = (q or "").strip()
    if q:
        escaped = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        stmt = stmt.where(Roadmap.title.ilike(f"%{escaped}%", escape="\\"))
    stmt = (
        stmt.order_by(*newest_first(Roadmap))
        .offset(offset)
        .limit(limit + 1)
        .options(selectinload(Roadmap.owner))
    )
    rows = list((await db.execute(stmt)).scalars().all())
    return rows[:limit], len(rows) > limit


async def progress_map(
    db: AsyncSession, enrollment: RoadmapEnrollment | None
) -> dict[int, RoadmapStepProgress]:
    """This member's progress rows on one roadmap, keyed by step."""
    if enrollment is None:
        return {}
    rows = (
        await db.execute(
            select(RoadmapStepProgress).where(
                RoadmapStepProgress.roadmap_enrollment_id == enrollment.id
            )
        )
    ).scalars().all()
    return {row.step_id: row for row in rows}


async def visible_challenge_ids(
    db: AsyncSession, user_id: int | None, challenge_ids: list[int]
) -> set[int]:
    """Which of these challenges the reader may actually read.

    **This is where a public roadmap stops being a way to read a private
    challenge.** ``roadmap_scope_filter`` opens the challenges of a course you
    are *walking*; somebody merely looking at one gets exactly what they could
    already see, and the step renders as «چالش خصوصی» with no title. Composed
    from ``challenge_visibility_filter`` itself rather than reimplemented, so
    loosening the rule stays an edit to one function.
    """
    if not challenge_ids:
        return set()
    return set(
        (
            await db.execute(
                select(Challenge.id).where(
                    Challenge.id.in_(set(challenge_ids)),
                    challenge_visibility_filter(user_id),
                )
            )
        )
        .scalars()
        .all()
    )


async def current_steps_for(
    db: AsyncSession, *, user_id: int, limit: int = 3
) -> list[tuple[Roadmap, RoadmapStep, RoadmapStepProgress]]:
    """What is open for this member right now, across every course.

    The home dashboard's «قدم فعلی تو» card, and the one caller of
    :func:`app.roadmaps.step_state_filter`'s SQL half: the question is asked
    across roadmaps, so it cannot be answered by rendering one. Ordered by
    when the step opened -- oldest first, because the step that has been open
    longest is the one being neglected.
    """
    rows = (
        await db.execute(
            select(Roadmap, RoadmapStep, RoadmapStepProgress)
            .join(
                RoadmapEnrollment,
                RoadmapEnrollment.id == RoadmapStepProgress.roadmap_enrollment_id,
            )
            .join(RoadmapStep, RoadmapStep.id == RoadmapStepProgress.step_id)
            .join(Roadmap, Roadmap.id == RoadmapEnrollment.roadmap_id)
            .where(
                RoadmapEnrollment.user_id == user_id,
                RoadmapStep.removed_at.is_(None),
                Roadmap.lifecycle_status != LifecycleStatus.ARCHIVED.value,
                RoadmapStepProgress.state.in_(
                    [
                        RoadmapStepState.AVAILABLE.value,
                        RoadmapStepState.IN_PROGRESS.value,
                    ]
                ),
            )
            .options(selectinload(RoadmapStep.challenge))
            .order_by(
                RoadmapStepProgress.unlocked_at.asc(), RoadmapStepProgress.id.asc()
            )
            .limit(limit)
        )
    ).all()
    return [(r, s, p) for r, s, p in rows]


async def roadmap_read(
    db: AsyncSession, roadmap: Roadmap, viewer_id: int | None
) -> RoadmapRead:
    steps, members = await roadmap_counts(db, [roadmap.id])
    enrollment = await my_enrollment(db, roadmap.id, viewer_id)
    # `db.get` rather than `roadmap.owner`: this is reached with rows loaded by
    # several different queries (and, after a PATCH, with an expired one), and
    # a lazy load on an async session surfaces as `MissingGreenlet` during
    # serialisation rather than as a query error (CLAUDE.md, Async DB access).
    # It is an identity-map hit whenever the row is already in the session.
    owner = await db.get(User, roadmap.owner_id)
    return RoadmapRead(
        id=roadmap.id,
        title=roadmap.title,
        description=roadmap.description,
        visibility=roadmap.visibility,
        strict=bool(roadmap.strict),
        image_square=roadmap.image_square,
        owner_id=roadmap.owner_id,
        owner_name=owner.name if owner else None,
        lifecycle_status=roadmap.lifecycle_status,
        group_id=roadmap.group_id,
        created_at=roadmap.created_at,
        step_count=steps.get(roadmap.id, 0),
        member_count=members.get(roadmap.id, 0),
        my_status=enrollment.status if enrollment else None,
        is_owner=roadmap.owner_id == viewer_id,
    )


async def roadmap_invites(db: AsyncSession, roadmap_id: int) -> list[RoadmapInvite]:
    return list(
        (
            await db.execute(
                select(RoadmapInvite)
                .where(RoadmapInvite.roadmap_id == roadmap_id)
                .order_by(*newest_first(RoadmapInvite))
            )
        )
        .scalars()
        .all()
    )


def invite_rows(invites: list[RoadmapInvite]) -> list[RoadmapInviteRead]:
    now = datetime.now(UTC)
    return [
        RoadmapInviteRead(
            id=i.id,
            code=i.code,
            label=i.label,
            max_uses=i.max_uses,
            uses=i.uses or 0,
            expires_at=i.expires_at,
            revoked_at=i.revoked_at,
            created_at=i.created_at,
            state=invite_state(i, now),
        )
        for i in invites
    ]


async def invite_by_code(db: AsyncSession, code: str) -> RoadmapInvite:
    invite = (
        await db.execute(
            select(RoadmapInvite)
            .where(RoadmapInvite.code == code)
            .options(selectinload(RoadmapInvite.roadmap))
        )
    ).scalar_one_or_none()
    if invite is None:
        raise HTTPException(status_code=404, detail="Invite not found")
    return invite


async def _ordered_steps(
    db: AsyncSession, roadmap_id: int, *, load_challenge: bool = False
) -> list[RoadmapStep]:
    """The course in reading order. The one ``ORDER BY`` every step query uses,
    written once so a move, a listing and the engine cannot disagree about
    which step is "next"."""
    stmt = select(RoadmapStep).where(
        RoadmapStep.roadmap_id == roadmap_id,
        RoadmapStep.removed_at.is_(None),
    )
    if load_challenge:
        stmt = stmt.options(selectinload(RoadmapStep.challenge))
    return list(
        (
            await db.execute(
                stmt
                .order_by(
                    RoadmapStep.stage_index,
                    RoadmapStep.order_in_stage,
                    RoadmapStep.id,
                )
            )
        )
        .scalars()
        .all()
    )


async def _step_of(db: AsyncSession, roadmap: Roadmap, step_id: int) -> RoadmapStep:
    step = (
        await db.execute(
            select(RoadmapStep)
            .where(
                RoadmapStep.id == step_id,
                RoadmapStep.roadmap_id == roadmap.id,
                RoadmapStep.removed_at.is_(None),
            )
            .options(selectinload(RoadmapStep.challenge))
        )
    ).scalar_one_or_none()
    if step is None:
        raise HTTPException(status_code=404, detail="Step not found")
    return step


# ---------------------------------------------------------------------------
# The roadmap itself
# ---------------------------------------------------------------------------


@router.post("/", response_model=RoadmapRead, status_code=201)
async def create_roadmap(
    payload: RoadmapCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Anyone signed in may build a course; the creator owns it.

    A roadmap under a group goes through the same gate a challenge does --
    ``resolve_group_for_create``, so the 404-for-a-group-you-are-not-in and
    the 403-for-a-member-who-may-not-publish-there are one rule and not two.
    """
    group = await resolve_group_for_create(db, payload.group_id, current_user)
    roadmap = Roadmap(
        **payload.model_dump(exclude={"group_id"}),
        group_id=group.id if group else None,
        owner_id=current_user.id,
        last_modifier_user_id=current_user.id,
    )
    db.add(roadmap)
    await db.commit()
    await db.refresh(roadmap)
    log_event(
        logger,
        "roadmap.created",
        roadmap_id=roadmap.id,
        visibility=roadmap.visibility,
        group_id=roadmap.group_id,
    )
    return await roadmap_read(db, roadmap, current_user.id)


@router.get("/", response_model=list[RoadmapRead])
async def list_roadmaps(
    current_user_id: int | None = Depends(get_optional_user_id),
    db: AsyncSession = Depends(get_db),
    scope: str = Query(default=SCOPE_ALL, pattern=SCOPE_PATTERN),
    q: str | None = Query(default=None, max_length=100),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(
        default=DEFAULT_ROADMAP_PAGE_SIZE, ge=1, le=MAX_ROADMAP_PAGE_SIZE
    ),
):
    roadmaps, _ = await fetch_roadmap_page(
        db, user_id=current_user_id, scope=scope, q=q, offset=offset, limit=limit
    )
    steps, members = await roadmap_counts(db, [r.id for r in roadmaps])
    mine = (
        dict(
            (
                await db.execute(
                    select(
                        RoadmapEnrollment.roadmap_id, RoadmapEnrollment.status
                    ).where(
                        RoadmapEnrollment.user_id == current_user_id,
                        RoadmapEnrollment.roadmap_id.in_([r.id for r in roadmaps]),
                    )
                )
            ).all()
        )
        if current_user_id and roadmaps
        else {}
    )
    return [
        RoadmapRead(
            id=r.id,
            title=r.title,
            description=r.description,
            visibility=r.visibility,
            strict=bool(r.strict),
            image_square=r.image_square,
            owner_id=r.owner_id,
            owner_name=r.owner.name if r.owner else None,
            lifecycle_status=r.lifecycle_status,
            group_id=r.group_id,
            created_at=r.created_at,
            step_count=steps.get(r.id, 0),
            member_count=members.get(r.id, 0),
            my_status=mine.get(r.id),
            is_owner=r.owner_id == current_user_id,
        )
        for r in roadmaps
    ]


@router.get("/{roadmap_id}", response_model=RoadmapRead)
async def get_roadmap(
    roadmap_id: int,
    current_user_id: int | None = Depends(get_optional_user_id),
    db: AsyncSession = Depends(get_db),
):
    roadmap = await load_roadmap(db, roadmap_id, current_user_id)
    return await roadmap_read(db, roadmap, current_user_id)


@router.patch("/{roadmap_id}", response_model=RoadmapRead)
async def update_roadmap(
    roadmap_id: int,
    payload: RoadmapUpdate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Edit the course's own record. Owner-only.

    ``strict`` is the one field here that is *structural*: flipping it after
    forty people started changes what the order means for a run they are in
    the middle of. So it is locked by the same threshold that locks a
    challenge's cadence -- the first non-owner enrolment -- while the title,
    the description, the pictures and the visibility stay editable, because
    none of those changes what anybody has to do.
    """
    roadmap = await load_roadmap(db, roadmap_id, current_user.id)
    require(
        current_user, Perm.ROADMAP_EDIT, roadmap, "Not allowed to edit this roadmap"
    )
    updates = payload.model_dump(exclude_unset=True)

    flipping_strict = "strict" in updates and bool(updates["strict"]) != bool(
        roadmap.strict
    )
    if flipping_strict and await structure_is_locked(db, roadmap):
        raise HTTPException(
            status_code=409,
            detail="The order is locked once others have started",
        )

    replaced_media = {
        field: getattr(roadmap, field)
        for field in ("image_square",)
        if field in updates
    }
    for field, value in updates.items():
        setattr(roadmap, field, value)
    roadmap.updated_at = datetime.now(UTC)
    roadmap.last_modifier_user_id = current_user.id
    await db.commit()
    await db.refresh(roadmap)
    for field, previous in replaced_media.items():
        discard_replaced(previous, getattr(roadmap, field))
    log_event(logger, "roadmap.updated", roadmap_id=roadmap.id, fields=sorted(updates))
    return await roadmap_read(db, roadmap, current_user.id)


@router.delete("/{roadmap_id}", status_code=204)
async def delete_roadmap(
    roadmap_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Owner-only, and refused once somebody else is walking it.

    The same 409 ``DELETE /challenges/{id}`` answers, for a related reason:
    hard-deleting a roadmap takes every progress row with it, and those are
    the record of somebody else's run. The supported exit from a course that
    has run is ``PATCH lifecycle_status="archived"``, which takes it off every
    listing and leaves what people did intact.

    Note what is *not* destroyed either way: the ``Enrollments`` and
    ``CheckIns`` the course produced belong to the challenges, and the app
    never deletes those on somebody's behalf.
    """
    roadmap = await load_roadmap(db, roadmap_id, current_user.id)
    require(
        current_user,
        Perm.ROADMAP_DELETE,
        roadmap,
        "Not allowed to delete this roadmap",
    )
    if await count_non_owner_enrollments(db, roadmap.id, roadmap.owner_id) > 0:
        raise HTTPException(
            status_code=409,
            detail="Cannot delete a roadmap others have started; archive it instead",
        )
    orphaned = (roadmap.image_square,)
    await db.delete(roadmap)
    await db.commit()
    for key in orphaned:
        discard(key)
    log_event(
        logger, "roadmap.deleted", level=logging.WARNING, roadmap_id=roadmap_id
    )


# ---------------------------------------------------------------------------
# Steps
# ---------------------------------------------------------------------------


@router.get("/{roadmap_id}/steps", response_model=list[StepRead])
async def list_steps(
    roadmap_id: int,
    current_user_id: int | None = Depends(get_optional_user_id),
    db: AsyncSession = Depends(get_db),
):
    """The course in order, with the reader's own state on each step.

    A step whose challenge the reader cannot see keeps its place and loses its
    title -- see :func:`visible_challenge_ids`.
    """
    roadmap = await load_roadmap(db, roadmap_id, current_user_id)
    steps = await _ordered_steps(db, roadmap.id, load_challenge=True)
    enrollment = await my_enrollment(db, roadmap.id, current_user_id)
    progress = await progress_map(db, enrollment)
    visible = await visible_challenge_ids(
        db, current_user_id, [s.challenge_id for s in steps]
    )
    return [
        StepRead(
            id=s.id,
            roadmap_id=s.roadmap_id,
            challenge_id=s.challenge_id,
            challenge_title=(
                s.challenge.title
                if s.challenge and s.challenge_id in visible
                else None
            ),
            stage_index=s.stage_index,
            order_in_stage=s.order_in_stage,
            required=bool(s.required),
            completion_rule=parse_rule(s),
            note=s.note,
            state=(
                progress[s.id].state
                if s.id in progress
                else RoadmapStepState.LOCKED.value
            ),
        )
        for s in steps
    ]


@router.post("/{roadmap_id}/steps", response_model=StepRead, status_code=201)
async def add_step(
    roadmap_id: int,
    payload: StepCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Append one challenge to the end of the course.

    Three refusals, and each is about a step nobody could leave behind or a
    challenge nobody could reach:

    * the challenge must be one the **builder** can see, composed from
      ``challenge_visibility_filter`` itself -- **404** otherwise, so a
      roadmap cannot be used to probe for private challenges by id;
    * the rule must fit the challenge (:func:`rule_refusal`) -- **422**, with
      the reason in Farsi, because the builder is the person who has to fix it;
    * the same challenge twice is a **409**.

    Appending stays allowed once people are walking: the new step lands in a
    stage after everything that exists, so there is nobody down there to
    disturb. Everything that *rearranges* what is above goes through
    :func:`structure_is_locked`.
    """
    roadmap = await load_roadmap(db, roadmap_id, current_user.id)
    require(
        current_user, Perm.ROADMAP_MANAGE, roadmap, "Not allowed to edit this roadmap"
    )
    challenge = (
        await db.execute(
            select(Challenge).where(
                Challenge.id == payload.challenge_id,
                challenge_visibility_filter(current_user.id),
            )
        )
    ).scalar_one_or_none()
    if challenge is None:
        raise HTTPException(status_code=404, detail="Challenge not found")

    refusal = rule_refusal(payload.completion_rule, challenge)
    if refusal:
        raise HTTPException(status_code=422, detail=refusal)

    last_stage = (
        await db.execute(
            select(func.max(RoadmapStep.stage_index)).where(
                RoadmapStep.roadmap_id == roadmap.id,
                RoadmapStep.removed_at.is_(None),
            )
        )
    ).scalar_one_or_none()
    step = RoadmapStep(
        roadmap_id=roadmap.id,
        challenge_id=challenge.id,
        stage_index=0 if last_stage is None else last_stage + 1,
        order_in_stage=0,
        required=payload.required,
        completion_rule=payload.completion_rule.model_dump(mode="json"),
        note=payload.note,
        last_modifier_user_id=current_user.id,
    )
    db.add(step)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(
            status_code=409, detail="This challenge is already a step of the roadmap"
        ) from None
    await db.refresh(step)
    log_event(
        logger,
        "roadmap.step_added",
        roadmap_id=roadmap.id,
        step_id=step.id,
        challenge_id=challenge.id,
        rule=payload.completion_rule.kind,
    )
    return StepRead(
        id=step.id,
        roadmap_id=roadmap.id,
        challenge_id=challenge.id,
        challenge_title=challenge.title,
        stage_index=step.stage_index,
        order_in_stage=step.order_in_stage,
        required=bool(step.required),
        completion_rule=payload.completion_rule,
        note=step.note,
    )


@router.patch("/{roadmap_id}/steps/{step_id}", response_model=StepRead)
async def update_step(
    roadmap_id: int,
    step_id: int,
    payload: StepUpdate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Change what a step asks, or where it sits. Owner-only.

    **Every field here is structural**, so the whole route is refused once
    somebody else is mid-course: rewriting the rule changes what they have to
    do, and moving the step changes when they have to do it. That is the same
    threshold and the same argument as the challenge lock on
    ``cadence``/``goal_*``/``identity_mode``.
    """
    roadmap = await load_roadmap(db, roadmap_id, current_user.id)
    require(
        current_user, Perm.ROADMAP_MANAGE, roadmap, "Not allowed to edit this roadmap"
    )
    step = await _step_of(db, roadmap, step_id)
    updates = payload.model_dump(exclude_unset=True)
    if not updates:
        return await _step_read(db, step, current_user.id)
    if await structure_is_locked(db, roadmap):
        raise HTTPException(
            status_code=409,
            detail="The steps are locked once others have started walking",
        )
    if payload.completion_rule is not None:
        refusal = rule_refusal(payload.completion_rule, step.challenge)
        if refusal:
            raise HTTPException(status_code=422, detail=refusal)
        step.completion_rule = payload.completion_rule.model_dump(mode="json")
    for field in ("required", "note", "stage_index", "order_in_stage"):
        if field in updates:
            setattr(step, field, updates[field])
    step.updated_at = datetime.now(UTC)
    step.last_modifier_user_id = current_user.id
    await db.commit()
    await db.refresh(step)
    return await _step_read(db, step, current_user.id)


async def _step_read(
    db: AsyncSession, step: RoadmapStep, viewer_id: int | None
) -> StepRead:
    """One step as JSON.

    The challenge is fetched with ``db.get`` rather than read off
    ``step.challenge``: this runs after a commit, which expires the instance,
    and a lazy load on an expired relationship in an async session surfaces as
    ``MissingGreenlet`` rather than as a query error (CLAUDE.md, Async DB
    access). ``db.get`` is an identity-map hit whenever the row is already
    loaded, so this costs nothing in the common case.
    """
    _ = viewer_id
    challenge = await db.get(Challenge, step.challenge_id)
    return StepRead(
        id=step.id,
        roadmap_id=step.roadmap_id,
        challenge_id=step.challenge_id,
        challenge_title=challenge.title if challenge else None,
        stage_index=step.stage_index,
        order_in_stage=step.order_in_stage,
        required=bool(step.required),
        completion_rule=parse_rule(step),
        note=step.note,
    )


@router.post("/{roadmap_id}/steps/{step_id}/move", response_model=list[StepRead])
async def move_step(
    roadmap_id: int,
    step_id: int,
    direction: str = Query(pattern="^(up|down)$"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Swap a step with its neighbour. One route, one transaction.

    A route of its own rather than two ``PATCH``es of ``stage_index`` from the
    page, because a swap is *one* act: two requests can half-succeed, and the
    half that lands leaves two steps sharing a stage -- which is a legal shape
    (steps in one stage open together) and therefore a silent corruption of
    the order rather than a visible error.

    Structural, so it is refused once anybody else is walking, exactly as
    editing a step is.
    """
    roadmap = await load_roadmap(db, roadmap_id, current_user.id)
    require(
        current_user, Perm.ROADMAP_MANAGE, roadmap, "Not allowed to edit this roadmap"
    )
    if await structure_is_locked(db, roadmap):
        raise HTTPException(
            status_code=409,
            detail="The steps are locked once others have started walking",
        )
    step = await _step_of(db, roadmap, step_id)
    order = await _ordered_steps(db, roadmap.id)
    index = next((i for i, s in enumerate(order) if s.id == step.id), None)
    target = index + (-1 if direction == "up" else 1)
    if index is None or not 0 <= target < len(order):
        # Already at the end it is being pushed towards. Not an error -- the
        # button simply had nothing to do -- so the answer is the order as it
        # stands rather than a 4xx for a tap that changed nothing.
        return [await _step_read(db, s, current_user.id) for s in order]

    other = order[target]
    step.stage_index, other.stage_index = other.stage_index, step.stage_index
    step.order_in_stage, other.order_in_stage = (
        other.order_in_stage,
        step.order_in_stage,
    )
    now = datetime.now(UTC)
    for row in (step, other):
        row.updated_at = now
        row.last_modifier_user_id = current_user.id
    await db.commit()
    # Re-read rather than swapping the loaded list in place: the commit above
    # expired both rows, and the answer this route owes is the order as the
    # database now holds it.
    return [
        await _step_read(db, s, current_user.id)
        for s in await _ordered_steps(db, roadmap.id)
    ]


@router.delete("/{roadmap_id}/steps/{step_id}", status_code=204)
async def remove_step(
    roadmap_id: int,
    step_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Take a step out of the course. A stamp, not a DELETE.

    Allowed even once people are walking, unlike every other structural
    change, because removing a step can only ever *unblock* somebody: the
    stage after it stops waiting on it, and the progress row -- and everything
    the member logged against its challenge -- stays exactly where it is.
    """
    roadmap = await load_roadmap(db, roadmap_id, current_user.id)
    require(
        current_user, Perm.ROADMAP_MANAGE, roadmap, "Not allowed to edit this roadmap"
    )
    step = await _step_of(db, roadmap, step_id)
    step.removed_at = datetime.now(UTC)
    step.last_modifier_user_id = current_user.id
    await db.commit()
    log_event(
        logger,
        "roadmap.step_removed",
        level=logging.WARNING,
        roadmap_id=roadmap.id,
        step_id=step.id,
    )


# ---------------------------------------------------------------------------
# Walking it
# ---------------------------------------------------------------------------


@router.post("/{roadmap_id}/enroll", response_model=RoadmapRead, status_code=201)
async def enroll(
    roadmap_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    timezone: str | None = Query(default=None, max_length=64),
):
    """Start walking. Idempotent -- tapping twice is joining once.

    The first stage opens as part of this, which means its enrollments are
    written here: from this moment the challenges of stage one are ordinary
    challenges of the member's, and «امروز» shows their occurrences with
    nothing in ``routers/today.py`` knowing why.
    """
    roadmap = await load_roadmap(db, roadmap_id, current_user.id)
    if roadmap.lifecycle_status == LifecycleStatus.ARCHIVED.value:
        raise HTTPException(status_code=409, detail="This roadmap is archived")
    _enrollment, created = await join_roadmap(
        db,
        roadmap=roadmap,
        user_id=current_user.id,
        timezone=resolve_timezone(timezone),
    )
    await db.commit()
    if created:
        log_event(logger, "roadmap.joined", roadmap_id=roadmap.id)
    return await roadmap_read(db, roadmap, current_user.id)


@router.delete("/{roadmap_id}/enroll", status_code=204)
async def leave(
    roadmap_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Stop walking. The challenges you joined along the way stay yours.

    Deliberately *not* the group's «انصراف از همهٔ چالش‌ها»: a group challenge
    was handed to you and the obligation ends with the membership, while a
    roadmap step is a challenge you actually started and logged against.
    Ending the course must not quietly delete that history -- and a member who
    wants out of one of those challenges leaves it the ordinary way, from the
    challenge itself.
    """
    enrollment = await my_enrollment(db, roadmap_id, current_user.id)
    if enrollment is None:
        raise HTTPException(status_code=404, detail="Not walking this roadmap")
    await db.delete(enrollment)
    await db.commit()
    log_event(logger, "roadmap.left", roadmap_id=roadmap_id)


@router.post("/{roadmap_id}/steps/{step_id}/complete", response_model=StepRead)
async def complete_step(
    roadmap_id: int,
    step_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    timezone: str | None = Query(default=None, max_length=64),
):
    """«تمام شد» -- and **only** for a step whose rule is ``manual``.

    Every other rule is a measurement the engine makes, so a route that could
    mark them done would be a way around the condition the builder set. A
    ``manual`` step is the one case where nothing in this app can see the
    thing happen, which is exactly why that rule exists.

    Refused on a locked step of a ``strict`` roadmap, through
    :func:`app.roadmaps.step_is_open` -- the same question the page asks
    before drawing the button, so the control and the server cannot disagree.
    """
    roadmap = await load_roadmap(db, roadmap_id, current_user.id)
    step = await _step_of(db, roadmap, step_id)
    if not isinstance(parse_rule(step), ManualRule):
        raise HTTPException(
            status_code=409,
            detail="This step finishes on its own condition, not by hand",
        )
    enrollment = await my_enrollment(db, roadmap.id, current_user.id)
    if enrollment is None:
        raise HTTPException(status_code=404, detail="Not walking this roadmap")
    progress = (await progress_map(db, enrollment)).get(step.id)
    state = progress.state if progress else RoadmapStepState.LOCKED.value
    if not step_is_open(roadmap, state):
        raise HTTPException(status_code=409, detail="This step is not open yet")

    now = datetime.now(UTC)
    if progress is None:
        progress = RoadmapStepProgress(
            roadmap_enrollment_id=enrollment.id, step_id=step.id, unlocked_at=now
        )
        db.add(progress)
    progress.state = RoadmapStepState.COMPLETED.value
    progress.completed_at = now
    progress.last_modifier_user_id = current_user.id
    await db.flush()

    outcome = await refresh_progress(
        db,
        roadmap=roadmap,
        enrollment=enrollment,
        timezone=resolve_timezone(timezone),
        now=now,
    )
    await announce_outcome(
        db, roadmap=roadmap, enrollment=enrollment, outcome=outcome
    )
    await db.commit()
    log_event(
        logger, "roadmap.step_completed", roadmap_id=roadmap.id, step_id=step.id
    )
    return await _step_read(db, step, current_user.id)


# ---------------------------------------------------------------------------
# Invite links
# ---------------------------------------------------------------------------


@router.post("/{roadmap_id}/invites", response_model=RoadmapInviteRead, status_code=201)
async def create_invite(
    roadmap_id: int,
    payload: RoadmapInviteCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """A link into the course, with a real capacity (``app/invites.py``).

    There is no ``requires_approval`` half here, unlike a group's: a group is
    a room whose membership an administrator curates, while a roadmap is
    something you either published or you did not. If it is public, the link
    is a convenience; if it is unlisted, the link *is* the way in. A queue in
    front of a course nobody has to be admitted to would be a control with
    nothing to decide.
    """
    roadmap = await load_roadmap(db, roadmap_id, current_user.id)
    require(
        current_user,
        Perm.ROADMAP_MANAGE,
        roadmap,
        "Not allowed to manage this roadmap",
    )
    invite = RoadmapInvite(
        roadmap_id=roadmap.id,
        code=new_invite_code(),
        label=payload.label,
        max_uses=payload.max_uses,
        expires_at=payload.expires_at,
        uses=0,
        created_by_user_id=current_user.id,
        last_modifier_user_id=current_user.id,
    )
    db.add(invite)
    await db.commit()
    await db.refresh(invite)
    return invite_rows([invite])[0]


@router.get("/{roadmap_id}/invites", response_model=list[RoadmapInviteRead])
async def list_invites(
    roadmap_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    roadmap = await load_roadmap(db, roadmap_id, current_user.id)
    require(
        current_user,
        Perm.ROADMAP_MANAGE,
        roadmap,
        "Not allowed to manage this roadmap",
    )
    return invite_rows(await roadmap_invites(db, roadmap.id))


@router.delete("/{roadmap_id}/invites/{invite_id}", status_code=204)
async def revoke_invite(
    roadmap_id: int,
    invite_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Kill a link. A stamp, not a DELETE -- an administrator looking at the
    list needs to see that the link they handed out is dead, and a row that
    vanishes reads as one that never existed."""
    roadmap = await load_roadmap(db, roadmap_id, current_user.id)
    require(
        current_user,
        Perm.ROADMAP_MANAGE,
        roadmap,
        "Not allowed to manage this roadmap",
    )
    invite = (
        await db.execute(
            select(RoadmapInvite).where(
                RoadmapInvite.id == invite_id,
                RoadmapInvite.roadmap_id == roadmap.id,
            )
        )
    ).scalar_one_or_none()
    if invite is None:
        raise HTTPException(status_code=404, detail="Invite not found")
    if invite.revoked_at is None:
        invite.revoked_at = datetime.now(UTC)
        invite.last_modifier_user_id = current_user.id
        await db.commit()


@invite_router.get("/{code}", response_model=RoadmapInvitePreview)
async def preview_invite(
    code: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """What the link says about the course, before it is used.

    The title, the picture, how many steps and how many people -- and not the
    steps themselves. Whoever holds the code is not walking it yet, so
    ``roadmap_scope_filter`` grants them nothing, and a preview listing the
    challenges would make a forwarded link a way to read a private one.
    """
    invite = await invite_by_code(db, code)
    roadmap = invite.roadmap
    steps, members = await roadmap_counts(db, [roadmap.id])
    enrollment = await my_enrollment(db, roadmap.id, current_user.id)
    return RoadmapInvitePreview(
        roadmap_id=roadmap.id,
        title=roadmap.title,
        description=roadmap.description,
        image_square=roadmap.image_square,
        step_count=steps.get(roadmap.id, 0),
        member_count=members.get(roadmap.id, 0),
        state=invite_state(invite),
        already_member=enrollment is not None,
    )


@invite_router.post("/{code}/accept", response_model=RoadmapRead, status_code=201)
async def accept_invite(
    code: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    timezone: str | None = Query(default=None, max_length=64),
):
    """Use a link and start walking.

    **The seat is taken with a conditional UPDATE** -- ``consume_seat``, the
    same statement a group invite spends, so a one-seat link admits one person
    even when four tap it in the same instant. Everything is one transaction,
    so a failed enrolment takes the consumed seat back with it.

    Somebody already walking it does not spend a seat: re-opening the link you
    joined with is not joining twice.
    """
    invite = await invite_by_code(db, code)
    roadmap = invite.roadmap
    if roadmap.lifecycle_status == LifecycleStatus.ARCHIVED.value:
        raise HTTPException(status_code=409, detail="This roadmap is archived")

    existing = await my_enrollment(db, roadmap.id, current_user.id)
    if existing is not None:
        return await roadmap_read(db, roadmap, current_user.id)

    if not await consume_seat(db, RoadmapInvite, invite.id):
        await db.rollback()
        raise HTTPException(
            status_code=409, detail="This invite link can no longer be used"
        )
    try:
        await join_roadmap(
            db,
            roadmap=roadmap,
            user_id=current_user.id,
            timezone=resolve_timezone(timezone),
        )
        await db.commit()
    except IntegrityError:
        # Lost a race against another tab. The rollback returns the seat.
        await db.rollback()
        raise HTTPException(status_code=409, detail="Already walking this") from None
    log_event(
        logger, "roadmap.invite_accepted", roadmap_id=roadmap.id, invite_id=invite.id
    )
    return await roadmap_read(db, roadmap, current_user.id)
