from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.enrollment import Enrollment
from app.schemas.enrollment import EnrollmentRead

router = APIRouter(prefix="/enrollments", tags=["enrollments"])

CURRENT_USER_ID = 1  # TODO: replace with real auth


@router.post("/{challenge_id}", response_model=EnrollmentRead, status_code=201)
async def enroll(challenge_id: int, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(Enrollment).where(
            Enrollment.challenge_id == challenge_id,
            Enrollment.user_id == CURRENT_USER_ID,
        )
    )
    if result.scalar_one_or_none():
        raise HTTPException(status_code=409, detail="Already enrolled")

    enrollment = Enrollment(challenge_id=challenge_id, user_id=CURRENT_USER_ID)
    db.add(enrollment)
    await db.commit()
    await db.refresh(enrollment)
    return enrollment


@router.delete("/{challenge_id}", status_code=204)
async def unenroll(challenge_id: int, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(Enrollment).where(
            Enrollment.challenge_id == challenge_id,
            Enrollment.user_id == CURRENT_USER_ID,
        )
    )
    enrollment = result.scalar_one_or_none()
    if not enrollment:
        raise HTTPException(status_code=404, detail="Enrollment not found")

    await db.delete(enrollment)
    await db.commit()
