"""Regenerate the Android launcher's *legacy* icons into the native shell.

    python -m app.scripts.generate_android_icons

Same rule as its sibling `generate_app_icons`, which this imports rather than
copies: the artwork is **generated once and committed**, and the mark is the
app's own `.brand-mark` drawn from the same numbers, so the launcher icon, the
PWA icon and the sign-in card can never drift into three different drawings.

Only two files per density are written here, and the reason is worth keeping:
everything else in `res/` that carries this mark is a **vector**, hand-written
beside these. An adaptive icon (API 26+), the splash and the Android 12 splash
icon are all one small `<vector>` each -- resolution-independent, sharp at
every density, and a single file to edit. What a vector cannot do is be the
launcher icon on API 23-25, which has no adaptive icon and reads
`@mipmap/ic_launcher` as a bitmap. `minSdkVersion` is 23, so those five
densities are the whole of what has to be rasterised.

`ic_launcher` is the rounded square the PWA ships; `ic_launcher_round` is the
same drawing on a circle, for the launchers that ask for one.
"""

from __future__ import annotations

from pathlib import Path

from app.config import BASE_DIR
from app.scripts.generate_app_icons import MARK_ANY, render

#: `app/` is BASE_DIR, so the shell sits beside the Python package.
RES_DIR: Path = BASE_DIR.parent / "mobile" / "android" / "app" / "src" / "main" / "res"

#: The five density buckets Android asks a legacy launcher icon for, at the
#: 48dp the platform draws it at.
DENSITIES: tuple[tuple[str, int], ...] = (
    ("mdpi", 48),
    ("hdpi", 72),
    ("xhdpi", 96),
    ("xxhdpi", 144),
    ("xxxhdpi", 192),
)


def main() -> None:
    if not RES_DIR.is_dir():
        raise SystemExit(
            f"{RES_DIR} does not exist -- run `npx cap add android` in mobile/ first."
        )

    for suffix, size in DENSITIES:
        out = RES_DIR / f"mipmap-{suffix}"
        out.mkdir(parents=True, exist_ok=True)

        square = render(size, span=MARK_ANY, rounded=True)
        square.save(out / "ic_launcher.png", "PNG", optimize=True)

        # `rounded=False` gives a full-bleed tile; the circular mask is applied
        # after, so the gradient runs edge to edge under it exactly as it does
        # in the square.
        circle = render(size, span=MARK_ANY, rounded=False)
        circle.putalpha(_circle_mask(size))
        circle.save(out / "ic_launcher_round.png", "PNG", optimize=True)

        print(f"wrote mipmap-{suffix}/ic_launcher[_round].png ({size}x{size})")


def _circle_mask(size: int):
    from PIL import Image, ImageDraw

    # Drawn 4x and reduced, for the same reason the icons themselves are:
    # Pillow's ellipse has no anti-aliasing of its own.
    scale = 4
    mask = Image.new("L", (size * scale, size * scale), 0)
    ImageDraw.Draw(mask).ellipse((0, 0, size * scale - 1, size * scale - 1), fill=255)
    return mask.resize((size, size), Image.LANCZOS)


if __name__ == "__main__":
    main()
