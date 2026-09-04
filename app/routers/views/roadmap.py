# routers/views/roadmap.py
"""The «مسیر» screens.

Presentation only: every query here comes from ``app/routers/roadmap.py`` or
``app/roadmaps.py``, the same "query logic in the API router, presentation in
views/" split the challenge and group screens follow. In particular the step
rows are built from :func:`app.roadmaps.roadmap_step_state`,
:func:`app.roadmaps.rule_progress` and ``describe_cadence`` -- the cadence is
put into words by the *same* function ``build_cadence_plan`` uses for its own
one-line rule, so a step and the challenge's own plan card can never describe
the same cadence differently.

**Which screens exist, and why these.**

* ``/views/roadmaps/`` -- your courses and the public ones, one paged list with
  a two-button scope. There is no second «کشف» page: the rows are identical
  and the difference really is one clause.
* ``/views/roadmaps/{id}`` -- «مسیر» / «من» / «درباره» as hash-backed panels,
  the shape challenge-detail and the group page already use. **None of the
  three pages**: a course is a handful of steps read in one go, so the step
  list is not paged and the URL has only the panel to carry.
* ``/views/roadmaps/{id}/manage`` -- the builder's settings screen, one row
  per editable value, **404** to anybody else.
* ``/views/roadmap-invites/{code}`` -- the one screen somebody outside the
  course ever sees.

**The engine runs on a page load, and that is deliberate.** A ``duration``
rule finishes because time passed, with nobody touching anything, and this app
has no job runner (see ``purge_expired_codes``). So opening the course
recomputes it. Everything a *check-in* could change is already handled the
moment it happens (``advance_after_checkin``), so this GET is the clock's
half, not the app's only chance to be right.
"""

import logging
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_user, get_page_user
from app.avatars import register_avatar_filters
from app.config import BASE_DIR
from app.database import get_db
from app.date_filters import register_date_filters
from app.explainers import register_explainer_filters
from app.icons import register_icon_filters
from app.invites import INVITE_ACTIVE, invite_state, register_invite_filters
from app.media import register_media_filters
from app.models.challenge import LifecycleStatus
from app.models.roadmap import Roadmap, RoadmapEnrollment, RoadmapStepState
from app.models.user import User
from app.permissions import Perm, can
from app.roadmaps import (
    ChallengeFacts,
    active_steps,
    announce_outcome,
    describe_rule,
    member_facts,
    parse_rule,
    refresh_progress,
    register_roadmap_filters,
    roadmap_step_state,
    rule_progress,
    step_is_open,
    structure_is_locked,
)
from app.routers.challenge import DEFAULT_TIMEZONE
from app.routers.checkin import parse_cadence
from app.routers.roadmap import (
    DEFAULT_ROADMAP_PAGE_SIZE,
    MAX_ROADMAP_PAGE_SIZE,
    SCOPE_ALL,
    SCOPE_MINE,
    SCOPE_PATTERN,
    current_steps_for,
    fetch_roadmap_page,
    invite_by_code,
    invite_rows,
    load_roadmap,
    my_enrollment,
    progress_map,
    roadmap_counts,
    roadmap_invites,
    visible_challenge_ids,
)
from app.routers.views.challenge import describe_cadence
from app.schemas.completion import ManualRule

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/views/roadmaps", tags=["roadmap-views"])
invite_router = APIRouter(prefix="/views/roadmap-invites", tags=["roadmap-views"])

templates = Jinja2Templates(directory=BASE_DIR / "templates")
register_date_filters(templates.env)
# Every views router builds its own environment and owes the registrations for
# what it renders: the explainer dots, the shell's icons plus the category and
# cadence glyphs a step card wears, pictures (both the roadmap's own and the
# challenges'), the owner's avatar, and the roadmap's own Farsi label maps.
register_explainer_filters(templates.env)
register_icon_filters(templates.env)
register_media_filters(templates.env)
register_avatar_filters(templates.env)
register_roadmap_filters(templates.env)
# The invite rows on the management screen wear the same four derived-state
# words a group's do (`app/invites.py`), so they come from the same map.
register_invite_filters(templates.env)

# ---------------------------------------------------------------------------
# The step rows both the course page and the home card read
# ---------------------------------------------------------------------------


