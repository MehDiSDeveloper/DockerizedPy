"""«مسیر»: gating, the six completion rules, and the two ways it could leak.

Only the rules that could regress *silently* are pinned here, per CLAUDE.md.
Four of them are worth naming because each one is invisible when it breaks:

- **A locked step has no enrollment.** That absence -- not a filter in
  ``routers/today.py`` -- is what keeps a step nobody has reached out of
  «امروز». Break it and the app quietly asks people to do things that are not
  their turn yet, and nothing raises.
- **The rules are evaluated from live rows, never counted up.** A rule that
  drifted would show a green step nobody earned.
- **A private challenge does not leak through a public roadmap.** The whole
  visibility question the feature turns on.
- **The structure freezes once somebody else is walking.** A silent
  rearrangement changes the deal under forty people mid-course.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select

from app.invites import new_invite_code
from app.models.enrollment import Enrollment
from app.models.roadmap import RoadmapInvite, RoadmapStepProgress, RoadmapStepState
from app.roadmaps import (
    is_bounded,
    join_roadmap,
    refresh_progress,
    rule_refusal,
    step_is_open,
)
from app.schemas.completion import ChallengeFinishedRule
from tests.roadmap_helpers import (
    add_step,
    log_checkins,
    make_challenge,
    make_roadmap,
    make_user,
    set_streak,
    sign_in,
)

DAILY = {"kind": "recurring_days", "mode": "every_n_days", "n": 1}


async def _walk(db, roadmap, user, *, timezone="Asia/Tehran"):
    """Join and recompute -- what ``POST /roadmaps/{id}/enroll`` does."""
    enrollment, _ = await join_roadmap(
        db, roadmap=roadmap, user_id=user.id, timezone=timezone
    )
    await db.commit()
    return enrollment


async def _states(db, enrollment) -> list[str]:
    """Every step's state for one walk, in step order.

    Takes an id rather than the instance where the caller has expired the
    session (after the app has written through its *own* session), because an
    expired instance would lazy-load and that surfaces as ``MissingGreenlet``
    in an async session -- the trap CLAUDE.md names.
    """
    enrollment_id = enrollment if isinstance(enrollment, int) else enrollment.id
    rows = (
        await db.execute(
            select(RoadmapStepProgress)
            .where(RoadmapStepProgress.roadmap_enrollment_id == enrollment_id)
            .order_by(RoadmapStepProgress.step_id)
        )
    ).scalars().all()
    return [row.state for row in rows]


async def _enrolled(db, user, challenge) -> bool:
    return await _enrolled_id(db, user.id, challenge.id)


async def _enrolled_id(db, user_id: int, challenge_id: int) -> bool:
    return (
        await db.execute(
            select(func.count())
            .select_from(Enrollment)
            .where(
                Enrollment.user_id == user_id,
                Enrollment.challenge_id == challenge_id,
            )
        )
    ).scalar_one() > 0


# ---------------------------------------------------------------------------
# Gating: a locked step is an *absence*, not a filter
# ---------------------------------------------------------------------------


async def test_only_the_first_stage_is_enrolled_on_joining(db):
    author = await make_user(db, "Author")
    walker = await make_user(db, "Walker")
    roadmap = await make_roadmap(db, author)
    first = await make_challenge(db, author, title="قدم یک")
    second = await make_challenge(db, author, title="قدم دو")
    await add_step(db, roadmap, first)
    await add_step(db, roadmap, second)

    enrollment = await _walk(db, roadmap, walker)

    assert await _states(db, enrollment) == [
        RoadmapStepState.AVAILABLE.value,
        RoadmapStepState.LOCKED.value,
    ]
    assert await _enrolled(db, walker, first)
    # The whole gate, in one assertion: the locked step has no enrollment, so
    # nothing downstream -- «امروز» included -- can offer it.
    assert not await _enrolled(db, walker, second)


async def test_a_locked_step_stays_out_of_today(client, db):
    """The end-to-end version of the rule above, through the real feed.

    `routers/today.py` is untouched by this feature; if it ever needs a
    roadmap-shaped filter, the gate has been designed wrong somewhere else.
    """
    author = await make_user(db, "Author")
    walker = await make_user(db, "Walker")
    roadmap = await make_roadmap(db, author)
    first = await make_challenge(db, author, title="اول", cadence=DAILY)
    second = await make_challenge(db, author, title="دوم", cadence=DAILY)
    await add_step(db, roadmap, first)
    await add_step(db, roadmap, second)
    await _walk(db, roadmap, walker)

    sign_in(client, walker)
    items = (await client.get("/today/")).json()
    titles = {item["challenge_title"] for item in items}
    assert "اول" in titles
    assert "دوم" not in titles


async def test_finishing_a_step_opens_the_next_one(db):
    author = await make_user(db, "Author")
    walker = await make_user(db, "Walker")
    roadmap = await make_roadmap(db, author)
    first = await make_challenge(db, author, title="اول", cadence=DAILY)
    second = await make_challenge(db, author, title="دوم", cadence=DAILY)
    await add_step(db, roadmap, first, rule={"kind": "count", "n": 2})
    await add_step(db, roadmap, second)
    enrollment = await _walk(db, roadmap, walker)

    await log_checkins(db, user=walker, challenge=first, count=2)
    await refresh_progress(
        db, roadmap=roadmap, enrollment=enrollment, timezone="Asia/Tehran"
    )
    await db.commit()

    assert await _states(db, enrollment) == [
        RoadmapStepState.COMPLETED.value,
        RoadmapStepState.AVAILABLE.value,
    ]
    assert await _enrolled(db, walker, second)


async def test_soft_and_strict_orders_differ_only_in_one_answer(db):
    """`strict` is one boolean the page and the route both read -- nothing
    else in the engine branches on it, and a locked step is locked either
    way."""
    soft = await make_roadmap(db, await make_user(db, "A"), strict=False)
    hard = await make_roadmap(db, await make_user(db, "B"), strict=True)
    locked = RoadmapStepState.LOCKED.value
    assert step_is_open(soft, locked) is True
    assert step_is_open(hard, locked) is False
    assert step_is_open(hard, RoadmapStepState.AVAILABLE.value) is True


# ---------------------------------------------------------------------------
# The six completion rules
# ---------------------------------------------------------------------------


async def test_count_rule_counts_completed_checkins(db):
    author = await make_user(db, "Author")
    walker = await make_user(db, "Walker")
    roadmap = await make_roadmap(db, author)
    challenge = await make_challenge(db, author, cadence=DAILY)
    await add_step(db, roadmap, challenge, rule={"kind": "count", "n": 3})
    enrollment = await _walk(db, roadmap, walker)

    await log_checkins(db, user=walker, challenge=challenge, count=2)
    await refresh_progress(db, roadmap=roadmap, enrollment=enrollment, timezone="UTC")
    await db.commit()
    assert await _states(db, enrollment) == [RoadmapStepState.IN_PROGRESS.value]

    await log_checkins(db, user=walker, challenge=challenge, count=1)
    await refresh_progress(db, roadmap=roadmap, enrollment=enrollment, timezone="UTC")
    await db.commit()
    assert await _states(db, enrollment) == [RoadmapStepState.COMPLETED.value]


async def test_a_skipped_checkin_is_not_progress(db):
    """`skipped` is a state a member records on purpose, and it is not doing
    the thing."""
    author = await make_user(db, "Author")
    walker = await make_user(db, "Walker")
    roadmap = await make_roadmap(db, author)
    challenge = await make_challenge(db, author, cadence=DAILY)
    await add_step(db, roadmap, challenge, rule={"kind": "count", "n": 1})
    enrollment = await _walk(db, roadmap, walker)

    await log_checkins(db, user=walker, challenge=challenge, count=3, state="skipped")
    await refresh_progress(db, roadmap=roadmap, enrollment=enrollment, timezone="UTC")
    await db.commit()
    assert await _states(db, enrollment) == [RoadmapStepState.AVAILABLE.value]


async def test_streak_rule_reads_the_recomputed_column(db):
    author = await make_user(db, "Author")
    walker = await make_user(db, "Walker")
    roadmap = await make_roadmap(db, author)
    challenge = await make_challenge(db, author, cadence=DAILY)
    await add_step(db, roadmap, challenge, rule={"kind": "streak", "n": 4})
    enrollment = await _walk(db, roadmap, walker)

    await set_streak(db, user=walker, challenge=challenge, n=3)
    await refresh_progress(db, roadmap=roadmap, enrollment=enrollment, timezone="UTC")
    await db.commit()
    assert await _states(db, enrollment) == [RoadmapStepState.AVAILABLE.value]

    await set_streak(db, user=walker, challenge=challenge, n=4)
    await refresh_progress(db, roadmap=roadmap, enrollment=enrollment, timezone="UTC")
    await db.commit()
    assert await _states(db, enrollment) == [RoadmapStepState.COMPLETED.value]


async def test_amount_rule_sums_recorded_amounts(db):
    author = await make_user(db, "Author")
    walker = await make_user(db, "Walker")
    roadmap = await make_roadmap(db, author)
    challenge = await make_challenge(db, author, cadence=DAILY, goal_unit="کیلومتر")
    await add_step(db, roadmap, challenge, rule={"kind": "amount", "target": "10"})
    enrollment = await _walk(db, roadmap, walker)

    await log_checkins(db, user=walker, challenge=challenge, count=2, amount=4)
    await refresh_progress(db, roadmap=roadmap, enrollment=enrollment, timezone="UTC")
    await db.commit()
    assert await _states(db, enrollment) == [RoadmapStepState.IN_PROGRESS.value]

    await log_checkins(db, user=walker, challenge=challenge, count=1, amount=3)
    await refresh_progress(db, roadmap=roadmap, enrollment=enrollment, timezone="UTC")
    await db.commit()
    assert await _states(db, enrollment) == [RoadmapStepState.COMPLETED.value]


async def test_duration_rule_measures_from_when_the_step_opened(db):
    author = await make_user(db, "Author")
    walker = await make_user(db, "Walker")
    roadmap = await make_roadmap(db, author)
    challenge = await make_challenge(db, author, cadence=DAILY)
    await add_step(db, roadmap, challenge, rule={"kind": "duration", "days": 30})
    enrollment = await _walk(db, roadmap, walker)
    assert await _states(db, enrollment) == [RoadmapStepState.AVAILABLE.value]

    later = datetime.now(UTC) + timedelta(days=31)
    await refresh_progress(
        db, roadmap=roadmap, enrollment=enrollment, timezone="UTC", now=later
    )
    await db.commit()
    assert await _states(db, enrollment) == [RoadmapStepState.COMPLETED.value]


async def test_manual_is_the_only_rule_a_member_may_declare(client, db):
    author = await make_user(db, "Author")
    walker = await make_user(db, "Walker")
    roadmap = await make_roadmap(db, author)
    manual_challenge = await make_challenge(db, author, title="دستی")
    counted = await make_challenge(db, author, title="شمارشی", cadence=DAILY)
    manual_step = await add_step(db, roadmap, manual_challenge, rule={"kind": "manual"})
    counted_step = await add_step(
        db, roadmap, counted, rule={"kind": "count", "n": 5}
    )
    await _walk(db, roadmap, walker)

    sign_in(client, walker)
    ok = await client.post(
        f"/roadmaps/{roadmap.id}/steps/{manual_step.id}/complete"
    )
    assert ok.status_code == 200

    # The other five rules are measurements, so «تمام شد» on one would be a
    # way past the condition the builder set.
    refused = await client.post(
        f"/roadmaps/{roadmap.id}/steps/{counted_step.id}/complete"
    )
    assert refused.status_code == 409


# ---------------------------------------------------------------------------
# A step nobody could ever leave behind
# ---------------------------------------------------------------------------


async def test_challenge_finished_is_refused_on_an_endless_cadence(client, db):
    """A `recurring_*` challenge with no end never reads «تمام شده», so a step
    carrying that rule on one is a step everybody gets stuck behind. Refused
    at the write boundary, in Farsi, because the builder is who has to fix
    it."""
    author = await make_user(db, "Author")
    endless = await make_challenge(db, author, title="بی‌انتها", cadence=DAILY)
    bounded = await make_challenge(db, author, title="کراندار")
    roadmap = await make_roadmap(db, author)

    assert is_bounded(endless) is False
    assert is_bounded(bounded) is True
    assert rule_refusal(ChallengeFinishedRule(), endless) is not None
    assert rule_refusal(ChallengeFinishedRule(), bounded) is None

    sign_in(client, author)
    refused = await client.post(
        f"/roadmaps/{roadmap.id}/steps",
        json={
            "challenge_id": endless.id,
            "completion_rule": {"kind": "challenge_finished"},
        },
    )
    assert refused.status_code == 422

    allowed = await client.post(
        f"/roadmaps/{roadmap.id}/steps",
        json={
            "challenge_id": bounded.id,
            "completion_rule": {"kind": "challenge_finished"},
        },
    )
    assert allowed.status_code == 201


async def test_amount_is_refused_on_a_challenge_with_no_unit(client, db):
    """A check-in's `amount` is NULL unless the challenge has a `goal_unit`,
    so this rule would sum nothing forever."""
    author = await make_user(db, "Author")
    unitless = await make_challenge(db, author, cadence=DAILY)
    roadmap = await make_roadmap(db, author)
    sign_in(client, author)
    refused = await client.post(
        f"/roadmaps/{roadmap.id}/steps",
        json={
            "challenge_id": unitless.id,
            "completion_rule": {"kind": "amount", "target": "10"},
        },
    )
    assert refused.status_code == 422


# ---------------------------------------------------------------------------
# An archived challenge must not strand the course
# ---------------------------------------------------------------------------


async def test_archiving_a_challenge_skips_its_step_and_moves_on(client, db):
    author = await make_user(db, "Author")
    walker = await make_user(db, "Walker")
    walker_id = walker.id
    roadmap = await make_roadmap(db, author)
    first = await make_challenge(db, author, title="اول", cadence=DAILY)
    second = await make_challenge(db, author, title="دوم", cadence=DAILY)
    await add_step(db, roadmap, first, rule={"kind": "count", "n": 99})
    await add_step(db, roadmap, second)
    enrollment = await _walk(db, roadmap, walker)
    enrollment_id, second_id = enrollment.id, second.id
    assert await _states(db, enrollment_id) == [
        RoadmapStepState.AVAILABLE.value,
        RoadmapStepState.LOCKED.value,
    ]

    sign_in(client, author)
    archived = await client.patch(
        f"/challenges/{first.id}", json={"lifecycle_status": "archived"}
    )
    assert archived.status_code == 200

    # The app wrote through its own session; this one has to be told.
    db.expire_all()
    assert await _states(db, enrollment_id) == [
        RoadmapStepState.SKIPPED.value,
        RoadmapStepState.AVAILABLE.value,
    ]
    # And the member is actually in the next challenge, not merely told about
    # it: the skip has to open the stage, or the course is stopped anyway.
    assert await _enrolled_id(db, walker_id, second_id)


# ---------------------------------------------------------------------------
# The structural lock
# ---------------------------------------------------------------------------


async def test_structure_freezes_once_somebody_else_is_walking(client, db):
    author = await make_user(db, "Author")
    walker = await make_user(db, "Walker")
    roadmap = await make_roadmap(db, author)
    a = await make_challenge(db, author, title="الف", cadence=DAILY)
    b = await make_challenge(db, author, title="ب", cadence=DAILY)
    c = await make_challenge(db, author, title="ج", cadence=DAILY)
    step_a = await add_step(db, roadmap, a, rule={"kind": "count", "n": 3})
    step_b = await add_step(db, roadmap, b)

    sign_in(client, author)
    # Before anybody else joins the builder may still rearrange freely.
    assert (
        await client.post(f"/roadmaps/{roadmap.id}/steps/{step_b.id}/move?direction=up")
    ).status_code == 200
    assert (
        await client.patch(
            f"/roadmaps/{roadmap.id}/steps/{step_a.id}",
            json={"completion_rule": {"kind": "count", "n": 9}},
        )
    ).status_code == 200

    await _walk(db, roadmap, walker)

    assert (
        await client.post(f"/roadmaps/{roadmap.id}/steps/{step_b.id}/move?direction=up")
    ).status_code == 409
    assert (
        await client.patch(
            f"/roadmaps/{roadmap.id}/steps/{step_a.id}",
            json={"completion_rule": {"kind": "count", "n": 1}},
        )
    ).status_code == 409
    assert (
        await client.patch(f"/roadmaps/{roadmap.id}", json={"strict": True})
    ).status_code == 409

    # Appending stays free -- there is nobody down there to disturb -- and so
    # does removing a step, which can only ever unblock somebody.
    appended = await client.post(
        f"/roadmaps/{roadmap.id}/steps",
        json={"challenge_id": c.id, "completion_rule": {"kind": "manual"}},
    )
    assert appended.status_code == 201
    assert (
        await client.delete(f"/roadmaps/{roadmap.id}/steps/{step_b.id}")
    ).status_code == 204
    # Editing what the course *says* is never structural.
    assert (
        await client.patch(f"/roadmaps/{roadmap.id}", json={"title": "نام تازه"})
    ).status_code == 200


async def test_deleting_is_refused_once_somebody_else_has_started(client, db):
    author = await make_user(db, "Author")
    walker = await make_user(db, "Walker")
    roadmap = await make_roadmap(db, author)
    challenge = await make_challenge(db, author, cadence=DAILY)
    await add_step(db, roadmap, challenge)
    await _walk(db, roadmap, walker)

    sign_in(client, author)
    assert (await client.delete(f"/roadmaps/{roadmap.id}")).status_code == 409
    # Archiving is the supported exit, and it leaves every walk intact.
    assert (
        await client.patch(
            f"/roadmaps/{roadmap.id}", json={"lifecycle_status": "archived"}
        )
    ).status_code == 200


# ---------------------------------------------------------------------------
# The screens render at all
# ---------------------------------------------------------------------------


async def test_every_roadmap_screen_renders(client, db):
    """The five registration contracts, checked rather than trusted.

    Every views router builds its own Jinja environment and owes the
    registrations for what it renders -- icons, media, avatars, the invite
    state labels, the roadmap's own maps -- and forgetting one is a **render**
    error, invisible until somebody opens that page. `tests/test_explainers.py`
    makes the same call for the explainer global; this walks the screens
    themselves, including the home dashboard's card, which is rendered by a
    different router than the rest.
    """
    author = await make_user(db, "Author")
    roadmap = await make_roadmap(db, author, title="مسیر آرامش")
    challenge = await make_challenge(db, author, title="کتاب بخوان", cadence=DAILY)
    await add_step(db, roadmap, challenge, rule={"kind": "count", "n": 12})
    await _walk(db, roadmap, author)

    invite = RoadmapInvite(
        roadmap_id=roadmap.id, code=new_invite_code(), created_by_user_id=author.id
    )
    db.add(invite)
    await db.commit()
    code = invite.code

    sign_in(client, author)
    for path in (
        "/views/roadmaps/",
        "/views/roadmaps/fragment",
        f"/views/roadmaps/{roadmap.id}",
        f"/views/roadmaps/{roadmap.id}/manage",
        f"/views/roadmap-invites/{code}",
        # The «قدم فعلی تو» card lives on a page owned by another router.
        "/views/home/",
    ):
        response = await client.get(path)
        assert response.status_code == 200, path

    detail = (await client.get(f"/views/roadmaps/{roadmap.id}")).text
    assert "کتاب بخوان" in detail
    assert "۱۲ بار ثبت کنی" in detail or "12 بار ثبت کنی" in detail
    assert "مسیر آرامش" in (await client.get("/views/home/")).text
