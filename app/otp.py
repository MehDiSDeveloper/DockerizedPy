"""One-time SMS codes -- the whole policy, in the one place a code is made.

:mod:`app.sms` knows how to text somebody. This module knows *whether to*,
*what*, and *for how long*, and it is the only door in: routers call
:func:`request_code` and :func:`verify_code` and never touch
:class:`~app.models.otp.OtpCode` themselves. That is the same shape
``app/notifications.py`` has, and for the same reason -- every invariant here
is one a call site would otherwise have to remember, and the one that forgets
is not a visible error but a login anybody can walk into.

Six invariants, and each is a real attack if it is dropped:

1. **The code is never stored.** :func:`hash_code` is an HMAC keyed on
   ``secret_key`` and bound to the mobile, so a leaked table is not a set of
   working logins, and a code seen for one number cannot be replayed against
   another.
2. **A code dies on the first success and on the last wrong guess.** Six
   digits is a million possibilities, which is nothing to a script if
   guessing is free -- so ``attempts`` is counted on the row and the row is
   burned at :data:`MAX_ATTEMPTS`. Rate-limiting the *requests* alone would
   leave the guessing unbounded.
3. **Requesting a new code kills the old ones.** Otherwise every request
   widens the set of currently-valid codes, and a member who taps the resend
   button three times has tripled an attacker's odds rather than helped
   themselves.
4. **Sending is bounded per number and per source.** Each SMS costs real
   money and lands on somebody's phone, so an unbounded endpoint is both a
   bill and a way to harass a stranger. Two windows, because either one alone
   is trivially sidestepped -- one host walking a list of numbers, or a
   botnet hammering one number.
5. **Nothing here says whether an account exists.** Sign-up and sign-in are
   one flow (see ``app/routers/otp.py``), so the request step has no branch
   to leak and no message to differ. This is *why* the flow is unified, not a
   side effect of it.
6. **The code goes out before the row is committed.** :func:`request_code`
   flushes and sends but never commits -- the caller commits, exactly as
   ``notify()`` does. A gateway failure therefore rolls the row back with the
   request, so a member is never left holding a live code they were never
   sent, and never charged a rate-limit slot for an SMS that did not happen.
"""

import hashlib
import hmac
import logging
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.otp import OtpCode
from app.sms import send_otp

logger = logging.getLogger("app.otp")

#: Digits in a code. Six is the length people expect from an Iranian SMS code
#: and the length carrier autofill heuristics look for; the guessing budget it
#: implies is bounded by `MAX_ATTEMPTS`, not by the length itself.
CODE_LENGTH = 6

#: How long a code lives. Two minutes is long enough for a slow carrier and
#: short enough that a code read off a lock screen hours later is worthless.
TTL_SECONDS = 120

#: Wrong guesses one code will take before it is burned.
MAX_ATTEMPTS = 5

#: How long before a resend is offered. Also the floor on how fast the app can
#: be made to spend money on one number.
RESEND_COOLDOWN_SECONDS = 60

#: Ceilings per number and per source address, over `RATE_WINDOW_SECONDS`.
MAX_CODES_PER_MOBILE = 5
MAX_CODES_PER_IP = 20
RATE_WINDOW_SECONDS = 3600

#: The only purpose today. The column exists so a code requested to log in
#: cannot later satisfy a different question -- see `app/models/otp.py`.
PURPOSE_LOGIN = "login"

# Farsi refusals. Kept together here rather than at each raise site for the
# same reason `NOTIFICATION_META` exists: these are the sentences a member
# reads at the door, and two copies of one of them drift.
MSG_COOLDOWN = "کد قبلی هنوز معتبر است. چند لحظه دیگر دوباره تلاش کن."
MSG_TOO_MANY_FOR_MOBILE = (
    "تعداد درخواست‌های کد برای این شماره زیاد شده. یک ساعت دیگر دوباره تلاش کن."
)
MSG_TOO_MANY_OVERALL = "تعداد درخواست‌های کد زیاد شده. کمی بعد دوباره تلاش کن."
MSG_BAD_CODE = "کد واردشده درست نیست یا منقضی شده."
MSG_BURNED = "تعداد تلاش‌های اشتباه زیاد شد. یک کد تازه بگیر."


