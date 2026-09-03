"""The one door an uploaded picture comes in through.

Deliberately *not* a route per surface (`/users/{id}/avatar`,
`/challenges/{id}/cover`). Storing a file and choosing what a row points at
are two different acts, and keeping them apart is what makes the picker one
piece of client code instead of one per field: this route answers a **key**,
and the existing `PATCH /users/{id}` / `PATCH /challenges/{id}` write that key
onto the column that wanted it, through the schema validators that already
guard every other field. It also means a picture chosen in the create wizard
-- where there is no row yet to attach it to -- takes exactly the same path.

Signing in is the whole authorisation: an upload that is never saved onto a
row is an orphan nobody can reach (the key is 24 random hex characters and is
returned to one caller), so there is nothing here for a member to do to
anybody else. Ownership is checked where it always was, at the PATCH.
"""

import logging

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile

from app.auth import get_current_user_id
from app.logging_config import log_event
from app.media import MAX_UPLOAD_BYTES, SHAPES, ImageRejected, media_url, store_image

router = APIRouter(prefix="/media", tags=["media"])
logger = logging.getLogger("app.routers.media")


@router.post("/", status_code=201)
async def upload_image(
    shape: str = Form(...),
    file: UploadFile = File(...),
    user_id: int = Depends(get_current_user_id),
):
    """Store one picture and answer `{key, url}`.

    The size is checked against what was actually read, not against the
    `Content-Length` header a client writes -- the header is a claim, and
    `UploadFile` has already spooled the body by the time we see it.
    """
    if shape not in SHAPES:
        raise HTTPException(status_code=422, detail="قالب تصویر نامعتبر است.")

    raw = await file.read()
    if not raw:
        raise HTTPException(status_code=422, detail="فایلی دریافت نشد.")
    if len(raw) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="حجم تصویر بیش از حد مجاز است.")

    try:
        key = store_image(raw, shape)
    except ImageRejected as exc:
        # A member's own mistake (a PDF, a broken file), not a fault: WARNING
        # and a 422, like every other refusal somebody can cause.
        log_event(logger, "media.rejected", level=logging.WARNING, shape=shape, user_id=user_id)
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    return {"key": key, "url": media_url(key)}
