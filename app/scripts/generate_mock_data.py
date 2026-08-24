"""
generate_mock_data.py
Uses the real ORM models via async SQLAlchemy session.
Run from project root: python -m app.scripts.generate_mock_data
"""

import asyncio
import random
from datetime import datetime, timedelta, timezone

from faker import Faker
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import (
    AsyncSessionLocal as async_session_maker,
)  # adjust import to your actual session factory
from app.models.user import User
from app.models.challenge import (
    ChallengeCategory,
    RecurringChallenge,
    OneTimeChallenge,
    RecurringType,
)
from app.models.enrollment import Enrollment, EnrollmentStatus

fake = Faker()

NUM_USERS = 10
NUM_CHALLENGES = 30
NUM_ENROLLMENTS = 60


async def create_users(session: AsyncSession) -> list[User]:
    users = []
    for _ in range(NUM_USERS):
        user = User(
            name=fake.name(),
            email=fake.unique.email(),
            password="hashed_password_placeholder",
        )
        session.add(user)
        users.append(user)
    await session.flush()
    return users


async def create_challenges(session: AsyncSession, users: list[User]) -> list:
    challenges = []
    for _ in range(NUM_CHALLENGES):
        owner = random.choice(users)
        category = random.choice(list(ChallengeCategory))
        due_date = datetime.now(timezone.utc) + timedelta(days=random.randint(5, 60))
        common_kwargs = dict(
            title=fake.sentence(nb_words=4).rstrip("."),
            description=fake.paragraph(nb_sentences=3),
            rules=fake.sentence(nb_words=8),
            due_date=due_date,
            owner_id=owner.id,
            category=category,
        )

        if random.random() < 0.5:
            challenge = OneTimeChallenge(**common_kwargs)
        else:
            challenge = RecurringChallenge(
                **common_kwargs,
                recurrence_pattern=random.choice(list(RecurringType)),
                interval=random.randint(1, 3),
                end_date=due_date + timedelta(days=30),
            )

        session.add(challenge)
        challenges.append(challenge)
    await session.flush()
    return challenges


async def create_enrollments(
    session: AsyncSession, users: list[User], challenges: list
):
    pairs_used = set()
    created = 0
    while created < NUM_ENROLLMENTS:
        user = random.choice(users)
        challenge = random.choice(challenges)
        key = (user.id, challenge.id)
        if key in pairs_used:
            continue
        pairs_used.add(key)

        status = random.choice(list(EnrollmentStatus))
        completed_count = (
            random.randint(0, 10)
            if status == EnrollmentStatus.DONE
            else random.randint(0, 5)
        )

        enrollment = Enrollment(
            user_id=user.id,
            challenge_id=challenge.id,
            status=status,
            completed_count=completed_count,
        )
        session.add(enrollment)
        created += 1


async def main():
    async with async_session_maker() as session:
        users = await create_users(session)
        challenges = await create_challenges(session, users)
        await create_enrollments(session, users, challenges)
        await session.commit()
        print(
            f"Inserted {len(users)} users, {len(challenges)} challenges, {NUM_ENROLLMENTS} enrollments."
        )


if __name__ == "__main__":
    asyncio.run(main())