class OtpRefused(Exception):
    """A code was not sent, or not accepted.

    Carries the Farsi sentence the member should see and, where the answer is
    "not yet", how many seconds until it changes -- the routers turn that into
    a ``Retry-After`` header and the page into a countdown. One exception type
    for both halves of the flow, because a caller's handling is identical:
    show the message, sign nobody in.
    """

    def __init__(
        self, message: str, *, retry_after: int | None = None, status_code: int = 400
    ) -> None:
        super().__init__(message)
        self.message = message
        self.retry_after = retry_after
        self.status_code = status_code


@dataclass(frozen=True)
class OtpTicket:
    """What the member's screen needs once a code has gone out.

    Deliberately says nothing about *who* the number belongs to -- invariant 5.
    """

    expires_in: int
    resend_after: int


def generate_code() -> str:
    """A zero-padded `CODE_LENGTH`-digit code from the CSPRNG.

    ``secrets``, not ``random``: a code drawn from a predictable generator is
    a code an observer of one login can compute for the next.
    """
    return str(secrets.randbelow(10**CODE_LENGTH)).zfill(CODE_LENGTH)


def hash_code(mobile: str, code: str) -> str:
    """HMAC-SHA256 of the code, keyed on ``secret_key`` and bound to ``mobile``.

    Keyed rather than a bare digest: the space is a million entries, so a
    plain SHA-256 of a six-digit code is a lookup table anybody builds in a
    second. Binding the mobile in is what makes a digest useless anywhere but
    the number it was issued for.

    Rotating ``secret_key`` invalidates every outstanding code -- the same
    consequence it already has for every session cookie.
    """
    message = f"{mobile}:{code}".encode()
    return hmac.new(
        settings.secret_key.encode("utf-8"), message, hashlib.sha256
    ).hexdigest()


def _aware(value: datetime | None) -> datetime | None:
    """Read a stored timestamp back as UTC-aware.

    SQLite has no aware datetime type (CLAUDE.md, Dates), so a column written
    as UTC comes back naive and comparing it to ``datetime.now(UTC)`` raises.
    Same guard ``app.groups.invite_state`` uses.
    """
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=UTC)


async def _count_since(db: AsyncSession, column, value: str, since: datetime) -> int:
    stmt = (
        select(func.count())
        .select_from(OtpCode)
        .where(column == value, OtpCode.created_at >= since)
    )
    return int((await db.execute(stmt)).scalar_one())


async def _live_code(db: AsyncSession, mobile: str, now: datetime) -> OtpCode | None:
    """The newest code for this number that could still let somebody in.

    "Newest" rather than "any": invariant 3 consumes the others on every
    request, so at most one should ever qualify -- taking the newest anyway
    means a row that escaped that (a crash between flush and commit, a clock
    stepping backwards) fails safe instead of resurrecting an older code.
    """
    stmt = (
        select(OtpCode)
        .where(
            OtpCode.mobile == mobile,
            OtpCode.purpose == PURPOSE_LOGIN,
            OtpCode.consumed_at.is_(None),
            OtpCode.expires_at > now,
        )
        .order_by(OtpCode.created_at.desc(), OtpCode.id.desc())
        .limit(1)
    )
    return (await db.execute(stmt)).scalars().first()


