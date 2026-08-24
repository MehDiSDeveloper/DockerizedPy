# routers/views/challenge.py
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import BASE_DIR
from app.database import get_db
from app.models.challenge import (
    Challenge,
    ChallengeCategory,
    ChallengeType,
    OneTimeChallenge,
    RecurringChallenge,
)
from app.models.enrollment import Enrollment
from app.schemas.challenge import ChallengeCreateUnion

router = APIRouter(prefix="/views/challenges", tags=["challenge-views"])
templates = Jinja2Templates(directory=BASE_DIR / "templates")


@router.get("/create")
async def create_challenge_form(request: Request):
    return templates.TemplateResponse(
        "challenge/create-challenge.html",
        {"request": request},
    )


@router.post("/create")
async def create_challenge(
    challenge: ChallengeCreateUnion,
    request: Request,
    db: AsyncSession = Depends(get_db),  # noqa: B008
):
    try:
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
        result = await db.execute(
            select(Challenge).where(Challenge.title == challenge.title)
        )
        print(result.scalar_one_or_none())
        return templates.TemplateResponse("home/explore.html", {"request": request})
    except HTTPException:
        await db.rollback()
        raise
    except SQLAlchemyError as exc:
        await db.rollback()
        return templates.TemplateResponse(
            "common/error.html",
            {"request": request, "error": str(exc)},
            status_code=500,
        )


@router.get("/")
async def challenge_list(request: Request, db: AsyncSession = Depends(get_db)):  # noqa: B008
    result = await db.execute(
        select(Challenge).options(selectinload(Challenge.enrollments))
    )
    challenges = result.scalars().all()
    return templates.TemplateResponse(
        "challenge/challenge-list.html",
        {"request": request, "challenges": challenges, "categories": ChallengeCategory},
    )


@router.get("/{challenge_id}")
async def challenge_detail(
    challenge_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),  # noqa: B008
):
    result = await db.execute(
        select(Challenge)
        .options(selectinload(Challenge.enrollments).selectinload(Enrollment.user))
        .where(Challenge.id == challenge_id)
    )
    db_challenge = result.scalar_one_or_none()
    if not db_challenge:
        raise HTTPException(status_code=404, detail="Challenge not found")

    is_enrolled = any(e.user_id == 1 for e in db_challenge.enrollments)

    return templates.TemplateResponse(
        "challenge/challenge-detail.html",
        {"request": request, "challenge": db_challenge, "is_enrolled": is_enrolled},
    )
