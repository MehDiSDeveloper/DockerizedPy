"""The evidence axis: what a report has to carry, and whether it does.

Three things live here and nothing else does:

1. :func:`parse_proof` -- the one place ``Challenge.proof`` becomes a typed
   object. It reads a NULL column as "the default shape of ``proof_kind``",
   which is what makes the column nullable and the migration a no-op for
   every challenge written before it existed.
2. :func:`claim_proof_asset` -- the write boundary an uploaded picture
   crosses to become a piece of evidence. Every rule that separates evidence
   from decoration is in that one function.
3. :func:`proof_satisfied` -- the branch per kind. Adding a kind adds a
   branch here and nowhere else.

**What the server can honestly promise about a photograph.** Not that it was
taken by a camera rather than picked from a gallery -- nothing reaching a
server can tell those apart, and a design that claims otherwise is worse than
one that does not, because people will rely on it. What it *can* promise, and
does:

* the **time is the server's**. ``ProofAsset.captured_at`` is stamped when
  the bytes arrive. The device's clock is not consulted and EXIF is stripped
  by ``store_image`` before anything reads the file, so a photograph's own
  claim about when it was taken never enters the record.
* the **picture is recent**. An asset may only be attached to a report
  within :data:`PROOF_MAX_AGE` of arriving, so a photograph kept from last
  week is not evidence for today.
* the **picture is yours, and is used once**. ``user_id`` and ``claimed_at``,
  so somebody else's upload cannot be attached and the same picture cannot
  cover two occurrences.
* the **bytes are fingerprinted**. ``sha256`` of what was stored, so which
  picture was submitted has an answer that outlives the file.

The gallery being closed is a client-side courtesy (``capture`` on the
input), stated as one. Where a deployment needs more than this, the next
proof kind is a device-attested one -- which is a member in the union, not a
rewrite of any of this.
"""

from __future__ import annotations

import hashlib
import logging
from datetime import UTC, datetime, timedelta

from pydantic import TypeAdapter, ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.act import ProofAsset
from app.models.challenge import Challenge, ProofKind
from app.schemas.proof import PhotoProof, ProofUnion, SelfProof

logger = logging.getLogger("app.proof")

_proof_adapter = TypeAdapter(ProofUnion)

#: How long an uploaded picture stays attachable. Ten minutes is the window
#: between opening the camera and finishing a check-in, with room for a slow
#: connection and a member who got distracted -- and short enough that a
#: picture cannot be stockpiled. Measured from the *server's* stamp.
PROOF_MAX_AGE = timedelta(minutes=10)

#: The shape key `app/media.py` stores a proof picture under. Its own frame
#: rather than a reuse of `challenge_square`: a proof is a photograph of a
#: thing that happened, not a banner, and a frame is the thing the cropper
#: names to the member.
PROOF_SHAPE = "proof"


def parse_proof(challenge: Challenge) -> ProofUnion:
    """The typed proof policy of one act.

    A NULL ``proof`` column means "the default shape of ``proof_kind``", so
    every challenge that predates this reads as :class:`SelfProof` without a
    backfill. A column holding something this app can no longer parse falls
    back the same way rather than raising -- an act nobody can check in to is
    worse than one behaving like the default it was created with, which is
    the call :func:`app.identity.resolve_anonymity` makes for the same reason.
    """
    raw = challenge.proof
    if isinstance(raw, dict) and raw.get("kind"):
        try:
            return _proof_adapter.validate_python(raw)
        except ValidationError:
            logger.warning(
                "unparseable proof policy on challenge %s; falling back to %s",
                getattr(challenge, "id", None),
                challenge.proof_kind,
            )
    if challenge.proof_kind == ProofKind.PHOTO.value:
        return PhotoProof()
    return SelfProof()


def requires_photo(challenge: Challenge) -> bool:
    """Whether a report on this act must carry a picture."""
    return isinstance(parse_proof(challenge), PhotoProof)


