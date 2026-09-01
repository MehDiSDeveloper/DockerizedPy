"""Sending an SMS -- the one door out, and the only place Kavenegar exists.

Everything above this module deals in "an OTP was sent"; the provider, its
URL shape, its numeric status codes and its Farsi failure messages live here
and nowhere else. Swapping provider is rewriting :func:`send_otp` and the
settings it reads, not touching :mod:`app.otp` or the routers.

**The provider is Kavenegar's ``verify/lookup``, not ``sms/send``.** That is
the deliberate choice for one-time codes in Iran: it is the dedicated OTP
route, it needs no approved sender line, it reaches numbers that have opted
out of bulk messaging, and it is not subject to the night-time delivery
window ordinary campaigns are. The cost is that the *wording* lives in an
approved template on Kavenegar's panel rather than in this repo -- so the
app sends a token, and the panel decides the sentence around it. That is the
same shape as the rest of the app's "store the event, derive the sentence"
rule, with the derivation happening one system over.

``sms/send`` remains as a fallback for a deploy that has a sender line and no
approved template yet (set ``KAVENEGAR_SENDER`` and leave
``KAVENEGAR_OTP_TEMPLATE`` empty). It is the lesser path on purpose.

**Development without a working gateway does not fail.** Configuring an SMS
provider to run the app locally would be a barrier for something every other
part of the stack runs offline, so in a development environment an
unconfigured -- or half-configured -- gateway prints the code to the server
log and the send counts as successful. Outside development the same state is
a hard failure: a login screen that silently accepts codes nobody was sent is
worse than one that is plainly down.
"""

import logging

import httpx

from app.config import settings
from app.phone import national_mobile

logger = logging.getLogger("app.sms")

KAVENEGAR_BASE = "https://api.kavenegar.com/v1"

# Kavenegar answers 200 OK at the HTTP layer for most failures and carries the
# real outcome in `return.status`. Only the codes worth naming differently to
# an operator reading the log are listed; anything else falls through to the
# provider's own message.
_STATUS_HINTS = {
    400: "پارامترهای ارسال ناقص یا نامعتبر است.",
    401: "حساب کاوه‌نگار غیرفعال است.",
    402: "عملیات ارسال ناموفق بود.",
    403: "کلید API کاوه‌نگار نامعتبر است.",
    406: "پارامترهای اجباری خالی فرستاده شده.",
    411: "شماره گیرنده نامعتبر است.",
    412: "خط فرستنده نامعتبر است.",
    418: "اعتبار حساب کاوه‌نگار کافی نیست.",
    424: "الگوی (template) پیامک پیدا نشد یا تأیید نشده است.",
    426: "این سرویس نیازمند حساب تجاری است.",
    428: "ارسال کد تأیید برای این شماره مجاز نیست.",
}

# The provider is a third party on the far side of a mobile network; a login
# screen may not hang on it. Short enough that a stalled gateway becomes a
# retryable error rather than a spinner the member watches.
_TIMEOUT = httpx.Timeout(10.0, connect=5.0)


class SmsError(Exception):
    """The message was not handed to the carrier.

    Raised for every failure mode -- transport, HTTP status, provider status
    code -- because the caller has exactly one decision to make either way:
    do not store the code, and tell the member to try again. The Farsi
    ``message`` is safe to show; the details go to the log.
    """

    def __init__(self, message: str, *, detail: str | None = None) -> None:
        super().__init__(detail or message)
        self.message = message
        self.detail = detail or message


async def send_otp(mobile: str, code: str) -> None:
    """Text ``code`` to ``mobile`` (canonical ``+98…``), or raise `SmsError`.

    Returns nothing on success: there is no provider message-id the app has a
    use for, and inventing a "delivery status" it never polls would be a
    field that is always wrong.
    """
    receptor = national_mobile(mobile)

    # A key on its own cannot send anything -- the route is chosen by which of
    # the two delivery settings is present -- so "configured" means the pair,
    # not the key. Asking the whole question here rather than at each branch
    # is what keeps the development fallback below true while a deploy is
    # half-configured: a key pasted in before its template is approved is the
    # normal state of this integration for a day or two, and a login screen
    # that dies during it is worse than one that logs the code.
    configured = bool(settings.kavenegar_api_key) and bool(
        settings.kavenegar_otp_template or settings.kavenegar_sender
    )
    if not configured:
        if settings.environment == "development":
            logger.warning("SMS (dev, no gateway) -> %s: code %s", receptor, code)
            return
        raise SmsError(
            "سرویس پیامک پیکربندی نشده است.",
            detail=(
                "KAVENEGAR_API_KEY plus one of KAVENEGAR_OTP_TEMPLATE / "
                "KAVENEGAR_SENDER is required outside development"
            ),
        )

    if settings.kavenegar_otp_template:
        url = f"{KAVENEGAR_BASE}/{settings.kavenegar_api_key}/verify/lookup.json"
        params = {
            "receptor": receptor,
            "token": code,
            "template": settings.kavenegar_otp_template,
        }
    elif settings.kavenegar_sender:
        url = f"{KAVENEGAR_BASE}/{settings.kavenegar_api_key}/sms/send.json"
        params = {
            "receptor": receptor,
            "sender": settings.kavenegar_sender,
            "message": settings.otp_sms_text.format(code=code),
        }
    else:  # pragma: no cover -- `configured` above already excluded this
        raise SmsError(
            "سرویس پیامک پیکربندی نشده است.",
            detail="neither KAVENEGAR_OTP_TEMPLATE nor KAVENEGAR_SENDER is set",
        )

    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            # POST, not GET: the code would otherwise sit in the provider's
            # access log as a query string, which is the one place a
            # short-lived secret should never be written down.
            response = await client.post(url, data=params)
    except httpx.HTTPError as exc:
        raise SmsError(
            "ارسال پیامک ممکن نشد. لطفاً دوباره تلاش کن.",
            detail=f"transport error: {exc!r}",
        ) from exc

    _raise_for_provider_status(response)


def _raise_for_provider_status(response: httpx.Response) -> None:
    """Turn Kavenegar's ``{"return": {"status": …}}`` envelope into success or
    an `SmsError`.

    The HTTP status alone is not the answer -- the API answers ``200 OK``
    carrying ``status: 418`` for an empty account -- so both layers are
    checked, and the provider's own layer wins when the two disagree.
    """
    try:
        payload = response.json()
        status = int(payload["return"]["status"])
        message = str(payload["return"].get("message", ""))
    except (ValueError, KeyError, TypeError):
        raise SmsError(
            "ارسال پیامک ممکن نشد. لطفاً دوباره تلاش کن.",
            detail=f"unreadable provider response (HTTP {response.status_code}): "
            f"{response.text[:300]}",
        ) from None

    if status == 200:
        return

    hint = _STATUS_HINTS.get(status)
    logger.error("kavenegar refused: status=%s message=%s", status, message)
    raise SmsError(
        hint or "ارسال پیامک ممکن نشد. لطفاً دوباره تلاش کن.",
        detail=f"kavenegar status {status}: {message}",
    )
