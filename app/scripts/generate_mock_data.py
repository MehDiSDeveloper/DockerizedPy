"""
generate_mock_data.py
Uses the real ORM models via async SQLAlchemy session.
Run from project root: python -m app.scripts.generate_mock_data
"""

import asyncio
import random
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from faker import Faker
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import hash_password
from app.avatars import AVATAR_IDS
from app.database import (
    AsyncSessionLocal as async_session_maker,
)  # adjust import to your actual session factory
from app.models.challenge import (
    Challenge,
    ChallengeCategory,
    LifecycleStatus,
    Visibility,
)
from app.models.checkin import CheckIn
from app.models.enrollment import Enrollment, EnrollmentStatus
from app.models.stats import ChallengeStats
from app.models.user import User
from app.occurrences import compute_streaks, is_occurrence_day, local_today, period_key
from app.schemas.cadence import (
    CadenceUnion,
    OnceCadence,
    RecurringDaysCadence,
    RecurringQuotaCadence,
    ScheduleCadence,
)

fake = Faker("fa_IR")

NUM_USERS = 10
NUM_CHALLENGES = 30
NUM_ENROLLMENTS = 60  # includes the mandatory owner auto-enrollment (D1) per challenge
CHECKIN_WINDOW_DAYS = 30
GOAL_UNITS = ["کیلومتر", "دقیقه", "صفحه", "لیوان آب"]
DEFAULT_TIMEZONE = "Asia/Tehran"


async def create_users(session: AsyncSession) -> list[User]:
    users = []
    for _ in range(NUM_USERS):
        user = User(
            name=fake.name(),
            email=fake.unique.email(),
            password_hash=hash_password("placeholder-password"),
            # One seeded user is left without a pick so the placeholder is
            # visible in a freshly seeded database, not only on legacy rows.
            avatar=random.choice(AVATAR_IDS) if len(users) else None,
        )
        session.add(user)
        users.append(user)
    await session.flush()
    return users


def random_cadence(now: datetime) -> CadenceUnion:
    kind = random.choice(["once", "schedule", "recurring_days", "recurring_quota"])

    if kind == "once":
        return OnceCadence()

    if kind == "schedule":
        count = random.randint(1, 5)
        picked = set()
        while len(picked) < count:
            dt = now - timedelta(days=random.randint(0, CHECKIN_WINDOW_DAYS - 5))
            picked.add(dt.replace(minute=0, second=0, microsecond=0))
        return ScheduleCadence(datetimes=sorted(picked))

    if kind == "recurring_days":
        mode = random.choice(["weekdays", "every_n_days", "every_n_weeks", "every_n_months"])
        if mode == "weekdays":
            weekdays = sorted(random.sample(range(7), k=random.randint(1, 3)))
            return RecurringDaysCadence(mode=mode, weekdays=weekdays)
        return RecurringDaysCadence(mode=mode, n=random.randint(1, 3))

    # recurring_quota
    return RecurringQuotaCadence(
        period=random.choice(["week", "month"]), count=random.randint(2, 5)
    )


async def create_challenges(
    session: AsyncSession, users: list[User]
) -> list[tuple[Challenge, CadenceUnion]]:
    now = datetime.now(UTC)
    challenges = []
    for _ in range(NUM_CHALLENGES):
        owner = random.choice(users)
        category = random.choice(list(ChallengeCategory))
        due_date = now + timedelta(days=random.randint(5, 60))
        visibility = random.choices(
            [Visibility.PUBLIC.value, Visibility.UNLISTED.value, Visibility.PRIVATE.value],
            weights=[0.8, 0.1, 0.1],
        )[0]
        cadence = random_cadence(now)
        has_goal = random.random() < 0.3
        goal_amount = Decimal(random.randint(20, 200)) if has_goal else None
        goal_unit = random.choice(GOAL_UNITS) if has_goal else None

        challenge = Challenge(
            title=fake.sentence(nb_words=4).rstrip(".")[:50],  # Challenges.title is VARCHAR(50)
            description=fake.paragraph(nb_sentences=3),
            rules=fake.sentence(nb_words=8),
            due_date=due_date,
            owner_id=owner.id,
            category=category,
            visibility=visibility,
            lifecycle_status=LifecycleStatus.ACTIVE.value,
            cadence_kind=cadence.kind,
            cadence=cadence.model_dump(mode="json"),
            goal_amount=goal_amount,
            goal_unit=goal_unit,
        )
        session.add(challenge)
        challenges.append((challenge, cadence))
    await session.flush()
    return challenges


