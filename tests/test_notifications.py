"""The notification feed: who is told, who is not, and who can read it.

Four things are pinned here, and they are the four ways this feature can go
wrong:

* **the invariant** -- `notify()` drops a notification whose recipient is its
  actor. Every emitting call site has a case where the two coincide (the
  creator's own auto-enrolment, an owner archiving their own challenge), so a
  regression here does not crash: it just fills every member's feed with
  their own taps until nobody reads the bell.
* **ownership** -- the whole table is per-member and there is no visibility
  filter to compose, so `Notification.user_id == <session user>` written into
  every query *is* the access rule. A feed that leaks is a feed that reports
  who joined whose challenge.
* **the gate** -- the page owes a signed-out visitor a 303 to login and the
  fragment owes a `fetch()` a real 401, the same split every other page and
  `/fragment` pair in the app uses.
* **the transaction** -- a notification is added to the same transaction as
  the event it describes. A refused enrolment must not announce itself.
"""

from __future__ import annotations

from datetime import date

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import create_session_cookie, hash_password
from app.models.challenge import Challenge, ChallengeCategory
from app.models.enrollment import ChallengeRole, Enrollment
from app.models.notification import Notification, NotificationKind
from app.models.user import User, UserRole
from app.notifications import NOTIFICATION_META, notification_text

