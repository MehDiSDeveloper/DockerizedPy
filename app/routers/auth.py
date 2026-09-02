import logging

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import clear_session_cookie, set_session_cookie, verify_password
from app.database import get_db
from app.logging_config import log_event, mask_email
from app.models.user import User
from app.schemas.user import UserLogin, UserRead

router = APIRouter(prefix="/auth", tags=["auth"])
logger = logging.getLogger(__name__)


@router.post("/login", response_model=UserRead)
async def login(
    credentials: UserLogin, response: Response, db: AsyncSession = Depends(get_db)
):
    result = await db.execute(select(User).where(User.email == credentials.email))
    user = result.scalar_one_or_none()
    if user is None or not verify_password(credentials.password, user.password_hash):
        # WARNING, not INFO: a run of these from one client_ip (the access log
        # carries it) is the only signal this app has for password guessing.
        # The address is masked and the password never reaches the record.
        log_event(
            logger,
            "auth.login_failed",
            level=logging.WARNING,
            email=mask_email(credentials.email),
            reason="unknown_email" if user is None else "bad_password",
        )
        raise HTTPException(status_code=401, detail="Invalid email or password")
    set_session_cookie(response, user.id)
    log_event(logger, "auth.login_ok", user_id=user.id, method="password")
    return user


@router.post("/logout", status_code=204)
async def logout(response: Response):
    clear_session_cookie(response)
    log_event(logger, "auth.logout")