async def build_step_rows(
    db: AsyncSession,
    *,
    roadmap: Roadmap,
    viewer_id: int | None,
    enrollment=None,
) -> list[dict]:
    """The course, as the reader sees it. One dict per step.

    Dicts rather than ORM rows, for the reason every other panel builder in
    this app returns them: the template must not be the thing that decides
    what an unreadable challenge or a missing progress row looks like.

    **A step whose challenge the reader cannot see keeps its place and loses
    its name.** That is the whole answer to "does a public roadmap leak a
    private challenge": ``visible_challenge_ids`` composes
    ``challenge_visibility_filter`` itself, and
    ``app.roadmaps.roadmap_scope_filter`` is what makes that clause say *yes*
    for somebody actually walking the course. Somebody browsing gets the
    shape of the course and none of its private contents.
    """
    steps = await active_steps(db, roadmap.id)
    progress = await progress_map(db, enrollment)
    visible = await visible_challenge_ids(
        db, viewer_id, [s.challenge_id for s in steps]
    )
    facts = (
        await member_facts(
            db, user_id=viewer_id, challenge_ids=[s.challenge_id for s in steps]
        )
        if viewer_id is not None
        else {}
    )
    now = datetime.now(UTC)

    rows = []
    for index, step in enumerate(steps, start=1):
        row_progress = progress.get(step.id)
        state = roadmap_step_state(row_progress)
        is_visible = step.challenge_id in visible
        challenge = step.challenge if is_visible else None
        rule = parse_rule(step)
        rows.append(
            {
                "index": index,
                "id": step.id,
                "challenge_id": step.challenge_id,
                "visible": is_visible,
                "title": challenge.title if challenge else "چالش خصوصی",
                "category": challenge.category if challenge else None,
                "cadence_kind": challenge.cadence_kind if challenge else None,
                # The one place a cadence is put into words, borrowed rather
                # than restated -- `build_cadence_plan` calls the same
                # function for its own `rule` line.
                "cadence": (
                    describe_cadence(parse_cadence(challenge)) if challenge else None
                ),
                "rule_kind": rule.kind,
                # The one place a stored rule becomes a sentence -- borrowed
                # from the domain module rather than branched on here, so no
                # template ever asks what kind of rule a step carries.
                "rule_text": describe_rule(step),
                "required": bool(step.required),
                "note": step.note,
                "state": state,
                "is_open": step_is_open(roadmap, state),
                "is_manual": isinstance(rule, ManualRule),
                # Only for a step that has actually opened. A locked step's
                # share is meaningless -- nothing has been asked of the reader
                # yet -- and a bar that can only read 0 is a worse answer than
                # no bar, which is the same call `rule_progress` makes for a
                # rule with nothing to count.
                "progress": (
                    rule_progress(
                        rule,
                        facts=facts.get(step.challenge_id, ChallengeFacts()),
                        progress=row_progress,
                        now=now,
                    )
                    if row_progress is not None and row_progress.unlocked_at
                    else None
                ),
                "unlocked_at": row_progress.unlocked_at if row_progress else None,
                "completed_at": row_progress.completed_at if row_progress else None,
            }
        )
    return rows


def _summary(rows: list[dict]) -> dict:
    """«۳ از ۷ قدم» -- counted off the rows the page is already drawing.

    Never a stored column: a share of the course is exactly the kind of
    derived figure ``ChallengeStats.participant_count`` is a cautionary tale
    about, and the rows are in hand.
    """
    done = sum(
        1
        for r in rows
        if r["state"]
        in (RoadmapStepState.COMPLETED.value, RoadmapStepState.SKIPPED.value)
    )
    total = len(rows)
    return {
        "done": done,
        "total": total,
        "pct": round(done / total * 100) if total else 0,
        "current": next(
            (
                r
                for r in rows
                if r["state"]
                in (
                    RoadmapStepState.AVAILABLE.value,
                    RoadmapStepState.IN_PROGRESS.value,
                )
            ),
            None,
        ),
    }


# ---------------------------------------------------------------------------
# The list
# ---------------------------------------------------------------------------


async def list_context(
    db: AsyncSession, *, user_id: int | None, scope: str, q: str | None,
    offset: int, limit: int,
) -> dict:
    roadmaps, has_more = await fetch_roadmap_page(
        db, user_id=user_id, scope=scope, q=q, offset=offset, limit=limit
    )
    steps, members = await roadmap_counts(db, [r.id for r in roadmaps])
    mine = set()
    if user_id is not None and roadmaps:
        mine = set(
            (
                await db.execute(
                    select(RoadmapEnrollment.roadmap_id).where(
                        RoadmapEnrollment.user_id == user_id,
                        RoadmapEnrollment.roadmap_id.in_([r.id for r in roadmaps]),
                    )
                )
            )
            .scalars()
            .all()
        )
    return {
        "roadmaps": roadmaps,
        "step_counts": steps,
        "member_counts": members,
        "walking_ids": mine,
        "current_user_id": user_id,
        "has_more": has_more,
    }


