"""The challenge leaderboard: its gate, its ranking, and what it may print.

Four things are pinned here, and they are the four ways this feature can go
wrong:

* **the gate** — the board answers "who is in this", which is exactly what
  `profile_visibility_filter` withholds from members everywhere else. The
  answer chosen here is *membership*, not a widening of that filter: a
  signed-out visitor gets the login redirect, a member who has not joined
  gets 403, and someone who cannot even see the challenge gets 404 — in that
  order, so a private challenge is never confirmed by a permission error.
* **the ranking** — counted live off `CheckIns`. Ranking off
  `ChallengeStats.total_completions` (challenge-wide) or
  `Enrollments.legacy_completed_count` (frozen pre-migration data) would look
  plausible and be wrong, which is the failure mode worth a test.
* **ties** — equal scores share a rank. Positional numbering invents a
  difference the data does not contain, and whoever is shown 4th against an
  equal 3rd is being told something untrue.
* **anonymity** — an anonymous participant keeps their score and loses their
  name. Both halves matter: printing the name breaks the promise the
  challenge made, and dropping the row lets everyone else derive who is
  missing.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import create_session_cookie, hash_password
from app.models.challenge import Challenge, ChallengeCategory
from app.models.checkin import CheckIn
from app.models.enrollment import ChallengeRole, Enrollment
from app.models.user import User

ANONYMOUS_NAME = "عضو ناشناس"


def board_path(challenge_id: int, sort: str | None = None) -> str:
    path = f"/views/challenges/{challenge_id}/leaderboard"
    return f"{path}?sort={sort}" if sort else path


def fragment_path(challenge_id: int) -> str:
    return f"/views/challenges/{challenge_id}/leaderboard/fragment"


async def make_user(db: AsyncSession, name: str) -> User:
    user = User(
        name=name,
        email=f"{name.lower().replace(' ', '.')}@example.com",
        password_hash=hash_password("password123"),
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


def sign_in(client: AsyncClient, user: User) -> None:
    client.cookies.set("session", create_session_cookie(user.id))


async def make_challenge(
    db: AsyncSession,
    owner: User,
    *,
    title: str = "چالش تست",
    visibility: str = "public",
) -> Challenge:
    challenge = Challenge(
        title=title,
        owner_id=owner.id,
        category=ChallengeCategory.OTHER,
        cadence_kind="recurring_days",
        cadence={
            "kind": "recurring_days",
            "mode": "weekdays",
            "weekdays": [0, 1, 2, 3, 4, 5, 6],
        },
        visibility=visibility,
        lifecycle_status="active",
    )
    db.add(challenge)
    await db.flush()
    db.add(
        Enrollment(
            challenge_id=challenge.id,
            user_id=owner.id,
            start_date=date(2026, 1, 1),
            role=ChallengeRole.OWNER.value,
        )
    )
    await db.commit()
    await db.refresh(challenge)
    return challenge


async def join(
    db: AsyncSession,
    challenge: Challenge,
    user: User,
    *,
    anonymous: bool = False,
    streak: int = 0,
) -> Enrollment:
    enrollment = Enrollment(
        challenge_id=challenge.id,
        user_id=user.id,
        start_date=date(2026, 1, 1),
        role=ChallengeRole.PARTICIPANT.value,
        is_anonymous=anonymous,
        current_streak=streak,
    )
    db.add(enrollment)
    await db.commit()
    await db.refresh(enrollment)
    return enrollment


async def log_completions(
    db: AsyncSession, enrollment: Enrollment, count: int, *, state: str = "completed"
) -> None:
    """`count` check-ins for one enrollment, with distinct occurrence keys.

    Written straight to the table rather than through `POST /checkins`: the
    backfill window would refuse most of these, and what is under test is how
    the board counts rows, not how they get there.
    """
    for index in range(count):
        db.add(
            CheckIn(
                enrollment_id=enrollment.id,
                challenge_id=enrollment.challenge_id,
                occurrence_key=f"D2026-01-{index + 1:02d}",
                state=state,
                occurrence_local_date=date(2026, 1, index + 1),
                occurred_at_utc=datetime(2026, 1, index + 1, 9, tzinfo=UTC),
                timezone="Asia/Tehran",
            )
        )
    await db.commit()


# --------------------------------------------------------------------------
# The gate
# --------------------------------------------------------------------------


async def test_signed_out_visitor_is_sent_to_login(
    client: AsyncClient, db: AsyncSession
):
    """Authentication fails before authorization does — a 303 to the login
    page carrying `next`, never the 403 a signed-in outsider gets."""
    owner = await make_user(db, "Owner")
    challenge = await make_challenge(db, owner)

    response = await client.get(board_path(challenge.id), follow_redirects=False)

    assert response.status_code == 303
    assert "/views/auth/" in response.headers["location"]


async def test_member_who_has_not_joined_is_refused(
    client: AsyncClient, db: AsyncSession
):
    """403, not 404: the challenge is public, so the caller can already see
    it, and hiding the board behind a "no such page" would withhold the one
    thing they need to know — that joining is what opens it."""
    owner = await make_user(db, "Owner")
    outsider = await make_user(db, "Outsider")
    challenge = await make_challenge(db, owner)
    sign_in(client, outsider)

    assert (await client.get(board_path(challenge.id))).status_code == 403
    assert (await client.get(fragment_path(challenge.id))).status_code == 403


async def test_invisible_challenge_is_404_not_403(
    client: AsyncClient, db: AsyncSession
):
    """Reachability is checked first and composed into the query, so a
    private challenge falls out as "no such row" — a 403 here would confirm
    the existence of a challenge `GET /views/challenges/{id}` hides."""
    owner = await make_user(db, "Owner")
    outsider = await make_user(db, "Outsider")
    challenge = await make_challenge(db, owner, visibility="private")
    sign_in(client, outsider)

    assert (await client.get(board_path(challenge.id))).status_code == 404


async def test_participant_may_read_the_board(client: AsyncClient, db: AsyncSession):
    owner = await make_user(db, "Owner")
    member = await make_user(db, "Member")
    challenge = await make_challenge(db, owner)
    await join(db, challenge, member)
    sign_in(client, member)

    response = await client.get(board_path(challenge.id))

    assert response.status_code == 200
    assert "Owner" in response.text
    assert "Member" in response.text


async def test_fragment_answers_401_when_signed_out(
    client: AsyncClient, db: AsyncSession
):
    """`createInfiniteScroller()` reads the status to redirect itself, and a
    303 would be followed silently and the login form appended as rows."""
    owner = await make_user(db, "Owner")
    challenge = await make_challenge(db, owner)

    response = await client.get(fragment_path(challenge.id), follow_redirects=False)

    assert response.status_code == 401


# --------------------------------------------------------------------------
# The ranking
# --------------------------------------------------------------------------


async def test_rows_are_ordered_by_live_completions(
    client: AsyncClient, db: AsyncSession
):
    from app.routers.enrollment import fetch_leaderboard_page

    owner = await make_user(db, "Owner")
    busy = await make_user(db, "Busy")
    quiet = await make_user(db, "Quiet")
    challenge = await make_challenge(db, owner)
    busy_enrollment = await join(db, challenge, busy)
    quiet_enrollment = await join(db, challenge, quiet)
    await log_completions(db, busy_enrollment, 5)
    await log_completions(db, quiet_enrollment, 2)

    rows, has_more = await fetch_leaderboard_page(db, challenge_id=challenge.id)

    assert has_more is False
    assert [row["enrollment"].user_id for row in rows][:2] == [busy.id, quiet.id]
    assert [row["completions"] for row in rows][:2] == [5, 2]


async def test_skipped_checkins_do_not_count(client: AsyncClient, db: AsyncSession):
    """Only `completed` rows. A skip is a recorded occurrence, not a done
    one, and a board that counted both would rank the two identically."""
    from app.routers.enrollment import fetch_leaderboard_page

    owner = await make_user(db, "Owner")
    challenge = await make_challenge(db, owner)
    member = await make_user(db, "Member")
    enrollment = await join(db, challenge, member)
    await log_completions(db, enrollment, 3, state="skipped")

    rows, _ = await fetch_leaderboard_page(db, challenge_id=challenge.id)
    mine = next(r for r in rows if r["enrollment"].user_id == member.id)

    assert mine["completions"] == 0


async def test_streak_sort_uses_its_own_leading_key(
    client: AsyncClient, db: AsyncSession
):
    from app.routers.enrollment import LEADERBOARD_STREAK, fetch_leaderboard_page

    owner = await make_user(db, "Owner")
    volume = await make_user(db, "Volume")
    steady = await make_user(db, "Steady")
    challenge = await make_challenge(db, owner)
    volume_enrollment = await join(db, challenge, volume, streak=1)
    await join(db, challenge, steady, streak=9)
    await log_completions(db, volume_enrollment, 20)

    by_volume, _ = await fetch_leaderboard_page(db, challenge_id=challenge.id)
    by_streak, _ = await fetch_leaderboard_page(
        db, challenge_id=challenge.id, sort=LEADERBOARD_STREAK
    )

    assert by_volume[0]["enrollment"].user_id == volume.id
    assert by_streak[0]["enrollment"].user_id == steady.id


async def test_equal_scores_share_a_rank(client: AsyncClient, db: AsyncSession):
    """Competition ranking: two people on eleven check-ins are both 1st, and
    the next person is 3rd — not 2nd, and not one of them arbitrarily 2nd."""
    from app.routers.enrollment import (
        assign_ranks,
        fetch_leaderboard_page,
        leaderboard_rank,
    )

    owner = await make_user(db, "Owner")
    first = await make_user(db, "First")
    second = await make_user(db, "Second")
    challenge = await make_challenge(db, owner)
    await log_completions(db, await join(db, challenge, first), 3)
    await log_completions(db, await join(db, challenge, second), 3)

    rows, _ = await fetch_leaderboard_page(db, challenge_id=challenge.id)
    first_rank = await leaderboard_rank(
        db, challenge_id=challenge.id, sort="completions", score=rows[0]["score"]
    )
    assign_ranks(rows, offset=0, first_rank=first_rank)

    # The owner is enrolled too and has logged nothing, so they trail both.
    assert [row["rank"] for row in rows] == [1, 1, 3]


async def test_my_rank_agrees_with_my_row(client: AsyncClient, db: AsyncSession):
    """The «رتبه تو» card and the row it points at are one query, so they can
    never disagree — this is what pins that they still share it."""
    from app.routers.enrollment import (
        assign_ranks,
        fetch_leaderboard_page,
        leaderboard_rank,
        leaderboard_standing,
    )

    owner = await make_user(db, "Owner")
    member = await make_user(db, "Member")
    challenge = await make_challenge(db, owner)
    enrollment = await join(db, challenge, member)
    await log_completions(db, enrollment, 4)

    standing = await leaderboard_standing(db, enrollment, "completions")
    rows, _ = await fetch_leaderboard_page(db, challenge_id=challenge.id)
    assign_ranks(
        rows,
        offset=0,
        first_rank=await leaderboard_rank(
            db, challenge_id=challenge.id, sort="completions", score=rows[0]["score"]
        ),
    )
    mine = next(r for r in rows if r["enrollment"].id == enrollment.id)

    assert standing["rank"] == mine["rank"] == 1
    assert standing["completions"] == 4


# --------------------------------------------------------------------------
# Anonymity
# --------------------------------------------------------------------------


async def test_anonymous_participant_keeps_the_score_and_loses_the_name(
    client: AsyncClient, db: AsyncSession
):
    owner = await make_user(db, "Owner")
    hidden = await make_user(db, "Hidden Person")
    watcher = await make_user(db, "Watcher")
    challenge = await make_challenge(db, owner)
    hidden_enrollment = await join(db, challenge, hidden, anonymous=True)
    await join(db, challenge, watcher)
    await log_completions(db, hidden_enrollment, 7)
    sign_in(client, watcher)

    response = await client.get(board_path(challenge.id))

    assert response.status_code == 200
    assert "Hidden Person" not in response.text
    assert ANONYMOUS_NAME in response.text
    # The row is still there, and still carries its figure: dropping it would
    # let everyone else derive who is missing.
    assert ">7<" in response.text


async def test_an_anonymous_member_still_sees_their_own_name(
    client: AsyncClient, db: AsyncSession
):
    """You already know who you are, and a board you cannot find yourself on
    is a board nobody can read their own position off."""
    owner = await make_user(db, "Owner")
    hidden = await make_user(db, "Hidden Person")
    challenge = await make_challenge(db, owner)
    await join(db, challenge, hidden, anonymous=True)
    sign_in(client, hidden)

    response = await client.get(board_path(challenge.id))

    assert response.status_code == 200
    assert "Hidden Person" in response.text


# --------------------------------------------------------------------------
# The way in
# --------------------------------------------------------------------------


async def test_challenge_detail_links_to_the_board_for_a_participant(
    client: AsyncClient, db: AsyncSession
):
    """A working route nothing links to is a feature nobody finds. The link
    is rendered only once there is somebody to be ranked against."""
    owner = await make_user(db, "Owner")
    member = await make_user(db, "Member")
    challenge = await make_challenge(db, owner)
    await join(db, challenge, member)
    sign_in(client, member)

    response = await client.get(f"/views/challenges/{challenge.id}")

    assert response.status_code == 200
    assert f"/views/challenges/{challenge.id}/leaderboard" in response.text


async def test_challenge_detail_hides_the_link_from_a_non_participant(
    client: AsyncClient, db: AsyncSession
):
    """The board 403s them, so a visible link could only disappoint."""
    owner = await make_user(db, "Owner")
    outsider = await make_user(db, "Outsider")
    challenge = await make_challenge(db, owner)
    sign_in(client, outsider)

    response = await client.get(f"/views/challenges/{challenge.id}")

    assert response.status_code == 200
    assert f"/views/challenges/{challenge.id}/leaderboard" not in response.text


@pytest.mark.parametrize("sort", ["completions", "streak"])
async def test_both_orderings_render(client: AsyncClient, db: AsyncSession, sort: str):
    owner = await make_user(db, "Owner")
    challenge = await make_challenge(db, owner)
    sign_in(client, owner)

    assert (await client.get(board_path(challenge.id, sort))).status_code == 200


async def test_an_unknown_sort_is_refused(client: AsyncClient, db: AsyncSession):
    """The `?sort=` pattern refuses «velocity» rather than ignoring it —
    "what is hot" is a discovery question, and silently falling back would
    render a board that is not the one the URL asked for."""
    owner = await make_user(db, "Owner")
    challenge = await make_challenge(db, owner)
    sign_in(client, owner)

    assert (await client.get(board_path(challenge.id, "velocity"))).status_code == 422
