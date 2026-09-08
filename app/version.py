"""The one place this app states which version it is.

Every other version string in the repository is *derived* from this one by
``python -m app.scripts.bump_version`` -- ``mobile/app.config.json`` (the
APK's ``versionName``), the service worker's ``CACHE_VERSION``, and the
released heading at the top of ``CHANGELOG.md``. Nothing is typed twice, so
the deploy, the APK and the changelog cannot disagree about what shipped;
``tests/test_version.py`` is what keeps that promise honest.

The scheme is SemVer read as a *product* rather than as a library, because
nobody imports this app:

    MAJOR  a release the whole product is renamed by -- a migration that
           cannot be rolled back, an API or URL contract broken, a rewrite
           somebody has to be told about.
    MINOR  a feature. A new screen, a new notification kind, a new column
           somebody can see. This is the common one.
    PATCH  a fix, a copy change, a style tweak, a refactor with no visible
           consequence.

The number is bumped once per *release* (a deploy, an APK upload), never
once per commit: a version nobody shipped is a version nobody can be told
to check. Between releases, work is written into ``CHANGELOG.md`` under
``## [Unreleased]``, which is what the bump then promotes.
"""

__version__ = "1.0.2"
