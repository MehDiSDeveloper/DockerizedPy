"""Cut a release: one number, changed everywhere it is written.

    python -m app.scripts.bump_version patch      # 1.0.0 -> 1.0.1
    python -m app.scripts.bump_version minor      # 1.0.1 -> 1.1.0
    python -m app.scripts.bump_version major      # 1.1.0 -> 2.0.0
    python -m app.scripts.bump_version --set 1.4.0
    python -m app.scripts.bump_version patch --dry-run

The version lives in ``app/version.py`` and nowhere else by hand. This
script is what carries it to the three other places that cannot import it:

* ``mobile/app.config.json`` -- ``versionName``, plus ``versionCode`` + 1.
  Google's and Bazaar's stores order builds by that integer alone and it may
  never go backwards, so it is counted here rather than derived from the
  three parts.
* ``app/static/js/sw.js`` -- ``CACHE_VERSION``. Retiring the old cache is
  what makes a deploy's CSS and JS actually arrive; tying it to the release
  means the discipline is the release rather than somebody remembering.
* ``CHANGELOG.md`` -- everything under ``## [Unreleased]`` becomes the new
  version's section, dated today, and a fresh empty ``Unreleased`` is put
  back on top.

It writes files and stops there: committing and tagging are the operator's,
so a bump can be read before it is published. The commands to do that are
printed at the end.
"""

import argparse
import json
import re
import sys
from datetime import UTC, datetime
from pathlib import Path

from app.version import __version__ as CURRENT

ROOT = Path(__file__).resolve().parents[2]
VERSION_PY = ROOT / "app" / "version.py"
APP_CONFIG = ROOT / "mobile" / "app.config.json"
SERVICE_WORKER = ROOT / "app" / "static" / "js" / "sw.js"
CHANGELOG = ROOT / "CHANGELOG.md"

PARTS = ("major", "minor", "patch")
SEMVER = re.compile(r"^\d+\.\d+\.\d+$")


def next_version(current: str, part: str) -> str:
    major, minor, patch = (int(n) for n in current.split("."))
    if part == "major":
        return f"{major + 1}.0.0"
    if part == "minor":
        return f"{major}.{minor + 1}.0"
    return f"{major}.{minor}.{patch + 1}"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _write(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8", newline="\n")


def bump_version_py(new: str) -> str:
    text = _read(VERSION_PY)
    updated, n = re.subn(
        r'^__version__ = "[^"]+"$', f'__version__ = "{new}"', text, flags=re.MULTILINE
    )
    if n != 1:
        sys.exit("app/version.py: could not find the __version__ line.")
    return updated


def bump_app_config(new: str) -> tuple[str, int]:
    data = json.loads(_read(APP_CONFIG))
    data["versionName"] = new
    data["versionCode"] = int(data["versionCode"]) + 1
    return json.dumps(data, ensure_ascii=False, indent=2) + "\n", data["versionCode"]


def bump_service_worker(new: str) -> str:
    text = _read(SERVICE_WORKER)
    updated, n = re.subn(
        r'const CACHE_VERSION = "[^"]+";',
        f'const CACHE_VERSION = "v{new}";',
        text,
        count=1,
    )
    if n != 1:
        sys.exit("sw.js: could not find the CACHE_VERSION line.")
    return updated


def bump_changelog(new: str, allow_empty: bool) -> str:
    """Promote ``## [Unreleased]`` to ``## [<new>] - <today>``.

    The body between that heading and the next ``## `` is what is being
    released. An empty one is refused: a version with nothing written under
    it is a release nobody can be told what is in.
    """
    text = _read(CHANGELOG)
    match = re.search(r"^## \[Unreleased\][^\n]*\n", text, flags=re.MULTILINE)
    if not match:
        sys.exit("CHANGELOG.md: no '## [Unreleased]' heading to promote.")
    body_start = match.end()
    nxt = re.search(r"^## ", text[body_start:], flags=re.MULTILINE)
    body_end = body_start + (nxt.start() if nxt else len(text) - body_start)
    body = text[body_start:body_end]
    if not body.strip() and not allow_empty:
        sys.exit(
            "CHANGELOG.md: nothing under '## [Unreleased]'. Write what this "
            "release contains first, or pass --allow-empty."
        )
    heading = f"## [{new}] — {datetime.now(UTC).date().isoformat()}\n"
    return (
        text[: match.start()]
        + "## [Unreleased]\n\n"
        + heading
        + (body if body.strip() else "\n")
        + text[body_end:]
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Cut a release version.")
    parser.add_argument("part", nargs="?", choices=PARTS, help="which part to bump")
    parser.add_argument("--set", dest="exact", help="set an exact X.Y.Z instead")
    parser.add_argument("--dry-run", action="store_true", help="print, write nothing")
    parser.add_argument(
        "--allow-empty",
        action="store_true",
        help="release even with an empty Unreleased section",
    )
    args = parser.parse_args()

    if args.exact:
        if not SEMVER.match(args.exact):
            sys.exit(f"--set expects X.Y.Z, got {args.exact!r}.")
        new = args.exact
    elif args.part:
        new = next_version(CURRENT, args.part)
    else:
        parser.error("give a part (major/minor/patch) or --set X.Y.Z")

    files = {
        VERSION_PY: bump_version_py(new),
        SERVICE_WORKER: bump_service_worker(new),
        CHANGELOG: bump_changelog(new, args.allow_empty),
    }
    config_text, version_code = bump_app_config(new)
    files[APP_CONFIG] = config_text

    print(f"{CURRENT} -> {new}  (APK versionCode {version_code})")
    for path in files:
        print(f"  {'would write' if args.dry_run else 'wrote'} {path.relative_to(ROOT)}")
    if args.dry_run:
        return
    for path, text in files.items():
        _write(path, text)
    print("\nNext:")
    print(f'  git commit -am "release {new}"')
    print(f"  git tag v{new}")


if __name__ == "__main__":
    main()
