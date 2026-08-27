import base64
import hashlib
import hmac
import secrets
import time

from fastapi import Depends, HTTPException, Request, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.models.user import User

_PBKDF2_ITERATIONS = 260_000
_SESSION_COOKIE_NAME = "session"
_SESSION_MAX_AGE_SECONDS = 60 * 60 * 24 * 7  # 7 days


def _b64encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64decode(data: str) -> bytes:
    padding = "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(data + padding)


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt, _PBKDF2_ITERATIONS, dklen=32
    )
    return f"pbkdf2_sha256${_PBKDF2_ITERATIONS}${_b64encode(salt)}${_b64encode(digest)}"


def verify_password(password: str, password_hash: str) -> bool:
    try:
        algorithm, iterations_str, salt_b64, digest_b64 = password_hash.split("$")
        if algorithm != "pbkdf2_sha256":
            return False
        iterations = int(iterations_str)
        salt = _b64decode(salt_b64)
        expected = _b64decode(digest_b64)
    except ValueError:
        return False
    actual = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt, iterations, dklen=len(expected)
    )
    return hmac.compare_digest(actual, expected)


def _sign(payload_b64: str) -> str:
    signature = hmac.new(
        settings.secret_key.encode("utf-8"), payload_b64.encode("ascii"), hashlib.sha256
    ).digest()
    return _b64encode(signature)


def create_session_cookie(user_id: int) -> str:
    expires_at = int(time.time()) + _SESSION_MAX_AGE_SECONDS
    payload_b64 = _b64encode(f"{user_id}:{expires_at}".encode("ascii"))
    return f"{payload_b64}.{_sign(payload_b64)}"


def verify_session_cookie(cookie_value: str | None) -> int | None:
    if not cookie_value or "." not in cookie_value:
        return None
    payload_b64, _, signature = cookie_value.partition(".")
    if not hmac.compare_digest(_sign(payload_b64), signature):
        return None
    try:
        user_id_str, expires_at_str = _b64decode(payload_b64).decode("ascii").split(":")
        user_id, expires_at = int(user_id_str), int(expires_at_str)
    except (ValueError, UnicodeDecodeError):
        return None
    if time.time() > expires_at:
        return None
    return user_id


def set_session_cookie(response: Response, user_id: int) -> None:
    response.set_cookie(
        _SESSION_COOKIE_NAME,
        create_session_cookie(user_id),
        max_age=_SESSION_MAX_AGE_SECONDS,
        httponly=True,
        samesite="lax",
        secure=settings.environment != "development",
        path="/",
    )


def clear_session_cookie(response: Response) -> None:
    response.delete_cookie(_SESSION_COOKIE_NAME, path="/")


def get_optional_user_id(request: Request) -> int | None:
    return verify_session_cookie(request.cookies.get(_SESSION_COOKIE_NAME))


def get_current_user_id(request: Request) -> int:
    user_id = get_optional_user_id(request)
    if user_id is None:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return user_id


async def get_current_user(
    user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
) -> User:
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return user
