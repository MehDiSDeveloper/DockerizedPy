"""Comments -- the domain module. The only door into the `Comments` table.

The rule it exists to hold is the one `app/reactions.py` holds: **a comment
is only as visible as the thing it is about.** The table is polymorphic, so
nothing in the database can refuse a body naming a challenge the caller
cannot see; :data:`SUBJECT_RESOLVERS` is where that is refused, one entry per
:class:`CommentSubject`, each composing that subject's *own* visibility
filter -- never a copy of the rule -- so an unreachable subject comes back as
"no such row" and the route answers 404.

Two more rules live here rather than in the router:

* **A thread is one level deep.** :func:`create_comment` re-points a reply to
  a reply at its root, so no read ever has to recurse and the UI never has to
  flatten. Nothing else in the app knows about that normalisation.
* **Reading a page is three queries, not one per row.** The roots come
  newest-first (the app's one ordering rule), their replies come back in a
  single `parent_id IN (...)` query oldest-first (a conversation reads
  forwards -- the same exception the group request queue takes), and the
  reply counts are not stored anywhere.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.audit_base import newest_first
from app.models.challenge import Challenge
from app.models.comment import Comment, CommentSubject

#: How many replies a thread shows before «پاسخ‌های بیشتر». The rest are
#: rendered too and merely hidden, so «بیشتر» is a class toggle rather than a
#: second request: a paged list *inside* a paged list would need its own
#: endpoint, its own offset and its own share of one URL (CLAUDE.md, the
#: group screens) to solve a problem one level of threading does not have.
REPLY_PREVIEW = 3

#: Threads per page, and the ceiling a client may ask for -- the same pair
#: every other paged list in the app carries.
DEFAULT_COMMENT_PAGE_SIZE = 10
MAX_COMMENT_PAGE_SIZE = 50


async def _challenge_exists(db: AsyncSession, subject_id: int, user_id: int | None) -> bool:
    # Imported here, not at module scope: `routers/challenge.py` imports
    # models, and a top-level import would close the cycle.
    from app.routers.challenge import challenge_visibility_filter

    row = await db.execute(
        select(Challenge.id)
        .where(Challenge.id == subject_id)
        .where(challenge_visibility_filter(user_id))
    )
    return row.scalar_one_or_none() is not None


#: subject kind -> "may this member see this subject at all"
SUBJECT_RESOLVERS: dict[
    CommentSubject, Callable[[AsyncSession, int, int | None], Awaitable[bool]]
] = {
    CommentSubject.CHALLENGE: _challenge_exists,
}


async def subject_is_visible(
    db: AsyncSession, subject: CommentSubject, subject_id: int, user_id: int | None
) -> bool:
    return await SUBJECT_RESOLVERS[subject](db, subject_id, user_id)


def _base(subject: CommentSubject, subject_id: int):
    return select(Comment).where(
        Comment.subject_type == subject.value, Comment.subject_id == subject_id
    )


async def count_for(
    db: AsyncSession, subject: CommentSubject, subject_id: int
) -> int:
    """Everything said under this subject, replies included -- the badge."""
    rows = await db.execute(
        select(func.count(Comment.id)).where(
            Comment.subject_type == subject.value, Comment.subject_id == subject_id
        )
    )
    return rows.scalar_one()


async def counts_for(
    db: AsyncSession, subject: CommentSubject, subject_ids: list[int]
) -> dict[int, int]:
    """`{subject_id: count}` -- one query for a whole page of cards.

    The list form of :func:`count_for`, for the reason `counts_for` in
    `app/reactions.py` takes a list: a page of cards asks this once, not once
    per card. A subject nobody has written under is simply absent, so the
    template's `.get(id, 0)` is the zero.
    """
    if not subject_ids:
        return {}
    rows = await db.execute(
        select(Comment.subject_id, func.count(Comment.id))
        .where(
            Comment.subject_type == subject.value,
            Comment.subject_id.in_(subject_ids),
        )
        .group_by(Comment.subject_id)
    )
    return {sid: n for sid, n in rows.all()}


async def fetch_thread_page(
    db: AsyncSession,
    *,
    subject: CommentSubject,
    subject_id: int,
    offset: int,
    limit: int,
) -> tuple[list[dict], bool]:
    """One page of threads: `[{comment, replies, extra_replies}]`, has_more.

    `limit + 1` is fetched to answer "is there more" without a second COUNT,
    the same trick `fetch_challenge_page` uses.
    """
    result = await db.execute(
        _base(subject, subject_id)
        .where(Comment.parent_id.is_(None))
        .options(selectinload(Comment.user))
        .order_by(*newest_first(Comment))
        .offset(offset)
        .limit(limit + 1)
    )
    roots = list(result.scalars().all())
    has_more = len(roots) > limit
    roots = roots[:limit]
    if not roots:
        return [], False

    root_ids = [c.id for c in roots]
    replies_rows = await db.execute(
        select(Comment)
        .where(Comment.parent_id.in_(root_ids))
        .options(selectinload(Comment.user))
        .order_by(Comment.created_at.asc(), Comment.id.asc())
    )
    by_parent: dict[int, list[Comment]] = {rid: [] for rid in root_ids}
    for reply in replies_rows.scalars().all():
        by_parent[reply.parent_id].append(reply)

    threads = [
        {
            "comment": root,
            "replies": by_parent[root.id],
            # How many of those the template starts with hidden.
            "extra_replies": max(0, len(by_parent[root.id]) - REPLY_PREVIEW),
            "preview": REPLY_PREVIEW,
        }
        for root in roots
    ]
    return threads, has_more


async def load_comment(db: AsyncSession, comment_id: int) -> Comment | None:
    result = await db.execute(
        select(Comment).where(Comment.id == comment_id).options(selectinload(Comment.user))
    )
    return result.scalar_one_or_none()


async def create_comment(
    db: AsyncSession,
    *,
    user_id: int,
    subject: CommentSubject,
    subject_id: int,
    body: str | None,
    sticker: str | None,
    parent: Comment | None,
) -> Comment:
    """Write one comment. Does not commit -- the caller owns the transaction.

    A reply to a reply is stored against the **root**: the thread stays one
    level deep no matter what the client sends, so the normalisation cannot
    be forgotten by a second call site.
    """
    root_id = None
    if parent is not None:
        root_id = parent.parent_id or parent.id
    comment = Comment(
        user_id=user_id,
        subject_type=subject.value,
        subject_id=subject_id,
        parent_id=root_id,
        body=body,
        sticker=sticker,
        last_modifier_user_id=user_id,
    )
    db.add(comment)
    await db.flush()
    return comment


async def delete_comment(db: AsyncSession, comment: Comment) -> None:
    """Delete one comment, and -- if it is a root -- the thread under it.

    One statement for the children rather than an ORM cascade or an
    `ON DELETE CASCADE`: every environment here runs SQLite, which enforces
    foreign keys only when `PRAGMA foreign_keys` is on, so a database-level
    cascade would work on Postgres and silently orphan every reply
    everywhere else. Does not commit -- the caller owns the transaction.
    """
    if comment.parent_id is None:
        await db.execute(delete(Comment).where(Comment.parent_id == comment.id))
    await db.delete(comment)
