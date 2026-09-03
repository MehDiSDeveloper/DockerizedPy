"""Uploaded pictures: the one place a file a member chose becomes a stored id.

The same contract ``app/avatars.py`` has, for the same reasons -- the database
stores an **id**, never a path or a URL, and one accessor turns that id into a
URL. What changes is where the bytes come from: an avatar is one of forty
files committed to the repo, and one of these is whatever a phone's camera
roll handed over.

Two rules follow from that, and they are the whole module:

**Nothing a client sends is trusted.** Not the filename (never used -- the id
is generated here), not the content type, not the extension, not the pixel
dimensions. Every upload is *decoded and re-encoded* by Pillow, which is what
strips EXIF (a photograph carries the GPS coordinates it was taken at), what
guarantees the stored bytes really are an image, and what caps the file's size
on a disk that is a mounted volume rather than an object store. A ``.webp``
one of ours wrote is the only thing this directory ever contains.

**An id is a filename, and it is generated, so it cannot be a path.** The key
is ``<24 hex>.webp`` and nothing else parses -- ``is_media_key`` is the gate
every read goes through, so a value that reached the column by any route at
all still cannot escape ``MEDIA_ROOT``. Files fan out into a directory per
first byte-pair: a flat directory of tens of thousands of entries is slow to
stat on most filesystems, and this costs one ``mkdir``.

There is no ``Media`` table. A key is a small opaque string living in the
column that needed a picture (``Users.avatar``, ``Challenges.image_square``)
-- a row of metadata beside it would have to be kept in step with those
columns by hand, and answers no question the app asks. What that costs is that
an upload nobody ever saved is an orphan; ``discard`` at each write boundary
is the answer for the common case (replacing a picture), and a sweep for the
rest is a job this app has no runner for (see ``purge_expired_codes``).
"""

from __future__ import annotations

import io
import logging
import re
import secrets
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageOps

from app.config import MEDIA_ROOT
from app.logging_config import log_event

logger = logging.getLogger("app.media")

# The URL prefix `app/main.py` mounts MEDIA_ROOT on.
MEDIA_URL_PREFIX = "/media"

# A key is exactly this and can be nothing else. Generated here, so the
# pattern is a *guarantee* rather than a hope -- and it is re-checked on the
# way out, because the column it was stored in is writable through a schema.
_KEY_RE = re.compile(r"^[0-9a-f]{24}\.webp$")

# Longest key is 29 chars; the columns holding one are sized well past that.
MEDIA_KEY_MAX_LENGTH = 40

# What the browser is allowed to send. The client already crops and re-encodes
# through a canvas, so anything near this is a hand-crafted request rather
# than a phone photo -- the cap is there to keep one from occupying a worker
# and the disk, not because a real upload approaches it.
MAX_UPLOAD_BYTES = 8 * 1024 * 1024

# Pillow will happily allocate a few gigabytes for a "small" file that claims
# enormous dimensions. This is the decompression-bomb ceiling, well above any
# real photograph.
Image.MAX_IMAGE_PIXELS = 64 * 1024 * 1024


@dataclass(frozen=True)
class ImageShape:
    """One crop the app asks for: its aspect, its ceiling, its quality.

    The *aspect* is the client's business (the cropper is locked to it, and a
    frame is a thing you see rather than a number you validate), so it is
    carried here only so one place names both halves. What the server enforces
    is the ceiling: an image is scaled down to fit inside it, never up, so a
    small original stays small.
    """

    key: str
    width: int
    height: int
    quality: int = 80


