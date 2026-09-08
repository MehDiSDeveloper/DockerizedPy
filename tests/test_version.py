"""One version, four files.

`app/version.py` is the source; `app.scripts.bump_version` carries it to the
APK config, the service worker and the changelog. Nothing at runtime reads
those three, so a hand-edit that touches one and not the others fails
silently -- as a store rejecting a build, a member pinned to last release's
CSS, or a changelog whose top entry is not what is deployed. This is the
test that makes that noisy instead.
"""

import json
import re
from pathlib import Path

from app.version import __version__

ROOT = Path(__file__).resolve().parents[1]

SEMVER = re.compile(r"^\d+\.\d+\.\d+$")


def test_version_is_semver():
    assert SEMVER.match(__version__), f"{__version__!r} is not X.Y.Z"


def test_apk_version_name_matches():
    config = json.loads((ROOT / "mobile" / "app.config.json").read_text("utf-8"))
    assert config["versionName"] == __version__
    # Stores order builds by this integer alone and it may never go
    # backwards, so it is counted rather than derived -- but it must exist
    # and be a positive int.
    assert isinstance(config["versionCode"], int) and config["versionCode"] > 0


def test_service_worker_cache_is_this_release():
    """A release that reused the last one's cache key serves its CSS."""
    sw = (ROOT / "app" / "static" / "js" / "sw.js").read_text("utf-8")
    match = re.search(r'const CACHE_VERSION = "([^"]+)";', sw)
    assert match, "sw.js has no CACHE_VERSION line"
    assert match.group(1) == f"v{__version__}"


def test_changelog_names_this_version_first():
    """The topmost released heading is what is running.

    `## [Unreleased]` is allowed above it and is where work in progress is
    written; the first *numbered* heading has to be this version.
    """
    text = (ROOT / "CHANGELOG.md").read_text("utf-8")
    headings = re.findall(r"^## \[(\d+\.\d+\.\d+)\]", text, flags=re.MULTILINE)
    assert headings, "CHANGELOG.md has no released version heading"
    assert headings[0] == __version__
