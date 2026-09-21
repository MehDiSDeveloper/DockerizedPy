"""The verification loop: what a referee can do, and what nobody can.

Four paths, and they are the four the feature exists for:

* **an act with no referee is unchanged.** A report is ``auto`` the moment it
  is made, it counts, and the streak moves. This is the test that says every
  challenge that existed before this feature still behaves exactly as it did
  -- if it breaks, the migration's "no data change" claim is false.
* **approval closes the loop.** A report on a reviewed act is ``pending``,
  does not count, and *does* count once a referee says yes -- with the
  streak restored by recomputation rather than by arithmetic.
* **rejection closes it the other way.** The report stays, the doer is told,
  and nothing counts.
* **nobody rules on their own report.** The rule a pact rests on: two people
  who are each other's referee must not be able to approve themselves.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import create_session_cookie, hash_password
from app.models.act import ChallengeReferee, RefereeState
from app.models.challenge import Challenge, ChallengeCategory, ReviewMode
from app.models.checkin import CheckIn
from app.models.enrollment import ChallengeRole, Enrollment
from app.models.notification import Notification, NotificationKind
from app.models.stats import ChallengeStats
from app.models.user import User
from app.verification import (
    VERDICT_APPROVED,
    VERDICT_AUTO,
    VERDICT_PENDING,
    VERDICT_REJECTED,
)


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
    review_mode: str = ReviewMode.AUTO.value,
    proof_kind: str = "self",
) -> Challenge:
    """A daily act, so an occurrence is due today for anybody enrolled."""
    challenge = Challenge(
        title="A daily act",
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


async def make_referee(
    db: AsyncSession, challenge: Challenge, user: User, inviter: User
) -> ChallengeReferee:
    row = ChallengeReferee(
        challenge_id=challenge.id,
        user_id=user.id,
        state=RefereeState.ACTIVE.value,
        invited_by_user_id=inviter.id,
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return row


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


# --- the act nobody watches ---------------------------------------------


async def test_an_act_with_no_referee_settles_on_the_spot(
    client: AsyncClient, db: AsyncSession
):
    """The behaviour every challenge in the app already had.

    If this ever fails, the migration's whole claim -- that adding these
    columns changes nothing for anybody -- is false.
    """
    owner = await make_user(db, "Sara")
    challenge = await make_act(db, owner)
    sign_in(client, owner)

    res = await report(client, challenge.id)
    assert res.status_code == 201
    assert res.json()["verdict"] == VERDICT_AUTO

    enrollment = (
        await db.execute(
            select(Enrollment).where(
                Enrollment.challenge_id == challenge.id,
                Enrollment.user_id == owner.id,
            )
        )
    ).scalar_one()
    await db.refresh(enrollment)
    assert enrollment.current_streak == 1

    stats = (
        await db.execute(
            select(ChallengeStats).where(
                ChallengeStats.challenge_id == challenge.id
            )
        )
    ).scalar_one()
    await db.refresh(stats)
    assert stats.total_completions == 1


# --- the act somebody watches -------------------------------------------


async def test_a_report_waits_and_does_not_count_until_it_is_approved(
    client: AsyncClient, db: AsyncSession
):
    owner = await make_user(db, "Owner")
    doer = await make_user(db, "Doer")
    challenge = await make_act(db, owner, review_mode=ReviewMode.REFEREE.value)
    enrollment = await enrol(db, challenge, doer)
    await make_referee(db, challenge, owner, owner)

    sign_in(client, doer)
    res = await report(client, challenge.id)
    assert res.status_code == 201
    assert res.json()["verdict"] == VERDICT_PENDING

    # It is recorded, and it counts for nothing -- neither the streak nor
    # the challenge's monotonic counter has moved.
    await db.refresh(enrollment)
    assert enrollment.current_streak == 0
    stats = (
        await db.execute(
            select(ChallengeStats).where(
                ChallengeStats.challenge_id == challenge.id
            )
        )
    ).scalar_one()
    await db.refresh(stats)
    assert stats.total_completions == 0

    # The referee was told, and the report is in their queue.
    sign_in(client, owner)
    queue = await client.get("/verifications/")
    assert queue.status_code == 200
    assert [r["id"] for r in queue.json()] == [res.json()["id"]]
    assert (await client.get("/verifications/count")).json()["count"] == 1

    ruled = await client.post(
        f"/verifications/{res.json()['id']}", json={"verdict": "approved"}
    )
    assert ruled.status_code == 200
    assert ruled.json()["verdict"] == VERDICT_APPROVED
    assert ruled.json()["verdict_by_user_id"] == owner.id

    # The streak was *recomputed*, not incremented -- which is why an
    # approval restores exactly what the pending report was holding open.
    await db.refresh(enrollment)
    assert enrollment.current_streak == 1
    await db.refresh(stats)
    assert stats.total_completions == 1

    # The doer heard, and the queue is empty again.
    kinds = (
        (
            await db.execute(
                select(Notification.kind).where(Notification.user_id == doer.id)
            )
        )
        .scalars()
        .all()
    )
    assert NotificationKind.CHECKIN_APPROVED.value in kinds
    assert (await client.get("/verifications/count")).json()["count"] == 0


async def test_a_rejection_keeps_the_report_and_counts_nothing(
    client: AsyncClient, db: AsyncSession
):
    owner = await make_user(db, "Owner")
    doer = await make_user(db, "Doer")
    challenge = await make_act(db, owner, review_mode=ReviewMode.REFEREE.value)
    enrollment = await enrol(db, challenge, doer)
    await make_referee(db, challenge, owner, owner)

    sign_in(client, doer)
    checkin_id = (await report(client, challenge.id)).json()["id"]

    sign_in(client, owner)
    ruled = await client.post(
        f"/verifications/{checkin_id}",
        json={"verdict": "rejected", "note": "عکس ربطی نداشت"},
    )
    assert ruled.status_code == 200
    assert ruled.json()["verdict"] == VERDICT_REJECTED
    assert ruled.json()["verdict_note"] == "عکس ربطی نداشت"

    # The row survives -- a refusal is a record, not a deletion.
    row = (
        await db.execute(select(CheckIn).where(CheckIn.id == checkin_id))
    ).scalar_one()
    await db.refresh(row)
    assert row.state == "completed"

    await db.refresh(enrollment)
    assert enrollment.current_streak == 0
    assert enrollment.last_checkin_local_date is None

    kinds = (
        (
            await db.execute(
                select(Notification.kind).where(Notification.user_id == doer.id)
            )
        )
        .scalars()
        .all()
    )
    assert NotificationKind.CHECKIN_REJECTED.value in kinds


async def test_a_rejection_must_carry_a_reason(client: AsyncClient, db: AsyncSession):
    """A refusal with no reason is a refusal nobody can act on."""
    owner = await make_user(db, "Owner")
    doer = await make_user(db, "Doer")
    challenge = await make_act(db, owner, review_mode=ReviewMode.REFEREE.value)
    await enrol(db, challenge, doer)
    await make_referee(db, challenge, owner, owner)

    sign_in(client, doer)
    checkin_id = (await report(client, challenge.id)).json()["id"]

    sign_in(client, owner)
    assert (
        await client.post(
            f"/verifications/{checkin_id}", json={"verdict": "rejected"}
        )
    ).status_code == 422
    # An approval needs none: nothing follows from it.
    assert (
        await client.post(
            f"/verifications/{checkin_id}", json={"verdict": "approved"}
        )
    ).status_code == 200


async def test_a_second_ruling_is_refused(client: AsyncClient, db: AsyncSession):
    """Two referees disagreeing after the fact is not a correction."""
    owner = await make_user(db, "Owner")
    doer = await make_user(db, "Doer")
    other = await make_user(db, "Other")
    challenge = await make_act(db, owner, review_mode=ReviewMode.REFEREE.value)
    await enrol(db, challenge, doer)
    await make_referee(db, challenge, owner, owner)
    await make_referee(db, challenge, other, owner)

    sign_in(client, doer)
    checkin_id = (await report(client, challenge.id)).json()["id"]

    sign_in(client, owner)
    assert (
        await client.post(
            f"/verifications/{checkin_id}", json={"verdict": "approved"}
        )
    ).status_code == 200

    sign_in(client, other)
    assert (
        await client.post(
            f"/verifications/{checkin_id}",
            json={"verdict": "rejected", "note": "نه"},
        )
    ).status_code == 409


# --- the rule a pact rests on -------------------------------------------


async def test_nobody_rules_on_their_own_report(
    client: AsyncClient, db: AsyncSession
):
    """In a pact both people hold both rows -- so this is the only thing
    stopping each of them from simply approving themselves."""
    a = await make_user(db, "Ali")
    b = await make_user(db, "Bita")
    challenge = await make_act(db, a, review_mode=ReviewMode.REFEREE.value)
    await enrol(db, challenge, b)
    await make_referee(db, challenge, a, a)
    await make_referee(db, challenge, b, a)

    sign_in(client, a)
    mine = (await report(client, challenge.id)).json()["id"]

    # 403, not 404: `a` can demonstrably already see this report -- it is
    # theirs -- and being told plainly is the only way a pact reads.
    assert (
        await client.post(f"/verifications/{mine}", json={"verdict": "approved"})
    ).status_code == 403

    # And it is not in their own queue either, which is the half that keeps
    # a scroller's offset honest -- own rows are excluded in SQL, not
    # filtered out afterwards.
    assert (await client.get("/verifications/")).json() == []

    # The other half of the pact can rule on it.
    sign_in(client, b)
    assert [r["id"] for r in (await client.get("/verifications/")).json()] == [mine]
    assert (
        await client.post(f"/verifications/{mine}", json={"verdict": "approved"})
    ).status_code == 200


async def test_a_stranger_gets_404_not_403(client: AsyncClient, db: AsyncSession):
    """Check-in ids are sequential and nothing links somebody to another
    member's report, so a 403 here would map every member's logged history."""
    owner = await make_user(db, "Owner")
    doer = await make_user(db, "Doer")
    stranger = await make_user(db, "Stranger")
    challenge = await make_act(db, owner, review_mode=ReviewMode.REFEREE.value)
    await enrol(db, challenge, doer)
    await make_referee(db, challenge, owner, owner)

    sign_in(client, doer)
    checkin_id = (await report(client, challenge.id)).json()["id"]

    sign_in(client, stranger)
    assert (
        await client.post(
            f"/verifications/{checkin_id}", json={"verdict": "approved"}
        )
    ).status_code == 404


async def test_an_invited_referee_holds_no_power_until_they_accept(
    client: AsyncClient, db: AsyncSession
):
    owner = await make_user(db, "Owner")
    doer = await make_user(db, "Doer")
    watcher = await make_user(db, "Watcher")
    challenge = await make_act(db, owner, review_mode=ReviewMode.REFEREE.value)
    await enrol(db, challenge, doer)
    row = await make_referee(db, challenge, watcher, owner)
    row.state = RefereeState.INVITED.value
    await db.commit()

    sign_in(client, doer)
    checkin_id = (await report(client, challenge.id)).json()["id"]

    sign_in(client, watcher)
    assert (await client.get("/verifications/")).json() == []
    assert (
        await client.post(
            f"/verifications/{checkin_id}", json={"verdict": "approved"}
        )
    ).status_code == 404

    # Accepting is what turns the invitation into a power.
    assert (
        await client.patch(
            f"/challenges/{challenge.id}/referees/me", json={"accept": True}
        )
    ).status_code == 200
    assert [r["id"] for r in (await client.get("/verifications/")).json()] == [
        checkin_id
    ]


async def test_amending_a_report_puts_it_back_in_the_queue(
    client: AsyncClient, db: AsyncSession
):
    """Otherwise a doer has any claim approved by submitting an acceptable
    one first and editing it afterwards."""
    owner = await make_user(db, "Owner")
    doer = await make_user(db, "Doer")
    challenge = await make_act(db, owner, review_mode=ReviewMode.REFEREE.value)
    await enrol(db, challenge, doer)
    await make_referee(db, challenge, owner, owner)

    sign_in(client, doer)
    checkin_id = (await report(client, challenge.id)).json()["id"]

    sign_in(client, owner)
    await client.post(f"/verifications/{checkin_id}", json={"verdict": "approved"})
    assert (await client.get("/verifications/count")).json()["count"] == 0

    sign_in(client, doer)
    amended = await client.patch(
        f"/checkins/{checkin_id}", json={"note": "در واقع فرق داشت"}
    )
    assert amended.status_code == 200
    assert amended.json()["verdict"] == VERDICT_PENDING
    # The ruling that was written is gone with the report it was about.
    assert amended.json()["verdict_by_user_id"] is None

    sign_in(client, owner)
    assert (await client.get("/verifications/count")).json()["count"] == 1


async def test_a_skip_never_waits_on_anybody(client: AsyncClient, db: AsyncSession):
    """There is nothing to vouch for in somebody saying they did not do it,
    and a queue of skips is a queue nobody should have to work through."""
    owner = await make_user(db, "Owner")
    doer = await make_user(db, "Doer")
    challenge = await make_act(db, owner, review_mode=ReviewMode.REFEREE.value)
    await enrol(db, challenge, doer)
    await make_referee(db, challenge, owner, owner)

    sign_in(client, doer)
    res = await client.post(
        "/checkins/",
        json={
            "challenge_id": challenge.id,
            "occurrence_key": today_key(),
            "state": "skipped",
        },
    )
    assert res.status_code == 201
    assert res.json()["verdict"] == VERDICT_AUTO

    sign_in(client, owner)
    assert (await client.get("/verifications/count")).json()["count"] == 0


async def test_a_pending_report_is_not_offered_again_today(
    client: AsyncClient, db: AsyncSession
):
    """It occupies its seat whatever became of it -- «امروز» must not offer
    an occurrence somebody has already reported on."""
    owner = await make_user(db, "Owner")
    doer = await make_user(db, "Doer")
    challenge = await make_act(db, owner, review_mode=ReviewMode.REFEREE.value)
    await enrol(db, challenge, doer)
    await make_referee(db, challenge, owner, owner)

    sign_in(client, doer)
    assert len((await client.get("/today/")).json()) == 1
    await report(client, challenge.id)
    assert (await client.get("/today/")).json() == []


async def test_the_verdict_cannot_be_written_through_the_checkin_patch(
    client: AsyncClient, db: AsyncSession
):
    """`update_my_enrollment`'s precedent: a field a blind setattr would
    apply is a field the policy cannot guard, so it is off the schema."""
    owner = await make_user(db, "Owner")
    doer = await make_user(db, "Doer")
    challenge = await make_act(db, owner, review_mode=ReviewMode.REFEREE.value)
    await enrol(db, challenge, doer)
    await make_referee(db, challenge, owner, owner)

    sign_in(client, doer)
    checkin_id = (await report(client, challenge.id)).json()["id"]

    res = await client.patch(
        f"/checkins/{checkin_id}", json={"verdict": "approved"}
    )
    # Accepted as a request (extra keys are ignored) and refused as a write.
    assert res.status_code == 200
    assert res.json()["verdict"] == VERDICT_PENDING


async def test_a_referee_can_open_the_act_they_were_asked_about(
    client: AsyncClient, db: AsyncSession
):
    """Otherwise the invitation is a notification about a 404."""
    owner = await make_user(db, "Owner")
    watcher = await make_user(db, "Watcher")
    challenge = await make_act(db, owner, review_mode=ReviewMode.REFEREE.value)
    challenge.visibility = "private"
    await db.commit()

    sign_in(client, watcher)
    assert (await client.get(f"/challenges/{challenge.id}")).status_code == 404

    row = await make_referee(db, challenge, watcher, owner)
    row.state = RefereeState.INVITED.value
    await db.commit()

    # An `invited` row is enough: deciding whether to accept means reading
    # what you would be vouching for.
    assert (await client.get(f"/challenges/{challenge.id}")).status_code == 200


async def test_the_verdict_survives_the_referee_being_removed(
    client: AsyncClient, db: AsyncSession
):
    """A verdict is stored, so it is not re-decided when the person who
    wrote it is taken off -- the same trade `Enrollment.is_anonymous` makes
    about a mode that changed later."""
    owner = await make_user(db, "Owner")
    doer = await make_user(db, "Doer")
    watcher = await make_user(db, "Watcher")
    challenge = await make_act(db, owner, review_mode=ReviewMode.REFEREE.value)
    await enrol(db, challenge, doer)
    await make_referee(db, challenge, watcher, owner)

    sign_in(client, doer)
    checkin_id = (await report(client, challenge.id)).json()["id"]

    sign_in(client, watcher)
    await client.post(f"/verifications/{checkin_id}", json={"verdict": "approved"})

    sign_in(client, owner)
    assert (
        await client.delete(f"/challenges/{challenge.id}/referees/{watcher.id}")
    ).status_code == 204

    row = (
        await db.execute(select(CheckIn).where(CheckIn.id == checkin_id))
    ).scalar_one()
    await db.refresh(row)
    assert row.verdict == VERDICT_APPROVED
    assert row.verdict_by_user_id == watcher.id


async def test_the_queue_is_oldest_first(client: AsyncClient, db: AsyncSession):
    """A queue of work, not a feed: whoever reported first has waited
    longest. The one list in the app besides the group's request queue that
    does not read `newest_first`."""
    owner = await make_user(db, "Owner")
    first = await make_user(db, "First")
    second = await make_user(db, "Second")
    challenge = await make_act(db, owner, review_mode=ReviewMode.REFEREE.value)
    await enrol(db, challenge, first)
    await enrol(db, challenge, second)
    await make_referee(db, challenge, owner, owner)

    sign_in(client, first)
    a = (await report(client, challenge.id)).json()["id"]
    sign_in(client, second)
    b = (await report(client, challenge.id)).json()["id"]

    sign_in(client, owner)
    assert [r["id"] for r in (await client.get("/verifications/")).json()] == [a, b]


async def test_turning_review_on_does_not_re_decide_what_is_settled(
    client: AsyncClient, db: AsyncSession
):
    """`review_mode` is deliberately not in `locked_fields`: it changes what
    happens next, never what a report that already stands means."""
    owner = await make_user(db, "Owner")
    challenge = await make_act(db, owner)
    sign_in(client, owner)
    checkin_id = (await report(client, challenge.id)).json()["id"]

    assert (
        await client.patch(
            f"/challenges/{challenge.id}", json={"review_mode": "referee"}
        )
    ).status_code == 200

    row = (
        await db.execute(select(CheckIn).where(CheckIn.id == checkin_id))
    ).scalar_one()
    await db.refresh(row)
    assert row.verdict == VERDICT_AUTO


async def test_a_verdict_is_stamped_with_who_and_when(
    client: AsyncClient, db: AsyncSession
):
    owner = await make_user(db, "Owner")
    doer = await make_user(db, "Doer")
    challenge = await make_act(db, owner, review_mode=ReviewMode.REFEREE.value)
    await enrol(db, challenge, doer)
    await make_referee(db, challenge, owner, owner)

    sign_in(client, doer)
    checkin_id = (await report(client, challenge.id)).json()["id"]

    before = datetime.now(UTC)
    sign_in(client, owner)
    await client.post(f"/verifications/{checkin_id}", json={"verdict": "approved"})

    row = (
        await db.execute(select(CheckIn).where(CheckIn.id == checkin_id))
    ).scalar_one()
    await db.refresh(row)
    assert row.verdict_by_user_id == owner.id
    stamped = row.verdict_at
    if stamped.tzinfo is None:
        stamped = stamped.replace(tzinfo=UTC)
    assert stamped >= before.replace(microsecond=0)


async def test_the_queue_page_renders_the_report_and_its_invitations(
    client: AsyncClient, db: AsyncSession
):
    """The SSR half: one screen carrying both lists, and the row a referee
    acts on without opening anything else."""
    owner = await make_user(db, "Owner")
    doer = await make_user(db, "Doer")
    watcher = await make_user(db, "Watcher")
    challenge = await make_act(db, owner, review_mode=ReviewMode.REFEREE.value)
    await enrol(db, challenge, doer)
    await make_referee(db, challenge, watcher, owner)

    sign_in(client, doer)
    await report(client, challenge.id, note="پنج کیلومتر دویدم")

    # A second act this member has only been *invited* to referee.
    pending = await make_act(db, owner, review_mode=ReviewMode.REFEREE.value)
    row = await make_referee(db, pending, watcher, owner)
    row.state = RefereeState.INVITED.value
    await db.commit()

    sign_in(client, watcher)
    page = await client.get("/views/verifications/")
    assert page.status_code == 200
    body = page.text
    assert "تأییدها" in body
    # Everything a ruling needs is on the row: who, which act, and what they
    # wrote -- a referee who has to open another screen decides later.
    assert "Doer" in body
    assert "پنج کیلومتر دویدم" in body
    # And the invitation that would fill the queue further.
    assert "دعوت به ناظری" in body

    fragment = await client.get("/views/verifications/fragment?offset=0&limit=20")
    assert fragment.status_code == 200
    assert fragment.headers["X-Has-More"] == "false"

    # A signed-out visitor is an *authentication* failure: 303 on the page,
    # a real 401 on the fragment (CLAUDE.md, the 303/401 split).
    client.cookies.clear()
    assert (await client.get("/views/verifications/")).status_code == 303
    assert (await client.get("/views/verifications/fragment")).status_code == 401