PAGE_PATH = "/views/notifications/"
FRAGMENT_PATH = "/views/notifications/fragment"
API_PATH = "/notifications/"
COUNT_PATH = "/notifications/unread-count"
READ_PATH = "/notifications/read"
PREFS_PAGE_PATH = "/views/settings/notifications"


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
    db: AsyncSession,
    owner: User,
    *,
    title: str = "چالش آزمایشی",
    lifecycle: str = "active",
    visibility: str = "unlisted",
) -> Challenge:
    """Unlisted by default, because that is where membership is an *event*.

    A public challenge deliberately raises no join/leave notification (see
    `membership_is_announced`), so a fixture that defaulted to public would
    have every test here asserting the exception rather than the rule.
    Unlisted is the "reached by a link the owner handed out" case, and it is
    still joinable by a non-member -- private is not.
    """
    challenge = Challenge(
        title=title,
        owner_id=owner.id,
        category=ChallengeCategory.OTHER,
        cadence_kind="once",
        cadence={"kind": "once"},
        visibility=visibility,
        lifecycle_status=lifecycle,
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


async def notifications_for(db: AsyncSession, user: User) -> list[Notification]:
    return list(
        (
            await db.execute(
                select(Notification)
                .where(Notification.user_id == user.id)
                .order_by(Notification.id)
            )
        )
        .scalars()
        .all()
    )


# --- who gets told ----------------------------------------------------------


async def test_joining_tells_the_owner(client: AsyncClient, db: AsyncSession):
    owner = await make_user(db, "Owner One")
    joiner = await make_user(db, "Joiner One")
    challenge = await make_challenge(db, owner)

    sign_in(client, joiner)
    assert (await client.post(f"/enrollments/{challenge.id}")).status_code == 201

    rows = await notifications_for(db, owner)
    assert [r.kind for r in rows] == [NotificationKind.ENROLLMENT_JOINED.value]
    assert rows[0].actor_user_id == joiner.id
    assert rows[0].challenge_id == challenge.id
    assert rows[0].read_at is None


async def test_leaving_tells_the_owner(client: AsyncClient, db: AsyncSession):
    """Growth and loss are both reported -- a feed that only ever announces
    new members is a feed that flatters rather than informs."""
    owner = await make_user(db, "Owner Two")
    joiner = await make_user(db, "Joiner Two")
    challenge = await make_challenge(db, owner)

    sign_in(client, joiner)
    await client.post(f"/enrollments/{challenge.id}")
    assert (await client.delete(f"/enrollments/{challenge.id}")).status_code == 204

    rows = await notifications_for(db, owner)
    assert [r.kind for r in rows] == [
        NotificationKind.ENROLLMENT_JOINED.value,
        NotificationKind.ENROLLMENT_LEFT.value,
    ]


async def test_a_public_challenge_announces_no_comings_and_goings(
    client: AsyncClient, db: AsyncSession
):
    """A public challenge is a noticeboard, not an invitation.

    Anyone who finds it may join, and its owner invited none of them, so an
    arrival is a number on the roster rather than an event -- and a feed of
    them buries the notifications that *are* about the owner's own doing.
    Both halves are silenced by the one rule, so an owner cannot end up with
    a feed that only ever announces departures.
    """
    owner = await make_user(db, "Owner Public")
    joiner = await make_user(db, "Joiner Public")
    challenge = await make_challenge(db, owner, visibility="public")

    sign_in(client, joiner)
    assert (await client.post(f"/enrollments/{challenge.id}")).status_code == 201
    assert (await client.delete(f"/enrollments/{challenge.id}")).status_code == 204

    assert await notifications_for(db, owner) == []


async def test_a_public_challenge_still_broadcasts_its_lifecycle(
    client: AsyncClient, db: AsyncSession
):
    """The membership rule is about membership only. An archived challenge
    stops producing occurrences whoever can see it, so the broadcast every
    participant depends on is untouched by how the challenge is listed."""
    owner = await make_user(db, "Owner Public Two")
    member = await make_user(db, "Member Public Two")
    challenge = await make_challenge(db, owner, visibility="public")

    sign_in(client, member)
    await client.post(f"/enrollments/{challenge.id}")

    sign_in(client, owner)
    res = await client.patch(
        f"/challenges/{challenge.id}", json={"lifecycle_status": "archived"}
    )
    assert res.status_code == 200

    kinds = [r.kind for r in await notifications_for(db, member)]
    assert kinds == [NotificationKind.CHALLENGE_ARCHIVED.value]


async def test_nobody_is_told_about_their_own_action(
    client: AsyncClient, db: AsyncSession
):
    """The invariant `notify()` exists to hold. The owner enrolling in their
    own challenge is the case every emitting site actually hits."""
    owner = await make_user(db, "Owner Three")
    challenge = await make_challenge(db, owner)

    # The owner's D1 auto-enrolment is written directly by the create route,
    # so unenrol and re-enrol through the API to exercise both call sites.
    sign_in(client, owner)
    assert (await client.delete(f"/enrollments/{challenge.id}")).status_code == 204
    assert (await client.post(f"/enrollments/{challenge.id}")).status_code == 201

    assert await notifications_for(db, owner) == []


async def test_a_refused_enrolment_announces_nothing(
    client: AsyncClient, db: AsyncSession
):
    """The notification rides the event's own transaction. A second enrolment
    is a 409, and a 409 must leave the owner's feed exactly as it was."""
    owner = await make_user(db, "Owner Four")
    joiner = await make_user(db, "Joiner Four")
    challenge = await make_challenge(db, owner)

    sign_in(client, joiner)
    await client.post(f"/enrollments/{challenge.id}")
    assert (await client.post(f"/enrollments/{challenge.id}")).status_code == 409

    assert len(await notifications_for(db, owner)) == 1


# --- the lifecycle broadcast ------------------------------------------------


async def test_archiving_tells_every_participant_but_the_actor(
    client: AsyncClient, db: AsyncSession
):
    """The one broadcast in the app, and the only challenge edit that changes
    what the app expects of *other* people: an archived challenge stops
    producing occurrences, so a member who is not told just watches their
    streak stop."""
    owner = await make_user(db, "Owner Five")
    a = await make_user(db, "Member Five A")
    b = await make_user(db, "Member Five B")
    challenge = await make_challenge(db, owner)

    for member in (a, b):
        sign_in(client, member)
        await client.post(f"/enrollments/{challenge.id}")

    sign_in(client, owner)
    res = await client.patch(
        f"/challenges/{challenge.id}", json={"lifecycle_status": "archived"}
    )
    assert res.status_code == 200

    for member in (a, b):
        kinds = [r.kind for r in await notifications_for(db, member)]
        assert kinds == [NotificationKind.CHALLENGE_ARCHIVED.value]
    # The owner acted, so the owner is not told -- their feed still holds only
    # the two joins.
    owner_kinds = [r.kind for r in await notifications_for(db, owner)]
    assert owner_kinds == [NotificationKind.ENROLLMENT_JOINED.value] * 2


async def test_a_patch_that_changes_no_state_broadcasts_nothing(
    client: AsyncClient, db: AsyncSession
):
    """Keyed on a *transition*. Writing the state it already had is not news,
    and a PATCH that touches other fields is not news either."""
    owner = await make_user(db, "Owner Six")
    member = await make_user(db, "Member Six")
    challenge = await make_challenge(db, owner)

    sign_in(client, member)
    await client.post(f"/enrollments/{challenge.id}")

    sign_in(client, owner)
    assert (
        await client.patch(
            f"/challenges/{challenge.id}", json={"lifecycle_status": "active"}
        )
    ).status_code == 200
    assert (
        await client.patch(f"/challenges/{challenge.id}", json={"title": "نام تازه"})
    ).status_code == 200

    assert await notifications_for(db, member) == []


async def test_a_moderator_archiving_tells_the_owner(
    client: AsyncClient, db: AsyncSession
):
    """Moderation is the case the broadcast matters most for: the owner did
    not do this and has no other way to find out."""
    owner = await make_user(db, "Owner Seven")
    admin = await make_user(db, "Admin Seven", role=UserRole.ADMIN.value)
    challenge = await make_challenge(db, owner)

    sign_in(client, admin)
    res = await client.patch(
        f"/challenges/{challenge.id}", json={"lifecycle_status": "archived"}
    )
    assert res.status_code == 200

    kinds = [r.kind for r in await notifications_for(db, owner)]
    assert kinds == [NotificationKind.CHALLENGE_ARCHIVED.value]


# --- ownership --------------------------------------------------------------


async def test_the_feed_is_own_only(client: AsyncClient, db: AsyncSession):
    owner = await make_user(db, "Owner Eight")
    joiner = await make_user(db, "Joiner Eight")
    challenge = await make_challenge(db, owner)

    sign_in(client, joiner)
    await client.post(f"/enrollments/{challenge.id}")

    # The joiner caused the owner's only notification and still sees nothing.
    assert (await client.get(API_PATH)).json() == []
    assert (await client.get(COUNT_PATH)).json() == {"unread": 0}

    sign_in(client, owner)
    body = (await client.get(API_PATH)).json()
    assert len(body) == 1
    assert body[0]["kind"] == NotificationKind.ENROLLMENT_JOINED.value
    assert body[0]["actor_name"] == joiner.name
    assert body[0]["challenge_title"] == challenge.title


async def test_marking_read_touches_only_your_own(
    client: AsyncClient, db: AsyncSession
):
    owner_a = await make_user(db, "Owner Nine A")
    owner_b = await make_user(db, "Owner Nine B")
    joiner = await make_user(db, "Joiner Nine")
    challenge_a = await make_challenge(db, owner_a, title="چالش الف")
    challenge_b = await make_challenge(db, owner_b, title="چالش ب")

    sign_in(client, joiner)
    await client.post(f"/enrollments/{challenge_a.id}")
    await client.post(f"/enrollments/{challenge_b.id}")

    sign_in(client, owner_a)
    assert (await client.post(READ_PATH)).json() == {"updated": 1}
    assert (await client.get(COUNT_PATH)).json() == {"unread": 0}
    # Re-reading changes nothing: `read_at` records when it was *first* seen.
    assert (await client.post(READ_PATH)).json() == {"updated": 0}

    sign_in(client, owner_b)
    assert (await client.get(COUNT_PATH)).json() == {"unread": 1}


async def test_the_feed_is_newest_first(client: AsyncClient, db: AsyncSession):
    """The one rule every paginated list in the app shares. A broadcast writes
    a whole block of rows inside one `created_at` second, so the id tie-break
    in `newest_first` is what keeps this deterministic at all."""
    owner = await make_user(db, "Owner Ten")
    first = await make_user(db, "Joiner Ten A")
    second = await make_user(db, "Joiner Ten B")
    challenge = await make_challenge(db, owner)

    for member in (first, second):
        sign_in(client, member)
        await client.post(f"/enrollments/{challenge.id}")

    sign_in(client, owner)
    body = (await client.get(API_PATH)).json()
    assert [row["actor_name"] for row in body] == [second.name, first.name]


# --- the gate ---------------------------------------------------------------


async def test_the_page_offers_an_anonymous_visitor_a_login(client: AsyncClient):
    res = await client.get(PAGE_PATH, follow_redirects=False)
    assert res.status_code == 303
    assert "/views/auth/" in res.headers["location"]


async def test_the_fragment_answers_a_signed_out_fetch_with_401(client: AsyncClient):
    """A real 401, not the page's 303: `createInfiniteScroller()` reads that
    status to redirect itself, and a `fetch()` would follow a 303 silently and
    append the login page's markup as if it were notification rows."""
    res = await client.get(FRAGMENT_PATH, follow_redirects=False)
    assert res.status_code == 401


@pytest.mark.parametrize("path", [API_PATH, COUNT_PATH, READ_PATH])
async def test_the_json_api_is_closed_to_anonymous(client: AsyncClient, path: str):
    method = client.post if path == READ_PATH else client.get
    assert (await method(path)).status_code == 401


# --- rendering --------------------------------------------------------------


async def test_the_page_renders_the_sentence_and_the_unread_mark(
    client: AsyncClient, db: AsyncSession
):
    owner = await make_user(db, "Owner Eleven")
    joiner = await make_user(db, "Joiner Eleven")
    challenge = await make_challenge(db, owner, title="دویدن هر روز")

    sign_in(client, joiner)
    await client.post(f"/enrollments/{challenge.id}")

    sign_in(client, owner)
    html = (await client.get(PAGE_PATH)).text
    assert joiner.name in html
    assert challenge.title in html
    # Unread is spelled out, not only tinted.
    assert "جدید" in html
    assert "is-unread" in html
    # The row is a link to the challenge it is about.
    assert f'href="/views/challenges/{challenge.id}"' in html


async def test_every_kind_has_words_and_a_glyph():
    """The map in `app/notifications.py` is the only place a stored event
    becomes Farsi, so a kind added to the enum without an entry there would
    render as the fallback and nobody would notice until a member did."""
    assert set(NOTIFICATION_META) == {k.value for k in NotificationKind}
    for meta in NOTIFICATION_META.values():
        assert meta["title"] and meta["text"] and meta["icon"]


async def test_a_sentence_degrades_rather_than_failing():
    """`actor_user_id` and `challenge_id` are both nullable, and a feed that
    raises on one odd row shows the member nothing at all."""
    orphan = Notification(kind=NotificationKind.ENROLLMENT_JOINED.value)
    orphan.actor = None
    orphan.challenge = None
    text = notification_text(orphan)
    assert text and "{" not in text


async def test_an_unknown_kind_still_renders():
    stray = Notification(kind="something_new")
    stray.actor = None
    stray.challenge = None
    assert notification_text(stray)


# --- the bell ---------------------------------------------------------------


async def test_the_shell_renders_a_bell_only_for_a_member(
    client: AsyncClient, db: AsyncSession
):
    """A notification bell for someone with no account is a control that can
    only disappoint -- challenge discovery is open to anonymous readers."""
    anon = (await client.get("/views/challenges/")).text
    assert "data-notif-bell" not in anon

    member = await make_user(db, "Beller One")
    sign_in(client, member)
    page = (await client.get("/views/challenges/")).text
    assert 'href="/views/notifications/"' in page
    assert "data-notif-badge" in page


async def test_the_feed_page_carries_no_bell_of_its_own(
    client: AsyncClient, db: AsyncSession
):
    member = await make_user(db, "Beller Two")
    sign_in(client, member)
    assert "data-notif-bell" not in (await client.get(PAGE_PATH)).text


# --- the cascade ------------------------------------------------------------


async def test_deleting_a_challenge_takes_its_notifications_with_it(
    client: AsyncClient, db: AsyncSession
):
    """A notification about a challenge that no longer exists is a sentence
    about nothing that opens a 404."""
    owner = await make_user(db, "Owner Twelve")
    joiner = await make_user(db, "Joiner Twelve")
    challenge = await make_challenge(db, owner)

    sign_in(client, joiner)
    await client.post(f"/enrollments/{challenge.id}")
    # Deletion is refused while others are enrolled, so leave first.
    await client.delete(f"/enrollments/{challenge.id}")
    assert len(await notifications_for(db, owner)) == 2

    sign_in(client, owner)
    assert (await client.delete(f"/challenges/{challenge.id}")).status_code == 204

    assert await notifications_for(db, owner) == []


# --- the list mechanics -----------------------------------------------------


async def test_an_empty_feed_says_so(client: AsyncClient, db: AsyncSession):
    """The good empty case, not a failure: a member with nothing yet is told
    which things would show up here."""
    member = await make_user(db, "Quiet One")
    sign_in(client, member)
    html = (await client.get(PAGE_PATH)).text
    assert "هنوز خبری نیست" in html
    # Rendered visible, not merely present -- `hidden` is what the scroller
    # toggles, and a permanently hidden empty state is a blank screen.
    assert 'id="notifEmpty" >' in html or 'id="notifEmpty">' in html


async def test_the_fragment_pages_and_reports_more(
    client: AsyncClient, db: AsyncSession
):
    """`X-Has-More` is the header `createInfiniteScroller()` reads to decide
    whether to keep going, and the offset it advances counts the fragment's
    own top-level children -- so the fragment must return rows and nothing
    else."""
    owner = await make_user(db, "Owner Paged")
    challenge = await make_challenge(db, owner)
    joiners = [await make_user(db, f"Joiner P{i}") for i in range(22)]
    for joiner in joiners:
        sign_in(client, joiner)
        await client.post(f"/enrollments/{challenge.id}")

    sign_in(client, owner)
    page = await client.get(PAGE_PATH)
    assert page.text.count("notif-row") == 20
    assert 'data-has-more="true"' in page.text

    rest = await client.get(f"{FRAGMENT_PATH}?offset=20&limit=20")
    assert rest.status_code == 200
    assert rest.headers["X-Has-More"] == "false"
    assert rest.text.count("notif-row") == 2
    # Bare rows: no wrapper, no empty state, no counter.
    assert "notif-list" not in rest.text


# --- preferences: which kinds reach me --------------------------------------
#
# The switch is enforced in `notify()` -- the one door in -- rather than at
# each emitting call site, so what these pin is that *events stop arriving*,
# not that a column was written. A preference honoured only by the call sites
# that remembered it is a preference that silently stops working the first
# time a new kind is added.

PREFS_PATH = "/notifications/prefs"


async def test_prefs_answer_every_kind_on_by_default(
    client: AsyncClient, db: AsyncSession
):
    """The full set, always: a client draws one switch per kind, and a
    response listing only the mutes would leave it to reconstruct the rest."""
    member = await make_user(db, "Prefs Default")
    sign_in(client, member)

    res = await client.get(PREFS_PATH)
    assert res.status_code == 200
    assert {row["kind"] for row in res.json()} == {k.value for k in NotificationKind}
    assert all(row["enabled"] for row in res.json())


async def test_muting_a_kind_stops_it_arriving(client: AsyncClient, db: AsyncSession):
    owner = await make_user(db, "Prefs Owner")
    joiner = await make_user(db, "Prefs Joiner")
    challenge = await make_challenge(db, owner)

    sign_in(client, owner)
    res = await client.put(
        PREFS_PATH,
        json={"kind": NotificationKind.ENROLLMENT_JOINED.value, "enabled": False},
    )
    assert res.status_code == 200
    assert {row["kind"]: row["enabled"] for row in res.json()}[
        NotificationKind.ENROLLMENT_JOINED.value
    ] is False

    sign_in(client, joiner)
    assert (await client.post(f"/enrollments/{challenge.id}")).status_code == 201
    assert await notifications_for(db, owner) == []

    # The other kinds are untouched -- a mute is one switch, not a mode.
    assert (await client.delete(f"/enrollments/{challenge.id}")).status_code == 204
    rows = await notifications_for(db, owner)
    assert [r.kind for r in rows] == [NotificationKind.ENROLLMENT_LEFT.value]


async def test_a_mute_is_per_member_in_a_broadcast(
    client: AsyncClient, db: AsyncSession
):
    """The lifecycle broadcast reads every recipient's own switch, not one
    answer for the whole group."""
    owner = await make_user(db, "Broadcast Owner")
    quiet = await make_user(db, "Broadcast Quiet")
    loud = await make_user(db, "Broadcast Loud")
    challenge = await make_challenge(db, owner)

    for member in (quiet, loud):
        sign_in(client, member)
        assert (await client.post(f"/enrollments/{challenge.id}")).status_code == 201

    sign_in(client, quiet)
    await client.put(
        PREFS_PATH,
        json={"kind": NotificationKind.CHALLENGE_ARCHIVED.value, "enabled": False},
    )

    sign_in(client, owner)
    res = await client.patch(
        f"/challenges/{challenge.id}", json={"lifecycle_status": "archived"}
    )
    assert res.status_code == 200

    archived = NotificationKind.CHALLENGE_ARCHIVED.value
    assert archived not in [r.kind for r in await notifications_for(db, quiet)]
    assert archived in [r.kind for r in await notifications_for(db, loud)]


async def test_unmuting_lets_the_kind_through_again(
    client: AsyncClient, db: AsyncSession
):
    owner = await make_user(db, "Prefs Toggle Owner")
    joiner = await make_user(db, "Prefs Toggle Joiner")
    challenge = await make_challenge(db, owner)
    kind = NotificationKind.ENROLLMENT_JOINED.value

    sign_in(client, owner)
    await client.put(PREFS_PATH, json={"kind": kind, "enabled": False})
    await client.put(PREFS_PATH, json={"kind": kind, "enabled": True})

    sign_in(client, joiner)
    await client.post(f"/enrollments/{challenge.id}")
    assert [r.kind for r in await notifications_for(db, owner)] == [kind]


async def test_prefs_are_own_only_and_closed_to_anonymous(
    client: AsyncClient, db: AsyncSession
):
    """The recipient is the session user, never a body field -- so there is
    no shape of request that writes someone else's switches."""
    client.cookies.clear()
    assert (await client.get(PREFS_PATH)).status_code == 401
    assert (
        await client.put(
            PREFS_PATH,
            json={"kind": NotificationKind.ENROLLMENT_JOINED.value, "enabled": False},
        )
    ).status_code == 401

    other = await make_user(db, "Prefs Bystander")
    mine = await make_user(db, "Prefs Mine")
    sign_in(client, mine)
    await client.put(
        PREFS_PATH,
        json={"kind": NotificationKind.ENROLLMENT_JOINED.value, "enabled": False},
    )
    await db.refresh(other)
    assert not (other.muted_notification_kinds or [])


async def test_an_unknown_kind_is_refused_at_the_boundary(
    client: AsyncClient, db: AsyncSession
):
    """A mute nothing will ever read is worse than a 422."""
    member = await make_user(db, "Prefs Unknown")
    sign_in(client, member)
    res = await client.put(PREFS_PATH, json={"kind": "not_a_kind", "enabled": False})
    assert res.status_code == 422


async def test_every_kind_has_a_switch_with_words(client: AsyncClient, db: AsyncSession):
    """The settings screen builds its rows from `NOTIFICATION_META`, so a kind
    cannot ship without a switch -- and the switch is worded by the same map
    that words the notification itself."""
    for meta in NOTIFICATION_META.values():
        assert meta["hint"]

    member = await make_user(db, "Prefs Screen")
    sign_in(client, member)
    html = (await client.get(PREFS_PAGE_PATH)).text
    for kind in NotificationKind:
        assert f'data-notif-pref="{kind.value}"' in html
    assert NOTIFICATION_META[NotificationKind.ENROLLMENT_JOINED.value]["hint"] in html


async def test_the_switches_are_a_page_the_index_points_at(
    client: AsyncClient, db: AsyncSession
):
    """The index is a list of questions, one row each. Notifications are the
    one whose row count grows with `NOTIFICATION_META`, so they live on a
    page of their own and the index keeps a pointer plus the current answer
    -- a row that holds neither is a row that says nothing."""
    member = await make_user(db, "Prefs Index")
    sign_in(client, member)
    index = (await client.get("/views/settings/")).text

    assert f'href="{PREFS_PAGE_PATH}"' in index
    # The switches themselves are not here -- that is the whole move.
    assert "data-notif-pref=" not in index
    # ... but the answer is, without opening anything: nothing is muted yet.
    assert f"{len(NOTIFICATION_META)} از {len(NOTIFICATION_META)} روشن" in index


async def test_the_switch_page_needs_a_session(client: AsyncClient):
    """A page, so authentication fails as a redirect -- the 303/401 split.

    `/views/settings/` is in `test_page_authorization.py`'s list; its
    sub-page is pinned here, next to the switches it carries.
    """
    response = await client.get(PREFS_PAGE_PATH)
    assert response.status_code == 303
    assert "/views/auth/" in response.headers["location"]
