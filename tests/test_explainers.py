"""The in-place explainer is a contract between three files.

``app/explainers.py`` holds every word of the glossary, the templates name an
entry by its key, and each ``Jinja2Templates`` environment has to have had
``register_explainer_filters`` called on it. Two of those three joins fail
silently in the ways that matter:

- a **key** a template names but the registry does not hold is a render error,
  which is loud -- but only on the page that carries it, and only once
  somebody opens it. Walking the templates finds it at test time instead.
- a **registration** somebody forgot is the real hazard. The app already has
  four of these contracts (icons, avatars, groups, notifications) and CLAUDE.md
  warns about forgetting them; this one is chrome that appears on nearly every
  screen, so instead of leaving it to be discovered at render time, every view
  router's environment is checked here.

The third direction -- every registry entry is used by some template -- is
deliberately *not* asserted: an entry written ahead of the screen that will
carry it is not a bug, and a rule that forbids it only teaches people to
delete text rather than place it.
"""

from __future__ import annotations

import importlib
import re
from pathlib import Path

import pytest

from app.explainers import EXPLAINERS, explain

ROOT = Path(__file__).resolve().parent.parent
TEMPLATES = ROOT / "app" / "templates"

#: `{{ explain("key") }}` as the templates actually write it, either quote.
EXPLAIN_CALL = re.compile(r"""explain\(\s*['"]([\w-]+)['"]\s*\)""")

#: Every module that builds a Jinja2Templates environment. Listed rather than
#: discovered, so *adding* a view router without registering the global fails
#: at the same moment as forgetting the call in one that already exists.
TEMPLATE_MODULES = [
    "app.main",
    "app.routers.views.admin",
    "app.routers.views.auth",
    "app.routers.views.challenge",
    "app.routers.views.group",
    "app.routers.views.home",
    "app.routers.views.notification",
    "app.routers.views.roadmap",
    "app.routers.views.settings",
    "app.routers.views.today",
    "app.routers.views.user",
]


def template_files() -> list[Path]:
    return sorted(TEMPLATES.rglob("*.html"))


def keys_used() -> dict[str, list[str]]:
    """Every key named by a template, mapped to the templates naming it."""
    used: dict[str, list[str]] = {}
    for path in template_files():
        source = path.read_text(encoding="utf-8")
        for key in EXPLAIN_CALL.findall(source):
            used.setdefault(key, []).append(str(path.relative_to(ROOT)))
    return used


def test_every_key_a_template_names_exists() -> None:
    """A typo'd key is a 500 on that page; find it here instead."""
    unknown = {k: v for k, v in keys_used().items() if k not in EXPLAINERS}
    assert not unknown, f"templates name explainer keys that do not exist: {unknown}"


def test_the_explainer_is_actually_used() -> None:
    """The registry is only worth its weight if the screens carry the dots."""
    assert len(keys_used()) >= 10


@pytest.mark.parametrize("module_name", TEMPLATE_MODULES)
def test_every_template_environment_can_explain(module_name: str) -> None:
    """The registration contract, checked rather than trusted."""
    module = importlib.import_module(module_name)
    env = module.templates.env
    assert "explain" in env.globals, (
        f"{module_name} builds a Jinja2Templates environment without calling "
        "register_explainer_filters(templates.env)"
    )


def test_an_unknown_key_fails_loudly() -> None:
    """Failing closed is what makes the two tests above worth writing."""
    with pytest.raises(KeyError):
        explain("no-such-entry")


def test_entries_are_escaped_into_attributes() -> None:
    """The whole explanation travels in the markup, so it has to survive it.

    Farsi quotation marks («») are fine, but an entry is ordinary prose that
    may one day contain a straight quote — which would end the attribute and
    silently truncate the explanation into a broken tag.
    """
    for key in EXPLAINERS:
        markup = str(explain(key))
        assert markup.count('data-explain-title="') == 1
        assert markup.count('data-explain-body="') == 1
        # every attribute closes exactly where it should
        assert markup.endswith("</button>")
        assert '"' not in markup.split('data-explain-body="', 1)[1].split('"', 1)[0]


def test_points_render_as_one_line_each() -> None:
    """The renderer splits on the newline, so points may not contain one."""
    for key, item in EXPLAINERS.items():
        for point in item.points:
            assert "\n" not in point, f"{key}: a point may not span lines"
            assert " — " in point, (
                f"{key}: a point reads '<name> — <meaning>'; the dash is what "
                "lets the name be emphasised without a second field"
            )
