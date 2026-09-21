"""Photo evidence: what the server actually promises, and what it refuses.

The rules that separate evidence from decoration all live in
``claim_proof_asset``, and each of them is one test here:

* **it is yours** -- and «not yours» is worded as «not found», the way a
  check-in that is not yours is a 404: asset ids are sequential.
* **it is used once** -- so yesterday's photograph cannot be re-submitted,
  and one photograph cannot cover two occurrences.
* **it is recent** -- measured from the *server's* stamp, never the device's.
* **the act decides whether one is needed at all**, and a skip never is.

What is deliberately not tested is that the picture came from a camera
rather than a gallery: nothing reaching a server can tell those apart, and a
test asserting it would be asserting a promise the code does not make.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from io import BytesIO
from zoneinfo import ZoneInfo

from httpx import AsyncClient
from PIL import Image
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import create_session_cookie, hash_password
from app.models.act import ProofAsset
from app.models.challenge import Challenge, ChallengeCategory, ProofKind
from app.models.checkin import CheckIn
from app.models.enrollment import ChallengeRole, Enrollment
from app.models.stats import ChallengeStats
from app.models.user import User
from app.proof import PROOF_MAX_AGE, digest, parse_proof, requires_photo
from app.schemas.proof import PhotoProof, SelfProof


async def make_user(db: AsyncSession, name: str) -> User:
    user = User(
        name=name,
        email=f"{name.lower()}@example.com",
        password_hash=hash_password("password123"),
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


def sign_in(client: AsyncClient, user: User) -> None:
    client.cookies.set("session", create_session_cookie(user.id))


async def make_act(db: AsyncSession, owner: User, *, proof_kind: str) -> Challenge:
    challenge = Challenge(
        title="A daily act",
        owner_id=owner.id,
        category=ChallengeCategory.OTHER,
        cadence_kind="recurring_days",
        cadence={"kind": "recurring_days", "mode": "every_n_days", "n": 1},
        visibility="public",
        lifecycle_status="active",
        proof_kind=proof_kind,
        proof={"kind": proof_kind},
    )
    db.add(challenge)
    await db.flush()
    db.add(
        Enrollment(
            challenge_id=challenge.id,
            user_id=owner.id,
            timezone="Asia/Tehran",
            start_date=local_today(),
            role=ChallengeRole.OWNER.value,
        )
    )
    db.add(ChallengeStats(challenge_id=challenge.id, participant_count=1))
    await db.commit()
    await db.refresh(challenge)
    return challenge


def a_photograph(colour=(20, 120, 90)) -> bytes:
    buf = BytesIO()
    Image.new("RGB", (64, 48), colour).save(buf, format="PNG")
    return buf.getvalue()


async def upload(client: AsyncClient, raw: bytes | None = None):
    return await client.post(
        "/media/proof",
        files={"file": ("shot.png", raw or a_photograph(), "image/png")},
    )


def local_today() -> date:
    """Today in the enrollment timezone these tests all use.

    Not `date.today()`: an act's occurrences are derived in each
    enrollment's *own* zone (CLAUDE.md, the single most important edge
    case), so a test asking "is today's occurrence due" has to ask in the
    same zone the engine will.
    """
    return datetime.now(ZoneInfo("Asia/Tehran")).date()


def today_key() -> str:
    return f"D{local_today().isoformat()}"


async def report(client: AsyncClient, challenge_id: int, **extra):
    return await client.post(
        "/checkins/",
        json={
            "challenge_id": challenge_id,
            "occurrence_key": today_key(),
            "state": "completed",
            **extra,
        },
    )


# --- the policy ----------------------------------------------------------


def test_a_null_proof_column_reads_as_self_report():
    """Every challenge written before this feature -- so the migration needs
    no backfill, which is the whole reason the column is nullable."""
    challenge = Challenge(proof_kind=ProofKind.SELF.value, proof=None)
    assert isinstance(parse_proof(challenge), SelfProof)
    assert requires_photo(challenge) is False

    photo = Challenge(proof_kind=ProofKind.PHOTO.value, proof=None)
    assert isinstance(parse_proof(photo), PhotoProof)


def test_an_unparseable_policy_falls_back_rather_than_raising():
    """An act nobody can check in to is worse than one behaving like the
    default it was created with -- the call `resolve_anonymity` makes."""
    challenge = Challenge(
        proof_kind=ProofKind.SELF.value, proof={"kind": "from-the-future"}
    )
    assert isinstance(parse_proof(challenge), SelfProof)


# --- the upload ----------------------------------------------------------


async def test_an_upload_records_the_servers_own_clock_and_digest(
    client: AsyncClient, db: AsyncSession
):
    member = await make_user(db, "Member")
    sign_in(client, member)

    before = datetime.now(UTC)
    res = await upload(client)
    assert res.status_code == 201
    body = res.json()
    assert body["expires_in"] == int(PROOF_MAX_AGE.total_seconds())

    asset = (
        await db.execute(
            select(ProofAsset).where(ProofAsset.id == body["proof_asset_id"])
        )
    ).scalar_one()
    assert asset.user_id == member.id
    assert asset.claimed_at is None
    # The digest is of what was *stored*, and storing means re-encoding --
    # so it is deliberately not the digest of what was sent.
    assert asset.sha256 == body["sha256"]
    assert asset.sha256 != digest(a_photograph())
    stamped = asset.captured_at
    if stamped.tzinfo is None:
        stamped = stamped.replace(tzinfo=UTC)
    assert stamped >= before.replace(microsecond=0)


async def test_a_file_that_is_not_an_image_is_refused(
    client: AsyncClient, db: AsyncSession
):
    member = await make_user(db, "Member")
    sign_in(client, member)
    assert (await upload(client, b"not an image at all")).status_code == 422


async def test_uploading_needs_a_session(client: AsyncClient, db: AsyncSession):
    assert (await upload(client)).status_code == 401


# --- the claim -----------------------------------------------------------


async def test_a_photo_act_refuses_a_report_with_no_evidence(
    client: AsyncClient, db: AsyncSession
):
    owner = await make_user(db, "Owner")
    challenge = await make_act(db, owner, proof_kind=ProofKind.PHOTO.value)
    sign_in(client, owner)

    assert (await report(client, challenge.id)).status_code == 422

    asset_id = (await upload(client)).json()["proof_asset_id"]
    res = await report(client, challenge.id, proof_asset_id=asset_id)
    assert res.status_code == 201
    assert res.json()["proof_asset_id"] == asset_id

    # The asset is spent.
    asset = (
        await db.execute(select(ProofAsset).where(ProofAsset.id == asset_id))
    ).scalar_one()
    await db.refresh(asset)
    assert asset.claimed_at is not None


async def test_a_skip_on_a_photo_act_needs_no_evidence(
    client: AsyncClient, db: AsyncSession
):
    """There is nothing to prove in saying you did not do it, and demanding
    a photograph of an absence teaches people to lie."""
    owner = await make_user(db, "Owner")
    challenge = await make_act(db, owner, proof_kind=ProofKind.PHOTO.value)
    sign_in(client, owner)
    res = await client.post(
        "/checkins/",
        json={
            "challenge_id": challenge.id,
            "occurrence_key": today_key(),
            "state": "skipped",
        },
    )
    assert res.status_code == 201


async def test_an_asset_is_single_use(client: AsyncClient, db: AsyncSession):
    """So yesterday's photograph cannot be re-submitted for today, and one
    photograph cannot cover two occurrences."""
    owner = await make_user(db, "Owner")
    challenge = await make_act(db, owner, proof_kind=ProofKind.PHOTO.value)
    other = await make_act(db, owner, proof_kind=ProofKind.PHOTO.value)
    sign_in(client, owner)

    asset_id = (await upload(client)).json()["proof_asset_id"]
    assert (
        await report(client, challenge.id, proof_asset_id=asset_id)
    ).status_code == 201
    assert (
        await report(client, other.id, proof_asset_id=asset_id)
    ).status_code == 422


async def test_somebody_elses_asset_is_a_plain_404_message(
    client: AsyncClient, db: AsyncSession
):
    """Asset ids are sequential, so a distinct refusal would map which of
    them exist -- the call `_load_enrollment_for_checkin` makes."""
    mine = await make_user(db, "Mine")
    theirs = await make_user(db, "Theirs")
    challenge = await make_act(db, theirs, proof_kind=ProofKind.PHOTO.value)

    sign_in(client, mine)
    asset_id = (await upload(client)).json()["proof_asset_id"]

    sign_in(client, theirs)
    res = await report(client, challenge.id, proof_asset_id=asset_id)
    assert res.status_code == 422
    assert res.json()["detail"] == "مدرک پیدا نشد."


async def test_a_stale_asset_is_refused(client: AsyncClient, db: AsyncSession):
    """The freshness window is the enforceable half of «عکس در لحظه», and it
    is measured from the server's own stamp."""
    owner = await make_user(db, "Owner")
    challenge = await make_act(db, owner, proof_kind=ProofKind.PHOTO.value)
    sign_in(client, owner)

    asset_id = (await upload(client)).json()["proof_asset_id"]
    asset = (
        await db.execute(select(ProofAsset).where(ProofAsset.id == asset_id))
    ).scalar_one()
    asset.captured_at = datetime.now(UTC) - PROOF_MAX_AGE - timedelta(minutes=1)
    await db.commit()

    res = await report(client, challenge.id, proof_asset_id=asset_id)
    assert res.status_code == 422
    assert "همین حالا" in res.json()["detail"]


