"""Signing in with a phone number -- the JSON half.

Two endpoints, and **one flow for both signing up and signing in**. That is
the central decision here, and it buys three things at once:

- *Nothing to leak.* ``POST /auth/otp/request`` has no branch on whether the
  number is known, so the response, the timing and the SMS are identical
  either way. An enumeration attempt learns nothing, and it learns nothing
  without a single "do not reveal" rule anywhere in the code -- there is
  simply no second path to reveal.
- *Nothing to choose.* The member does not have to know whether they have an
  account before they can act, which is the question a login/register tab
  pair asks and nobody arrives able to answer with certainty.
- *No personal information.* An account is created from the one thing that
  was proved -- the number. No name, no email, no password: a placeholder
  name is generated and the profile is where a member replaces it if they
  want to. What the app needs to know about somebody is what they choose to
  tell it later.

The SSR page at ``/views/auth/`` calls exactly these two endpoints, the same
way every other page in this app calls the JSON API rather than growing a
parallel ``/views/`` mutation (CLAUDE.md, "Two router layers").
"""

import logging
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import set_session_cookie, unusable_password_hash
from app.database import get_db
from app.models.user import User, UserRole
from app.otp import OtpRefused, request_code, verify_code
from app.phone import mask_mobile, national_mobile
from app.schemas.otp import (
    OtpRequestBody,
    OtpRequestResponse,
    OtpVerifyBody,
    OtpVerifyResponse,
)
from app.sms import SmsError

logger = logging.getLogger("app.routers.otp")

router = APIRouter(prefix="/auth/otp", tags=["auth"])

# Farsi digits, for the placeholder name. Storage is ASCII everywhere else in
# this app, but a name is read rather than matched, and «کاربر 4567» in a
# right-to-left sentence reads as broken rather than as a name.
_FA_DIGITS = str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹")


def client_ip(request: Request) -> str | None:
    """The address a code request came from, for the per-source rate window.

    ``X-Forwarded-For`` is honoured because the deploy sits behind a proxy and
    every request would otherwise share the proxy's own address -- one bucket
    for the whole internet, which is the same as no per-source limit at all.

    It is **spoofable**, and that is accepted rather than overlooked: the
    header only ever *widens* how many buckets exist, so forging it lets an
    attacker escape their own limit but never lets them consume somebody
    else's. The limit that actually protects a member -- and the app's SMS
    bill for one number -- is the per-mobile one, which no header can move.
    """
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        first = forwarded.split(",")[0].strip()
        if first:
            return first[:45]
    return request.client.host if request.client else None


def placeholder_name(mobile: str) -> str:
    """The name an account gets when the member told us nothing but a number.

    ``Users.name`` is NOT NULL and every screen in the app renders it, so
    there has to be *something*; the last four digits are what the member
    themselves would recognise, and a shared constant like «کاربر» alone would
    make a participant list unreadable. It is a placeholder in the honest
    sense -- the profile can change it, and nothing in the app treats a name
    as unique or as identity.
    """
    tail = national_mobile(mobile)[-4:].translate(_FA_DIGITS)
    return f"کاربر {tail}"


@router.post("/request", response_model=OtpRequestResponse)
async def request_otp(
    body: OtpRequestBody,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Text a fresh code to the number in ``body``.

    Answers the same thing whether the number has an account or not -- see the
    module docstring. Nothing is written about the *member* here at all; the
    account, if one is needed, is created at ``/verify`` and only once the
    code has been proved. A code request that created an account would let
    anybody fill the members table with numbers they do not hold.
    """
    try:
        ticket = await request_code(db, body.mobile, request_ip=client_ip(request))
    except OtpRefused as refused:
        await db.rollback()
        raise HTTPException(
            status_code=refused.status_code,
            detail=refused.message,
            headers=(
                {"Retry-After": str(refused.retry_after)}
                if refused.retry_after
                else None
            ),
        ) from refused
    except SmsError as failure:
        # The row is rolled back with the request, so the member is not left
        # holding a live code that was never delivered, and has not spent a
        # rate-limit slot on an SMS that did not happen (invariant 6).
        await db.rollback()
        logger.error("otp: gateway refused a send: %s", failure.detail)
        raise HTTPException(status_code=502, detail=failure.message) from failure

    await db.commit()
    return OtpRequestResponse(
        mobile_masked=mask_mobile(body.mobile),
        expires_in=ticket.expires_in,
        resend_after=ticket.resend_after,
    )


@router.post("/verify", response_model=OtpVerifyResponse)
async def verify_otp(
    body: OtpVerifyBody,
    response: Response,
    db: AsyncSession = Depends(get_db),
):
    """Prove the code, then sign in -- creating the account if this is the first time.

    Everything after the code is accepted happens in the one transaction the
    consumption is in, so a failure partway cannot burn a member's code
    without signing them in. The session cookie is the app's existing one:
    OTP is a new way to *prove* who you are, not a second kind of session.
    """
    now = datetime.now(UTC)
    try:
        await verify_code(db, body.mobile, body.code, now=now)
    except OtpRefused as refused:
        # Committed, not rolled back: the attempt counter and the burn are the
        # record of the guessing, and discarding them on the way out is what
        # would make `MAX_ATTEMPTS` unenforceable.
        await db.commit()
        raise HTTPException(
            status_code=refused.status_code,
            detail=refused.message,
            headers=(
                {"Retry-After": str(refused.retry_after)}
                if refused.retry_after
                else None
            ),
        ) from refused

    user = (
        await db.execute(select(User).where(User.mobile == body.mobile))
    ).scalar_one_or_none()

    is_new_account = user is None
    if user is None:
        user = User(
            name=placeholder_name(body.mobile),
            email=None,
            # No password exists and none is asked for. The column is NOT NULL,
            # so it gets the marker `verify_password` can never match -- see
            # `app.auth.unusable_password_hash`.
            password_hash=unusable_password_hash(),
            mobile=body.mobile,
            mobile_verified_at=now,
            role=UserRole.MEMBER.value,
        )
        db.add(user)
        try:
            await db.commit()
        except IntegrityError:
            # Two verifies for one number landing together. The code is
            # single-use so only one of them proved anything, but the loser
            # should still be signed into the account the winner made rather
            # than told the number is taken -- it is their own number.
            await db.rollback()
            user = (
                await db.execute(select(User).where(User.mobile == body.mobile))
            ).scalar_one_or_none()
            if user is None:
                raise
            is_new_account = False
    else:
        # Re-stamped on every sign-in: the column answers "when did we last
        # see proof of this number", which is the question support asks, and
        # a value frozen at first signup answers a question nobody has.
        user.mobile_verified_at = now
        user.updated_at = now
        user.last_modifier_user_id = user.id
        await db.commit()

    await db.refresh(user)
    set_session_cookie(response, user.id)
    return OtpVerifyResponse(user=user, is_new_account=is_new_account)
