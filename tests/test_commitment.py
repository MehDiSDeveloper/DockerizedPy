"""«نمرهٔ تعهد» and the act's log -- the two things derived from everything else.

The score is pinned at the three places a kept-promise rate goes wrong:

* **the denominator is settled reports**, so a slow referee never drags
  somebody's number down and a skip counts against them.
* **a rejected report is in the denominator and not the numerator** -- which
  is the whole difference between «you reported» and «you kept».
* **the breakdown is by how hard the evidence was**, because a 100% is only
  worth knowing under what standard it was earned.

The log is pinned at the one property that makes it worth having: it is
append-only, in order, and a line outlives the report it is about.
"""

from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import create_session_cookie, hash_password
from app.commitment import (
    LEVEL_PHOTO,
    LEVEL_REFEREE,
    LEVEL_SELF,
    commitment_score,
    proof_level,
)
from app.models.act import ActEvent, ActEventKind, ChallengeReferee, RefereeState
from app.models.challenge import Challenge, ChallengeCategory, ProofKind, ReviewMode
from app.models.enrollment import ChallengeRole, Enrollment
from app.models.stats import ChallengeStats
from app.models.user import User


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


async def make_act(
    db: AsyncSession,
    owner: User,
    *,
    title: str = "An act",
    review_mode: str = ReviewMode.AUTO.value,
    proof_kind: str = ProofKind.SELF.value,
) -> Challenge:
    challenge = Challenge(
        title=title,
        owner_id=owner.id,
        category=ChallengeCategory.OTHER,
        cadence_kind="recurring_days",
        cadence={"kind": "recurring_days", "mode": "every_n_days", "n": 1},
        visibility="public",
        lifecycle_status="active",
        review_mode=review_mode,
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


async def enrol(db: AsyncSession, challenge: Challenge, user: User) -> Enrollment:
    enrollment = Enrollment(
        challenge_id=challenge.id,
        user_id=user.id,
        timezone="Asia/Tehran",
        start_date=local_today(),
    )
    db.add(enrollment)
    await db.commit()
    await db.refresh(enrollment)
    return enrollment


async def make_referee(db: AsyncSession, challenge: Challenge, user: User) -> None:
    db.add(
        ChallengeReferee(
            challenge_id=challenge.id,
            user_id=user.id,
            state=RefereeState.ACTIVE.value,
            invited_by_user_id=challenge.owner_id,
        )
    )
    await db.commit()


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


async def report(client: AsyncClient, challenge_id: int, state: str = "completed"):
    return await client.post(
        "/checkins/",
        json={
            "challenge_id": challenge_id,
            "occurrence_key": today_key(),
            "state": state,
        },
    )


# --- the fold ------------------------------------------------------------


def test_referee_is_the_top_level_whatever_the_proof_kind_is():
    """A human who said yes is a stronger claim than a file nobody opened,
    so a new proof kind does not add a level unless it changes *who is
    convinced*."""
    assert proof_level(ProofKind.SELF.value, ReviewMode.AUTO.value) == LEVEL_SELF
    assert proof_level(ProofKind.PHOTO.value, ReviewMode.AUTO.value) == LEVEL_PHOTO
    assert (
        proof_level(ProofKind.SELF.value, ReviewMode.REFEREE.value) == LEVEL_REFEREE
    )
    assert (
        proof_level(ProofKind.PHOTO.value, ReviewMode.REFEREE.value) == LEVEL_REFEREE
    )


# --- the score -----------------------------------------------------------


async def test_a_member_with_no_history_has_no_score(
    client: AsyncClient, db: AsyncSession
):
    """A 0٪ for somebody who has not started reads as a failure rather than
    as a beginning -- the call `avatar_url(None)` makes about no picture."""
    member = await make_user(db, "Member")
    score = await commitment_score(db, member.id)
    assert score.has_history is False
    assert score.rate == 0

    sign_in(client, member)
    assert "نمرهٔ تعهد" not in (await client.get(f"/views/users/{member.id}")).text


async def test_a_skip_counts_against_the_score(
    client: AsyncClient, db: AsyncSession
):
    """A skip is settled the moment it is made: the member decided, and said
    so."""
    member = await make_user(db, "Member")
    kept = await make_act(db, member, title="Kept")
    missed = await make_act(db, member, title="Missed")
    sign_in(client, member)

    await report(client, kept.id)
    await report(client, missed.id, state="skipped")

    score = await commitment_score(db, member.id)
    assert (score.kept, score.settled, score.rate) == (1, 2, 50)


async def test_a_pending_report_is_in_neither_half(
    client: AsyncClient, db: AsyncSession
):
    """So a slow referee never drags somebody's number down."""
    owner = await make_user(db, "Owner")
    doer = await make_user(db, "Doer")
    watched = await make_act(
        db, owner, title="Watched", review_mode=ReviewMode.REFEREE.value
    )
    await enrol(db, watched, doer)
    await make_referee(db, watched, owner)

    plain = await make_act(db, owner, title="Plain")
    await enrol(db, plain, doer)

    sign_in(client, doer)
    await report(client, plain.id)
    pending_id = (await report(client, watched.id)).json()["id"]

    score = await commitment_score(db, doer.id)
    assert (score.kept, score.settled, score.rate) == (1, 1, 100)
    assert score.pending == 1

    # Approving moves it into both halves at once.
    sign_in(client, owner)
    await client.post(f"/verifications/{pending_id}", json={"verdict": "approved"})
    score = await commitment_score(db, doer.id)
    assert (score.kept, score.settled, score.pending) == (2, 2, 0)


async def test_a_rejected_report_counts_against_the_score(
    client: AsyncClient, db: AsyncSession
):
    """The whole difference between «you reported» and «you kept»."""
    owner = await make_user(db, "Owner")
    doer = await make_user(db, "Doer")
    watched = await make_act(
        db, owner, title="Watched", review_mode=ReviewMode.REFEREE.value
    )
    await enrol(db, watched, doer)
    await make_referee(db, watched, owner)
    plain = await make_act(db, owner, title="Plain")
    await enrol(db, plain, doer)

    sign_in(client, doer)
    await report(client, plain.id)
    rejected_id = (await report(client, watched.id)).json()["id"]

    sign_in(client, owner)
    await client.post(
        f"/verifications/{rejected_id}",
        json={"verdict": "rejected", "note": "نبود"},
    )

    score = await commitment_score(db, doer.id)
    assert (score.kept, score.settled, score.rate) == (1, 2, 50)


async def test_the_breakdown_only_shows_levels_this_member_has_used(
    client: AsyncClient, db: AsyncSession
):
    """«با تأیید ناظر ۰٪» for somebody who has never been in such an act is
    a figure about nothing, and it reads as a failure."""
    member = await make_user(db, "Member")
    plain = await make_act(db, member, title="Plain")
    photo = await make_act(
        db, member, title="Photo", proof_kind=ProofKind.PHOTO.value
    )
    sign_in(client, member)
    await report(client, plain.id)
    # A photo act refuses a report with no evidence, so this one is a skip --
    # which is still a settled report at the photo level.
    await report(client, photo.id, state="skipped")

    score = await commitment_score(db, member.id)
    levels = {row.level: (row.kept, row.settled) for row in score.by_level}
    assert levels == {LEVEL_SELF: (1, 1), LEVEL_PHOTO: (0, 1)}
    assert [row.level for row in score.by_level] == [LEVEL_SELF, LEVEL_PHOTO]

    body = (await client.get(f"/views/users/{member.id}")).text
    assert "نمرهٔ تعهد" in body
    # Two bars, not three. Asserted on the rows rather than on the words:
    # «با تأیید ناظر» is also one of the lines the explainer spells out, and
    # that one is a *definition* -- it names every level whether or not this
    # member has used one.
    assert body.count('class="cs-level"') == 2


# --- the log -------------------------------------------------------------


async def test_the_log_records_every_act_in_order(
    client: AsyncClient, db: AsyncSession
):
    owner = await make_user(db, "Owner")
    doer = await make_user(db, "Doer")
    challenge = await make_act(db, owner, review_mode=ReviewMode.REFEREE.value)
    await make_referee(db, challenge, owner)

    sign_in(client, doer)
    await client.post(f"/enrollments/{challenge.id}")
    checkin_id = (await report(client, challenge.id)).json()["id"]

    sign_in(client, owner)
    await client.post(f"/verifications/{checkin_id}", json={"verdict": "approved"})

    kinds = (
        (
            await db.execute(
                select(ActEvent.kind)
                .where(ActEvent.challenge_id == challenge.id)
                .order_by(ActEvent.id.asc())
            )
        )
        .scalars()
        .all()
    )
    # No `act.created`: this act was built straight through the model rather
    # than through `create_challenge_record`, which is the one funnel that
    # writes that line -- and the funnel is where it belongs, so a create
    # through either front door cannot miss it.
    assert kinds == [
        ActEventKind.DOER_JOINED.value,
        ActEventKind.CHECKIN_REPORTED.value,
        ActEventKind.CHECKIN_APPROVED.value,
    ]

    # It reads forwards on the act's own page -- a log is read from the
    # beginning, because the order events happened in is what it preserves.
    body = (await client.get(f"/views/challenges/{challenge.id}")).text
    assert "دفتر رویدادها" in body
    assert body.index("به این تعهد پیوست") < body.index("را تأیید کرد")


async def test_a_line_outlives_the_report_it_is_about(
    client: AsyncClient, db: AsyncSession
):
    """`ActEvent.checkin_id` is deliberately not a foreign key: withdrawing
    a report must not erase the record that it was made."""
    owner = await make_user(db, "Owner")
    challenge = await make_act(db, owner)
    sign_in(client, owner)

    checkin_id = (await report(client, challenge.id)).json()["id"]
    assert (await client.delete(f"/checkins/{checkin_id}")).status_code == 204

    rows = (
        (
            await db.execute(
                select(ActEvent).where(ActEvent.challenge_id == challenge.id)
            )
        )
        .scalars()
        .all()
    )
    kinds = [r.kind for r in rows]
    assert ActEventKind.CHECKIN_REPORTED.value in kinds
    assert ActEventKind.CHECKIN_WITHDRAWN.value in kinds
    # The id is kept as a bare number, pointing at a row that is gone.
    withdrawn = next(
        r for r in rows if r.kind == ActEventKind.CHECKIN_WITHDRAWN.value
    )
    assert withdrawn.checkin_id == checkin_id


async def test_a_rolled_back_action_leaves_no_line(
    client: AsyncClient, db: AsyncSession
):
    """`record` adds and never commits -- so an act that did not happen
    cannot leave a line saying it did."""
    owner = await make_user(db, "Owner")
    stranger = await make_user(db, "Stranger")
    challenge = await make_act(db, owner)

    sign_in(client, stranger)
    # Refused: only the owner staffs an act.
    assert (
        await client.post(
            f"/challenges/{challenge.id}/referees",
            json={"identifier": stranger.email},
        )
    ).status_code == 403

    kinds = (
        (
            await db.execute(
                select(ActEvent.kind).where(ActEvent.challenge_id == challenge.id)
            )
        )
        .scalars()
        .all()
    )
    assert ActEventKind.REFEREE_INVITED.value not in kinds


async def test_the_log_has_no_route_that_changes_it(client: AsyncClient):
    """Append-only is enforced by what does not exist: one writer, no
    update, no delete, and a model with no `updated_at` for an edit to be
    expressible through."""
    from app.main import app

    paths = [
        p
        for p in app.openapi()["paths"]
        if "/events" in p or "/actlog" in p or "/act-log" in p
    ]
    assert paths == []
    assert not hasattr(ActEvent, "updated_at")
    assert not hasattr(ActEvent, "last_modifier_user_id")
