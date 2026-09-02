"""The comments door. One route shape, every subject kind.

`/comments/{subject_type}/{subject_id}` rather than
`/challenges/{id}/comments`, for the reason the reactions router takes the
subject as a path *value*: the second thing worth commenting on needs a
`CommentSubject` member and a resolver, not another router.

The acting user is the session, never the body (CLAUDE.md, Ownership). A
subject the caller cannot see is a **404** -- a comment must not become a way
to learn that a private challenge exists -- and so is somebody else's
comment on a delete, for the reason a check-in is: a 403 there would map
who has said what under a challenge.

Reading is not here. A page of threads is server-rendered markup
(`/views/challenges/{id}/comments`), like every other paged list in this app.
"""

import logging

from fastapi import APIRouter, Depends, HTTPException, Path
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_user_id
from app.comments import (
    create_comment,
    load_comment,
    subject_is_visible,
)
from app.comments import (
    delete_comment as delete_comment_row,
)
from app.database import get_db
from app.logging_config import log_event
from app.models.challenge import Challenge
from app.models.comment import Comment, CommentSubject
from app.models.notification import NotificationKind
from app.notifications import notify
from app.schemas.comment import CommentCreate, CommentRead

router = APIRouter(prefix="/comments", tags=["comments"])
logger = logging.getLogger(__name__)

NOT_FOUND = "چنین موردی وجود ندارد"


async def _subject(
    db: AsyncSession, subject_type: str, subject_id: int, user_id: int | None
) -> CommentSubject:
    try:
        subject = CommentSubject(subject_type)
    except ValueError:
        raise HTTPException(status_code=404, detail=NOT_FOUND) from None
    if not await subject_is_visible(db, subject, subject_id, user_id):
        raise HTTPException(status_code=404, detail=NOT_FOUND)
    return subject


async def _announce(
    db: AsyncSession,
    *,
    comment: Comment,
    parent: Comment | None,
    subject: CommentSubject,
    subject_id: int,
    actor_id: int,
) -> None:
    """Tell the one person who would not otherwise find out.

    A reply is heard by the person answered; a new thread is heard by the
    challenge's owner. `notify()` drops the case where that is the actor
    themselves, so neither branch needs to check.
    """
    if subject is not CommentSubject.CHALLENGE:
        return
    if parent is not None:
        await notify(
            db,
            user_id=parent.user_id,
            kind=NotificationKind.COMMENT_REPLIED,
            actor_user_id=actor_id,
            challenge_id=subject_id,
        )
        return
    owner_id = (
        await db.execute(select(Challenge.owner_id).where(Challenge.id == subject_id))
    ).scalar_one_or_none()
    if owner_id is not None:
        await notify(
            db,
            user_id=owner_id,
            kind=NotificationKind.CHALLENGE_COMMENTED,
            actor_user_id=actor_id,
            challenge_id=subject_id,
        )


@router.post(
    "/{subject_type}/{subject_id}", response_model=CommentRead, status_code=201
)
async def post_comment(
    payload: CommentCreate,
    subject_type: str,
    subject_id: int = Path(ge=1),
    current_user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    subject = await _subject(db, subject_type, subject_id, current_user_id)

    parent = None
    if payload.parent_id is not None:
        parent = await load_comment(db, payload.parent_id)
        # A parent from another subject is not a parent: it would splice one
        # challenge's thread into another's page.
        if (
            parent is None
            or parent.subject_type != subject.value
            or parent.subject_id != subject_id
        ):
            raise HTTPException(status_code=404, detail=NOT_FOUND)

    comment = await create_comment(
        db,
        user_id=current_user_id,
        subject=subject,
        subject_id=subject_id,
        body=payload.body,
        sticker=payload.sticker,
        parent=parent,
    )
    await _announce(
        db,
        comment=comment,
        parent=parent,
        subject=subject,
        subject_id=subject_id,
        actor_id=current_user_id,
    )
    await db.commit()
    log_event(
        logger,
        "comment.created",
        comment_id=comment.id,
        subject_type=subject.value,
        subject_id=subject_id,
        is_reply=parent is not None,
    )
    return await load_comment(db, comment.id)


@router.delete("/{comment_id}", status_code=204)
async def delete_comment(
    comment_id: int = Path(ge=1),
    current_user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    comment = await load_comment(db, comment_id)
    # Somebody else's comment is a 404, not a 403: the honest answer would
    # tell an enumerator which ids are whose.
    if comment is None or comment.user_id != current_user_id:
        raise HTTPException(status_code=404, detail=NOT_FOUND)
    await delete_comment_row(db, comment)
    await db.commit()
    log_event(logger, "comment.deleted", comment_id=comment_id)
