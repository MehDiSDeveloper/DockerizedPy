"""The reactions door. One route shape, every subject kind.

`/reactions/{subject_type}/{subject_id}/likes` rather than
`/challenges/{id}/likes`: the subject is a path *value*, so the second thing
worth liking needs a `ReactionSubject` member and a resolver in
`app/reactions.py` -- not a route, a schema and a piece of page JS per kind.

The acting user is the session, never the body (CLAUDE.md, Ownership). A
subject the caller cannot see is a **404**: reactions add no way to learn
that a private challenge exists.
"""

import logging

from fastapi import APIRouter, Depends, HTTPException, Path
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_user_id, get_optional_user_id
from app.database import get_db
from app.logging_config import log_event
from app.models.reaction import ReactionSubject
from app.reactions import reaction_state, set_reaction, subject_is_visible
from app.schemas.reaction import ReactionSet, ReactionState

router = APIRouter(prefix="/reactions", tags=["reactions"])
logger = logging.getLogger(__name__)


async def _subject(
    db: AsyncSession,
    subject_type: str,
    subject_id: int,
    user_id: int | None,
) -> ReactionSubject:
    try:
        subject = ReactionSubject(subject_type)
    except ValueError:
        raise HTTPException(status_code=404, detail="چنین موردی وجود ندارد.") from None
    if not await subject_is_visible(db, subject, subject_id, user_id):
        raise HTTPException(status_code=404, detail="چنین موردی وجود ندارد.")
    return subject


@router.get("/{subject_type}/{subject_id}/likes", response_model=ReactionState)
async def get_likes(
    subject_type: str,
    subject_id: int = Path(ge=1),
    current_user_id: int | None = Depends(get_optional_user_id),
    db: AsyncSession = Depends(get_db),
):
    subject = await _subject(db, subject_type, subject_id, current_user_id)
    count, liked = await reaction_state(db, current_user_id, subject, subject_id)
    return ReactionState(count=count, liked=liked)


@router.put("/{subject_type}/{subject_id}/likes", response_model=ReactionState)
async def set_like(
    payload: ReactionSet,
    subject_type: str,
    subject_id: int = Path(ge=1),
    current_user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    subject = await _subject(db, subject_type, subject_id, current_user_id)
    await set_reaction(
        db,
        user_id=current_user_id,
        subject=subject,
        subject_id=subject_id,
        liked=payload.liked,
    )
    await db.commit()
    log_event(
        logger,
        "reaction.set",
        subject_type=subject.value,
        subject_id=subject_id,
        liked=payload.liked,
    )
    count, liked = await reaction_state(db, current_user_id, subject, subject_id)
    return ReactionState(count=count, liked=liked)
