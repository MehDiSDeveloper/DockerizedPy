from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.challenge import (
    Challenge,
    ChallengeType,
    OneTimeChallenge,
    RecurringChallenge,
)
from app.schemas.challenge import (
    ChallengeCreateUnion,
    ChallengeReadUnion,
    ChallengeUpdate,
)

router = APIRouter(prefix="/challenges", tags=["challenges"])


@router.post("/", response_model=ChallengeReadUnion, status_code=201)
async def create_challenge(
    challenge: ChallengeCreateUnion, db: AsyncSession = Depends(get_db)
):
    if challenge.challenge_type == ChallengeType.OneTimeChallenge:
        db_challenge = OneTimeChallenge(**challenge.model_dump())
    elif challenge.challenge_type == ChallengeType.RecurringChallenge:
        db_challenge = RecurringChallenge(**challenge.model_dump())
    else:
        raise HTTPException(status_code=422, detail="Unknown challenge_type")
    db_challenge.last_modifier_user_id = 1
    db_challenge.owner_id = 1
    db.add(db_challenge)
    await db.commit()
    await db.refresh(db_challenge)
    return db_challenge


@router.get("/", response_model=list[ChallengeReadUnion])
async def list_challenges(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Challenge))
    return result.scalars().all()


@router.get("/{challenge_id}", response_model=ChallengeReadUnion)
async def get_challenge(challenge_id: int, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Challenge).where(Challenge.id == challenge_id))
    db_challenge = result.scalar_one_or_none()
    if not db_challenge:
        raise HTTPException(status_code=404, detail="Challenge not found")
    return db_challenge


@router.patch("/{challenge_id}", response_model=ChallengeReadUnion)
async def update_challenge(
    challenge_id: int, challenge: ChallengeUpdate, db: AsyncSession = Depends(get_db)
):
    result = await db.execute(select(Challenge).where(Challenge.id == challenge_id))
    db_challenge = result.scalar_one_or_none()
    if not db_challenge:
        raise HTTPException(status_code=404, detail="Challenge not found")

    for field, value in challenge.model_dump(exclude_unset=True).items():
        setattr(db_challenge, field, value)

    await db.commit()
    await db.refresh(db_challenge)
    return db_challenge


@router.delete("/{challenge_id}", status_code=204)
async def delete_challenge(challenge_id: int, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Challenge).where(Challenge.id == challenge_id))
    db_challenge = result.scalar_one_or_none()
    if not db_challenge:
        raise HTTPException(status_code=404, detail="Challenge not found")

    await db.delete(db_challenge)
    await db.commit()
