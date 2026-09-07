"""Regenerate the PWA's launcher icons into `app/static/img/`.

    python -m app.scripts.generate_app_icons

Same trade the avatars and the emoji catalogue make: the artwork is
**generated once and committed**, never fetched or drawn at render time, so
the app works inside the Docker image with no outbound network. Regenerating
means running this and committing the PNGs it writes.

The mark is «actpact»'s own logo -- an A whose crossbar and stem make the t,
with the play triangle in its lower quadrant -- and its *form* is fixed
artwork: everything this file is allowed to decide is colour and ground.

It is drawn in the pastel pair on a deep sage roundel rather than in the app's
own cream, and that is the one deliberate choice here. A launcher icon sits on
somebody else's wallpaper: a cream tile disappears into half the wallpapers
there are, while the deep ground makes the two hues the logo is actually made
of read at 48dp. The same pair paints `.brand-mark` in styles.css and the
vectors in `mobile/android/.../res`, so the home-screen icon, the launch
screen and the sign-in card are one picture. The values are copied here on
purpose -- a PNG cannot read a CSS variable, and an icon that changed with the
member's theme would be a different app on their home screen every week.

Three shapes ship, and the difference between them is only the safe area:

* ``icon-192`` / ``icon-512`` (`purpose: "any"`) are a rounded square, since
  nothing masks them and a bare square reads as an unfinished icon.
* ``icon-maskable-512`` (`purpose: "maskable"`) is full-bleed with the mark
  pulled well inside: Android crops these to whatever shape the launcher
  wants, and a mark drawn at the "any" size loses its corners to a circle.
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

#: The roundel, and the logo's two halves. The same five values live in the
#: `--brand-*` tokens in styles.css and in the shell's colors.xml.
TILE_TOP = (36, 68, 59)  # --brand-tile-1  #24443b
TILE_BOTTOM = (21, 46, 40)  # --brand-tile-2  #152e28
COOL = (159, 217, 195)  # --brand-cool    #9fd9c3
WARM = (240, 160, 106)  # --brand-warm    #f0a06a

SUPERSAMPLE = 4

#: Fraction of the icon's width the logo's own 200-unit box spans. The
#: maskable one is smaller because Android may crop away everything outside
#: the middle 80%.
MARK_ANY = 0.78
MARK_MASKABLE = 0.62

#: Corner radius of the "any" icon, as a fraction of its width.
CORNER = 0.22


def _ground(size: int, radius: float) -> Image.Image:
    """The roundel: a vertical gradient, optionally rounded off."""
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    gradient = Image.new("RGBA", (1, size))
    for y in range(size):
        t = y / max(size - 1, 1)
        gradient.putpixel(
            (0, y),
            tuple(round(a + (b - a) * t) for a, b in zip(TILE_TOP, TILE_BOTTOM))
            + (255,),
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


#: The logo, verbatim from the SVG: four round-capped strokes and the play
#: triangle, in its own 200-unit box. Nothing below may edit these numbers --
#: the form is the one thing about this mark that is not ours to move.
STROKES: tuple[tuple[tuple[float, float], tuple[float, float], float, bool], ...] = (
    ((34, 162), (100, 34), 22, False),  # the A's left leg
    ((166, 162), (100, 34), 22, True),  # its right leg, the warm half
    ((66, 120), (134, 120), 20, False),  # the crossbar
    ((100, 120), (100, 164), 20, False),  # the stem
)
PLAY: tuple[tuple[float, float], ...] = ((108, 124), (140, 144), (108, 164))


def _mark(img: Image.Image, span: float) -> None:
    """Draw the logo centred in ``img``, spanning ``span`` of its width."""
    size = img.size[0]
    draw = ImageDraw.Draw(img)
    unit = size * span / 200.0
    origin = (size - 200 * unit) / 2.0

    def at(x: float, y: float) -> tuple[float, float]:
        return (origin + x * unit, origin + y * unit)

    for start, end, width, warm in STROKES:
        colour = (WARM if warm else COOL) + (255,)
        stroke = max(round(width * unit), 1)
        points = [at(*start), at(*end)]
        draw.line(points, fill=colour, width=stroke)
        # `stroke-linecap="round"`, which Pillow's line() does not have: the
        # two ends are dotted in by hand.
        for x, y in points:
            r = stroke / 2
            draw.ellipse((x - r, y - r, x + r, y + r), fill=colour)

    draw.polygon([at(*p) for p in PLAY], fill=WARM + (255,))


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