# The shapes the app has surfaces for. A caller names one; nothing else is
# accepted, so a new frame is a row here plus the surface that renders it.
SHAPES: dict[str, ImageShape] = {
    # A member's own picture. Rendered at 84px at the largest and often at
    # 28px, so 512 is already generous -- and it is the one shape a member
    # sees dozens of at once, in a roster.
    "avatar": ImageShape("avatar", 512, 512),
    # The challenge's square banner: the detail hero's ground, and the rail
    # card's 4:3 cover, which crops a band out of this rather than asking for
    # a second picture.
    "challenge_square": ImageShape("challenge_square", 1080, 1080),
    # The full-screen reader's panel. Portrait, because that is the shape of
    # the phone it fills edge to edge.
    "challenge_tall": ImageShape("challenge_tall", 1080, 1920, quality=78),
    # A roadmap's own banner. The same square as a challenge's and a frame of
    # its own anyway: a frame is the thing the cropper *names* to the member,
    # and «تصویر مربعی چالش» over a roadmap's picker is the app telling them
    # they are somewhere they are not.
    "roadmap_square": ImageShape("roadmap_square", 1080, 1080),
}


def is_media_key(value: str | None) -> bool:
    """True only for a string this module generated."""
    return bool(value) and bool(_KEY_RE.match(value))


def media_url(value: str | None) -> str | None:
    """Public URL for a key, or ``None`` for anything that is not one.

    ``None`` rather than a placeholder: unlike an avatar, "no picture" here is
    a surface that draws something else entirely (the category's own gradient
    cover), so the template has to be able to ask.
    """
    return f"{MEDIA_URL_PREFIX}/{value[:2]}/{value}" if is_media_key(value) else None


def _path_for(key: str) -> Path:
    return MEDIA_ROOT / key[:2] / key


class ImageRejected(Exception):
    """The bytes are not an image this app will store. Message is user-facing."""


def store_image(raw: bytes, shape_key: str) -> str:
    """Re-encode ``raw`` into MEDIA_ROOT and answer its key.

    Everything about the original is discarded except its pixels: metadata,
    format, colour profile and any orientation flag (applied first, so a
    photo taken sideways is stored the way it was seen).
    """
    shape = SHAPES[shape_key]
    try:
        with Image.open(io.BytesIO(raw)) as opened:
            img = ImageOps.exif_transpose(opened)
            # Flatten onto white rather than keeping alpha: every surface
            # renders these as an opaque cover, and a transparent PNG through
            # a WebP would show the page behind a face.
            if img.mode in ("RGBA", "LA", "P"):
                img = img.convert("RGBA")
                ground = Image.new("RGB", img.size, (255, 255, 255))
                ground.paste(img, mask=img.split()[-1])
                img = ground
            else:
                img = img.convert("RGB")
            img.thumbnail((shape.width, shape.height), Image.LANCZOS)
            buf = io.BytesIO()
            img.save(buf, format="WEBP", quality=shape.quality, method=4)
    except Exception as exc:  # Pillow raises a dozen unrelated types
        raise ImageRejected("فایل انتخاب‌شده یک تصویر معتبر نیست.") from exc

    payload = buf.getvalue()
    key = f"{secrets.token_hex(12)}.webp"
    path = _path_for(key)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    log_event(logger, "media.stored", shape=shape.key, key=key, bytes=len(payload))
    return key


def discard(value: str | None) -> None:
    """Delete the file behind a key, if that is what the value is.

    Best-effort and never raises: this is called *after* the row that pointed
    at the file has already been changed, and a picture that outlives its row
    is wasted bytes, while an exception here would fail a save that already
    happened.
    """
    if not is_media_key(value):
        return
    try:
        _path_for(value).unlink(missing_ok=True)
        log_event(logger, "media.discarded", key=value)
    except OSError:
        logger.warning("could not delete media file %s", value)


def discard_replaced(old: str | None, new: str | None) -> None:
    """``discard`` the old value only when the new one really replaced it."""
    if old != new:
        discard(old)


def register_media_filters(env) -> None:
    """Expose ``media_url`` on one templates environment.

    Every views router builds its own ``Jinja2Templates`` (the same contract
    ``register_icon_filters`` and ``register_avatar_filters`` have), so each
    one rendering a picture has to call this or the filter is missing at
    render time.
    """
    env.filters["media_url"] = media_url
