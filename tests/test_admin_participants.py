"""The participant roster, the membership sort, and the links that reach them.

Three things are pinned here, and they are the three ways this feature can go
wrong:

* **the gate** -- the participant roster is the one screen in the app that
  answers "who is in this", and `profile_visibility_filter` is own-only
  precisely so members cannot ask that. So the page must answer a member 404
  and its fragment 403, the same split every other admin surface uses. If
  this ever relaxes, it must relax by loosening that one filter, not by
  opening this route.
* **the order** -- `sort=members` ranks by *live* enrollment rows. Ordering
  by `ChallengeStats.participant_count` would look identical until somebody
  unenrols, because that counter is monotonic and never comes back down.
* **the way in** -- an operator reaches all of this by tapping counts and
  rows, so the three panel cards and the roster row must actually carry the
  hrefs the feature is built on. A working route nothing links to is a
  feature nobody finds.
"""

from __future__ import annotations

from datetime import date

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import create_session_cookie, hash_password
from app.models.challenge import Challenge, ChallengeCategory
from app.models.enrollment import ChallengeRole, Enrollment
from app.models.user import User, UserRole

PANEL_PATH = "/views/admin/"
LIST_PATH = "/views/admin/challenges"


def members_path(challenge_id: int) -> str:
    return f"/views/admin/challenges/{challenge_id}/members"


def fragment_path(challenge_id: int) -> str:
    return f"{members_path(challenge_id)}/fragment"


