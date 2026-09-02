"""The sticker catalogue -- a fixed set, stored by id, never by character.

Same contract as `app/avatars.py`: a comment stores the **id** (`"joy"`),
the id is validated at the one write boundary (`schemas/comment.py`), and
turning it into something a reader sees happens here. That is what lets the
set be re-drawn -- swapped for real artwork one day -- without a data
migration, and what stops a comment body from smuggling arbitrary markup in
through a second field.

They are emoji rather than image files on purpose, for the reason the
avatars are committed rather than fetched: the app must work inside the
Docker image with no outbound network, and 40 SVGs bought nothing here that
a font the reader's device already has does not.

An unknown id -- one retired between two deploys -- renders as
:data:`FALLBACK_STICKER` rather than raising, the same way an unknown
notification kind renders as `FALLBACK_META`.
"""

#: group label -> [(id, character)]. The order is the picker's grid order.
STICKER_GROUPS: list[tuple[str, list[tuple[str, str]]]] = [
    ("احساس", [
        ("joy", "😂"), ("smile", "😊"), ("wink", "😉"), ("love", "😍"),
        ("wow", "😮"), ("sad", "😢"), ("angry", "😠"), ("think", "🤔"),
        ("cool", "😎"), ("relief", "😅"), ("sleep", "😴"), ("party-face", "🥳"),
    ]),
    ("تشویق", [
        ("clap", "👏"), ("thumbsup", "👍"), ("muscle", "💪"), ("fire", "🔥"),
        ("star", "⭐"), ("trophy", "🏆"), ("medal", "🥇"), ("rocket", "🚀"),
        ("party", "🎉"), ("hundred", "💯"), ("heart", "❤️"), ("sparkles", "✨"),
    ]),
    ("تمرین", [
        ("run", "🏃"), ("bike", "🚴"), ("swim", "🏊"), ("yoga", "🧘"),
        ("weights", "🏋️"), ("walk", "🚶"), ("book", "📚"), ("water", "💧"),
        ("apple", "🍎"), ("sun", "🌞"), ("moon", "🌙"), ("calendar", "📅"),
    ]),
    ("دیگر", [
        ("ok", "👌"), ("pray", "🙏"), ("wave", "👋"), ("eyes", "👀"),
        ("check", "✅"), ("cross", "❌"), ("bulb", "💡"), ("clock", "⏰"),
    ]),
]

#: id -> character, flattened once at import.
STICKERS: dict[str, str] = {
    sid: char for _, items in STICKER_GROUPS for sid, char in items
}

FALLBACK_STICKER = "❔"


def is_valid_sticker(sticker_id: str) -> bool:
    return sticker_id in STICKERS


def sticker_char(sticker_id: str | None) -> str:
    if not sticker_id:
        return ""
    return STICKERS.get(sticker_id, FALLBACK_STICKER)


def register_sticker_filters(env) -> None:
    """Expose `sticker_char` to a Jinja environment.

    Every views router builds its own ``Jinja2Templates``, so one that
    renders a comment must call this -- the fifth registration contract,
    exactly like `register_icon_filters` (CLAUDE.md).
    """
    env.filters["sticker_char"] = sticker_char