async def create_enrollments(
    session: AsyncSession,
    users: list[User],
    challenges: list[tuple[Challenge, CadenceUnion]],
    now: datetime,
) -> list[tuple[Enrollment, Challenge, CadenceUnion]]:
    """D1: every owner is auto-enrolled in their own challenge; the remaining
    budget is spread as random extra (user, challenge) enrollments."""
    enrollments = []
    pairs_used = set()

    def add_enrollment(user_id: int, challenge: Challenge, cadence: CadenceUnion):
        start_date = local_today(DEFAULT_TIMEZONE, now) - timedelta(
            days=random.randint(5, CHECKIN_WINDOW_DAYS - 2)
        )
        status = random.choices(
            [
                EnrollmentStatus.ACTIVE.value,
                EnrollmentStatus.COMPLETED.value,
                EnrollmentStatus.ABANDONED.value,
            ],
            weights=[0.7, 0.2, 0.1],
        )[0]
        enrollment = Enrollment(
            user_id=user_id,
            challenge_id=challenge.id,
            status=status,
            timezone=DEFAULT_TIMEZONE,
            start_date=start_date,
        )
        session.add(enrollment)
        enrollments.append((enrollment, challenge, cadence))

    for challenge, cadence in challenges:
        pairs_used.add((challenge.owner_id, challenge.id))
        add_enrollment(challenge.owner_id, challenge, cadence)

    extra_budget = max(0, NUM_ENROLLMENTS - len(challenges))
    attempts = 0
    created_extra = 0
    while created_extra < extra_budget and attempts < extra_budget * 20:
        attempts += 1
        user = random.choice(users)
        challenge, cadence = random.choice(challenges)
        key = (user.id, challenge.id)
        if key in pairs_used:
            continue
        pairs_used.add(key)
        add_enrollment(user.id, challenge, cadence)
        created_extra += 1

    await session.flush()
    return enrollments


def _random_checkin_state() -> str:
    return random.choices(["completed", "skipped"], weights=[0.8, 0.2])[0]


def _make_checkin(
    enrollment: Enrollment,
    challenge: Challenge,
    key: str,
    local_date,
    state: str,
    tz: str,
) -> CheckIn:
    amount = None
    unit = None
    if challenge.goal_unit and state == "completed":
        unit = challenge.goal_unit
        amount = Decimal(str(round(random.uniform(1, 20), 2)))
    occurred_at_utc = datetime(
        local_date.year, local_date.month, local_date.day, tzinfo=UTC
    ) + timedelta(hours=random.randint(6, 22))
    return CheckIn(
        enrollment_id=enrollment.id,
        challenge_id=challenge.id,
        occurrence_key=key,
        state=state,
        occurrence_local_date=local_date,
        occurred_at_utc=occurred_at_utc,
        timezone=tz,
        amount=amount,
        unit=unit,
        note=fake.sentence(nb_words=6) if random.random() < 0.2 else None,
        photo_url=fake.image_url() if random.random() < 0.1 else None,
    )


