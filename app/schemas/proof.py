"""What a check-in on this act must carry -- one shape per kind of evidence.

A discriminated union in a JSON column, exactly as ``app/schemas/cadence.py``
is and ``app/schemas/completion.py`` is, and for the reason both of them are:
the kinds do not share their fields. A photograph has a freshness rule; a
future GPS proof has a point and a radius; a webhook proof has a URL and a
secret. Expressed as columns that would be a row of mostly-NULLs with a
comment explaining which ones apply, and every reader would have to know the
mapping.

**Adding a kind is three edits and no migration**: a member here, a member in
:class:`~app.models.challenge.ProofKind`, and a branch in
``app.proof.proof_satisfied``. That is the whole extension contract the union
exists to give -- nothing else in the app branches on the kind.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, Field


class SelfProof(BaseModel):
    """The word of the person who made the promise.

    No fields, and that is not a placeholder: self-report genuinely has
    nothing to configure, and a kind with no options is still a kind -- it is
    what every challenge in the app was before this, and it is what most of
    them should stay.
    """

    kind: Literal["self"] = "self"


class PhotoProof(BaseModel):
    """A picture, taken now.

    ``live_only`` is what the app promises and, honestly, the limit of what a
    server can promise: the bytes are re-encoded on arrival (so EXIF -- the
    device's own claim about when and where -- is gone before anything reads
    it), the timestamp is the *server's*, and the picture has to be attached
    to a report within ``app.proof.PROOF_MAX_AGE`` of arriving. That rules
    out yesterday's photograph and somebody else's; it does not rule out a
    photograph of a photograph, and nothing running on a server can.

    The gallery is closed on the client (the picker asks the camera for it),
    which is the courtesy half -- it keeps an honest member from picking the
    wrong thing. The freshness window is the enforced half.
    """

    kind: Literal["photo"] = "photo"
    live_only: bool = True


ProofUnion = Annotated[SelfProof | PhotoProof, Field(discriminator="kind")]