async def resolve_scope(
    db: AsyncSession, user_id: int | None, scope: str | None
) -> str:
    """Which half of the list to open on, when the URL does not say.

    «مال من» for somebody who has courses, «همه» for somebody who has none --
    because an empty list with a filter on it reads as "there is nothing here"
    rather than as "you have not started one". Once the URL carries a scope it
    is obeyed exactly; this only fills in the first visit.
    """
    if scope in (SCOPE_MINE, SCOPE_ALL):
        return scope
    if user_id is None:
        return SCOPE_ALL
    rows, _ = await fetch_roadmap_page(
        db, user_id=user_id, scope=SCOPE_MINE, offset=0, limit=1
    )
    return SCOPE_MINE if rows else SCOPE_ALL


# The list page itself lives in `routers/views/space.py` («فضای من»): a
# course and a group are the same kind of thing to a member, and they now
# share one destination with two segments. The fragment stays here, beside
# the query it pages.
@router.get("/fragment")
async def roadmaps_fragment(
    request: Request,
    viewer: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    scope: str = Query(default=SCOPE_ALL, pattern=SCOPE_PATTERN),
    q: str | None = Query(default=None, max_length=100),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(
        default=DEFAULT_ROADMAP_PAGE_SIZE, ge=1, le=MAX_ROADMAP_PAGE_SIZE
    ),
):
    """`get_current_user`, not the page dependency: a `fetch()` needs a real
    401 to redirect on (CLAUDE.md, the 303/401 split)."""
    context = await list_context(
        db, user_id=viewer.id, scope=scope, q=q, offset=offset, limit=limit
    )
    response = templates.TemplateResponse(
        "roadmap/_roadmap_cards.html", {"request": request, **context}
    )
    response.headers["X-Has-More"] = "true" if context["has_more"] else "false"
    return response


# ---------------------------------------------------------------------------
# One course
# ---------------------------------------------------------------------------


@router.get("/{roadmap_id}")
async def roadmap_detail(
    roadmap_id: int,
    request: Request,
    viewer: User = Depends(get_page_user),
    db: AsyncSession = Depends(get_db),
):
    """The course: what it asks, where you are, and what it is."""
    roadmap = await load_roadmap(db, roadmap_id, viewer.id)
    enrollment = await my_enrollment(db, roadmap.id, viewer.id)

    # The clock's half of the engine -- see the module docstring. Only for
    # somebody actually walking it: there is nothing to recompute for a reader.
    if enrollment is not None:
        outcome = await refresh_progress(
            db,
            roadmap=roadmap,
            enrollment=enrollment,
            # An enrollment's own `timezone` is what every occurrence is
            # judged in, and this app has no per-account timezone column --
            # only per-enrollment ones -- so a step opened server-side gets
            # the same fallback every other server-side path uses
            # (`resolve_timezone`), and the member changes it on the enrolment
            # exactly as they always could.
            timezone=DEFAULT_TIMEZONE,
        )
        await announce_outcome(
            db, roadmap=roadmap, enrollment=enrollment, outcome=outcome
        )
        await db.commit()

    rows = await build_step_rows(
        db, roadmap=roadmap, viewer_id=viewer.id, enrollment=enrollment
    )
    steps, members = await roadmap_counts(db, [roadmap.id])
    owner = await db.get(User, roadmap.owner_id)
    return templates.TemplateResponse(
        "roadmap/detail.html",
        {
            "title": roadmap.title,
            "request": request,
            "current_user_id": viewer.id,
            "active_nav": "profile",
            "roadmap": roadmap,
            "owner": owner,
            "steps": rows,
            "summary": _summary(rows),
            "step_count": steps.get(roadmap.id, 0),
            "member_count": members.get(roadmap.id, 0),
            "is_walking": enrollment is not None,
            "can_manage": can(viewer, Perm.ROADMAP_MANAGE, roadmap=roadmap),
            "is_archived": roadmap.lifecycle_status
            == LifecycleStatus.ARCHIVED.value,
        },
    )