async def request_code(
    db: AsyncSession,
    mobile: str,
    *,
    request_ip: str | None = None,
    now: datetime | None = None,
) -> OtpTicket:
    """Send a fresh code to ``mobile``, or raise :class:`OtpRefused`.

    ``mobile`` must already be canonical (`app.phone.normalize_mobile`); this
    module never sees a raw typed string, so there is no second place a
    spelling could be resolved differently.

    Does not commit -- invariant 6.
    """
    now = now or datetime.now(UTC)
    window_start = now - timedelta(seconds=RATE_WINDOW_SECONDS)

    # The cooldown is asked first because it is the only refusal with a useful
    # answer attached: "not yet, N seconds". Checking a ceiling first would
    # replace that with a flat hour-long refusal for somebody who double-tapped.
    live = await _live_code(db, mobile, now)
    if live is not None:
        created = _aware(live.created_at) or now
        elapsed = (now - created).total_seconds()
        if elapsed < RESEND_COOLDOWN_SECONDS:
            raise OtpRefused(
                MSG_COOLDOWN,
                retry_after=max(1, int(RESEND_COOLDOWN_SECONDS - elapsed)),
                status_code=429,
            )

    sent_to_mobile = await _count_since(db, OtpCode.mobile, mobile, window_start)
    if sent_to_mobile >= MAX_CODES_PER_MOBILE:
        raise OtpRefused(
            MSG_TOO_MANY_FOR_MOBILE,
            retry_after=RATE_WINDOW_SECONDS,
            status_code=429,
        )

    if request_ip:
        sent_from_ip = await _count_since(
            db, OtpCode.request_ip, request_ip, window_start
        )
        if sent_from_ip >= MAX_CODES_PER_IP:
            raise OtpRefused(
                MSG_TOO_MANY_OVERALL,
                retry_after=RATE_WINDOW_SECONDS,
                status_code=429,
            )

    # Invariant 3: every earlier live code for this number stops working the
    # moment a new one is issued.
    await db.execute(
        update(OtpCode)
        .where(OtpCode.mobile == mobile, OtpCode.consumed_at.is_(None))
        .values(consumed_at=now)
    )

    code = generate_code()
    row = OtpCode(
        mobile=mobile,
        code_hash=hash_code(mobile, code),
        purpose=PURPOSE_LOGIN,
        attempts=0,
        # `created_at` is written explicitly rather than left to the column's
        # `func.now()` default: the rate windows above compare it against a
        # Python UTC instant, and a server-side clock would be a second source
        # of "now", rendered by the SQLite backend in a different shape from
        # the bound parameter it is compared with.
        created_at=now,
        expires_at=now + timedelta(seconds=TTL_SECONDS),
        request_ip=request_ip,
    )
    db.add(row)
    await db.flush()

    # Sending inside the transaction is deliberate (invariant 6): the caller
    # commits after this returns, so a gateway failure takes the row with it.
    await send_otp(mobile, code)

    return OtpTicket(expires_in=TTL_SECONDS, resend_after=RESEND_COOLDOWN_SECONDS)


async def verify_code(
    db: AsyncSession,
    mobile: str,
    code: str,
    *,
    now: datetime | None = None,
) -> None:
    """Accept and burn the code, or raise :class:`OtpRefused`.

    Returns nothing on success: the *only* thing a caller may conclude is
    "whoever sent this holds that number", and handing back the row would
    invite a second reader to decide that for itself.

    Does not commit. The consumption and whatever the caller does with it --
    creating the account, issuing the session -- land in one transaction, so
    a failure halfway cannot burn a code without signing anybody in.
    """
    now = now or datetime.now(UTC)
    row = await _live_code(db, mobile, now)

    # One message for "never asked", "expired", "already used" and "wrong".
    # Distinguishing them would tell an attacker which numbers have a code
    # outstanding, and tells a member nothing they can act on differently --
    # in all four cases the next step is to ask for a new code.
    wrong = OtpRefused(MSG_BAD_CODE, status_code=400)
    if row is None:
        raise wrong

    row.attempts = (row.attempts or 0) + 1

    if not hmac.compare_digest(row.code_hash, hash_code(mobile, code)):
        if row.attempts >= MAX_ATTEMPTS:
            # Invariant 2. Burned rather than merely counted, so the next
            # guess finds no live row at all.
            row.consumed_at = now
            logger.warning(
                "otp: burned a code after %s failed attempts", row.attempts
            )
            raise OtpRefused(MSG_BURNED, status_code=429)
        raise wrong

    row.consumed_at = now


async def purge_expired_codes(
    db: AsyncSession, *, older_than_days: int = 7, now: datetime | None = None
) -> int:
    """Delete rows that can no longer answer any question.

    Nothing calls this on a schedule -- there is no job runner in this app --
    so it exists for an operator console and for whatever runs one later. The
    window is days rather than minutes because a burned row is the only record
    of a number being hammered, and discarding that the second it expires
    makes the abuse invisible the moment it stops.
    """
    now = now or datetime.now(UTC)
    cutoff = now - timedelta(days=older_than_days)
    result = await db.execute(
        OtpCode.__table__.delete().where(OtpCode.created_at < cutoff)
    )
    return result.rowcount or 0
