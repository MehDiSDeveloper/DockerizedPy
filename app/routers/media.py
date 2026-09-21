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
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_user_id
from app.database import get_db
from app.logging_config import log_event
from app.media import (
    MAX_UPLOAD_BYTES,
    SHAPES,
    ImageRejected,
    encode_image,
    media_url,
    store_image,
    write_image,
)
from app.models.act import ProofAsset
from app.proof import PROOF_MAX_AGE, PROOF_SHAPE, digest

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


@router.post("/proof", status_code=201)
async def upload_proof(
    file: UploadFile = File(...),
    user_id: int = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    """Store one picture *as evidence* and answer the row that addresses it.

    A second door rather than a ``shape=proof`` on the one above, because the
    two answer different things. That one answers a **key**, which a later
    PATCH writes onto a column -- storing and choosing are two acts, and the
    route stays free of both. This one answers an **id**, because a proof
    asset is a row: it records the server's clock, the uploader and the
    digest of what was written, and those are exactly the three things
    ``app.proof.claim_proof_asset`` checks when the picture is submitted.

    The row is committed here, unattached. That is deliberate and it is safe:
    an unclaimed asset is a row nobody can reach -- it is addressable only by
    the id returned to this one caller, and the claim re-checks ownership --
    and committing it is what lets a member take the photograph, then take
    their time over the note.

    Nothing about the file is trusted: the bytes are re-encoded (so EXIF, and
    with it the device's own claim about when and where, is gone), and the
    timestamp is ours. See ``app/proof.py`` on what that can and cannot
    promise.
    """
    raw = await file.read()
    if not raw:
        raise HTTPException(status_code=422, detail="فایلی دریافت نشد.")
    if len(raw) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="حجم تصویر بیش از حد مجاز است.")

    try:
        payload = encode_image(raw, PROOF_SHAPE)
    except ImageRejected as exc:
        log_event(
            logger,
            "proof.rejected",
            level=logging.WARNING,
            user_id=user_id,
        )
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    key = write_image(payload, PROOF_SHAPE)
    now = datetime.now(UTC)
    asset = ProofAsset(
        media_key=key,
        user_id=user_id,
        # The digest of what was *stored*, not of what was sent: the stored
        # bytes are the ones that can be looked at later.
        sha256=digest(payload),
        byte_size=len(payload),
        captured_at=now,
        last_modifier_user_id=user_id,
    )
    db.add(asset)
    await db.commit()
    await db.refresh(asset)
    log_event(
        logger,
        "proof.stored",
        proof_asset_id=asset.id,
        user_id=user_id,
        bytes=len(payload),
    )
    return {
        "proof_asset_id": asset.id,
        "url": media_url(key),
        "sha256": asset.sha256,
        "captured_at": asset.captured_at,
        # How long the client has before the asset stops being attachable.
        # Sent rather than restated in JS, so the countdown a member sees and
        # the window the server enforces are one number.
        "expires_in": int(PROOF_MAX_AGE.total_seconds()),
    }
