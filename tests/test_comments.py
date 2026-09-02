"""Comments: the rules a polymorphic, threaded table can silently lose.

* **the subject gate** — the table has no FK to its subject, so the visibility
  rule is the code's job (`SUBJECT_RESOLVERS`), and the answer is 404: a
  comment must not become a way to learn a private challenge exists.
* **one level deep** — a reply to a reply is stored against the *root*, so no
  read ever has to recurse and no page has to flatten.
* **the acting user is the session**, and somebody else's comment is a 404 on
  delete, not a 403.
* **content** — text, a sticker, or both; never neither, and never a sticker
  id outside the catalogue.
* **the count is live** — nothing stores it, so deleting takes it back down,
  and deleting a root takes its replies with it.
"""

from __future__ import annotations

from datetime import date

from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import create_session_cookie, hash_password
from app.models.challenge import Challenge, ChallengeCategory
from app.models.comment import Comment
from app.models.enrollment import ChallengeRole, Enrollment
from app.models.notification import Notification, NotificationKind
from app.models.user import User


def comments_path(subject_id: int, subject_type: str = "challenge") -> str:
    return f"/comments/{subject_type}/{subject_id}"


async def make_user(db: AsyncSession, name: str) -> User:
    user = User(
        name=name,
        email=f"{name}@example.com",
        password_hash=hash_password("password123"),
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


def sign_in(client: AsyncClient, user: User) -> None:
    client.cookies.set("session", create_session_cookie(user.id))


async def make_challenge(
    db: AsyncSession, owner: User, *, visibility: str = "public"
) -> Challenge:
    challenge = Challenge(
        title="چالش تست",
        owner_id=owner.id,
        category=ChallengeCategory.OTHER,
        cadence_kind="once",
        cadence={"kind": "once"},
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


async def test_text_and_sticker_are_both_ways_to_say_something(
    client: AsyncClient, db: AsyncSession
) -> None:
    owner = await make_user(db, "owner")
    challenge = await make_challenge(db, owner)
    sign_in(client, owner)

    res = await client.post(comments_path(challenge.id), json={"body": "  سلام  "})
    assert res.status_code == 201
    assert res.json()["body"] == "سلام"

    res = await client.post(comments_path(challenge.id), json={"sticker": "fire"})
    assert res.status_code == 201
    assert res.json() == {**res.json(), "body": None, "sticker": "fire"}


async def test_empty_and_unknown_sticker_are_refused(
    client: AsyncClient, db: AsyncSession
) -> None:
    owner = await make_user(db, "owner")
    challenge = await make_challenge(db, owner)
    sign_in(client, owner)

    assert (await client.post(comments_path(challenge.id), json={})).status_code == 422
    assert (
        await client.post(comments_path(challenge.id), json={"body": "   "})
    ).status_code == 422
    assert (
        await client.post(comments_path(challenge.id), json={"sticker": "../secret"})
    ).status_code == 422


async def test_reply_to_a_reply_is_stored_against_the_root(
    client: AsyncClient, db: AsyncSession
) -> None:
    """One level, whatever the client sends -- otherwise every read recurses."""
    owner = await make_user(db, "owner")
    challenge = await make_challenge(db, owner)
    sign_in(client, owner)

    root = (
        await client.post(comments_path(challenge.id), json={"body": "ریشه"})
    ).json()
    reply = (
        await client.post(
            comments_path(challenge.id), json={"body": "پاسخ", "parent_id": root["id"]}
        )
    ).json()
    deep = (
        await client.post(
            comments_path(challenge.id),
            json={"body": "پاسخِ پاسخ", "parent_id": reply["id"]},
        )
    ).json()

    assert reply["parent_id"] == root["id"]
    assert deep["parent_id"] == root["id"]


async def test_parent_from_another_challenge_is_404(
    client: AsyncClient, db: AsyncSession
) -> None:
    owner = await make_user(db, "owner")
    one = await make_challenge(db, owner)
    two = await make_challenge(db, owner)
    sign_in(client, owner)

    root = (await client.post(comments_path(one.id), json={"body": "ریشه"})).json()
    res = await client.post(
        comments_path(two.id), json={"body": "جابه‌جا", "parent_id": root["id"]}
    )
    assert res.status_code == 404


async def test_invisible_challenge_is_404_not_403(
    client: AsyncClient, db: AsyncSession
) -> None:
    owner = await make_user(db, "owner")
    stranger = await make_user(db, "stranger")
    challenge = await make_challenge(db, owner, visibility="private")
    sign_in(client, stranger)

    res = await client.post(comments_path(challenge.id), json={"body": "سلام"})
    assert res.status_code == 404
    res = await client.get(f"/views/challenges/{challenge.id}/comments/fragment")
    assert res.status_code == 404


async def test_signed_out_may_read_but_not_write(
    client: AsyncClient, db: AsyncSession
) -> None:
    """Discovery is open, so the conversation under a public challenge is
    readable signed out; saying something needs an account."""
    owner = await make_user(db, "owner")
    challenge = await make_challenge(db, owner)
    sign_in(client, owner)
    await client.post(comments_path(challenge.id), json={"body": "سلام"})

    client.cookies.clear()
    assert (
        await client.get(f"/views/challenges/{challenge.id}/comments/fragment")
    ).status_code == 200
    assert (
        await client.post(comments_path(challenge.id), json={"body": "سلام"})
    ).status_code == 401


async def test_deleting_someone_elses_comment_is_404(
    client: AsyncClient, db: AsyncSession
) -> None:
    owner = await make_user(db, "owner")
    other = await make_user(db, "other")
    challenge = await make_challenge(db, owner)

    sign_in(client, owner)
    comment = (
        await client.post(comments_path(challenge.id), json={"body": "مال من"})
    ).json()

    sign_in(client, other)
    assert (await client.delete(f"/comments/{comment['id']}")).status_code == 404
    assert (await db.execute(select(func.count(Comment.id)))).scalar_one() == 1


async def test_deleting_a_root_takes_its_replies(
    client: AsyncClient, db: AsyncSession
) -> None:
    owner = await make_user(db, "owner")
    challenge = await make_challenge(db, owner)
    sign_in(client, owner)

    root = (await client.post(comments_path(challenge.id), json={"body": "ریشه"})).json()
    await client.post(
        comments_path(challenge.id), json={"body": "پاسخ", "parent_id": root["id"]}
    )
    assert (await db.execute(select(func.count(Comment.id)))).scalar_one() == 2

    assert (await client.delete(f"/comments/{root['id']}")).status_code == 204
    assert (await db.execute(select(func.count(Comment.id)))).scalar_one() == 0


async def test_the_owner_hears_about_a_thread_and_the_author_about_a_reply(
    client: AsyncClient, db: AsyncSession
) -> None:
    """Two kinds, two recipients -- and never about your own comment."""
    owner = await make_user(db, "owner")
    talker = await make_user(db, "talker")
    challenge = await make_challenge(db, owner)

    sign_in(client, talker)
    root = (await client.post(comments_path(challenge.id), json={"body": "سلام"})).json()

    sign_in(client, owner)
    await client.post(
        comments_path(challenge.id), json={"body": "ممنون", "parent_id": root["id"]}
    )

    rows = (await db.execute(select(Notification))).scalars().all()
    got = {(n.user_id, n.kind) for n in rows}
    assert got == {
        (owner.id, NotificationKind.CHALLENGE_COMMENTED.value),
        (talker.id, NotificationKind.COMMENT_REPLIED.value),
    }


async def test_the_conversation_lists_newest_thread_first(
    client: AsyncClient, db: AsyncSession
) -> None:
    owner = await make_user(db, "owner")
    challenge = await make_challenge(db, owner)
    sign_in(client, owner)

    await client.post(comments_path(challenge.id), json={"body": "اولی"})
    await client.post(comments_path(challenge.id), json={"body": "دومی"})

    body = (await client.get(f"/views/challenges/{challenge.id}/comments/fragment")).text
    assert body.index("دومی") < body.index("اولی")
