"""What the SMS login flow promises, and what it must never do.

The gateway is stubbed everywhere here (`sms` fixture) -- these tests are
about the policy in `app/otp.py` and the account handling in
`app/routers/otp.py`, not about Kavenegar. Capturing the code through the
stub is also the only way a test can know it: the row stores an HMAC, which
is the first thing asserted.
"""

from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import otp as otp_module
from app.auth import has_usable_password, verify_password
from app.models.otp import OtpCode
from app.models.user import User
from app.otp import MAX_ATTEMPTS, MAX_CODES_PER_MOBILE, hash_code
from app.phone import mask_mobile, normalize_mobile

MOBILE = "09121234567"
CANONICAL = "+989121234567"


class SmsSpy:
    """Stands in for the gateway, and remembers what it was asked to send."""

    def __init__(self) -> None:
        self.sent: list[tuple[str, str]] = []

    async def __call__(self, mobile: str, code: str) -> None:
        self.sent.append((mobile, code))

    @property
    def last_code(self) -> str:
        return self.sent[-1][1]


@pytest_asyncio.fixture
def sms(monkeypatch) -> SmsSpy:
    """Patch the name `app.otp` imported, not `app.sms.send_otp` itself.

    `app/otp.py` binds the function at import time, so patching the source
    module would leave the real gateway in place -- a test suite that
    silently texts nobody while passing.
    """
    spy = SmsSpy()
    monkeypatch.setattr(otp_module, "send_otp", spy)
    return spy


async def start_login(client: AsyncClient, mobile: str = MOBILE):
    return await client.post("/auth/otp/request", json={"mobile": mobile})


# ---------------------------------------------------------------------------
# The number is one identity, however it was typed
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "typed",
    ["09121234567", "+98 912 123 4567", "۰۹۱۲۱۲۳۴۵۶۷", "00989121234567", "9121234567"],
)
def test_every_spelling_folds_to_one_number(typed: str):
    assert normalize_mobile(typed) == CANONICAL


@pytest.mark.parametrize("typed", ["02112345678", "12345", "", None, "not a number"])
def test_non_mobiles_are_refused(typed):
    assert normalize_mobile(typed) is None


async def test_a_landline_never_reaches_the_gateway(client: AsyncClient, sms: SmsSpy):
    response = await start_login(client, "02112345678")
    assert response.status_code == 422
    assert sms.sent == []


async def test_two_spellings_are_one_account(client: AsyncClient, sms: SmsSpy):
    await start_login(client, "09121234567")
    await client.post(
        "/auth/otp/verify", json={"mobile": "09121234567", "code": sms.last_code}
    )
    await client.post("/auth/logout")

    # A different spelling of the same number, an hour later so no cooldown.
    await start_login(client, "+98 912 123 4567")
    second = await client.post(
        "/auth/otp/verify",
        json={"mobile": "۰۹۱۲۱۲۳۴۵۶۷", "code": sms.last_code},
    )
    assert second.status_code == 200
    assert second.json()["is_new_account"] is False


# ---------------------------------------------------------------------------
# The code itself
# ---------------------------------------------------------------------------


async def test_the_code_is_never_stored(client: AsyncClient, sms: SmsSpy, db):
    await start_login(client)
    code = sms.last_code

    row = (await db.execute(select(OtpCode))).scalar_one()
    assert code not in row.code_hash
    assert row.code_hash == hash_code(CANONICAL, code)
    assert len(row.code_hash) == 64


async def test_a_code_is_bound_to_its_number(sms: SmsSpy):
    assert hash_code("+989121234567", "123456") != hash_code("+989121234568", "123456")


async def test_the_request_reveals_nothing_about_the_account(
    client: AsyncClient, sms: SmsSpy
):
    """Sign-up and sign-in are one flow, so the two answers are identical."""
    unknown = await start_login(client, "09121234567")
    await client.post(
        "/auth/otp/verify", json={"mobile": "09121234567", "code": sms.last_code}
    )
    await client.post("/auth/logout")
    known = await start_login(client, "09121234567")

    assert unknown.status_code == known.status_code == 200
    assert unknown.json() == known.json()
    assert unknown.json()["mobile_masked"] == mask_mobile(CANONICAL)


async def test_a_wrong_code_is_refused(client: AsyncClient, sms: SmsSpy):
    await start_login(client)
    wrong = "".join("0" if c != "0" else "1" for c in sms.last_code)

    response = await client.post(
        "/auth/otp/verify", json={"mobile": MOBILE, "code": wrong}
    )
    assert response.status_code == 400
    assert "session" not in response.cookies


async def test_a_code_works_once(client: AsyncClient, sms: SmsSpy):
    await start_login(client)
    code = sms.last_code
    body = {"mobile": MOBILE, "code": code}

    assert (await client.post("/auth/otp/verify", json=body)).status_code == 200
    await client.post("/auth/logout")
    assert (await client.post("/auth/otp/verify", json=body)).status_code == 400


