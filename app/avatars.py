"""The avatar catalogue: which pictures a member may pick, and where they live.

The pictures are static SVGs under ``static/img/avatars/`` -- generated once
from DiceBear (https://www.dicebear.com) and committed, *not* fetched from
their API at render time. Two reasons: the app has to work inside the Docker
image with no outbound network, and an avatar that 404s on someone else's
outage is worse than no avatar. Regenerating one means replacing its file, not
changing code.

``Users.avatar`` stores the **id** (the filename stem, e.g. ``clay-ivy``),
never a URL or a path -- so the ids survive a move of the static directory,
and a value that is not in ``AVATAR_IDS`` can never turn into a path on disk.
``is_valid_avatar`` is that gate and every write goes through it; the schema
validator in ``schemas/user.py`` is the single place it is applied.

An uploaded picture lives in this same column as a media key (see
``app/media.py``): ``avatar_url`` resolves both, so every surface in the app
renders one without knowing the difference.

``None`` is a legitimate, permanent state -- every account that predates this
column has it, and picking is optional at signup -- so ``avatar_url`` answers
the shadowed-head placeholder rather than raising.
"""

from app.media import is_media_key, media_url

# Grouped by DiceBear style, four per style: the picker renders them in this
# order, so the grid reads as ten families rather than forty unrelated tiles.
AVATAR_IDS: tuple[str, ...] = (
    "glyphs-dana", "glyphs-ida", "glyphs-cai", "glyphs-eden",
    "cameo-devon", "cameo-ivy", "cameo-calla", "cameo-ewan",
    "marbles-denver", "marbles-iva", "marbles-curtis", "marbles-ella",
    "clay-dilan", "clay-ivy", "clay-calla", "clay-enid",
    "critters-dalia", "critters-ida", "critters-cosima", "critters-elio",
    "bottts-neutral-dalia", "bottts-neutral-idris", "bottts-neutral-cato", "bottts-neutral-eden",
    "shapes-dion", "shapes-ilona", "shapes-cosima", "shapes-elin",
    "squircles-dov", "squircles-ilaria", "squircles-chloe", "squircles-elio",
    "slice-denver", "slice-izumi", "slice-casper", "slice-elba",
    "stack-duncan", "stack-isabel", "stack-caleb", "stack-efe",
)

_AVATAR_SET = frozenset(AVATAR_IDS)

# Shadowed head and shoulders -- "nobody", deliberately not a letter or a
# colour, so it reads as *unset* rather than as a quieter kind of avatar.
DEFAULT_AVATAR_ID = "_default"

# Longest catalogue id today is 24 chars and an uploaded key is 29; the column
# is sized well past both, so a new style with a longer name is a data change
# rather than a migration.
AVATAR_ID_MAX_LENGTH = 60


def is_valid_avatar(value: str | None) -> bool:
    """True for ``None`` (no pick) or an id that has a file behind it.

    Two kinds of id share this column, and this is the only gate either one
    passes through. A *catalogue* id names one of the committed SVGs; a
    *media* key (`app.media`) names a picture the member uploaded. Keeping
    them in one column rather than adding a second is what lets every surface
    that already renders `x.avatar | avatar_url` -- the roster, the
    leaderboard, a comment, a group's emblem -- show an uploaded photo with
    no change at all, and what keeps "which of the two is showing" from
    becoming a question every one of them has to answer.
    """
    return value is None or value in _AVATAR_SET or is_media_key(value)


def avatar_url(value: str | None) -> str:
    """Static path for an avatar id, falling back to the placeholder.

    Anything unknown -- ``None``, or an id whose file was retired without a
    data migration -- resolves to the placeholder rather than a broken image.
    """
    uploaded = media_url(value)
    if uploaded is not None:
        return uploaded
    avatar_id = value if value in _AVATAR_SET else DEFAULT_AVATAR_ID
    return f"/static/img/avatars/{avatar_id}.svg"


def register_avatar_filters(env) -> None:
    """Expose ``avatar_url`` on one templates environment.

    Every views router builds its own ``Jinja2Templates`` (same as
    ``register_icon_filters``), so each one rendering a member has to call
    this or the filter is missing at render time.
    """
    env.filters["avatar_url"] = avatar_url
