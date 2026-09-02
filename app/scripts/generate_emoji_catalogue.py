"""Regenerate `app/static/js/emoji.js` from Unicode's own emoji-test.txt.

The picker is a *keyboard*, not a catalogue the server knows about: since
an emoji goes into the comment's text, nothing in Python ever has to name
one. So the set lives in a static JS file the browser caches once, and this
script is how that file is rebuilt -- the same trade the avatars make
(generated once, committed, never fetched at render time), because the app
must work inside the Docker image with no outbound network.

    python -m app.scripts.generate_emoji_catalogue

Two filters decide what ships:

* **fully-qualified only**, minus the `Component` group -- the qualified
  form is the one a font is guaranteed to draw.
* **skin-tone variants are dropped** and `MAX_EMOJI_VERSION` caps how new an
  emoji may be. A device whose font predates an emoji draws an empty box,
  and a keyboard full of boxes is worse than a shorter keyboard.
"""

from __future__ import annotations

import re
import sys
import urllib.request
from pathlib import Path

SOURCE = "https://unicode.org/Public/emoji/16.0/emoji-test.txt"
OUT = Path(__file__).resolve().parents[1] / "static" / "js" / "emoji.js"

#: Anything newer than this is left out -- see the module docstring.
MAX_EMOJI_VERSION = 15.0

#: Unicode group -> (tab id, Farsi label, icon name in `app.js`'s set).
#: «Smileys & Emotion» and «People & Body» share one tab, the way every
#: platform keyboard merges them: nobody looks for 👍 under a second tab.
GROUPS: dict[str, tuple[str, str, str]] = {
    "Smileys & Emotion": ("faces", "احساس و آدم‌ها", "emSmiley"),
    "People & Body": ("faces", "احساس و آدم‌ها", "emSmiley"),
    "Animals & Nature": ("nature", "حیوان و طبیعت", "emPaw"),
    "Food & Drink": ("food", "خوراکی", "emFood"),
    "Activities": ("activity", "سرگرمی", "emBall"),
    "Travel & Places": ("travel", "سفر و مکان", "emCar"),
    "Objects": ("objects", "اشیا", "emBulb"),
    "Symbols": ("symbols", "نمادها", "emSymbol"),
    "Flags": ("flags", "پرچم‌ها", "emFlag"),
}

SKIN_TONES = {0x1F3FB, 0x1F3FC, 0x1F3FD, 0x1F3FE, 0x1F3FF}
LINE = re.compile(
    r"^(?P<cps>[0-9A-F ]+);\s*(?P<status>[a-z-]+)\s*#\s*(?P<char>\S+)\s+E(?P<ver>[\d.]+)"
)


def build() -> str:
    with urllib.request.urlopen(SOURCE) as response:
        text = response.read().decode("utf-8")

    order: list[str] = []
    tabs: dict[str, dict] = {}
    group = ""
    for line in text.splitlines():
        if line.startswith("# group:"):
            group = line.split(":", 1)[1].strip()
            continue
        match = LINE.match(line)
        if not match or match["status"] != "fully-qualified":
            continue
        if group not in GROUPS:
            continue
        if float(match["ver"]) > MAX_EMOJI_VERSION:
            continue
        codepoints = [int(cp, 16) for cp in match["cps"].split()]
        if SKIN_TONES.intersection(codepoints):
            continue
        tab_id, label, icon = GROUPS[group]
        if tab_id not in tabs:
            tabs[tab_id] = {"label": label, "icon": icon, "emoji": []}
            order.append(tab_id)
        tabs[tab_id]["emoji"].append(match["char"])

    rows = []
    for tab_id in order:
        tab = tabs[tab_id]
        # Space-joined and split at load: ~1800 quoted strings is most of the
        # file's weight, and none of it is anything a reader would read.
        chars = " ".join(tab["emoji"])
        rows.append(
            f'  {{ id: "{tab_id}", label: "{tab["label"]}", '
            f'icon: "{tab["icon"]}", emoji: "{chars}".split(" ") }},'
        )

    total = sum(len(tab["emoji"]) for tab in tabs.values())
    header = f"""// ==========================================================================
// چالش | emoji.js — the emoji keyboard's catalogue
// ==========================================================================
//
// GENERATED FILE — do not edit by hand.
//   python -m app.scripts.generate_emoji_catalogue
//
// Source: {SOURCE} (fully-qualified, up to E{MAX_EMOJI_VERSION:g},
// skin-tone variants dropped) — {total} emoji in {len(order)} groups.
//
// It is a *keyboard*, so nothing here is stored: the picker writes the
// character into the comment's text at the caret, and the server never
// learns an emoji's name. Loaded only by the pages that carry a composer.

window.EMOJI_GROUPS = [
"""
    return header + "\n".join(rows) + "\n];\n"


if __name__ == "__main__":
    OUT.write_text(build(), encoding="utf-8")
    print(f"wrote {OUT} ({OUT.stat().st_size / 1024:.0f} KB)", file=sys.stderr)
