"""Regenerate the PWA's launcher icons into `app/static/img/`.

    python -m app.scripts.generate_app_icons

Same trade the avatars and the emoji catalogue make: the artwork is
**generated once and committed**, never fetched or drawn at render time, so
the app works inside the Docker image with no outbound network. Regenerating
means running this and committing the PNGs it writes.

The mark is the app's own `.brand-mark` -- a check inside a ring -- drawn on
the sage of «شن و مریم‌گلی» rather than in it, because a launcher icon sits on
somebody else's wallpaper and a translucent glass pane has nothing to sit on.
The colours are the light theme's `--fayrouzeh` / `--bg-0`, copied here on
purpose: a PNG cannot read a CSS variable, and an icon that changed with the
member's theme would be a different app on their home screen every week.

Three shapes ship, and the difference between them is only the safe area:

* ``icon-192`` / ``icon-512`` (`purpose: "any"`) are a rounded square, since
  nothing masks them and a bare square reads as an unfinished icon.
* ``icon-maskable-512`` (`purpose: "maskable"`) is full-bleed with the mark
  pulled well inside: Android crops these to whatever shape the launcher
  wants, and a mark drawn at the "any" size loses its ring to a circle mask.
* ``apple-touch-icon`` is full-bleed too -- iOS applies its own squircle --
  and exists because Safari does not read the manifest.

Everything is drawn at ``SUPERSAMPLE``x and reduced with LANCZOS: Pillow has
no anti-aliased vector renderer, so the smoothing has to come from the
downscale.
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

from app.config import BASE_DIR

OUT_DIR: Path = BASE_DIR / "static" / "img"

#: The light theme's `--fayrouzeh` / `--fayrouzeh-dim` / `--bg-0`.
SAGE = (70, 128, 111)
SAGE_DIM = (53, 101, 90)
CREAM = (248, 244, 238)

SUPERSAMPLE = 4

#: Fraction of the icon's width the mark's ring spans. The maskable one is
#: smaller because Android may crop away everything outside the middle 80%.
MARK_ANY = 0.60
MARK_MASKABLE = 0.46

#: Corner radius of the "any" icon, as a fraction of its width.
CORNER = 0.22


def _ground(size: int, radius: float) -> Image.Image:
    """The sage tile: a vertical gradient, optionally rounded off."""
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    gradient = Image.new("RGBA", (1, size))
    for y in range(size):
        t = y / max(size - 1, 1)
        gradient.putpixel(
            (0, y),
            tuple(round(a + (b - a) * t) for a, b in zip(SAGE, SAGE_DIM)) + (255,),
        )
    gradient = gradient.resize((size, size))

    mask = Image.new("L", (size, size), 0)
    draw = ImageDraw.Draw(mask)
    if radius:
        draw.rounded_rectangle((0, 0, size - 1, size - 1), radius=radius, fill=255)
    else:
        draw.rectangle((0, 0, size - 1, size - 1), fill=255)
    img.paste(gradient, (0, 0), mask)
    return img


def _mark(img: Image.Image, span: float) -> None:
    """The `.brand-mark` glyph, in the proportions of its 24-unit viewBox.

    The source is ``<circle r="9"/>`` plus ``<path d="M9 12l2 2 4-4"/>`` at
    ``stroke-width: 2.4``; every number below is that path divided by 24, so
    the launcher icon and the mark on the sign-in card are the same drawing.
    """
    size = img.size[0]
    draw = ImageDraw.Draw(img)
    unit = size * span / 18.0  # the ring is r=9, i.e. 18 units across
    cx = cy = size / 2
    stroke = max(round(2.4 * unit), 1)

    def at(x: float, y: float) -> tuple[float, float]:
        return (cx + (x - 12) * unit, cy + (y - 12) * unit)

    r = 9 * unit
    draw.ellipse(
        (cx - r, cy - r, cx + r, cy + r), outline=CREAM + (255,), width=stroke
    )
    # Round caps and joins: Pillow's line() has neither, so the joint and the
    # two ends are dotted in by hand.
    points = [at(9, 12), at(11, 14), at(15, 10)]
    draw.line(points, fill=CREAM + (255,), width=stroke, joint="curve")
    for x, y in points:
        draw.ellipse(
            (x - stroke / 2, y - stroke / 2, x + stroke / 2, y + stroke / 2),
            fill=CREAM + (255,),
        )


def render(size: int, *, span: float, rounded: bool) -> Image.Image:
    big = size * SUPERSAMPLE
    img = _ground(big, big * CORNER if rounded else 0)
    _mark(img, span)
    return img.resize((size, size), Image.LANCZOS)


ICONS: tuple[tuple[str, int, float, bool], ...] = (
    ("icon-192.png", 192, MARK_ANY, True),
    ("icon-512.png", 512, MARK_ANY, True),
    ("icon-maskable-512.png", 512, MARK_MASKABLE, False),
    ("apple-touch-icon.png", 180, MARK_ANY, False),
)


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for name, size, span, rounded in ICONS:
        path = OUT_DIR / name
        render(size, span=span, rounded=rounded).save(path, "PNG", optimize=True)
        print(f"wrote {path.relative_to(BASE_DIR.parent)} ({size}x{size})")


if __name__ == "__main__":
    main()