@router.get("/{roadmap_id}/manage")
async def roadmap_manage_page(
    roadmap_id: int,
    request: Request,
    viewer: User = Depends(get_page_user),
    db: AsyncSession = Depends(get_db),
):
    """The builder's screen: the steps, the links, and the course's record.

    **404 for anybody who is not the builder**, not 403 -- the same call
    ``group_manage_page`` and ``get_admin_page_user`` make: a page whose whole
    content is somebody else's controls has no business confirming to a reader
    that it exists. The JSON routes behind it answer 403, because those are
    reached by a ``fetch()`` that has to tell "not allowed" from "signed out".
    """
    roadmap = await load_roadmap(db, roadmap_id, viewer.id)
    if not can(viewer, Perm.ROADMAP_MANAGE, roadmap=roadmap):
        raise HTTPException(status_code=404, detail="Not found")

    rows = await build_step_rows(db, roadmap=roadmap, viewer_id=viewer.id)
    invites = invite_rows(await roadmap_invites(db, roadmap.id))
    _, members = await roadmap_counts(db, [roadmap.id])
    return templates.TemplateResponse(
        "roadmap/manage.html",
        {
            "title": f"مدیریت {roadmap.title}",
            "request": request,
            "current_user_id": viewer.id,
            "active_nav": "profile",
            "roadmap": roadmap,
            "steps": rows,
            "invites": invites,
            "member_count": members.get(roadmap.id, 0),
            # What the screen may still offer. Asked once here rather than
            # implied by each control, so the page and the routes refuse the
            # same things: every structural edit is locked from the first
            # non-owner enrolment, while appending stays free.
            "structure_locked": await structure_is_locked(db, roadmap),
            "is_archived": roadmap.lifecycle_status
            == LifecycleStatus.ARCHIVED.value,
        },
    )


# ---------------------------------------------------------------------------
# The invite landing
# ---------------------------------------------------------------------------


@invite_router.get("/{code}")
async def invite_landing(
    code: str,
    request: Request,
    viewer: User = Depends(get_page_user),
    db: AsyncSession = Depends(get_db),
):
    """The one roadmap screen somebody outside the course ever sees.

    A signed-out visitor is handled by machinery that already exists:
    ``get_page_user`` raises ``LoginRequired`` and ``app.main`` turns it into
    a 303 carrying ``?next=``, so they sign in, land back here, and the link
    still works.

    What it shows is the title, the picture, how many steps and how many
    people -- **and not the steps**. Whoever holds the code is not walking the
    course, so ``roadmap_scope_filter`` grants them nothing and a preview
    listing the challenges would make a forwarded link a way to read a private
    one.
    """
    invite = await invite_by_code(db, code)
    roadmap = invite.roadmap
    steps, members = await roadmap_counts(db, [roadmap.id])
    enrollment = await my_enrollment(db, roadmap.id, viewer.id)
    state = invite_state(invite)
    owner = await db.get(User, roadmap.owner_id)
    return templates.TemplateResponse(
        "roadmap/invite.html",
        {
            "title": "دعوت به مسیر",
            "request": request,
            "current_user_id": viewer.id,
            "active_nav": None,
            "roadmap": roadmap,
            "owner": owner,
            "code": code,
            "state": state,
            "is_usable": state == INVITE_ACTIVE
            and roadmap.lifecycle_status != LifecycleStatus.ARCHIVED.value,
            "step_count": steps.get(roadmap.id, 0),
            "member_count": members.get(roadmap.id, 0),
            "already_member": enrollment is not None,
        },
    )


# ---------------------------------------------------------------------------
# The home dashboard's card
# ---------------------------------------------------------------------------


async def home_roadmap_card(
    db: AsyncSession, *, user_id: int, now: datetime | None = None
) -> list[dict]:
    """«قدم فعلی تو» -- what is open, across every course, for the home page.

    A roadmap that is never seen at the daily level dies, and «امروز» shows
    *occurrences* rather than steps, so the course itself has to surface
    somewhere the member looks every day. This is the only place that asks the
    question across roadmaps, which is why it is the caller
    :func:`app.roadmaps.step_state_filter`'s SQL half exists for.

    Read-only and cheap: no refresh runs here. Everything a check-in could
    change was already recomputed when the check-in landed, and the clock's
    half runs when the course is opened.
    """
    now = now or datetime.now(UTC)
    rows = await current_steps_for(db, user_id=user_id, limit=3)
    if not rows:
        return []
    visible = await visible_challenge_ids(
        db, user_id, [s.challenge_id for _r, s, _p in rows]
    )
    facts = await member_facts(
        db, user_id=user_id, challenge_ids=[s.challenge_id for _r, s, _p in rows]
    )
    cards = []
    for roadmap, step, progress in rows:
        rule = parse_rule(step)
        is_visible = step.challenge_id in visible
        cards.append(
            {
                "roadmap_id": roadmap.id,
                "roadmap_title": roadmap.title,
                "challenge_id": step.challenge_id,
                "title": step.challenge.title if is_visible else "چالش خصوصی",
                "visible": is_visible,
                "category": step.challenge.category if is_visible else None,
                "state": progress.state,
                "rule_kind": rule.kind,
                "rule_text": describe_rule(step),
                "progress": rule_progress(
                    rule,
                    facts=facts.get(step.challenge_id, ChallengeFacts()),
                    progress=progress,
                    now=now,
                ),
            }
        )
    return cards