def digest(raw: bytes) -> str:
    """Hex SHA-256 of the bytes this app stored -- not of what was sent."""
    return hashlib.sha256(raw).hexdigest()


class ProofRejected(Exception):
    """The asset cannot serve as evidence here. Message is user-facing."""


async def claim_proof_asset(
    db: AsyncSession,
    *,
    asset_id: int,
    user_id: int,
    now_utc: datetime | None = None,
) -> ProofAsset:
    """Turn one uploaded picture into the evidence behind one report.

    Every rule that makes a picture evidence is here, in the order they fail:
    it has to be yours, unused, and recent. Nothing is committed -- the caller
    writes the check-in in the same transaction, so a report that rolls back
    releases the asset with it.

    Raises :class:`ProofRejected` with a Farsi sentence in every case. The
    "not yours" case is deliberately worded as "not found", the way a
    check-in that is not yours is a 404: asset ids are sequential, and a
    distinct refusal would map which of them exist.
    """
    now = now_utc or datetime.now(UTC)
    asset = (
        await db.execute(select(ProofAsset).where(ProofAsset.id == asset_id))
    ).scalar_one_or_none()
    if asset is None or asset.user_id != user_id:
        raise ProofRejected("مدرک پیدا نشد.")
    if asset.claimed_at is not None:
        raise ProofRejected("این مدرک قبلاً برای گزارش دیگری ثبت شده است.")

    captured = asset.captured_at
    if captured.tzinfo is None:
        # SQLite has no aware datetime type (CLAUDE.md, Dates).
        captured = captured.replace(tzinfo=UTC)
    if now - captured > PROOF_MAX_AGE:
        raise ProofRejected("عکس باید همین حالا گرفته شود؛ دوباره تلاش کن.")

    asset.claimed_at = now
    return asset


def proof_satisfied(challenge: Challenge, *, has_asset: bool) -> bool:
    """Does a report with (or without) an asset meet this act's policy?

    The one branch per kind, and the extension point named in the module
    docstring: a new :class:`~app.models.challenge.ProofKind` adds a case
    here and touches nothing else. ``has_asset`` rather than the asset itself
    because the *validity* of the asset was already decided by
    :func:`claim_proof_asset`; this answers the separate question of whether
    the act needed one at all.
    """
    policy = parse_proof(challenge)
    if isinstance(policy, PhotoProof):
        return has_asset
    return True


# --- how the act describes its own policy to a member --------------------
#
# The per-surface pattern (D7): these are short labels for one screen family,
# not a promise about behaviour, so they live beside the axis they name
# rather than in `app/explainers.py`. The wizard keeps its own client-side
# copy for the same reason the identity-mode chips do.

PROOF_LABELS = {
    ProofKind.SELF.value: "خوداظهاری",
    ProofKind.PHOTO.value: "عکس در لحظه",
}

PROOF_HINTS = {
    ProofKind.SELF.value: "ثبت هر وعده فقط با گفتهٔ خودت.",
    ProofKind.PHOTO.value: "هر وعده باید با عکسی که همان لحظه گرفته می‌شود ثبت شود.",
}

PROOF_ICONS = {
    ProofKind.SELF.value: "check",
    ProofKind.PHOTO.value: "camera",
}


def proof_label(kind: str | None) -> str:
    return PROOF_LABELS.get(kind or "", PROOF_LABELS[ProofKind.SELF.value])


def proof_icon(kind: str | None) -> str:
    return PROOF_ICONS.get(kind or "", PROOF_ICONS[ProofKind.SELF.value])


def register_proof_filters(env) -> None:
    """Expose the proof labels to one Jinja environment.

    The same registration contract ``register_icon_filters`` has -- every
    views router builds its own ``Jinja2Templates``, so one rendering an
    act's policy has to call this.
    """
    env.filters["proof_label"] = proof_label
    env.filters["proof_icon"] = proof_icon
    env.globals["proof_hints"] = PROOF_HINTS
