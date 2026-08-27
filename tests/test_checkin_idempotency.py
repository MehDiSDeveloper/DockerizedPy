from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import create_session_cookie, hash_password
from app.models.challenge import Challenge, ChallengeCategory
from app.models.checkin import CheckIn
from app.models.enrollment import Enrollment
from app.models.stats import ChallengeStats
from app.models.user import User
from app.occurrences import week_key

TZ = "Asia/Tehran"


def today_tehran() -> date:
    return datetime.now(UTC).astimezone(ZoneInfo(TZ)).date()


async def make_user(db: AsyncSession, name: str = "Test User") -> User:
    user = User(name=name, password_hash=hash_password("password123"))
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


def authenticate(client: AsyncClient, user_id: int) -> None:
    client.cookies.set("session", create_session_cookie(user_id))


async def make_challenge_with_enrollment(
    db: AsyncSession,
    owner: User,
    *,
    cadence_kind: str,
    cadence: dict,
    start_date: date,
    tz: str = TZ,
    goal_unit: str | None = None,
) -> tuple[Challenge, Enrollment]:
    challenge = Challenge(
        title="Test Challenge",
        owner_id=owner.id,
        category=ChallengeCategory.OTHER,
        cadence_kind=cadence_kind,
        cadence=cadence,
        goal_unit=goal_unit,
    )
    db.add(challenge)
    await db.flush()

    enrollment = Enrollment(
        challenge_id=challenge.id,
        user_id=owner.id,
        timezone=tz,
        start_date=start_date,
    )
    db.add(enrollment)

    stats = ChallengeStats(challenge_id=challenge.id, participant_count=1)
    db.add(stats)

    await db.commit()
    await db.refresh(challenge)
    await db.refresh(enrollment)
    return challenge, enrollment


async def post_checkin(
    client: AsyncClient, *, challenge_id: int, key: str, state: str = "completed", **extra
):
    payload = {
        "challenge_id": challenge_id,
        "occurrence_key": key,
        "state": state,
        **extra,
    }
    return await client.post("/checkins/", json=payload)


@pytest.mark.asyncio
async def test_duplicate_post_is_idempotent(client: AsyncClient, db: AsyncSession):
    owner = await make_user(db)
    authenticate(client, owner.id)
    today = today_tehran()
    challenge, _ = await make_challenge_with_enrollment(
        db,
        owner,
        cadence_kind="recurring_days",
        cadence={"kind": "recurring_days", "mode": "every_n_days", "n": 1, "weekdays": [], "end_date": None},
        start_date=today - timedelta(days=10),
    )
    key = f"D{today.isoformat()}"

    r1 = await post_checkin(client, challenge_id=challenge.id, key=key)
    assert r1.status_code == 201
    id1 = r1.json()["id"]

    r2 = await post_checkin(client, challenge_id=challenge.id, key=key)
    assert r2.status_code == 200
    assert r2.json()["id"] == id1

    count_result = await db.execute(
        select(func.count()).select_from(CheckIn).where(CheckIn.occurrence_key == key)
    )
    assert count_result.scalar_one() == 1


@pytest.mark.asyncio
async def test_concurrent_posts_produce_one_row(client: AsyncClient, db: AsyncSession):
    owner = await make_user(db)
    authenticate(client, owner.id)
    today = today_tehran()
    challenge, _ = await make_challenge_with_enrollment(
        db,
        owner,
        cadence_kind="recurring_days",
        cadence={"kind": "recurring_days", "mode": "every_n_days", "n": 1, "weekdays": [], "end_date": None},
        start_date=today - timedelta(days=10),
    )
    key = f"D{today.isoformat()}"

    r1, r2 = await asyncio.gather(
        post_checkin(client, challenge_id=challenge.id, key=key),
        post_checkin(client, challenge_id=challenge.id, key=key),
    )

    assert r1.status_code in (200, 201)
    assert r2.status_code in (200, 201)
    assert r1.json()["id"] == r2.json()["id"]

    count_result = await db.execute(
        select(func.count()).select_from(CheckIn).where(CheckIn.occurrence_key == key)
    )
    assert count_result.scalar_one() == 1


@pytest.mark.asyncio
async def test_quota_sequencing_then_refusal(client: AsyncClient, db: AsyncSession):
    owner = await make_user(db)
    authenticate(client, owner.id)
    today = today_tehran()
    challenge, _ = await make_challenge_with_enrollment(
        db,
        owner,
        cadence_kind="recurring_quota",
        cadence={"kind": "recurring_quota", "period": "week", "count": 3, "end_date": None},
        start_date=today - timedelta(days=30),
    )
    pkey = week_key(today)

    for seq in (1, 2, 3):
        resp = await post_checkin(client, challenge_id=challenge.id, key=f"{pkey}#{seq}")
        assert resp.status_code == 201, resp.text

    resp4 = await post_checkin(client, challenge_id=challenge.id, key=f"{pkey}#4")
    assert resp4.status_code == 403