async def create_checkins_and_stats(
    session: AsyncSession,
    enrollments: list[tuple[Enrollment, Challenge, CadenceUnion]],
    now: datetime,
) -> None:
    stats_by_challenge: dict[int, dict] = {}

    def touch_stats(challenge: Challenge):
        return stats_by_challenge.setdefault(
            challenge.id,
            {
                "participant_count": 0,
                "total_completions": 0,
                "total_amount": Decimal(0),
                "last_checkin_at": None,
            },
        )

    def record_checkin(challenge: Challenge, checkin: CheckIn):
        session.add(checkin)
        if checkin.state != "completed":
            return
        stats = touch_stats(challenge)
        stats["total_completions"] += 1
        if checkin.amount:
            stats["total_amount"] += checkin.amount
        if stats["last_checkin_at"] is None or checkin.occurred_at_utc > stats["last_checkin_at"]:
            stats["last_checkin_at"] = checkin.occurred_at_utc

    for enrollment, challenge, cadence in enrollments:
        touch_stats(challenge)["participant_count"] += 1

        tz = enrollment.timezone
        today = local_today(tz, now)
        window_start = max(enrollment.start_date, today - timedelta(days=CHECKIN_WINDOW_DAYS))
        completed_keys: set[str] = set()
        period_completed_counts: dict[str, int] = {}

        if isinstance(cadence, OnceCadence):
            if random.random() < 0.6:
                key = "single"
                state = "completed"
                record_checkin(
                    challenge,
                    _make_checkin(enrollment, challenge, key, enrollment.start_date, state, tz),
                )
                completed_keys.add(key)

        elif isinstance(cadence, ScheduleCadence):
            for dt in cadence.datetimes:
                local_date = dt.astimezone(ZoneInfo(tz)).date()
                if local_date < enrollment.start_date or local_date > today:
                    continue
                if random.random() >= 0.7:
                    continue
                key = f"S{dt.isoformat()}"
                state = _random_checkin_state()
                record_checkin(
                    challenge, _make_checkin(enrollment, challenge, key, local_date, state, tz)
                )
                if state == "completed":
                    completed_keys.add(key)

        elif isinstance(cadence, RecurringDaysCadence):
            d = window_start
            while d <= today:
                if is_occurrence_day(cadence, enrollment.start_date, d) and random.random() < 0.7:
                    key = f"D{d.isoformat()}"
                    state = _random_checkin_state()
                    record_checkin(
                        challenge, _make_checkin(enrollment, challenge, key, d, state, tz)
                    )
                    if state == "completed":
                        completed_keys.add(key)
                d += timedelta(days=1)

        elif isinstance(cadence, RecurringQuotaCadence):
            seen_periods: set[str] = set()
            d = window_start
            while d <= today:
                pkey = period_key(d, cadence.period)
                if pkey not in seen_periods:
                    seen_periods.add(pkey)
                    target = random.randint(0, cadence.count)
                    for seq in range(1, target + 1):
                        state = _random_checkin_state()
                        key = f"{pkey}#{seq}"
                        record_checkin(
                            challenge, _make_checkin(enrollment, challenge, key, d, state, tz)
                        )
                        if state == "completed":
                            period_completed_counts[pkey] = (
                                period_completed_counts.get(pkey, 0) + 1
                            )
                d += timedelta(days=1)

        current_streak, longest_streak = compute_streaks(
            cadence,
            start_date=enrollment.start_date,
            tz=tz,
            now_utc=now,
            completed_keys=completed_keys,
            period_completed_counts=period_completed_counts,
        )
        enrollment.current_streak = current_streak
        enrollment.longest_streak = longest_streak

    for challenge_id, stats in stats_by_challenge.items():
        session.add(
            ChallengeStats(
                challenge_id=challenge_id,
                participant_count=stats["participant_count"],
                total_completions=stats["total_completions"],
                total_amount=stats["total_amount"],
                last_checkin_at=stats["last_checkin_at"],
            )
        )


async def main():
    async with async_session_maker() as session:
        now = datetime.now(UTC)
        users = await create_users(session)
        challenges = await create_challenges(session, users)
        enrollments = await create_enrollments(session, users, challenges, now)
        await create_checkins_and_stats(session, enrollments, now)
        await session.commit()
        print(
            f"Inserted {len(users)} users, {len(challenges)} challenges, "
            f"{len(enrollments)} enrollments."
        )


if __name__ == "__main__":
    asyncio.run(main())