async def test_an_expired_code_is_refused(
    client: AsyncClient, sms: SmsSpy, db: AsyncSession
):
    await start_login(client)
    row = (await db.execute(select(OtpCode))).scalar_one()
    row.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    await db.commit()

    response = await client.post(
        "/auth/otp/verify", json={"mobile": MOBILE, "code": sms.last_code}
    )
    assert response.status_code == 400


async def test_guessing_burns_the_code(
    client: AsyncClient, sms: SmsSpy, db: AsyncSession
):
    """The real code stops working once the row is burned -- otherwise a
    million tries is a login."""
    await start_login(client)
    code = sms.last_code
    wrong = "".join("0" if c != "0" else "1" for c in code)

    for _ in range(MAX_ATTEMPTS - 1):
        assert (
            await client.post(
                "/auth/otp/verify", json={"mobile": MOBILE, "code": wrong}
            )
        ).status_code == 400

    burned = await client.post(
        "/auth/otp/verify", json={"mobile": MOBILE, "code": wrong}
    )
    assert burned.status_code == 429

    after = await client.post("/auth/otp/verify", json={"mobile": MOBILE, "code": code})
    assert after.status_code == 400


async def test_farsi_digits_in_the_code_are_accepted(client: AsyncClient, sms: SmsSpy):
    await start_login(client)
    farsi = sms.last_code.translate(str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹"))

    response = await client.post(
        "/auth/otp/verify", json={"mobile": MOBILE, "code": farsi}
    )
    assert response.status_code == 200


# ---------------------------------------------------------------------------
# Rate limits
# ---------------------------------------------------------------------------


async def test_a_second_request_waits_out_the_cooldown(
    client: AsyncClient, sms: SmsSpy
):
    assert (await start_login(client)).status_code == 200
    again = await start_login(client)

    assert again.status_code == 429
    assert int(again.headers["retry-after"]) > 0
    assert len(sms.sent) == 1


async def test_a_number_has_an_hourly_ceiling(
    client: AsyncClient, sms: SmsSpy, db: AsyncSession
):
    for _ in range(MAX_CODES_PER_MOBILE):
        assert (await start_login(client)).status_code == 200
        # Step past the cooldown without waiting: the ceiling is the rule
        # under test, and it must bite even for somebody who is patient.
        rows = (await db.execute(select(OtpCode))).scalars().all()
        for row in rows:
            row.created_at = datetime.now(UTC) - timedelta(minutes=5)
        await db.commit()

    refused = await start_login(client)
    assert refused.status_code == 429
    assert len(sms.sent) == MAX_CODES_PER_MOBILE


async def test_a_new_code_kills_the_previous_one(
    client: AsyncClient, sms: SmsSpy, db: AsyncSession
):
    await start_login(client)
    first = sms.last_code
    row = (await db.execute(select(OtpCode))).scalar_one()
    row.created_at = datetime.now(UTC) - timedelta(minutes=5)
    await db.commit()

    await start_login(client)
    assert sms.last_code != first

    stale = await client.post(
        "/auth/otp/verify", json={"mobile": MOBILE, "code": first}
    )
    assert stale.status_code == 400
    fresh = await client.post(
        "/auth/otp/verify", json={"mobile": MOBILE, "code": sms.last_code}
    )
    assert fresh.status_code == 200


async def test_a_failed_send_leaves_no_code_and_costs_no_slot(
    client: AsyncClient, monkeypatch, db: AsyncSession
):
    from app.sms import SmsError

    async def refuse(mobile: str, code: str) -> None:
        raise SmsError("گیت‌وی خطا داد")

    monkeypatch.setattr(otp_module, "send_otp", refuse)

    response = await start_login(client)
    assert response.status_code == 502
    assert (await db.execute(select(OtpCode))).scalars().all() == []


# ---------------------------------------------------------------------------
# The account on the far side
# ---------------------------------------------------------------------------


async def test_a_first_code_creates_an_account_with_no_personal_data(
    client: AsyncClient, sms: SmsSpy, db: AsyncSession
):
    await start_login(client)
    response = await client.post(
        "/auth/otp/verify", json={"mobile": MOBILE, "code": sms.last_code}
    )

    assert response.status_code == 200
    assert response.json()["is_new_account"] is True
    assert "session" in response.cookies

    user = (await db.execute(select(User))).scalar_one()
    assert user.mobile == CANONICAL
    assert user.mobile_verified_at is not None
    assert user.email is None
    assert user.name == "کاربر ۴۵۶۷"
    assert user.role == "member"


async def test_an_otp_account_has_no_password(
    client: AsyncClient, sms: SmsSpy, db: AsyncSession
):
    """Nothing may be signed in as with a password -- not the marker, not an
    empty string, not another OTP account's marker."""
    await start_login(client)
    await client.post(
        "/auth/otp/verify", json={"mobile": MOBILE, "code": sms.last_code}
    )

    user = (await db.execute(select(User))).scalar_one()
    assert not has_usable_password(user.password_hash)
    for attempt in ["", " ", user.password_hash, "password"]:
        assert verify_password(attempt, user.password_hash) is False


async def test_the_session_is_the_app_s_own(client: AsyncClient, sms: SmsSpy):
    """OTP is a new way to prove who you are, not a second kind of session --
    so a gated page opens straight after."""
    await start_login(client)
    await client.post(
        "/auth/otp/verify", json={"mobile": MOBILE, "code": sms.last_code}
    )

    page = await client.get("/views/home/")
    assert page.status_code == 200


async def test_signing_in_again_reuses_the_account(
    client: AsyncClient, sms: SmsSpy, db: AsyncSession
):
    await start_login(client)
    first = await client.post(
        "/auth/otp/verify", json={"mobile": MOBILE, "code": sms.last_code}
    )
    await client.post("/auth/logout")

    row = (await db.execute(select(OtpCode))).scalars().first()
    row.created_at = datetime.now(UTC) - timedelta(minutes=5)
    await db.commit()

    await start_login(client)
    second = await client.post(
        "/auth/otp/verify", json={"mobile": MOBILE, "code": sms.last_code}
    )

    assert second.json()["is_new_account"] is False
    assert second.json()["user"]["id"] == first.json()["user"]["id"]
    assert len((await db.execute(select(User))).scalars().all()) == 1


async def test_a_renamed_account_keeps_its_name(
    client: AsyncClient, sms: SmsSpy, db: AsyncSession
):
    """The generated name is a placeholder, not a thing the flow re-imposes."""
    await start_login(client)
    verified = await client.post(
        "/auth/otp/verify", json={"mobile": MOBILE, "code": sms.last_code}
    )
    user_id = verified.json()["user"]["id"]
    await client.patch(f"/users/{user_id}", json={"name": "مهدی"})
    await client.post("/auth/logout")

    row = (await db.execute(select(OtpCode))).scalars().first()
    row.created_at = datetime.now(UTC) - timedelta(minutes=5)
    await db.commit()

    await start_login(client)
    await client.post(
        "/auth/otp/verify", json={"mobile": MOBILE, "code": sms.last_code}
    )

    user = (await db.execute(select(User).where(User.id == user_id))).scalar_one()
    assert user.name == "مهدی"


async def test_the_mobile_is_not_writable_through_the_profile(
    client: AsyncClient, sms: SmsSpy, db: AsyncSession
):
    """The credential has one door, and `PATCH /users/{id}` is not it.

    `Users.phone` is a contact detail any member may set to anything; if the
    credential shared that door, one member could claim another's login.
    """
    await start_login(client)
    verified = await client.post(
        "/auth/otp/verify", json={"mobile": MOBILE, "code": sms.last_code}
    )
    user_id = verified.json()["user"]["id"]

    await client.patch(
        f"/users/{user_id}", json={"mobile": "+989350000000", "phone": "09350000000"}
    )

    user = (await db.execute(select(User).where(User.id == user_id))).scalar_one()
    assert user.mobile == CANONICAL
    assert user.phone == "09350000000"


# ---------------------------------------------------------------------------
# The page
# ---------------------------------------------------------------------------


async def test_the_login_page_offers_sms_first(client: AsyncClient):
    """The two anchors the page's JS is written against, plus the shape of
    the offer.

    Nothing links a step id to its handler at import time, so a renamed id is
    a silently dead form -- the same failure mode `test_tour_anchors.py`
    exists for.
    """
    page = await client.get("/views/auth/")
    body = page.text

    assert page.status_code == 200
    for anchor in ["otpPhoneForm", "otpCodeForm", "otpMobile", "otpCode"]:
        assert f'id="{anchor}"' in body

    # Six boxes, and the first one carries the autofill hint iOS reads.
    assert body.count('class="otp-digit"') == 6
    assert 'autocomplete="one-time-code"' in body

    # The password path is still reachable, and is the secondary offer.
    assert 'id="passwordPanel"' in body
    assert 'id="authAltBtn"' in body


async def test_the_login_page_asks_for_nothing_but_a_number(client: AsyncClient):
    """The SMS step collects no personal information.

    The avatar grid and the name/email boxes belong to the password panel,
    which is hidden behind the alternate-path link; the default panel has one
    input.
    """
    page = await client.get("/views/auth/")
    otp_panel = page.text.split('id="otpPanel"')[1].split('id="passwordPanel"')[0]

    assert otp_panel.count("<input") == 7  # one mobile + six code boxes
    assert "avatar" not in otp_panel
    assert "password" not in otp_panel
