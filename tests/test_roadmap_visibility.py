"""Who can reach a roadmap, and what a roadmap can reach on their behalf.

Two directions, and they are the two halves of the design's one risky idea:

- :func:`app.roadmaps.roadmap_visibility_filter` decides which roadmaps exist
  for a caller. A miss is a **404** through both front doors, like every other
  visibility filter in this app.
- :func:`app.roadmaps.roadmap_scope_filter` is one more leg on the *challenge*
  rule, so walking a course opens the challenges it is made of. **Merely
  looking at a public roadmap opens nothing**, which is what keeps a private
  challenge private after somebody drops it into a published course.
"""

from __future__ import annotations

from app.roadmaps import join_roadmap
from tests.roadmap_helpers import (
    add_step,
    make_challenge,
    make_roadmap,
    make_user,
    sign_in,
)


async def _walk(db, roadmap, user):
    await join_roadmap(db, roadmap=roadmap, user_id=user.id, timezone="UTC")
    await db.commit()


# ---------------------------------------------------------------------------
# A private challenge does not leak through a public roadmap
# ---------------------------------------------------------------------------


async def test_a_private_challenge_in_a_public_roadmap_stays_private(client, db):
    """The central rule: publishing a *course* does not publish its parts.

    The onlooker sees the course, sees that it has two steps, and cannot read
    the private one -- not its title through the API, not its page.
    """
    author = await make_user(db, "Author")
    onlooker = await make_user(db, "Onlooker")
    roadmap = await make_roadmap(db, author, visibility="public")
    secret = await make_challenge(db, author, title="راز بزرگ", visibility="private")
    await add_step(db, roadmap, secret)

    sign_in(client, onlooker)
    assert (await client.get(f"/roadmaps/{roadmap.id}")).status_code == 200

    steps = (await client.get(f"/roadmaps/{roadmap.id}/steps")).json()
    assert len(steps) == 1
    # The step keeps its place and loses its name.
    assert steps[0]["challenge_title"] is None
    assert (await client.get(f"/challenges/{secret.id}")).status_code == 404
    assert (await client.get(f"/views/challenges/{secret.id}")).status_code == 404


async def test_walking_the_course_is_what_opens_its_challenges(client, db):
    """The other half: an enrollment in the *roadmap* is the key, and it is
    the same kind of key `group_scope_filter`'s third leg is."""
    author = await make_user(db, "Author")
    walker = await make_user(db, "Walker")
    roadmap = await make_roadmap(db, author, visibility="public")
    first = await make_challenge(db, author, title="قدم یک", visibility="private")
    second = await make_challenge(db, author, title="قدم دو", visibility="private")
    await add_step(db, roadmap, first)
    await add_step(db, roadmap, second)

    sign_in(client, walker)
    assert (await client.get(f"/challenges/{second.id}")).status_code == 404

    await _walk(db, roadmap, walker)

    steps = (await client.get(f"/roadmaps/{roadmap.id}/steps")).json()
    assert [s["challenge_title"] for s in steps] == ["قدم یک", "قدم دو"]
    # Including the one that has not opened yet: reading what is ahead is the
    # whole point of the screen, and it is a different question from being
    # enrolled in it.
    assert (await client.get(f"/challenges/{second.id}")).status_code == 200


async def test_a_roadmap_step_never_reaches_the_public_listing(client, db):
    """`roadmap_scope_filter` is deliberately off `listing_visibility_filter`.

    A private challenge two steps ahead is reachable at its own URL and has no
    business in the app-wide list of things to discover -- which is exactly
    what `unlisted` already means here.
    """
    author = await make_user(db, "Author")
    walker = await make_user(db, "Walker")
    roadmap = await make_roadmap(db, author)
    opener = await make_challenge(db, author, title="قدم اول", visibility="private")
    ahead = await make_challenge(db, author, title="راز آینده", visibility="private")
    await add_step(db, roadmap, opener)
    await add_step(db, roadmap, ahead)
    await _walk(db, roadmap, walker)

    sign_in(client, walker)
    listed = {c["title"] for c in (await client.get("/challenges/")).json()}
    # The step still ahead of them is readable at its own URL and absent from
    # discovery. The one they have actually reached is in the list because
    # they are *enrolled* in it -- which is the rule that was always there.
    assert "راز آینده" not in listed
    assert (await client.get(f"/challenges/{ahead.id}")).status_code == 200
    assert "قدم اول" in listed