async def test_a_refused_report_releases_its_asset(
    client: AsyncClient, db: AsyncSession
):
    """The claim happens inside the report's own transaction, so a report
    that does not land must not spend a seat."""
    owner = await make_user(db, "Owner")
    challenge = await make_act(db, owner, proof_kind=ProofKind.PHOTO.value)
    challenge.goal_unit = "کیلومتر"
    await db.commit()
    sign_in(client, owner)

    asset_id = (await upload(client)).json()["proof_asset_id"]
    # Refused for a *different* reason (the goal needs an amount), after the
    # asset was already claimed in this transaction.
    assert (
        await report(client, challenge.id, proof_asset_id=asset_id)
    ).status_code == 422

    asset = (
        await db.execute(select(ProofAsset).where(ProofAsset.id == asset_id))
    ).scalar_one()
    await db.refresh(asset)
    assert asset.claimed_at is None

    # And it still works afterwards.
    assert (
        await report(client, challenge.id, proof_asset_id=asset_id, amount="5")
    ).status_code == 201


async def test_a_self_report_act_ignores_an_attached_asset(
    client: AsyncClient, db: AsyncSession
):
    """`proof_satisfied` answers whether one was *needed*; attaching one
    anyway is not an error, it is simply more than was asked for."""
    owner = await make_user(db, "Owner")
    challenge = await make_act(db, owner, proof_kind=ProofKind.SELF.value)
    sign_in(client, owner)

    asset_id = (await upload(client)).json()["proof_asset_id"]
    res = await report(client, challenge.id, proof_asset_id=asset_id)
    assert res.status_code == 201
    assert res.json()["proof_asset_id"] == asset_id