async def make_user(db: AsyncSession, name: str, role: str = UserRole.MEMBER.value):
    user = User(
        name=name,
        email=f"{name.lower().replace(' ', '.')}@example.com",
        password_hash=hash_password("password123"),
        role=role,
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


def sign_in(client: AsyncClient, user: User) -> None:
    client.cookies.set("session", create_session_cookie(user.id))


async def make_challenge(
    db: AsyncSession, owner: User, *, title: str = "A challenge"
) -> Challenge:
    challenge = Challenge(
        title=title,
        owner_id=owner.id,
        category=ChallengeCategory.OTHER,
        cadence_kind="once",
        cadence={"kind": "once"},
        visibility="public",
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


async def join(db: AsyncSession, challenge: Challenge, user: User) -> Enrollment:
    enrollment = Enrollment(
        challenge_id=challenge.id,
        user_id=user.id,
        start_date=date(2026, 1, 1),
        role=ChallengeRole.PARTICIPANT.value,
    )
    db.add(enrollment)
    await db.commit()
    await db.refresh(enrollment)
    return enrollment


# --- the gate ---------------------------------------------------------------


async def test_anonymous_is_offered_a_login(client: AsyncClient, db: AsyncSession):
    """Authentication fails first, and as a 303: a signed-out visitor is sent
    to log in rather than told the page is missing."""
    owner = await make_user(db, "Anon Owner")
    challenge = await make_challenge(db, owner)

    response = await client.get(members_path(challenge.id))
    assert response.status_code == 303
    assert response.headers["location"].startswith("/views/auth/?next=")


async def test_member_gets_a_404_from_the_page(client: AsyncClient, db: AsyncSession):
    """A member has no business learning the roster exists -- and this is the
    exact list `profile_visibility_filter` is own-only in order to withhold."""
    owner = await make_user(db, "Gated Owner")
    challenge = await make_challenge(db, owner)
    sign_in(client, await make_user(db, "Plain Member"))

    assert (await client.get(members_path(challenge.id))).status_code == 404


async def test_a_participant_still_cannot_see_the_roster(
    client: AsyncClient, db: AsyncSession
):
    """Being *in* the challenge is not reach over who else is: the app shows
    participant initials on challenge-detail and never a roster."""
    owner = await make_user(db, "Roster Owner")
    joiner = await make_user(db, "Curious Joiner")
    challenge = await make_challenge(db, owner)
    await join(db, challenge, joiner)
    sign_in(client, joiner)

    assert (await client.get(members_path(challenge.id))).status_code == 404


async def test_even_the_owner_cannot_see_the_roster(
    client: AsyncClient, db: AsyncSession
):
    """Owning the challenge is authorship, not reach over other members'
    identities -- the roster is an operator tool, not an owner's."""
    owner = await make_user(db, "Only Owner")
    challenge = await make_challenge(db, owner)
    sign_in(client, owner)

    assert (await client.get(members_path(challenge.id))).status_code == 404


async def test_fragment_answers_a_member_403_not_a_redirect(
    client: AsyncClient, db: AsyncSession
):
    """The fragment takes `get_admin_user`, so a `fetch()` gets a status it
    can act on -- the 303 the page dependency raises would be followed
    silently and the login page appended as if it were rows."""
    owner = await make_user(db, "Frag Owner")
    challenge = await make_challenge(db, owner)
    sign_in(client, await make_user(db, "Frag Member"))

    assert (await client.get(fragment_path(challenge.id))).status_code == 403


async def test_fragment_answers_anonymous_401(client: AsyncClient, db: AsyncSession):
    owner = await make_user(db, "Frag Anon Owner")
    challenge = await make_challenge(db, owner)

    assert (await client.get(fragment_path(challenge.id))).status_code == 401


async def test_admin_gets_a_404_for_a_challenge_that_is_not_there(
    client: AsyncClient, db: AsyncSession
):
    """A genuine miss, not the enumeration-proofing 404 the member-facing
    routes use: an admin already holds `CHALLENGE_LIST_ALL`."""
    sign_in(client, await make_user(db, "Seeking Admin", role=UserRole.ADMIN.value))

    assert (await client.get(members_path(4242))).status_code == 404


# --- what the roster says ---------------------------------------------------


async def test_roster_lists_every_participant_with_their_challenge_role(
    client: AsyncClient, db: AsyncSession
):
    """The per-challenge role is the whole reason this is not a filtered copy
    of the member roster: the same person is the owner here and a participant
    in everyone else's challenge (app/permissions.py, two axes)."""
    owner = await make_user(db, "Listed Owner")
    joiner = await make_user(db, "Listed Joiner")
    challenge = await make_challenge(db, owner)
    await join(db, challenge, joiner)
    sign_in(client, await make_user(db, "Reading Admin", role=UserRole.ADMIN.value))

    body = (await client.get(members_path(challenge.id))).text
    assert "Listed Owner" in body
    assert "Listed Joiner" in body
    assert "مالک" in body
    assert "شرکت‌کننده" in body


async def test_roster_holds_only_this_challenges_members(
    client: AsyncClient, db: AsyncSession
):
    owner = await make_user(db, "Scoped Owner")
    inside = await make_user(db, "Inside Member")
    outside = await make_user(db, "Outside Member")
    mine = await make_challenge(db, owner, title="Mine")
    theirs = await make_challenge(db, owner, title="Theirs")
    await join(db, mine, inside)
    await join(db, theirs, outside)
    sign_in(client, await make_user(db, "Scoping Admin", role=UserRole.ADMIN.value))

    body = (await client.get(members_path(mine.id))).text
    assert "Inside Member" in body
    assert "Outside Member" not in body


async def test_roster_search_spans_name_and_email(client: AsyncClient, db: AsyncSession):
    """`apply_member_filters` is reused rather than rewritten, so "search
    means name and email" stays one rule across both rosters."""
    owner = await make_user(db, "Search Owner")
    hit = await make_user(db, "Findable Person")
    miss = await make_user(db, "Other Person")
    challenge = await make_challenge(db, owner)
    await join(db, challenge, hit)
    await join(db, challenge, miss)
    sign_in(client, await make_user(db, "Searching Admin", role=UserRole.ADMIN.value))

    by_name = (
        await client.get(members_path(challenge.id), params={"q": "Findable"})
    ).text
    assert "Findable Person" in by_name
    assert "Other Person" not in by_name

    by_email = (
        await client.get(members_path(challenge.id), params={"q": "findable.person@"})
    ).text
    assert "Findable Person" in by_email
    assert "Other Person" not in by_email


async def test_fragment_reports_whether_more_pages_remain(
    client: AsyncClient, db: AsyncSession
):
    """The scroller advances on `X-Has-More`, so it is part of the contract
    rather than a detail of the response."""
    owner = await make_user(db, "Paging Owner")
    challenge = await make_challenge(db, owner)
    for i in range(3):
        await join(db, challenge, await make_user(db, f"Pager {i}"))
    sign_in(client, await make_user(db, "Paging Admin", role=UserRole.ADMIN.value))

    first = await client.get(fragment_path(challenge.id), params={"limit": 2})
    assert first.headers["X-Has-More"] == "true"

    rest = await client.get(fragment_path(challenge.id), params={"offset": 2, "limit": 2})
    assert rest.headers["X-Has-More"] == "false"


# --- the membership sort ----------------------------------------------------


async def test_members_sort_ranks_by_live_enrollments(
    client: AsyncClient, db: AsyncSession
):
    """Most-enrolled first. Counted off `Enrollments` and not off
    `ChallengeStats.participant_count`, which is monotonic: it would rank a
    challenge everyone has left above one they are still in."""
    owner = await make_user(db, "Sorting Owner")
    quiet = await make_challenge(db, owner, title="Quiet challenge")
    busy = await make_challenge(db, owner, title="Busy challenge")
    for i in range(3):
        await join(db, busy, await make_user(db, f"Crowd {i}"))
    sign_in(client, await make_user(db, "Sorting Admin", role=UserRole.ADMIN.value))

    body = (await client.get(LIST_PATH, params={"sort": "members"})).text
    assert body.index("Busy challenge") < body.index("Quiet challenge")

    # ...and the default ordering is still the app-wide newest-first rule.
    # Here the busier challenge is also the newer one, so the two orderings
    # agree -- what is pinned is that `sort` is what moved the list, i.e.
    # that the parameter is read at all rather than silently dropped.
    assert quiet.id < busy.id
    recent = (await client.get(LIST_PATH)).text
    assert recent.index("Busy challenge") < recent.index("Quiet challenge")


async def test_members_sort_outranks_the_newest_first_default(
    client: AsyncClient, db: AsyncSession
):
    """The one case that separates the two orderings: the *older* challenge
    carries the membership, so `sort=members` must lift it above the newer
    empty one that newest-first would lead with."""
    owner = await make_user(db, "Flip Owner")
    old_busy = await make_challenge(db, owner, title="Old but busy")
    new_quiet = await make_challenge(db, owner, title="New but quiet")
    for i in range(3):
        await join(db, old_busy, await make_user(db, f"Flipper {i}"))
    sign_in(client, await make_user(db, "Flipping Admin", role=UserRole.ADMIN.value))
    assert old_busy.id < new_quiet.id

    default = (await client.get(LIST_PATH)).text
    assert default.index("New but quiet") < default.index("Old but busy")

    ranked = (await client.get(LIST_PATH, params={"sort": "members"})).text
    assert ranked.index("Old but busy") < ranked.index("New but quiet")


async def test_members_sort_survives_a_search(client: AsyncClient, db: AsyncSession):
    """Sort and search compose in the one shared query, so a filtered roster
    is still ranked -- the same `fetch_challenge_page` the public list uses."""
    owner = await make_user(db, "Combo Owner")
    small = await make_challenge(db, owner, title="Yoga small")
    big = await make_challenge(db, owner, title="Yoga big")
    await make_challenge(db, owner, title="Running elsewhere")
    for i in range(3):
        await join(db, big, await make_user(db, f"Yogi {i}"))
    await join(db, small, await make_user(db, "Lone Yogi"))
    sign_in(client, await make_user(db, "Combo Admin", role=UserRole.ADMIN.value))

    body = (await client.get(LIST_PATH, params={"sort": "members", "q": "Yoga"})).text
    assert "Running elsewhere" not in body
    assert body.index("Yoga big") < body.index("Yoga small")


async def test_an_unknown_sort_is_refused_rather_than_ignored(
    client: AsyncClient, db: AsyncSession
):
    """Pattern-validated like every other query cut on this page. «velocity»
    is deliberately not among them: "what is hot" is a discovery question,
    not a moderation one, so it stays on the public list."""
    sign_in(client, await make_user(db, "Picky Admin", role=UserRole.ADMIN.value))

    assert (await client.get(LIST_PATH, params={"sort": "velocity"})).status_code == 422


# --- the way in -------------------------------------------------------------


@pytest.mark.parametrize(
    "href",
    ["#adminMembers", LIST_PATH, f"{LIST_PATH}?sort=members"],
    ids=["members", "challenges", "enrollments"],
)
async def test_each_panel_count_links_into_the_list_it_counts(
    client: AsyncClient, db: AsyncSession, href: str
):
    """A number an operator cannot open is a number they have to go looking
    for elsewhere. The members count is an in-page anchor because the member
    roster *is* this page's list; the enrollments count is the moderation
    roster ranked by membership, deliberately not a fourth screen listing the
    same rows in a different order."""
    sign_in(client, await make_user(db, "Tapping Admin", role=UserRole.ADMIN.value))

    body = (await client.get(PANEL_PATH)).text
    assert f'href="{href}"' in body
    assert 'id="adminMembers"' in body


async def test_a_moderation_row_links_to_its_own_roster(
    client: AsyncClient, db: AsyncSession
):
    """The drill-down is on the row, next to the count an operator is already
    reading, rather than buried in the action sheet -- it is navigation, not
    a moderation action."""
    owner = await make_user(db, "Linked Owner")
    challenge = await make_challenge(db, owner)
    await join(db, challenge, await make_user(db, "Linked Joiner"))
    sign_in(client, await make_user(db, "Linking Admin", role=UserRole.ADMIN.value))

    body = (await client.get(LIST_PATH)).text
    assert f'href="{members_path(challenge.id)}"' in body
    # The count on the link is every enrollment row -- the same figure the
    # panel's enrollments card totals and the same one `sort=members` ranks
    # by, so the number shown is the number that ranked the row.
    assert "2 عضو" in body
