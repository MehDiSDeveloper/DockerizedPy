from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.user import User
from app.schemas.user import UserRead, UserCreate, UserUpdate, UserUpdate


router = APIRouter(prefix="/users", tags=["users"])


@router.get("/", response_model=list[UserRead])
async def list_users(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(User))
    return result.scalars().all()


@router.get("/{user_id}", response_model=UserRead)
async def get_user(user_id: int, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(User).where(User.id == user_id))
    db_user = result.scalar_one_or_none()
    if not db_user:
        raise HTTPException(status_code=404, detail="User not found")
    return db_user


@router.post("/", response_model=UserRead, status_code=201)
async def create_user(user: UserCreate, db: AsyncSession = Depends(get_db)):  # noqa: B008
    db_user = User(**user.model_dump(exclude={"confirm_password"}))
    db.add(db_user)
    await db.commit()
    await db.refresh(db_user)
    return db_user


@router.patch("/{user_id}", response_model=UserRead)
async def update_user(
    user_id: int, user: UserUpdate, db: AsyncSession = Depends(get_db)
):  # noqa: B008
    result = await db.execute(select(User).where(User.id == user_id))
    db_user = result.scalar_one_or_none()
    if not db_user:
        raise HTTPException(status_code=404, detail="User not found")

    for key, value in user.model_dump(
        exclude={"confirm_password"}, exclude_unset=True
    ).items():
        setattr(db_user, key, value)

    await db.commit()
    await db.refresh(db_user)
    return db_user