async def test_withdrawing_a_report_does_not_resurrect_its_asset(
    client: AsyncClient, db: AsyncSession
):
    """An asset is spent once and stays spent: re-offering it would make the
    freshness window meaningless the moment somebody deletes a report."""
    owner = await make_user(db, "Owner")
    challenge = await make_act(db, owner, proof_kind=ProofKind.PHOTO.value)
    sign_in(client, owner)

    asset_id = (await upload(client)).json()["proof_asset_id"]
    checkin_id = (
        await report(client, challenge.id, proof_asset_id=asset_id)
    ).json()["id"]
    assert (await client.delete(f"/checkins/{checkin_id}")).status_code == 204

    asset = (
        await db.execute(select(ProofAsset).where(ProofAsset.id == asset_id))
    ).scalar_one()
    await db.refresh(asset)
    assert asset.claimed_at is not None
    assert (
        await report(client, challenge.id, proof_asset_id=asset_id)
    ).status_code == 422


async def test_the_proof_axis_is_editable_after_creation(
    client: AsyncClient, db: AsyncSession
):
    """Unlike `cadence`, it changes what happens *next* rather than what a
    report that already settled was measured against."""
    owner = await make_user(db, "Owner")
    challenge = await make_act(db, owner, proof_kind=ProofKind.SELF.value)
    sign_in(client, owner)

    res = await client.patch(
        f"/challenges/{challenge.id}", json={"proof": {"kind": "photo"}}
    )
    assert res.status_code == 200
    assert res.json()["proof_kind"] == ProofKind.PHOTO.value

    await db.refresh(challenge)
    # The discriminator and the JSON are written from one object, so they
    # cannot name two different kinds.
    assert challenge.proof_kind == "photo"
    assert challenge.proof["kind"] == "photo"

    # And the new policy is enforced from the next report onward.
    assert (await report(client, challenge.id)).status_code == 422
    assert (
        await db.execute(select(CheckIn).where(CheckIn.challenge_id == challenge.id))
    ).scalars().all() == []