# ---------------------------------------------------------------------------
# Reaching the roadmap itself: 404 through both doors
# ---------------------------------------------------------------------------


async def test_a_private_roadmap_is_404_on_both_front_doors(client, db):
    author = await make_user(db, "Author")
    stranger = await make_user(db, "Stranger")
    roadmap = await make_roadmap(db, author, visibility="private")

    sign_in(client, stranger)
    assert (await client.get(f"/roadmaps/{roadmap.id}")).status_code == 404
    assert (await client.get(f"/views/roadmaps/{roadmap.id}")).status_code == 404
    # And every write is narrowed through the same clause, so a PATCH cannot
    # be an oracle for what a GET hides.
    assert (
        await client.patch(f"/roadmaps/{roadmap.id}", json={"title": "دزدیده"})
    ).status_code == 404
    assert (await client.delete(f"/roadmaps/{roadmap.id}")).status_code == 404

    sign_in(client, author)
    assert (await client.get(f"/roadmaps/{roadmap.id}")).status_code == 200


async def test_an_unlisted_roadmap_is_reachable_but_not_listed(client, db):
    """What an invite link hands out: the URL works, the listing does not name
    it. The same two-part answer `Visibility.UNLISTED` gives a challenge."""
    author = await make_user(db, "Author")
    stranger = await make_user(db, "Stranger")
    roadmap = await make_roadmap(db, author, title="پنهان", visibility="unlisted")

    sign_in(client, stranger)
    assert (await client.get(f"/roadmaps/{roadmap.id}")).status_code == 200
    listed = {r["title"] for r in (await client.get("/roadmaps/")).json()}
    assert "پنهان" not in listed


async def test_a_visible_roadmap_you_do_not_own_refuses_with_403(client, db):
    """Once you can see it, the roadmap is not a secret from you, so refusing
    an action you may not take is honest rather than evasive."""
    author = await make_user(db, "Author")
    stranger = await make_user(db, "Stranger")
    roadmap = await make_roadmap(db, author, visibility="public")
    challenge = await make_challenge(db, author)

    sign_in(client, stranger)
    assert (
        await client.patch(f"/roadmaps/{roadmap.id}", json={"title": "نام دیگر"})
    ).status_code == 403
    assert (
        await client.post(
            f"/roadmaps/{roadmap.id}/steps",
            json={
                "challenge_id": challenge.id,
                "completion_rule": {"kind": "manual"},
            },
        )
    ).status_code == 403
    assert (await client.delete(f"/roadmaps/{roadmap.id}")).status_code == 403
    assert (await client.get(f"/roadmaps/{roadmap.id}/invites")).status_code == 403


async def test_the_management_page_is_404_to_everybody_but_the_builder(client, db):
    """A page whose whole content is somebody else's controls has no business
    confirming to a reader that it exists -- the call `group_manage_page` and
    `get_admin_page_user` both make."""
    author = await make_user(db, "Author")
    stranger = await make_user(db, "Stranger")
    roadmap = await make_roadmap(db, author, visibility="public")

    sign_in(client, stranger)
    assert (
        await client.get(f"/views/roadmaps/{roadmap.id}/manage")
    ).status_code == 404
    sign_in(client, author)
    assert (
        await client.get(f"/views/roadmaps/{roadmap.id}/manage")
    ).status_code == 200


async def test_a_step_may_only_name_a_challenge_the_builder_can_see(client, db):
    """404, not 403: otherwise adding a step is a way to probe for private
    challenges by id."""
    author = await make_user(db, "Author")
    somebody_else = await make_user(db, "Other")
    roadmap = await make_roadmap(db, author)
    hidden = await make_challenge(db, somebody_else, visibility="private")

    sign_in(client, author)
    assert (
        await client.post(
            f"/roadmaps/{roadmap.id}/steps",
            json={"challenge_id": hidden.id, "completion_rule": {"kind": "manual"}},
        )
    ).status_code == 404


async def test_a_signed_out_visitor_is_sent_to_the_login_page(client, db):
    """The page dependency raises `LoginRequired` and `app.main` turns it into
    a 303 with `?next=` -- the 303/401 split, so a deep link survives."""
    author = await make_user(db, "Author")
    roadmap = await make_roadmap(db, author, visibility="public")
    client.cookies.clear()
    page = await client.get(f"/views/roadmaps/{roadmap.id}")
    assert page.status_code == 303
    assert "/views/auth/" in page.headers["location"]