@pytest.mark.asyncio
async def test_backfill_window_boundary(client: AsyncClient, db: AsyncSession):
    owner = await make_user(db)
    authenticate(client, owner.id)
    today = today_tehran()
    challenge, _ = await make_challenge_with_enrollment(
        db,
        owner,
        cadence_kind="recurring_days",
        cadence={"kind": "recurring_days", "mode": "every_n_days", "n": 1, "weekdays": [], "end_date": None},
        start_date=today - timedelta(days=30),
    )

    key_2_days_back = f"D{(today - timedelta(days=2)).isoformat()}"
    resp_ok = await post_checkin(client, challenge_id=challenge.id, key=key_2_days_back)
    assert resp_ok.status_code == 201

    key_3_days_back = f"D{(today - timedelta(days=3)).isoformat()}"
    resp_refused = await post_checkin(client, challenge_id=challenge.id, key=key_3_days_back)
    assert resp_refused.status_code == 403


@pytest.mark.asyncio
async def test_patch_inside_window_ok_outside_window_403(
    client: AsyncClient, db: AsyncSession
):
    owner = await make_user(db)
    authenticate(client, owner.id)
    today = today_tehran()
    challenge, enrollment = await make_challenge_with_enrollment(
        db,
        owner,
        cadence_kind="recurring_days",
        cadence={"kind": "recurring_days", "mode": "every_n_days", "n": 1, "weekdays": [], "end_date": None},
        start_date=today - timedelta(days=30),
    )

    key = f"D{today.isoformat()}"
    created = await post_checkin(client, challenge_id=challenge.id, key=key)
    assert created.status_code == 201
    checkin_id = created.json()["id"]

    patched = await client.patch(f"/checkins/{checkin_id}", json={"note": "updated"})
    assert patched.status_code == 200
    assert patched.json()["note"] == "updated"

    stale_date = today - timedelta(days=10)
    stale_checkin = CheckIn(
        enrollment_id=enrollment.id,
        challenge_id=challenge.id,
        occurrence_key=f"D{stale_date.isoformat()}",
        state="completed",
        occurrence_local_date=stale_date,
        occurred_at_utc=datetime.now(UTC),
        timezone=enrollment.timezone,
    )
    db.add(stale_checkin)
    await db.commit()
    await db.refresh(stale_checkin)

    stale_patch = await client.patch(f"/checkins/{stale_checkin.id}", json={"note": "too late"})
    assert stale_patch.status_code == 403


@pytest.mark.asyncio
async def test_goal_unit_requires_amount(client: AsyncClient, db: AsyncSession):
    owner = await make_user(db)
    authenticate(client, owner.id)
    today = today_tehran()
    challenge, _ = await make_challenge_with_enrollment(
        db,
        owner,
        cadence_kind="once",
        cadence={"kind": "once"},
        start_date=today,
        goal_unit="km",
    )

    missing_amount = await post_checkin(client, challenge_id=challenge.id, key="single")
    assert missing_amount.status_code == 422

    with_amount = await post_checkin(
        client, challenge_id=challenge.id, key="single", amount="5.0"
    )
    assert with_amount.status_code == 201


@pytest.mark.asyncio
async def test_streak_counters_after_run_of_completions(
    client: AsyncClient, db: AsyncSession
):
    owner = await make_user(db)
    authenticate(client, owner.id)
    today = today_tehran()
    start = today - timedelta(days=2)
    challenge, enrollment = await make_challenge_with_enrollment(
        db,
        owner,
        cadence_kind="recurring_days",
        cadence={"kind": "recurring_days", "mode": "every_n_days", "n": 1, "weekdays": [], "end_date": None},
        start_date=start,
    )

    # BACKFILL_DAYS=2, so the oldest occurrence this run can reach through the
    # API is exactly two days back -- the run below spans the full window.
    for i in range(3):
        d = start + timedelta(days=i)
        resp = await post_checkin(client, challenge_id=challenge.id, key=f"D{d.isoformat()}")
        assert resp.status_code == 201, resp.text

    await db.refresh(enrollment)
    assert enrollment.current_streak == 3
    assert enrollment.longest_streak == 3


@pytest.mark.asyncio
async def test_challenge_stats_total_completions_matches_row_count(
    client: AsyncClient, db: AsyncSession
):
    owner = await make_user(db)
    authenticate(client, owner.id)
    today = today_tehran()
    start = today - timedelta(days=2)
    challenge, _ = await make_challenge_with_enrollment(
        db,
        owner,
        cadence_kind="recurring_days",
        cadence={"kind": "recurring_days", "mode": "every_n_days", "n": 1, "weekdays": [], "end_date": None},
        start_date=start,
    )

    states = ["completed", "completed", "skipped"]
    for i, state in enumerate(states):
        d = start + timedelta(days=i)
        resp = await post_checkin(
            client, challenge_id=challenge.id, key=f"D{d.isoformat()}", state=state
        )
        assert resp.status_code == 201, resp.text

    count_result = await db.execute(
        select(func.count())
        .select_from(CheckIn)
        .where(CheckIn.challenge_id == challenge.id, CheckIn.state == "completed")
    )
    completed_rows = count_result.scalar_one()

    stats_result = await db.execute(
        select(ChallengeStats).where(ChallengeStats.challenge_id == challenge.id)
    )
    stats = stats_result.scalar_one()
    await db.refresh(stats)
    assert stats.total_completions == completed_rows == 2
