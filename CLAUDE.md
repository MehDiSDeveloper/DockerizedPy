# CLAUDE.md

Guidance for Claude Code (claude.ai/code) working in this repository.

## Project overview

«چالش» / Challenge Manager — a FastAPI app where users create fitness/habit **challenges** (cadence-based: one-time, scheduled, recurring-days, recurring-quota) and **enroll** in each other's, then log progress as **check-ins** against a **Today** feed. Two front doors over one model set: a JSON CRUD API and server-rendered Jinja2 pages (Farsi, RTL). Auth is hand-rolled cookie sessions. Groups let an organisation gather its people and hand out challenges.

## Commands

Everything runs through the venv at `.venv/` (`.venv/Scripts/` on Windows).

```powershell
uvicorn app.main:app --reload          # local dev
docker-compose up                      # app + debugpy on :5678
ruff check .                           # lint (no config file — ruff defaults)
alembic upgrade head                   # only relevant on Postgres
python -m app.scripts.generate_mock_data          # seed 10 users / 30 challenges / 60 enrollments
python -m app.scripts.set_user_role EMAIL admin   # make the first admin (--list to see roles)
python -m app.scripts.seed_admin                  # ensure the SEED_ADMIN_* account exists
.venv\Scripts\python -m pytest tests\ -q          # in-memory SQLite, no Postgres needed
```

Dependency gotchas:

- **`requirements.txt` is UTF-16 encoded.** Writing it with a plain-text tool corrupts it — use `pip freeze > requirements.txt` and verify the BOM after editing.
- It pins `starlette<1.0.0`. The venv can drift ahead of that pin, which breaks **every** `TemplateResponse` call with a confusing signature error. If all HTML pages suddenly 500, check `pip show starlette` first.
- `generate_mock_data.py` imports `faker`, which is in the local venv but **missing from `requirements.txt`**, so the seed script fails inside the Docker image.

Debugging in Docker: `.vscode/launch.json` → "Docker: Attach to FastAPI" (`localhost:5678`, `${workspaceFolder}` → `/app`). The compose `command` already wraps uvicorn in debugpy, so leave `debugpy.listen(...)` commented in `app/main.py`.

## Testing

`tests/conftest.py` spins up in-memory SQLite (`sqlite+aiosqlite:///:memory:`), creates the schema with `Base.metadata.create_all`, overrides `get_db`, and drives the real app through `httpx.AsyncClient` (`ASGITransport`). `pytest.ini` sets `asyncio_mode = auto`.

This only works because `challenge_type`, `recurrence_pattern` and `Enrollments.status` are plain `VARCHAR` — SQLite cannot represent a PG enum, so an enum-shaped test failure means a native PG enum crept back onto one of those columns.

**Don't test everything either.** A test earns its place by pinning a rule that could silently regress. Skip UI and wording changes unless the behaviour itself matters.

**Don't run the suite for every small edit.** Run tests when a change touches a rule documented here (occurrences, auth/visibility, cadence keys, roles) or when something plausibly broke — not after a copy tweak, a style change, or a one-line template edit. Prefer the one relevant test file over the whole suite.

Test files are named after what they pin; read the relevant one before changing a rule below. Nearly every invariant documented here has a test: occurrences, jalali, timezone boundaries, check-in idempotency, challenge lifecycle/status, page & object authorization, roles, admin panel/challenges/participants/profile, home dashboard, list ordering, notifications, anonymity, leaderboard, OTP auth, explainers, tour anchors, the group subsystem (groups, invites, challenges, visibility, privacy, notifications, requests, leaving), and the roadmap subsystem (step gating, all six completion rules, the two refusals, the archive skip, the structural lock, both scope filters, invite capacity under concurrency).

## Configuration

`app/config.py` (`pydantic-settings`) reads `database_url`, `environment`, `secret_key` from `.env`. Two env files: `.env` (local, gitignored) and `.env.docker` (used by `docker-compose.yml`); `.env.example` documents the shape. `secret_key` signs session cookies. `environment != "development"` flips the session cookie to `secure=True`.

**Every environment runs on SQLite — local, compose, and the Liara deploy — on purpose.** They used to differ (Postgres locally, SQLite on Liara's disk), so a Postgres-only migration reached production as `no such column: Users.avatar`. `DEFAULT_SQLITE_URL` is `sqlite+aiosqlite:///./data/challenges.db`; `data/` is the Liara mount point and `WORKDIR` is `/app`. `settings.is_sqlite` / `settings.sqlite_path` are the accessors (the latter goes through `make_url`, since `sqlite:///rel` vs `sqlite:////abs` differ by one slash); importing `config` creates the parent directory.

**Postgres stays wired up**: the `alembic/` history, the `asyncpg` pin, and a commented `DATABASE_URL` in each env file. Switching back is uncommenting that line and running `alembic upgrade head`. `alembic.ini`'s `sqlalchemy.url` is dead — `alembic/env.py` overwrites it with `settings.database_url`.

**Schema changes go to the models, and nothing else.** `app/scripts/bootstrap_db.py` (run by `start.sh`) branches on backend: Postgres → `alembic upgrade head`; SQLite → `create_all` then `sync_sqlite_schema()`, which diffs each existing table against `Base.metadata` and issues `ALTER TABLE ADD COLUMN` / `CREATE INDEX`. `create_all` alone skips existing tables — that is how the avatar column shipped without arriving. The sync cannot *change* a column (SQLite has no real `ALTER COLUMN`) or add a NOT NULL column without a default to a non-empty table; the second case prints a loud warning naming the column rather than failing the boot.

**The operator account is seeded on every boot.** A deploy's database is a different database and the first admin cannot be made through the app, so `start.sh` runs `app/scripts/seed_admin.py`: creates `SEED_ADMIN_EMAIL`/`SEED_ADMIN_PASSWORD` with `role=admin` if unknown, promotes an existing member, does nothing if already admin, does nothing at all when `SEED_ADMIN_EMAIL` is empty (the default). **It never rewrites an existing account's password** — otherwise the env var is a way to take over any account whose email is put in it. `SEED_ADMIN_RESET_PASSWORD=true` forces it for one boot (the locked-out escape hatch). Both env files are gitignored *and* in `.dockerignore`; the deploy reads these from Liara's environment.

## Architecture

### Two router layers over one model set

`app/routers/` is the JSON API (`/challenges`, `/users`, `/enrollments`, `/groups`, `/invites`, `/roadmaps`, `/roadmap-invites`, `/auth`, `/checkins`, `/today`). `app/routers/views/` is the SSR layer (`/views/...`). Both mounted in `app/main.py`, both hitting the same models. `/` redirects to `/views/today/`.

They are **not** independent: query/visibility logic belongs in the API router and is imported by the view (`views/challenge.py` imports `challenge_visibility_filter`, `fetch_challenge_page` and the page-size constants from `routers/challenge.py`). Only presentation lives in `views/`.

`app/routers/group` is a **package** for size alone: `queries.py` holds shared queries with no routes, `members.py`/`invites.py`/`participants.py` hold handlers, and `__init__.py` re-exports every name imported from `app.routers.group` elsewhere.

Challenge creation goes through one `create_challenge_record` in `routers/challenge.py` (it does everything short of the commit; each caller commits and shapes its own response). The two copies had already drifted — the SSR half omitted `is_anonymous=False`, so an anonymous challenge could hide its own author. Some create/auth logic is still duplicated across the layers; when changing it, check the parallel handler.

**The layers are not meant to mirror each other.** Only `create` has a `/views/` twin (it answers JSON `{id}` to a `fetch()`); every other mutation is called from page JS against the JSON API. `GET /challenges/{id}/stats`, `GET /enrollments/{id}/history` and `GET /today/` are API-only (the SSR pages compute the same numbers inline). The `/views/*/fragment` routes return card markup for infinite scroll and are deliberately not JSON. What *is* a gap is a JSON endpoint no page can reach.

### Auth (`app/auth.py`)

Hand-rolled — no passlib, no JWT, no session store:

- Passwords: `pbkdf2_sha256$<iterations>$<salt_b64>$<digest_b64>`, 260k iterations, verified with `hmac.compare_digest`.
- Sessions: a stateless cookie `base64(user_id:expires_at).HMAC-SHA256` keyed on `settings.secret_key`, 7-day expiry. Nothing server-side, so **rotating `secret_key` logs everyone out** and there is no revocation.
- Dependencies: `get_current_user_id` (401s), `get_optional_user_id` (`None`), `get_current_user`, `get_page_user`.

`POST /auth/login` and `POST /users/` set the cookie; `POST /auth/logout` clears it. The HTML login page is `/views/auth/`; protected view routes redirect there with `?next=<path>`, and it bounces an already-signed-in visitor straight to `next`.

### Page auth vs API auth — the 303/401 split

- **`get_page_user`** is for full-page SSR routes. It raises `LoginRequired` — deliberately not an `HTTPException` — which `app/main.py` turns into a `303` to `/views/auth/?next=<path+query>`, clearing the cookie. Query strings are preserved.
- **`get_current_user_id`** stays on `/views/*/fragment` routes and the whole JSON API, where a real `401` is load-bearing: `createInfiniteScroller()` reads that status to redirect, and a `fetch()` would silently follow a `303` and append the login page as cards.

`get_page_user` returns the **`User` row**, not the id: a cookie signed for a since-deleted account still verifies, so the row lookup is the only thing that fails it closed.

**Gated pages** (`tests/test_page_authorization.py` is the list): `/views/today/`, `/views/home/`, `/views/challenges/create`, `/views/users/{id}`. Challenge **discovery stays open** — `/views/challenges/` and `/views/challenges/{id}` run on `challenge_visibility_filter(None)`, which already narrows an anonymous visitor to public challenges.

### Ownership and visibility — the rule to preserve

Routers derive the acting user from the session, **never** from a path/query/body param. Mutating a challenge or user requires ownership (403 otherwise). Enrollment routes are keyed on `challenge_id` + the session user; there is no client-supplied `user_id` in `routers/enrollment.py`.

**Object-level access is a composed SQL predicate, not a post-load `if`.** There are four such filters and no permissions framework: `challenge_visibility_filter` (`routers/challenge.py`) — visible if public, owned, enrolled, or a step of a roadmap you are walking; `profile_visibility_filter` (`routers/user.py`); `group_visibility_filter` (`app/groups.py`); `roadmap_visibility_filter` (`app/roadmaps.py`). Each is `.where()`-ed in so a miss falls out as "no such row", and each is shared by both front doors. Loosening a rule means editing one function.

**Profiles are own-only for members, reachable by an operator.** `profile_visibility_filter(viewer)` answers `true()` for a holder of `Perm.USER_VIEW_ANY` and `User.id == viewer.id` otherwise. Both `GET /views/users/{id}` and `GET /users/{id}` compose it. Nothing links a member to another profile and ids are sequential, so a miss is a **404** on both doors. The admin read still answers `UserPublicRead`; the wider `UserAdminRead` is confined to the `Perm.USER_LIST` roster route.

**403 vs 404.** Where the caller could already see the resource, 403 is honest. Where a 403 would answer the only question an enumerator is asking, it is a 404:

| Route | Not yours | Why |
|---|---|---|
| `/views/users/{id}`, `GET /users/{id}` | **404** | sequential ids, no in-app link to another profile |
| `PATCH`/`DELETE /checkins/{id}` | **404** | a 403 maps every member's logged history. The 403s left on these routes are *backfill-window* refusals about your own row |
| `PATCH`/`DELETE /challenges/{id}` | **404** if invisible, **403** if visible | the mutation selects through `challenge_visibility_filter` too |

Pages fail *authentication* with a 303 and *authorization* with 404/403; `get_page_user` running first keeps a signed-out visitor from getting the forbidden answer.

**`GET /users/` is the admin panel's roster** — the one read that ignores `profile_visibility_filter`, gated on `Perm.USER_LIST`, answering `UserAdminRead`. Paginated and filterable (`q`, `role`, `offset`, `limit`) through `fetch_member_page` / `apply_member_filters`, so the panel and the API cannot disagree about what a search matches.

### Roles — four axes, one `can()`

`app/permissions.py` is the only place a role becomes an answer. Callers ask for a permission, never a role: `can(user, Perm.CHALLENGE_DELETE, challenge=c)`. Policy is the grant maps there, so a new role is a row in a map instead of a sweep for `role == "admin"`. `can()` runs *after* a row is in hand — it does not replace the visibility filters, or the 404-vs-403 split collapses.

Four role sources, **deliberately never merged**:

- **`Users.role`** (`member` | `admin`) — app-wide, "may you run the place". Default `member`.
- **`Enrollments.role`** (`participant` | `owner`) — one challenge. The creator's auto-enrolment is `owner`.
- **`GroupMembership.role`** (`member` | `admin` | `owner`) — one group.
- **`Roadmap.owner_id`** (`owner` | nothing) — one course. The narrowest axis: one role and no membership table of roles to reconcile, and an axis anyway, because an `owner_id ==` comparison spread across a router is precisely what `can()` replaces.

Merging them makes an admin the silent owner of everything. **No app-wide role grants a per-challenge, `GROUP_*` or `ROADMAP_*` permission, and no group role grants a `CHALLENGE_*` one**; `tests/test_user_roles.py` and `tests/test_groups.py` parametrize that over both global roles.

`Challenge.owner_id` / `Group.owner_id` stay the record of ownership; `challenge_role(...)` and `group_role(...)` resolve the column against the membership row — an owner may unenrol from their own challenge, deleting the only row carrying `owner`. The membership handed in is checked to belong to the asker.

**Moderation is not ownership.** Admin grants `CHALLENGE_LIST_ALL`, `CHALLENGE_MODERATE`, `CHALLENGE_DELETE_ANY` and deliberately **not** `CHALLENGE_EDIT`. Changing what state a challenge is in is moderation; changing what it says is authorship. Two mechanics in `routers/challenge.py` enforce it: `reachable_for(user)` is the clause every challenge *mutation* selects through (the visibility filter for everyone, `true()` for `CHALLENGE_LIST_ALL`), and `MODERATABLE_FIELDS = {lifecycle_status, visibility}` is a whole-body rule — a non-owner's PATCH passes only when *every* key is moderatable, so one request that renames and archives is refused entire. Deletion buys nothing extra: the 409 protecting logged history applies to operator and owner alike.

**Support is the one place an admin may edit rather than moderate.** `USER_VIEW_ANY` / `USER_EDIT_ANY` let an operator open and correct any member's profile — the opposite call from `CHALLENGE_EDIT`, because an account's fields are the member's own record rather than authored content. Every such write goes through `update_user`, which stamps `last_modifier_user_id` with the **acting** user. Three things stay out: the **password** (no route sets someone else's; `seed_admin.py` is the reset), the **role** (`PATCH /users/{id}/role`), and **deletion** (`DELETE /users/{id}` is own-only and 409s once history exists).

**Writing a role.** `PATCH /users/{id}/role` (`UserRoleUpdate`, admin-only) is the only door, kept off `UserUpdate` so the profile PATCH can never be a side channel. Self-demotion is a 400. The *first* admin is made with `python -m app.scripts.set_user_role <email> admin`, not by a "first signup wins" rule.

**Migration** is models-only: both role columns carry a default so `sync_sqlite_schema` can add them, and `bootstrap_db.backfill_enrollment_roles` marks each creator's own enrolment `owner` (idempotent, every boot).

### The admin panel

`/views/admin/` + `templates/admin/index.html` is the member roster; it is a page of its own rather than admin-only branches inside member screens. `get_admin_page_user` answers a signed-in member **404**; `get_admin_user` answers a JSON caller **403**.

The roster is searched (name *and* email), role-filtered and lazily paged with the challenge list's machinery: a `.search-bar`, a three-option `.seg`, `createInfiniteScroller()` over `GET /views/admin/fragment` (which `{% include %}`s `admin/_member_rows.html` for page one and re-serves it bare with `X-Has-More`), state mirrored into the query string. Two load-bearing details: the fragment takes **`get_admin_user`**, not the page dependency (a `fetch()` needs a real 401); and a row carries its own `data-member-name` / `data-member-role` with one delegated listener, because a page-level JSON array cannot describe rows arriving on page two. A row whose role no longer matches the active filter is **repainted, never dropped** — the scroller's `offset` counts server-returned rows, so removing one skips a member.

**The profile is the panel's member screen**, not a second one: a roster row is a plain `<a>` into `/views/users/{id}`. That page branches on three flags from `routers/views/user.py`: `is_own_profile` (logout, settings, history), `can_administer` (the banner and the role row; false on your own profile, since the server refuses self-demotion), and `can_edit`, their union — the *only* flag the avatar picker and account sheet read, so an operator's edit is the same `PATCH /users/{id}` a member's is.

**The moderation roster** is `/views/admin/challenges` + `admin/_challenge_rows.html`: the public challenge list's own screen, served with `fetch_challenge_page(..., scope_all=True)` — the one caller allowed to skip `listing_visibility_filter` — with each row actionable. `scope_all` is a parameter on the shared query, not a second query builder.

A row shows the derived **status pill** *and* a hint spelling out the two stored fields moderation writes (lifecycle, visibility), because the derived badge cannot tell a draft from a not-yet-started challenge. The sheet PATCHes only changed fields and every action ends in `scroller.reset()` rather than a hand-patched row — the pill is derived. Delete refuses client-side with the reason when others have joined, matching the server's 409.

The panel index reaches it through a `.setting-row` plus three «یک نگاه» counts that link into the list each counts: «عضو» is an in-page anchor, «چالش» opens the roster, «عضویت» opens it with `?sort=members`.

**«عضویت» is a sort, not a fourth screen** — `fetch_challenge_page(sort=SORT_MEMBERS)` and a two-button `.seg`. The rank is counted live off `Enrollments` (`_member_count()`), **not** `ChallengeStats.participant_count`, which is monotonic and would rank an abandoned challenge above a live one. The roster does not offer `velocity` (a discovery question) and refuses it rather than ignoring it.

**The participant roster is the drill-down, and it is operator-only.** `/views/admin/challenges/{id}/members` (fragment `admin/_participant_rows.html`, query `fetch_participant_page` in `routers/enrollment.py`) is the one screen answering *who is in this* — exactly what `profile_visibility_filter` withholds from a member, which is why challenge-detail renders initials and never a roster. Page 404s a member, fragment 403s a `fetch()`, and there is **no JSON endpoint**. Rows link to profiles (an operator passes that filter) and carry **no actions** — removing someone from a challenge is not a power `permissions.py` grants. A member-facing participant list means loosening that one function, not opening this route. Search reuses `apply_member_filters`.

The way in is the moderation row's «N عضو» line, a real `<a>` in a `.sr-foot`; the row's delegated click handler bails on any `<a>` it contains. That count is *every* enrollment, while `data-challenge-others` is the non-owner count the delete guard uses — two different numbers on purpose.

Two entry points to the panel, both rendered only when `viewer_is_admin` and both pointing at a self-gating route: a `.setting-row` on `/views/settings/`, and a `.menu-item` on the profile. The profile one reads `viewer`, never the profile's own user. Neither is a fifth bottom-nav tab.

### List ordering — one rule, newest first

Every paginated list orders by `newest_first(Model)` in `app/models/audit_base.py` = `(created_at.desc(), id.desc())`. A member and an operator read the same list in the same order.

`created_at` is the key because it is what the ordering *means*, and it is immutable (routers only write `updated_at`), so a scroller cannot re-show a row. **The `id` tie-break is not decoration**: `server_default=func.now()` has second granularity on SQLite, so a seed run writes blocks of rows sharing a timestamp and a page boundary inside such a block drops or repeats one. `?sort=velocity` and `?sort=members` keep their own leading key and fall back to this pair.

**One list reads the other way**: a group's *pending* membership requests are a queue, so `fetch_request_page` orders oldest-first (still falling through to the id tie-break).

### Derived challenge status — the badge and the filter are one rule

A `Challenge` has no start/end column, so the status (`شروع نشده` / `در حال اجرا` / `تمام شده`) is **derived** in `routers/challenge.py` from `lifecycle_status`, `due_date`, and two keys of the cadence JSON:

- **finished** — archived, or `due_date` / `cadence.end_date` past
- **upcoming** — not finished, and either still a draft or `cadence.datetimes[0]` is ahead
- **active** — everything else

It is written twice on purpose: `challenge_status(challenge)` renders a card, `status_filter(status)` is the SQL behind `?status=` (the list pages server-side, so a Python-side filter could not page). **Both are restricted to the same four inputs so a card and the filter can never disagree** — which is why a `schedule` challenge whose sessions have all passed still reads *active* until `due_date` passes: only element `[0]` of `cadence.datetimes` is reachable portably from SQL (SQLite's `json_extract` has no negative index). Adding an input means adding it to both halves.

`?status=` is accepted by `GET /challenges/`, `/views/challenges/` **and its `/fragment`** — or the other statuses leak back in on scroll. Farsi labels and icons live in `STATUS_META` in `views/challenge.py`, reaching templates as the `status_meta` global plus a `challenge_status` filter. Colours are the `--st-*` tokens, shared by the card stripe, its pill and the matching filter button. The pill always spells the status out.

On the list page status and search stay on-screen; category and the "my challenges" scope live in a `createSheet` filter sheet (`type: "chips"`) with a count badge and removable chips, so a filter can never be left on invisibly.

### «ریل» و «لیست» — the challenge list's two shapes

`/views/challenges/` draws the **same cards** two ways, and the shape is the only state there is. `.rail` on `#clList` (the default) makes each card a full-width cover; `.is-immersive` on that same element turns the feed into a fixed, scroll-snapped reader. Everything lives in `challenge/_challenge_cards.html`, the «ریل» section of `styles.css`, and one block at the foot of `challenge-list.html`'s script.

**One markup, not two partials.** A second card template is a second thing to keep in step — and, more importantly, it would make the mode a *server* parameter, so switching would be a refetch. Because it is a class toggle, search, `?status=`, the filter sheet, the query-string mirroring and `createInfiniteScroller` all carry on untouched in both modes, and switching costs nothing and loses no position. `_challenge_cards.html` is rendered by the group router's Jinja env too, which registers a smaller filter set — a filter added here that only `views/challenge.py` registers is a 500 on the group page and a green suite everywhere else (`tests/test_challenge_view_modes.py` pins it).

**The cover is a placeholder with one seam.** There is no image field yet. `.explore-thumb` — the same element the list mode draws as a 52px thumb, painted from the same `[data-cat]` hue — becomes the cover: the category's gradient run deeper, its glyph at cover size, and the app's own `--tile-url` texture. When the field lands, an `{% if c.image_url %}<img …>{% endif %}` **inside that div** is the whole change, and every challenge without a picture keeps exactly what it has now. Colour adds no channel: status keeps its `--st-*` edge and pill, category keeps `--cat-*`.

**The card stays one real `<a>`.** «لیست» is therefore unchanged — the tap navigates and no handler has an opinion. «ریل» preventDefaults and opens the reader instead, with two exceptions the delegated listener names: the heart (which already stops its own event) and `[data-rail-open]`, the «صفحه چالش» action — which needs no handler at all, because *not* preventing the default is what opens the page.

**The reader is the list itself, gone `position:fixed`.** No panels are built, so a card from page two of the infinite scroll behaves exactly like one the page rendered; `.rail-extra` (the cadence in words, the collapsible description) ships inside every card and is `display:none` until then. Three things are load-bearing: `scroll-snap-type:y mandatory` **plus** `scroll-snap-stop:always`, so the reader is never parked between two challenges and a fling cannot skip one; the section's `min-height` is frozen to the list's rendered height before it leaves the flow, or the page behind collapses and the browser clamps away the scroll offset the member came from; and the reader asks `scroller.loadMore()` from its own scroll handler, because the page's sentinel is behind a fixed overlay and can never intersect again. It closes on ✕, Esc and `popstate` — a `pushState` on open is what makes the phone's back gesture close the reader rather than leave the page.

**The choice is remembered per device**, under `localStorage["chalesh-challenge-view"]`, read by a small inline script **immediately after** the container — «ریل» ships in the markup so only a member who chose «لیست» needs correcting, and from `{% block scripts %}` that correction would repaint a frame in. Same trade `layout.html` makes for the theme. The switch is a two-button `.seg` on a row of its own: it changes *how* results are drawn, not which, so it sits below the filters rather than among them, and the status row is already at the width a 360px phone can hold.

### The home dashboard

`/views/home/` is a dashboard over the member's own activity; `build_dashboard` in `views/home.py` is the only place reading a member's *whole* run. Four questions in order: «نبض امروز» (a ring of today's completions plus streak/week/lifetime tiles), «روند فعالیت» (the twelve-week grid), «تمرکز تو» (categories of the last 30 days), then the enrollment list.

Everything is **read live off `CheckIns`**: the only per-member stored numbers are the two streaks (recomputed, never incremented), and `legacy_completed_count` is frozen pre-migration data that must never surface as a live figure. The exception is the remaining-today count, which comes from `get_today_items` so the ring and the امروز tab cannot disagree.

Three load-bearing facts about the grid:

- **Columns are Saturday-aligned weeks** (`week_start`), not rolling 7-day chunks, so each *row* is one weekday — a habit that always breaks on the same day shows as a pale row. Losing the alignment leaves the grid rendering perfectly while meaning nothing.
- **Cells are keyed on the stored `occurrence_local_date`**, never re-derived, so a backfilled occurrence lands on the day it was *for*. Only "which day is today" needs a zone, and that takes `DEFAULT_TIMEZONE`.
- **Levels are capped, not normalised** (`HEATMAP_MAX_LEVEL`), or one heavy day repaints an unchanged past.

The ring reuses the `.mine-card`/`.ring-*` primitive and its legend carries **only the ring's own two slices**; month labels are ISO dates with a `month-only` hint converted by `renderDates()`; «تمرکز تو» rows read `--cat` from their own `data-cat` (with the accent fallback). Unenrolling ends in `window.location.reload()` — it moves the ring, both streaks, the week counter and the tab count at once.

**The «تمام‌شده» bucket is the challenge's derived status, not `Enrollments.status`.** No route ever writes anything but `active` onto that column (only the mock seed does), so a branch on `Enrollment.status == COMPLETED` was always empty. `_fetch_enrollment_page` and the `stat_active`/`stat_done` counts in `home()` (and their twins in `views/user.py`) join `Challenge` and filter with `status_filter(STATUS_FINISHED)`. The admin roster still shows the raw column, which answers a different question.

### Deleting a challenge — archive is the normal exit

`DELETE /challenges/{id}` is for a challenge that should never have existed. It cascades through every enrollment, check-in and the stats row, so it is refused with **409 once any non-owner has enrolled** — the same `count_non_owner_enrollments` threshold that locks `cadence`/`goal_*`/`identity_mode`. The supported exit from a live challenge is `PATCH lifecycle_status="archived"`.

The cascades on `Challenge.enrollments` / `.stats` / `.checkins` are **load-bearing**: every child FK is NOT NULL and `ChallengeStats.challenge_id` is a PK, so without them SQLAlchemy's de-association raises and *every* delete is a 500. `DELETE /users/{id}` does the opposite: no cascades, and it catches the `IntegrityError` to answer 409.

### Models (`app/models/`)

- `AuditBase` is an abstract `Base` subclass adding `created_at` / `updated_at` / `last_modifier_user_id`. Routers set the latter two by hand on every mutation — there is no ORM event hook.
- `Challenge` is a flat table (the old single-table-inheritance split is gone). Recurrence lives in `cadence_kind` (`once` | `schedule` | `recurring_days` | `recurring_quota`) and `cadence` (JSON, shaped by `CadenceUnion`). `visibility` and `lifecycle_status` are plain `String`. `goal_amount`/`goal_unit` describe an optional collective target; a check-in's `amount` is required iff `goal_unit is not None`. `legacy_cadence` is a verbatim JSON snapshot of pre-migration recurrence columns — preserved user data, never read by application code, not scaffolding to clean up.
- `Enrollment` joins `User`↔`Challenge`, unique on `(user_id, challenge_id)`. Per-enrollment `timezone` (IANA, default `Asia/Tehran`), `start_date`, `current_streak`/`longest_streak`, `last_checkin_local_date` feed the occurrence engine. `legacy_completed_count` is read-only and is never converted into `CheckIn` rows.
- `CheckIn` (table `CheckIns`) is one row per recorded occurrence: `enrollment_id` + a denormalized `challenge_id` (so the velocity query needs no join), `occurrence_key`, `state` (`completed`/`skipped` only — `pending`/`missed` are derived at read time), the local date and UTC instant, a `timezone` snapshot, optional `amount`/`unit`/`note`/`photo_url`. `UniqueConstraint(enrollment_id, occurrence_key)` is the idempotency guarantee.
- `ChallengeStats` (PK = `challenge_id`) holds **monotonic counters only**. There is no refresh job — Discover's 7-day velocity is computed live from `CheckIns` via `ix_checkins_challenge_created`.
- `User.avatar` and `Group.emblem` hold an **id from a fixed catalogue**, never a path. `NULL` is permanent and legitimate.
- **Table names are capitalized** (`Users`, `Challenges`, …) and must be quoted in raw SQL on Postgres.
- **`ChallengeCategory` is the one enum keeping Farsi values**; every enum from the cadence migration uses English codes. `ChallengeCategory.FITNESS == "سلامت جسمانی"` travels to the browser — JS payloads and `data-cat` attributes contain the literal Farsi, so renaming a *value* is a data migration plus template/JS changes. `Visibility`, `LifecycleStatus`, `CadenceKind`, `EnrollmentStatus` are English codes with no shared Farsi display map; each template/script inlines its own label (D7 — the per-surface pattern).

### Schemas (`app/schemas/`)

`schemas/challenge.py` is flat — `ChallengeCreate`/`Read`/`Update` each carry one `cadence: CadenceUnion`. The discriminated union lives in `app/schemas/cadence.py` (`OnceCadence` / `ScheduleCadence` / `RecurringDaysCadence` / `RecurringQuotaCadence`, keyed on `Field(discriminator="kind")`). Adding a cadence kind means a member there plus branches in `app/occurrences.py` and `build_cadence_plan` — not conditional fields on one flat schema.

`UserRead` vs `UserPublicRead` matters: the public variant drops `email` and is what `/users/` returns. `UserUpdate` overrides `name` to optional (so the avatar picker can PATCH `avatar` alone) with a validator rejecting an explicit `{"name": null}`, since that column is NOT NULL.

### Cadence, occurrences, and check-ins

`app/occurrences.py` is pure logic — no DB, no routes — turning a `CadenceUnion` plus an enrollment's `start_date`/`timezone` into today's due occurrences, streaks, the writability of a key, and the occurrences ahead (`upcoming_occurrences` / `count_occurrences_between`). Read it before touching `routers/checkin.py`, `routers/today.py`, or the challenge-detail heatmap.

**`occurrence_key` contract** — the letter prefix keeps keys from colliding if a cadence changes mid-life:

| Cadence | Key format | Example |
|---|---|---|
| `once` | `single` | `single` |
| `schedule` | `S` + ISO-8601 UTC instant | `S2026-09-14T06:00:00+00:00` |
| `recurring_days` | `D` + local ISO date | `D2026-09-14` |
| `recurring_quota` (week) | `W` + local Saturday ISO date + `#` + seq | `W2026-09-12#1` |
| `recurring_quota` (month) | `M` + **Jalali** `YYYY-MM` + `#` + seq | `M1405-06#3` |

**Weekdays are Iranian-indexed everywhere: `0 = Saturday … 6 = Friday`**, not Python's Monday-0. Convert with `(d.weekday() + 2) % 7` (`to_ir_weekday`). Weeks start Saturday — `week_key()` returns `W<local Saturday date>`, not an ISO year-week.

**Months are Jalali.** `month_key()` emits `M1405-06`, `period_bounds(d, "month")` returns that month's real span, and `is_occurrence_day`'s `every_n_months` steps Jalali months. This is not cosmetic — a Gregorian month reset mid-Shahrivar split a user's quota across two buckets. Migration `e91c47d2f0a3` rewrote the old `M2026-08`-style keys, re-deriving each row's period from its own `occurrence_local_date` (a Gregorian month straddles two Jalali months, so the old key alone is ambiguous). `_parse_period_key` rejects Jalali years outside `PLAUSIBLE_JALALI_YEARS` so an un-migrated key fails closed.

Conversion lives in **`app/jalali.py`** — pure arithmetic, no dependency. `tests/test_jalali.py` checks all ~44,000 days from 1300 to 1420 against ICU's Persian calendar, the same authority the browser formats with, so the server cannot bucket a date into a month the UI labels differently.

**Backfill window**: a key is writable only within `BACKFILL_DAYS = 2` of its occurrence closing (`once` never closes). `is_key_writable()` is the security boundary for `POST /checkins` — it re-derives whether a client-supplied key is legal for the cadence and enrollment; never trust the key as given. Outside the window, create/edit/delete return 403.

**Idempotency**: a duplicate `POST /checkins` is a success — on `IntegrityError` the router rolls back, re-selects the existing row and returns it with `200`. For `recurring_quota` keys a `#seq` collision is retried once with `seq + 1` before that 200 path; a second collision returns 409.

**Streaks are always recomputed, never incremented.** `compute_streaks()` walks `expected_keys_desc()` backwards from today and is the only writer of `current_streak`/`longest_streak` — there is no `+= 1` anywhere. Bounded by `MAX_STREAK_WALK = 400`.

The single most important edge case: two enrollments on one challenge in different timezones, fed the identical UTC instant, derive different `occurrence_key`s — anything computing "today" from a shared clock instead of each enrollment's own `timezone` breaks this.

**Challenge-detail is three tab panels, not one scroll.** `challenge-detail.html` splits by *question*: «من» (personal ring, streak tiles, run strip, timeline/quota history, membership facts), «برنامه» (the plan card, upcoming occurrences, `due_date`), «درباره» (description, rules, collective progress with participant avatars, and a details grid holding only what the other two don't). A visitor with no enrollment gets no «من» and lands on «برنامه». The selected panel lives in the URL hash, which makes a shared link land where it was shared from *and* survives the reload a check-in triggers. A figure appears in exactly one place — the hero's three stats swap with the viewer.

`build_cadence_plan` in `views/challenge.py` is the one place a cadence is described to a human, rendered for non-participants too. It branches per `CadenceKind` (`schedule` → sessions-passed plus first/last dates; `recurring_days` → a Saturday-first weekday strip and a 30-day count; `recurring_quota` → the current Jalali period's bounds; `once` → no calendar), reads in the viewer's own enrollment timezone (falling back to `DEFAULT_TIMEZONE`), and emits **ISO dates plus a `format` hint, never formatted text**.

The panel builders return dicts, not tuples: `build_history_timeline` → `items`/`summary`/`has_more`/`strip`/`today_key`; `build_quota_period_rows` → `rows`/`summary`/`today_key`. `summary` carries the full four-state tally because the ring shows a *share* and `missed` is derived at read time. `today_key` is the occurrence the sticky CTA offers to record.

**Every check-in mutation on this page ends in `window.location.reload()`** — one check-in moves the ring, the legend, both streak tiles, the run strip, the week counter and the CTA at once.

### Anonymous participation — the policy decides, the answer is stored

A challenge carries `identity_mode` (`named` | `anonymous` | `member_choice`), an enrollment carries `is_anonymous`, and `app/identity.py` is the only place the first becomes the second.

**The policy decides; the request is consulted only where the policy defers to it.** `resolve_anonymity(identity_mode, requested)` runs at the write boundary in `routers/enrollment.py`. A body asking to hide inside a `named` challenge gets a *named* enrolment and a 201, not a 422. An unrecognised mode falls back to the column default rather than raising.

**The answer is stored, not re-derived on every read.** Recomputing from the challenge's *current* mode is what makes a mode change retroactive and silently unmasks people. So `identity_mode` is in `locked_fields` (`update_challenge`), frozen by `count_non_owner_enrollments` — before anyone joins there is nothing to break.

**The owner is never anonymous.** The creator's auto-enrolment is `is_anonymous=False` in every mode and `resolve_anonymity` is not consulted for it; challenge-detail names them «سازنده». Anonymity is about participation; authorship is separate.

**Two surfaces hide something, one deliberately does not.** Participant avatars go through the `participant_avatar` filter, which answers `None` for an anonymous member so `avatar_url` renders `_default.svg` (their own avatar is recognisable elsewhere). The owner's join/leave notifications are raised with `anonymous_actor=True`, so the row is stored with **no** `actor_user_id` and the sentence falls back to `UNKNOWN_ACTOR` («یکی از اعضا»), indistinguishable from a deleted account — a wording reserved for anonymity would announce that someone chose it. That drop happens at the *write*, after the self-drop rule has run against the real actor: `ENROLLMENT_LEFT` outlives the enrollment row that carried the choice. The operator's participant roster is the exception (moderating abuse it cannot attribute is not moderation) and marks the row «ناشناس برای بقیه».

**`EnrollmentUpdate` deliberately does not carry `is_anonymous`** — `update_my_enrollment` writes its body onto the row with a blind `setattr`, so the field there would be a way around the resolution. Changing one's mind goes through `PATCH /enrollments/{challenge_id}/anonymity`, which runs the answer back through `resolve_anonymity` and answers **409** in a challenge that decides for everyone.

The choice is asked once, in the join sheet, and only when `asks_the_joiner(identity_mode)`. That sheet is why `createSheet` grew `onClose` (a sheet that *asks* must hear about a dismissal) and per-field `hint`. Farsi labels follow the per-surface pattern (D7): `IDENTITY_LABELS`/`HINTS`/`ICONS` in `views/challenge.py`, plus the create wizard's own client-side copy — adding a mode means editing both.

**A second write boundary, defaulting the other way.** `resolve_assigned_anonymity` is `resolve_anonymity` for somebody *put into* a challenge, differing in one case: `member_choice` resolves to **anonymous**. Somebody who was never asked is not named.

### Notifications — the event is stored, the sentence is derived

`app/models/notification.py` (the row), `app/notifications.py` (the wording and the only way one is created), `app/routers/notification.py` (queries + three JSON endpoints), `views/notification.py` + `templates/notification/` (the page and its `/fragment`). The bell in `layout.html` is the only entrance.

**A notification stores the event, never the text.** There is no `title`/`body` column — `kind` + `actor_user_id` + `challenge_id` + `group_id`, and `NOTIFICATION_META` is the single place a kind becomes Farsi (reaching templates as the `notification_title` / `notification_text` / `notification_icon` filters via `register_notification_filters`). Rewording is a code change rather than a migration over every row. `NotificationRead` keeps that outward: it answers `kind`/`actor_name`/`challenge_title` structured.

**`notify()` / `notify_many()` are the only door in**, holding four invariants a call site would forget:

1. **You are never told about your own action** — a notification whose recipient is its actor is dropped. Every emitting site has a case where the two coincide.
2. **It lands in the event's own transaction** — they `db.add()` and never `commit()`; the caller commits once.
3. **The recipient's mute list is honoured** — `Users.muted_notification_kinds` is a JSON list of codes (NULL and `[]` both mean nothing muted), checked here rather than at each site. This is why they are async: they read the recipients' column once per call (`_mute_map`), so a broadcast stays one query. A *mute* list rather than a subscribe list, so a new kind is on for everyone with no backfill.
4. **`anonymous_actor=True` drops the actor** (see anonymity above).

**Kinds.** Someone joined or left a challenge you own (both halves — a feed that only announces growth flatters); a challenge you are enrolled in changed lifecycle state; and six group kinds.

**The membership pair fires only for a non-public challenge** (`membership_is_announced` in `routers/enrollment.py`, asked by both halves). A public challenge is a noticeboard — its owner invited nobody — so an arrival is a roster number rather than an event; private and unlisted took a link the owner handed out. The two `hint`s say «خصوصی» for that reason. The lifecycle broadcast (`notify_lifecycle_change`) is deliberately *not* narrowed and is keyed on the **new state alone, only on a real transition**: an archived challenge stops producing occurrences, so an untold member just watches their streak stop. Moderation is the case it matters most for.

**Access is one clause**: every statement in `routers/notification.py` carries `Notification.user_id == <session user>`; there is no client-supplied recipient. The page takes `get_page_user`, the `/fragment` takes `get_current_user_id`.

**Marking read is a POST the page sends**, not a side effect of the GET. `POST /notifications/read` stamps every unread row of the caller's (`read_at.is_(None)` in the UPDATE, so re-reading never rewrites when it was first seen), fired once the rows are on screen and repainting nothing — so what was new keeps its «تازه» mark for that view while the bell goes quiet next page. Hence no per-row `PATCH`, no `DELETE`, no «خوانده‌نشده» tab.

**The switches are `/views/settings/notifications`** — one row per kind, built server-side from `NOTIFICATION_META` (which carries `hint` beside `title`/`text`/`icon`), so a kind cannot ship without a switch. The control is a native checkbox inside `.switch` wrapped by the `.sr-head` label. `initNotificationPrefs()` saves on tap through `GET`/`PUT /notifications/prefs` — **one kind per body**, not the whole map, so two devices cannot undo each other — moving optimistically and snapping back with a toast on failure.

**The bell's count is client-side on purpose.** `initNotificationBell()` asks `GET /notifications/unread-count` once the page is up; doing it server-side would mean every view router running one more COUNT and a route that forgot showing a wrong badge. It renders only for a signed-in visitor, uses plain `fetch` rather than `apiFetch` (a background count must not bounce someone out on a stale cookie), and `data-icon` sits on an **inner** span, because `renderIcons()` replaces its element's whole innerHTML.

Colour here means exactly one thing: **unread**. `Challenge.notifications` and `Group.notifications` cascade.

### The leaderboard

`/views/challenges/{id}/leaderboard` (+ `/fragment`) ranks one challenge's participants. Pieces: `fetch_leaderboard_page` / `leaderboard_rank` / `assign_ranks` / `leaderboard_standing` in `routers/enrollment.py`, `participant_display` in `app/identity.py`, `build_leaderboard_rows` in `views/challenge.py`, and `challenge/leaderboard.html` + `_leaderboard_rows.html`.

**It does not widen `profile_visibility_filter`.** A row is a `<div>`, not an `<a>`; the admin participant roster stays the only screen linking a participant to a profile.

**What it widens is membership.** `_leaderboard_challenge` gates in two steps and the order matters: `challenge_visibility_filter` composed into the query first (a challenge you cannot see is a plain **404**), then `challenge_role(...) is not None` (a member who has not joined a challenge they *can* see is a **403** — joining is the thing they need to be told). Asking through `challenge_role` lets an owner who unenrolled still reach it. Page/fragment follow the 303/401 split.

**Anonymity is honoured by `participant_display`**, the name-half of `participant_avatar` and deliberately in the same module. An anonymous participant keeps their score and loses their name and picture («عضو ناشناس»). They are **not** dropped — removing the row would let everyone derive who is missing. Your own row is always you.

**Ranks are counted live off `CheckIns`, and ties share a rank.** Volume is `COUNT(CheckIns) WHERE state='completed'` as a correlated subquery — never `ChallengeStats.total_completions` (challenge-wide *and* monotonic) or `legacy_completed_count`. Streaks come from `Enrollments.current_streak`, legitimate because `compute_streaks` recomputes it. Ranking reads **one** leading key, with the other metric shown beside it as the tie-break. `leaderboard_rank` is `1 + count(score > mine)` (competition ranking); `assign_ranks` extends that across a page — the first row's rank is that query and the rest is arithmetic — so a tie straddling a page boundary is still shared, which a bare `offset + i + 1` would break. «رتبه تو» is `leaderboard_standing`, calling the same two functions.

**`?sort=` is two options** (`completions` | `streak`), mirrored into the query string; an unrecognised value is **refused** by the pattern, not silently defaulted. Ordering falls through to `newest_first(Enrollment)`.

It is a page of its own because it is ordered and lazily paged. There is exactly **one** way in — a `.lb-entry` row in challenge-detail's «من» panel, rendered only when `participant_count > 1` — and it is in «من» rather than «درباره» because a link a non-participant could tap would lead to a 403. Colour means *rank* and borrows `--gold`; the podium is `[data-podium]` tinting the top three rank badges rather than a raised trio, so a four-way tie at rank 1 tints four badges instead of breaking a three-slot frame.

### Signing in by SMS

`app/phone.py` (a typed string becomes an identity), `app/sms.py` (the Kavenegar gateway, the only place the provider exists), `app/otp.py` (all policy), `app/models/otp.py`, `app/schemas/otp.py`, `app/routers/otp.py` (`POST /auth/otp/request`, `POST /auth/otp/verify`). The page is `/views/auth/`.

**Sign-up and sign-in are one flow.** There is no "do you have an account" branch: `/request` texts a code to whatever number it is given, and `/verify` creates the account if the proved number is new. So enumeration learns nothing, and **an account needs no personal information at all** — a new one gets a placeholder «کاربر ۴۵۶۷» from the last four digits of its own number.

**The credential is `Users.mobile`, deliberately not `Users.phone`.** `phone` stays an optional free-text contact detail writable through `PATCH /users/{id}` — which is exactly why it cannot be the credential. `mobile` is UNIQUE, canonical `+989…`, absent from every `User*` write schema, and has **one door**: `POST /auth/otp/verify`, after a code was proved. `mobile_verified_at` sits beside it, re-stamped on every sign-in. Both are nullable permanently (a password account has neither), which is also what lets `sync_sqlite_schema` add them to a populated table. The profile renders the verified number as a read-only «شماره ورود» row and only when there is one; the editable field is «شماره تماس».

**`app/phone.py` is the one place a typed string becomes an identity.** `09121234567`, `+98 912 123 4567`, `۰۹۱۲۱۲۳۴۵۶۷` and `00989121234567` are one account. It is Iran-only on purpose: this gates a login and the gateway delivers to Iranian carriers. That is a different question from `schemas/user.py`'s `_plausible_phone`, which stays permissive because it guards a contact detail. The invisible bidi marks an RTL paste carries are stripped, named by code point rather than written into the pattern.

**`app/otp.py` is the only door in**, holding six invariants:

1. **The code is never stored.** `hash_code` is an HMAC keyed on `secret_key` and **bound to the mobile**, so a leaked table is not a set of logins and a code cannot be replayed against another number. Rotating `secret_key` invalidates every outstanding code.
2. **A code dies on the first success and on the last wrong guess.** `attempts` is counted on the row and the row is *burned* at `MAX_ATTEMPTS` — the next guess must find no live row.
3. **Requesting a new code kills the old ones**, or every resend widens the set of valid codes.
4. **Sending is bounded per number *and* per source.** `X-Forwarded-For` is honoured and known to be spoofable: the header only ever *widens* how many buckets exist, so forging it escapes your own limit but never consumes somebody else's; the per-mobile limit no header can move is the one that protects a member and the SMS bill.
5. **Nothing says whether an account exists.** One refusal message covers "never asked", "expired", "already used" and "wrong".
6. **The code goes out before the row is committed.** `request_code` flushes and sends but never commits; a gateway failure rolls the row back with the request. `verify_code` is the opposite on refusal — the router **commits** the attempt counter, since discarding it would make `MAX_ATTEMPTS` unenforceable.

**Password accounts still work, and an OTP account has no password.** `Users.password_hash` is NOT NULL and SQLite cannot relax a column, so an OTP account gets `unusable_password_hash()` — a random `!`-prefixed marker. `verify_password` refuses it on the *meaning* rather than the parse, and the marker is random so two such accounts are not equal either.

**The provider is Kavenegar's `verify/lookup`, not `sms/send`**: no sender line to approve, it reaches numbers opted out of bulk messaging, and it escapes the night-time delivery window. The cost is that the *wording* lives in an approved template on the Kavenegar panel. `sms/send` remains the fallback for a deploy with a sender line and no template (`KAVENEGAR_SENDER` + `OTP_SMS_TEXT`). Kavenegar answers `200 OK` for most failures and carries the real outcome in `return.status`, so both layers are checked and the provider's own wins. **Development without a working gateway does not fail**: the code is printed to the server log and the send counts as successful. "Working" is the *pair* — a key plus one of template/sender. Outside development that same state is a hard failure.

**The page is two steps on one card.** Step two keeps naming the number the code went to (`.otp-target`, masked, with «تغییر شماره») — the commonest failure here is a typo in that number. Six `maxlength="1"` boxes running `dir="ltr"` inside the RTL page. **One `input` handler covers typing, pasting and platform autofill**: all three arrive as an `input` event, and autofill drops the whole code into whichever box is focused, so spreading from there means no second code path can get the order wrong. It folds Farsi digits, auto-submits on the sixth, and steps back on Backspace from an empty box. The resend control **is** the countdown until it reaches zero. Errors are an inline `.auth-error`, not a toast.

Email + password is kept for accounts that have one, behind a `.auth-alt` link under the card rather than a third tab.

**Where it grows.** Binding a mobile to an *existing* password account is a second `OtpCodes.purpose` value plus a session-gated route, deliberately not built (it carries a merge question). `purge_expired_codes` exists and nothing schedules it — there is no job runner — and its window is days rather than minutes because a burned row is the only record of a number being hammered.

### Groups — membership is one more conjunct, not a second visibility rule

A **group** is the space an organisation gathers its people in. `app/models/group.py` (four tables), `app/groups.py` (the domain module), `app/routers/group/` (the JSON door plus shared queries), `views/group.py` + `templates/group/` (the screens).

**Groups are not public and are not searchable.** There is no listing of all groups: `GET /groups/` answers *your* groups, every other route composes `group_visibility_filter(user_id)`, and the only way in from outside is an unguessable invite code. Hence `Group` has no `visibility` column.

**The whole access rule for a group's challenges is one extra conjunct.** `group_scope_filter` is `AND`-ed onto both challenge visibility filters and nothing else changes:

```
group_id IS NULL  OR  group_id IN <my groups>  OR  id IN <my enrollments>
```

Outside a group it is a no-op. Inside one, the existing three-way rule runs **unchanged**, which is what makes «عمومی» mean *public to the group*. `?status=`, `?q=`, the JSON list, the SSR list, its fragment and every detail route inherit it for free.

The third leg is load-bearing: **an existing enrollment is a key of its own.** Somebody *removed* from a group keeps the challenges they were already in (somebody who walks out themselves gives them up — see below), and a member who could neither see nor leave a challenge they are still enrolled in would be stuck. It leaks nothing: they were in the room. Somebody never in the group has neither key, so a group challenge does not exist for them at any URL through either door.

`fetch_challenge_page(group_id=…)` lists a group's challenges — the app-wide query with one more narrowing applied *on top of* the visibility clause, so asking for a group you are not in returns nothing.

**Group roles.** A group administrator gets `GROUP_CREATE_CHALLENGE`, checked *at the moment* a challenge is created under their group; from then on that challenge's edit rights come from the challenge axis alone. `admin` sits strictly below `owner` — `GROUP_MANAGE_ADMINS`, `GROUP_TRANSFER`, `GROUP_DELETE` are the owner's alone, because an administrator who could hand out admin is an owner with a delay. **The owner cannot leave without handing the group over** (409): no route anywhere could put a missing owner back. `POST /groups/{id}/transfer` moves the column and both membership rows in one transaction and leaves the outgoing owner an `admin`.

**404 vs 403, inside and out.** The group is a **404** to anyone not in it through both doors; `/manage` and the participants page are **404** to a member who is not an administrator. Once you are in, a refusal is an honest **403**. The one exception is a member id not in this group: **404**, because a 403 would confirm the account exists.

**Three doors in.**

*The invite link.* `GroupInvite` carries `code` (43 unguessable chars), optional `label`, `max_uses` (NULL = unlimited), `uses`, `expires_at`, `revoked_at`. **The capacity is real**: consumed by a conditional `UPDATE … SET uses = uses + 1 WHERE revoked_at IS NULL AND (expires_at IS NULL OR expires_at > now) AND (max_uses IS NULL OR uses < max_uses)` plus a `rowcount` check — never by reading `uses`, deciding, and writing back, which is the race that admits two people to one seat (`tests/test_group_invites.py` drives four concurrent accepts at one seat). One transaction, so a failed membership insert takes the consumed seat back. The `state` an administrator reads is **derived** (`invite_state`), never stored — a stored column would need a job the minute a link expired. Revoking is a timestamp, not a DELETE.

*The join request.* `GroupJoinRequest` is a table of its own rather than a `pending` value on `GroupMemberships`: with one table every "is this person in this group" query would have to *remember* `status = 'active'`, and the one that forgets is a silent access-control bug. **A membership row means membership, full stop.** The request is keyed on the **invite code**, not a group id (`POST /invites/{code}/request`) — an id route would let anybody walk the sequential ids and spam every group. Terminal rows are kept with `decided_by_user_id`, the only record of who let someone in.

*The public link.* Not a fourth mechanism: `GroupInvite.requires_approval` is a flag on the same table. Such a link reaches the same landing page and **admits nobody** — `accept_invite` refuses it with 409 and the page draws «درخواست عضویت». `POST /groups/{id}/invites/public` is **get-or-create** (one active unlimited approval link per group, `PUBLIC_INVITE_LABEL`), because minting a fresh code per tap would leave every copy already handed out alive. Rotating is revoke-then-ask-again, so there is no rotate endpoint.

*Adding somebody directly.* `POST /groups/{id}/members` is for the person standing in front of you. **Named by credential, never by id and never by a name search** — `MemberAdd.identifier` goes through `normalize_mobile` and falls back to an exact email. A search box here would hand every group administrator a way to walk the whole membership. An unknown number is an honest **404** (vs 409 for already-in): the oracle costs one whole number per guess, and the alternative is a screen that silently does nothing. The new member is told through `GROUP_MEMBER_ADDED` — its own kind, because nothing was asked for.

**A group challenge is handed out, not joined.** `Challenge.group_id` is a nullable FK (NULL = everything predating this), plus `group_audience` (`all` | `selected`) and `participation_mode` (`optional` | `mandatory`). All three are **create-only** — none is on `ChallengeUpdate`. Moving a challenge between groups would retroactively change who could see it; the other two are the terms participants were enrolled under.

«یک نفر خاص» is not a third audience — it is `selected` with one person picked. `all` carries a *standing* meaning: `apply_standing_audience` re-reads it whenever somebody joins the group, through **both** doors (archived challenges skipped — enrolling somebody in one gives them a row that can never be acted on). `assign_participants` is the one place a group challenge gains a participant, it is idempotent, and the audience is enforced server-side: `seed_group_participants` intersects `member_ids` with the group's membership, so a body naming an outsider enrols nobody — **dropped rather than refused**, so it is not possible to test whether an id is in the group by watching which bodies fail.

`ParticipationMode` is one refusal, in `leaving_is_allowed`, asked by the `unenroll` route, the bulk leave, and the page deciding whether to draw the button. **The obligation belongs to the membership, not the person**: a mandatory challenge cannot be left while you are still in the group that set it; the moment you are not, it becomes an ordinary enrollment.

**Leaving has two doors and they are one function.** `leave_group_challenges` in `app/groups.py` gives up every enrollment a group holds for one member; both `DELETE /groups/{id}/enrollments` and the self-leave half of `DELETE /groups/{id}/members/{me}` are it (the latter in the *same transaction* as the membership going). It is deliberately the same act `DELETE /enrollments/{id}` performs, repeated rather than shortcut: every counter comes down and every challenge owner still hears the departure through `membership_is_announced`.

`still_in_group` is the parameter separating the two, passed rather than looked up because the callers know the answer *about different moments*. The bulk route passes `True`: it keeps a mandatory challenge and **names it back** in the response so the page can explain why the list is not empty. Leaving the group passes `False`, so everything goes.

**Being removed is the opposite call.** An administrator's removal leaves the enrollments alone — destroying what another person logged is not a power running a group buys — and `group_scope_filter` keeps those challenges reachable. What ends either way is the obligation. There is deliberately no administrator version of the bulk route.

The two exits are `.setting-row`s at the foot of the «درباره» panel, each opening a `createSheet` rather than a `confirm()` (both are irreversible, and a `confirm()` cannot name which challenges those are). That is what `createSheet`'s `note` field type is for: a read-only paragraph, distinct from a field's `hint`. The owner gets **no leave row** and is told why, with a link to the transfer.

**Administering a group buys nothing about anybody's account.** `profile_visibility_filter` is not widened and no group screen links to a profile. `GroupMemberRead` carries a name, a picture and a role and nothing else; the per-challenge participants screen shows a checkbox rather than a score. What a group's administrators get about progress is the collective figures every member already sees.

**Six notification kinds**, each a place a member could not otherwise find out. `Notification.group_id` is cascaded like `challenge_id`, and `NOTIFICATION_META` has a `{group}` placeholder. Joining by link raises nothing (the joiner is their own actor, so invariant 1 drops the assignment notifications `apply_standing_audience` raises — they are looking at the result); somebody an administrator approved has the *administrator* as actor and is told what they were signed up to. The two assignment kinds are **separate kinds**, not one with an adjective — the row stores the event, and the split lets a member silence optional invitations while still hearing about obligations.

**Farsi labels live in `app/groups.py`** and reach templates through `register_group_filters(env)` — `NOTIFICATION_META`'s call rather than the per-surface one, because two routers render them (the group screens and the group chip on a challenge card). The create wizard keeps its own client-side copy, so adding a `GroupKind` means editing both.

**A group's emblem is an avatar id** from the *same* catalogue `Users.avatar` draws on, validated by the same `is_valid_avatar` at the same kind of write boundary and rendered by the same `avatar_url`. Every surface paints `--bg-1` behind it.

**Five screens.** `/views/groups/` (your groups; no discovery, so the empty state offers the two things that get somebody into one). `/views/groups/{id}` — «چالش‌ها» / «اعضا» / «درباره» as hash-backed tab panels, with only **one** of the three paged, because two paged lists on one screen would fight over one URL; «اعضا» is a screenful linking to `/views/groups/{id}/members`, the searched paged roster. `/views/groups/{id}/manage` holds the ways in, the invite links, the settings, and the open request queue **rendered directly on the page** — no pagination and no terminal state to filter between, so only `MAX_PENDING_ON_MANAGE` (50) rows show, with a note if the queue runs longer. Pending is a queue, oldest first (`fetch_request_page`). Decided rows are kept in the database but are no longer surfaced in the UI; `GET /groups/{id}/requests` still answers all three states.

**The manage screen's rule is one about marks.** It is a settings screen and nothing else: one row per *editable value*, the value on the row, and the end of the row saying what tapping does — a `.sr-chev` means a page opens, a `.sr-edit` pencil means an editor opens, an `.icon-btn.xs` means the thing happens here. That is why «مشخصات گروه» is four rows (نشان / نام / نوع / توضیح), each PATCHing only its own field, rather than one row opening a four-field sheet: a row naming a noun and hiding four answers behind it does not read as something you can change. The affirmative half of every pair takes `.accent` (`+` add, `✓` approve, copy) and the undoing half stays neutral (`✕`, `↻`), so the screen is scannable for actions without reading a word; the words those buttons used to carry live in `aria-label`/`title`. The «؟» explainers stay, because they answer *concepts* rather than what a control does. Anything irreversible opens a `createSheet` whose primary button backs out and whose `danger` button destroys — never a `confirm()`, which cannot name what stops working. `/views/groups/{id}/challenges/{cid}/participants` is the only screen that adds or removes somebody from a group challenge. `/views/invites/{code}` is the one screen a non-member sees and shows the group's name, emblem, kind and size and nothing else — a preview listing challenges or people would make an unused invite a way to read the group. A signed-out visitor tapping an invite is handled by `LoginRequired` → 303 with `?next=`.

**The way in is a profile menu row and a settings row**, both always rendered — being in no group is the normal state. Not a fifth bottom-nav tab.

**The create wizard is unchanged for a personal challenge.** `?group=` is absent from every existing entrance, so the two extra questions do not exist on that path; a group challenge is created from the group's own «+». Both create paths go through `create_challenge_record`, which calls `resolve_group_for_create` / `seed_group_participants` internally, so a missing permission check cannot go missing from one half.

**Colour means nothing new**: a group's identity is its emblem and a neutral `.group-chip`. Two things are coloured and both are states — an invite link that still works takes the accent, and «اجباری» takes `--gold`.

**Groups nest, and the whole of it is `group_ids_for`.** `Group.parent_id` is a nullable self-FK (NULL = a top-level group, which is every group predating this) and it is **create-only**, for the reason `Challenge.group_id` is: membership reaches upward through that column, so re-parenting would retroactively change who has been able to see everything ever published there. `group_tree()` is one *parameterless* recursive CTE — built once with `lru_cache`, because two differently-seeded CTEs of one name in a statement is a compile error and two names would be the same walk written twice — and reading it by `id` gives a group's ancestry while reading it by `ancestor_id` gives its subtree. `MAX_GROUP_DEPTH` (4) is enforced at creation with a 409; a cycle is impossible because the parent must already exist and can never change.

Two rules come out of that, and they are asymmetric on purpose:

- **Membership reaches upward.** Somebody in a department is in the company: they see its challenges, and `group_member_ids` (the subtree) is what «همه اعضا» means, so a company-wide challenge reaches the departments through both `seed_group_participants` and `apply_standing_audience` — which now applies the standing challenges of the joined group **and every group it sits inside**.
- **Authority reaches downward, and only authority.** An administrator of a group administers what is inside it; a plain member of the parent gets no reach into a sub-team at all, or a department would not be a room of its own. Inherited authority is **capped at `admin`** by `inherited_role`: `GROUP_DELETE` / `GROUP_TRANSFER` / `GROUP_MANAGE_ADMINS` stay with whoever owns that group, since a subgroup's creator owns what they opened.

`load_group` therefore hands `can()` an **`EffectiveMembership`** (`app/permissions.py`) rather than a row — `standing_in` resolves the caller's rows on the group, its ancestors and its subtree and keeps the strongest. It is an answer, not a record: nothing writes through it, and every route that acts on a *named* member still reads a real row through `_membership_of`, so managing somebody happens on the group their row is on. A **roster and an audience stay two different questions**: the roster screens list the people added *there*, the audience is the subtree.

Creating one is `POST /groups/` with `parent_id` — no second route, because a subgroup *is* a group (its own roster, links, invites and challenges) — gated on `Perm.GROUP_CREATE_SUBGROUP`, the administrator's, beside `GROUP_CREATE_CHALLENGE`. Deleting a group with children is a **409**, like one with challenges and for a wider version of the same reason. The screens: a «زیرگروهی از …» line on the group hero (the way up), a fourth `.dtab` on `/views/groups/{id}` rendered **only when there are children** (the group list's own cards, so a subgroup is entered like any other group), a «ساختار» section on `/views/groups/{id}/manage` whose «+» opens the create sheet, and a parent chip on the flat list of your groups. `tests/test_group_hierarchy.py` pins both directions and where the inheritance stops.

**Where it grows.** One challenge in several groups is a join table plus an edit to `group_scope_filter` and `group_ids_for` (every read reaches a group challenge through those two). Auto-join by email domain is a column on `Group` and one `apply_standing_audience` call at signup. None is implemented and none needs this design rewritten.

### «مسیر» — a course of challenges, where the exit condition belongs to the step

A **roadmap** is a reading order over challenges that already exist: «کتاب بخوان» → «پادکست گوش کن» → «نهال بکار» → «دویدن هفتگی». `app/models/roadmap.py` (five tables), `app/roadmaps.py` (the domain module and the engine), `app/routers/roadmap.py` (the JSON door plus the queries the pages share), `views/roadmap.py` + `templates/roadmap/` (the screens). `app/invites.py` is shared with the group subsystem.

**The problem it had to solve first.** This app has no notion of *finishing a challenge*: `EnrollmentStatus.COMPLETED` is written by no route, `challenge_status` is a challenge-level fact and not a per-person one, and a `recurring_days`/`recurring_quota` challenge with no `end_date` is endless **on purpose** — that is what a habit is. Adding "done" to `Challenge` would put a full stop on the thing designed not to have one.

**So the exit condition belongs to the step, and the challenge is never touched.** `RoadmapStep.completion_rule` is a **discriminated union in a JSON column**, exactly as `Challenge.cadence` is — `app/schemas/completion.py`, keyed on `Field(discriminator="kind")`. The same challenge is "۱۲ بار" in my course and "۴ هفته پشت‌سرهم" in yours. Six members: `challenge_finished`, `count(n)`, `streak(n)`, `amount(target)`, `duration(days)`, `manual`. Adding one is a member there plus a branch in `evaluate_rule` — not a nullable column per rule.

Two of them do not fit every challenge, and both are refused **422 at the write boundary** with a Farsi reason, because the builder is who has to fix it: `challenge_finished` on a cadence with no end (`is_bounded`) would be a step nobody could ever leave, and `amount` on a challenge with no `goal_unit` would sum nothing forever. `rule_refusal` is the one function; the wizard also hides those chips, which is a courtesy and never the gate.

**The builder never reads the words «شرط اتمام».** The wizard asks «این قدم کِی تمام می‌شود؟» and offers chips — «چند بار انجامش بدهد» / «چند نوبت پشت‌سرهم» / «گذشتن چند روز» — then a second sheet for the number that answer needs. Two sheets rather than one, because a single sheet would show a number field that means nothing for three of the six answers. The chip *wording* lives only in that sheet — the server renders whole sentences (`describe_rule`), never chip labels, so a Farsi label map here would be a map with no reader; `RULE_ICONS` is the half that does have one. Adding a rule is four edits: a member in `schemas/completion.py`, a branch in `evaluate_rule`, an icon, and the wizard's chip.

**A challenge is referenced, never copied — and that is the whole integration.** `RoadmapStep.challenge_id` points at the real row; reaching a step writes an ordinary `Enrollment` through `assign_participants` (the same function a group challenge is handed out with, so anonymity resolution and the `ChallengeStats` counter cannot drift between the two ways somebody is put into a challenge). Everything downstream — the occurrence engine, streaks, «امروز», the leaderboard, the backfill window, comments, likes — then works untouched, because nothing about it knows roadmaps exist. **`routers/today.py` is unchanged by this feature and must stay that way**: a locked step is kept out of «امروز» by *having no enrollment at all*, an absence rather than a filter. If that file ever needs a roadmap-shaped clause, the gate has been designed wrong somewhere else.

#### The two scope filters, and why they point in opposite directions

`roadmap_visibility_filter(user_id, listing=…)` is the **fourth** composed visibility clause in the app, after `challenge_visibility_filter`, `profile_visibility_filter` and `group_visibility_filter`, and it follows their rule: `.where()`-ed in, so a miss is a **404** through both front doors. Public / mine / one-I-am-walking, with the group gate `AND`-ed on when the roadmap belongs to one. `listing=True` drops `unlisted` for `listing_visibility_filter`'s reason. One function with a flag rather than two near-identical ones — the difference really is a single value in a single `IN`, and two copies is two places for the group gate to be forgotten.

`roadmap_scope_filter(user_id)` is the other direction and the one that matters: **one more `OR` leg on `challenge_visibility_filter`**, beside "public", "mine" and "enrolled". A group *narrows* what somebody may see, so `group_scope_filter` is a conjunct; a roadmap *widens* it for the person walking it, so this is a leg of the disjunction. The whole rule:

```
Challenge.id IN (SELECT challenge_id FROM RoadmapSteps
                 WHERE removed_at IS NULL AND roadmap_id IN <roadmaps I am walking>)
```

**Putting a private challenge into a public roadmap does not publish it.** The roadmap's visibility and the challenge's are two separate answers and neither rewrites the other. What reaches through is *roadmap enrollment* — the same trade `group_scope_filter`'s third leg makes: you were let into the room, so the rows in the room are readable. Somebody merely **looking** at a public roadmap gets the steps whose challenges they could already see, and a placeholder («چالش خصوصی») for the rest — `visible_challenge_ids` composes `challenge_visibility_filter` itself rather than reimplementing it.

It is deliberately **off `listing_visibility_filter`**: a private challenge two steps ahead is reachable at its own URL and has no business in the app-wide list of things to discover, which is precisely what `unlisted` already means here.

#### The engine: recomputed, never counted up

`refresh_progress` re-reads live `CheckIns` and re-decides every step each time it runs — the rule `compute_streaks` follows, and there is no `+= 1` anywhere in the subsystem. Running it twice is running it once.

The one thing it *writes* that it could not derive again is **`RoadmapStepProgress.unlocked_at`**: when a course reached a step has no other source in the app, it is the clock a `duration` rule counts from, and it is the line a `count`/`amount` rule counts *after* — which is `Roadmap.count_prior_progress = False` made concrete. Somebody who joined that challenge months ago starts the step at zero, so the course means the same thing for everybody walking it. (The column exists so the answer is recorded per roadmap rather than assumed app-wide; it is absent from every write schema, and v1 answers `False` for everybody.)

`unlocked_at` is stored **truncated to the second**, and that is load-bearing rather than tidy: `CheckIn.created_at` is `server_default=func.now()`, which on SQLite has second granularity (the same fact `newest_first`'s id tie-break exists for), so a check-in recorded in the very second a step opened would otherwise read as *earlier* than the unlock and not count — exactly the first check-in somebody makes on a step that just opened.

**There is deliberately no `enrollment_id` on the progress row.** The enrollment a step produced is already uniquely addressed by `(user_id, challenge_id)` — `Enrollments` carries a UNIQUE constraint on that pair — so a copy of the id would be a second name for a fact the database already guarantees, and one a plain `DELETE /enrollments/{id}` would leave dangling (SQLite does not enforce foreign keys in this app). `unlocked_at IS NOT NULL` is what says the enrollment was written, so **somebody who unenrols mid-course is never silently re-enrolled**: the step falls back to `in_progress` and waits for them.

**Where it runs, given there is no job runner** (see `purge_expired_codes`): the two halves are split by what could have changed the answer.

- `advance_after_checkin` runs inside `POST`/`PATCH`/`DELETE /checkins` — **this is where a step actually opens**. A member records a check-in on a challenge page and never goes near the roadmap; if the engine only ran when somebody opened the course, the next step's enrollment would not exist and «امروز» would be silently one step behind. It costs **one indexed query** (`ix_roadmap_steps_challenge`) for a member with no roadmaps, which is almost everybody.
- Opening `/views/roadmaps/{id}` recomputes too — that is the clock's half, for a `duration` rule that finishes because time passed with nobody touching anything.

**Structure is stages; the UI is a line.** `stage_index` + `order_in_stage` means "these two together, then that one" costs no migration later, while today's builder puts each step in a stage of its own. A stage opens when every `required` step of the one before it is **completed *or* skipped**.

#### The five step states, written twice

`locked` | `available` | `in_progress` | `completed` | `skipped`. `roadmap_step_state(progress)` renders one row, `step_state_filter(state)` is the SQL behind the cross-roadmap question ("what is open for this member", the home card) — **both restricted to the same single input**, the stored `state` with a missing progress row meaning `locked`, exactly as `challenge_status`/`status_filter` are restricted to the same four. Adding an input means adding it to both halves.

`skipped` is the one nobody chooses: when a challenge is archived, `skip_steps_for_challenge` marks every unfinished progress row for its steps `skipped`, refreshes everybody it just unblocked, and tells the **roadmap's builder** through `ROADMAP_STEP_SKIPPED`. Skipped counts as settled for opening the next stage — otherwise one archived challenge would strand everybody behind it forever, for a decision taken by somebody who may not even know the roadmap exists. It is called from `update_challenge`, beside `notify_lifecycle_change`, guarded by the same "only on a real transition" test.

#### Soft gating is the default

`Roadmap.strict` defaults to **`False`**, and the whole of it is `step_is_open(roadmap, state)` — one function the page and the route both ask, so a control and a refusal cannot disagree. With it off, the order is a *suggestion*: the next step still says «هنوز نوبتش نیست» and nothing stops somebody who is ready. With it on, a locked step is genuinely closed. A course somebody built for a friend is abandoned the first time a hard lock refuses something they were ready to do; a taught course needs the lock. Nothing else in the engine branches on it.

**The complement is not optional: every step is visible from the first screen**, with its title, its cadence in words and its condition. Hiding what is ahead makes the course impossible to judge before starting it, which is the one thing somebody deciding whether to start it has to do. The cadence sentence comes from `describe_cadence` — the *same* function `build_cadence_plan` uses for its own `rule` line — so a step and the challenge's own plan card can never word one cadence differently.

#### Editing, once forty people are halfway through

The same precedent the challenge lock sets: `structure_is_locked` is `count_non_owner_enrollments > 0`, the threshold that locks `cadence`/`goal_*`/`identity_mode`. Frozen from the first non-owner: reordering (`POST /roadmaps/{id}/steps/{step_id}/move`), every field of `PATCH .../steps/{step_id}`, and `strict`. **Free regardless**: appending a step (it lands in a stage after everything that exists — nobody has reached there), removing one (`removed_at`, a stamp; it can only ever *unblock* somebody and it destroys no progress row), and everything the course merely *says* (title, description, picture, visibility).

`move` is a route of its own rather than two `PATCH`es of `stage_index` from the page, because a swap is one act: two requests can half-succeed, and the half that lands leaves two steps sharing a stage — a legal shape, and therefore a silent corruption of the order rather than a visible error.

`DELETE /roadmaps/{id}` is refused **409** once anybody else has started, the same call `DELETE /challenges/{id}` makes; archiving is the supported exit. Note what neither destroys: the `Enrollments` and `CheckIns` the course produced belong to the challenges and stay exactly where they are — deleting a roadmap ends the course, never the history. Leaving a roadmap is the same rule (`DELETE /roadmaps/{id}/enroll`) and deliberately **not** the group's «انصراف از همهٔ چالش‌ها»: a group challenge was handed to you and the obligation ends with the membership, while a roadmap step is a challenge you actually started and logged against.

#### A fourth role axis, and a shared invite

`app/permissions.py` grows `ROADMAP_EDIT` / `ROADMAP_DELETE` / `ROADMAP_MANAGE`, granted by `roadmap_role` — one role, written as a map for the reason the other three are, and **no app-wide role grants a `ROADMAP_*` permission**: the same line drawn twice already for groups and challenges. An operator moderates published *challenges*; a course somebody assembled is authored work.

`app/invites.py` is new and is shared: `new_invite_code`, `invite_state` (derived, never stored), `INVITE_STATE_LABELS`, `register_invite_filters`, and **`consume_seat`** — the conditional `UPDATE … SET uses = uses + 1 WHERE <still usable>` plus the `rowcount` check that is the only version of a capacity that survives two people tapping a one-seat link at the same instant. `app/groups.py` re-exports the names its call sites and tests have always imported from it, and `accept_invite` on both sides is now the same statement. A roadmap link has **no `requires_approval` half**: a group is a room whose membership an administrator curates, while a course is either published or it is not, and a queue in front of something nobody has to be admitted to would be a control with nothing to decide.

#### Four notification kinds

`ROADMAP_JOINED` (somebody started your course — actor is the joiner), `ROADMAP_STEP_UNLOCKED`, `ROADMAP_COMPLETED`, `ROADMAP_STEP_SKIPPED`. `Notification.roadmap_id` is cascaded like `challenge_id` and `group_id`, and `NOTIFICATION_META` gains a `{roadmap}` placeholder; every kind gets its switch on `/views/settings/notifications` for free.

The two the engine raises carry **no actor at all** — nobody did this to anybody, the engine recomputed — which is the case `Notification.actor_user_id` is nullable for, and it is why `notify`'s first invariant cannot help here: the member *is* the person whose check-in caused it. `announce_outcome` is the one place they are raised, and joining passes `announce_unlocks=False`: the first stage opens as part of the tap and the member is looking straight at it.

#### The screens

`/views/roadmaps/` (your courses and the public ones — one paged list with a two-button scope, not a second «کشف» page, because the rows are identical and the difference is one clause; the scope defaults to «مال من» for somebody who has courses and «همه» for somebody who has none). `/views/roadmaps/{id}` — «مسیر» / «من» / «درباره» as hash-backed panels, and **none of them pages**: a course is a handful of steps read in one go, like `fetch_child_groups`, so the URL has only the panel to carry. `/views/roadmaps/{id}/manage` — the builder's settings screen, **404** to anybody else, one row per editable value with the value on the row and the mark at the end saying what tapping does. `/views/roadmap-invites/{code}` — the one screen a non-walker sees: title, picture, step count, member count, and **not the steps**.

**The «قدم فعلی تو» card on `/views/home/` is not optional.** «امروز» shows *occurrences*, and an occurrence says nothing about which step of which course it belongs to — a roadmap that is never seen at the daily level dies. It renders only when something is open, so a member with no courses pays one indexed query and no pixels.

Adding a step is a search, so it is a small panel borrowing `createSheet`'s own scrim and slide rather than a sheet field: `createSheet`'s fields are a static form, and the picker queries `GET /challenges/`, which already answers exactly what the builder is allowed to see.

**Colour gains no channel.** Status owns `--st-*` and category owns `--cat-*`; a step's state is shown by the node's shape, its glyph and its words. The one accent is the node of the step that is actually open — not a new meaning, since the accent has meant «this one is live» since the first screen. The connector between nodes fills in as steps settle, so the track *is* the progress bar of the whole course at no extra element.

#### Two deliberate departures from the brief, and why

- **`step_state_filter` has one caller, and it is the home card**, not a `?state=` filter on the step list. The steps of one course are not paged, so there would be no server-side paging for a Python-side filter to be unable to reproduce — which is the entire reason `challenge_status`/`status_filter` are written twice. The cross-roadmap question *is* server-side, so that is where the SQL half earns its place.
- **`Roadmap` carries one picture, not two.** A challenge has a second, portrait one because the full-screen rail reader is a surface shaped like a phone; a roadmap has no such surface, and a column nothing renders and no route writes is scaffolding pretending to be a feature. When a roadmap grows a reader it is one column and one row in `SHAPES`. Likewise `RoadmapStepProgress` has no `enrollment_id`, for the reason given above.

#### Where it grows

A second role on a course (a co-author) is a row in `ROADMAP_GRANTS`. `count_prior_progress = True` is one branch in `evaluate_rule`'s window. Two steps in one stage — «این دو را با هم انجام بده» — is already the schema and needs only a builder that writes the same `stage_index` twice. A roadmap made of roadmaps is the one thing this design does *not* invite, and that is on purpose: nesting courses is `Group.parent_id`'s problem, and it was solved there.

### Reactions — the subject is a value, not a table

A like is a row in **`Reactions`** (`app/models/reaction.py`): `user_id` + `subject_type` + `subject_id` + `kind`, unique on all four. Not a `challenge_likes` table — the next thing worth liking (a check-in, a group) is a new `ReactionSubject` member and nothing else. `kind` is the second axis for the same reason: a reaction table whose only kind is baked into its name gets copied the first time somebody wants a second one. Both are plain `String`, like every other enum-shaped column, so SQLite can hold them.

**A polymorphic table has no FK to its subject, so the visibility rule is the code's job.** `SUBJECT_RESOLVERS` in `app/reactions.py` is one entry per subject kind, each composing that subject's *own* filter — `challenge_visibility_filter` itself, never a copy — so a subject the caller cannot see comes back as "no such row" and the route answers **404**. Likes must not become a way to learn a private challenge exists. There is no path to a write that skips a resolver.

**There is no counter column.** Counts are read live off `ix_reactions_subject`, the same call the leaderboard makes about `CheckIns`, for the same reason `ChallengeStats.participant_count` cannot be the leaderboard's number. `counts_for` / `liked_subject_ids` take a *list* of ids: a page of cards asks both questions once, not once per card, and `like_context` in `views/challenge.py` is the one place a card surface spreads them into its context (the fragment template defaults both keys, so a caller that forgets renders a zeroed heart rather than raising).

**The route takes a state, never a toggle.** `PUT /reactions/{subject_type}/{subject_id}/likes` with `{"liked": …}` — a toggle over a flaky connection un-likes what the retry liked, and two devices disagree about whose tap was second. `set_reaction` is idempotent in both directions and swallows the unique-constraint race as a success, not a 409. `GET` is open (discovery is open); `PUT` takes `get_current_user_id`, so a signed-out tap is a real 401 that `apiFetch` turns into the login redirect.

Two surfaces, one control and one delegated listener (`initLikes`): the card's is a `<span role="button">` because the card is a single `<a>`, the hero's is a real `<button>`; both carry `data-like-subject`/`data-like-id`, so the page never knows what it is about. Optimistic, snapping back with a toast on failure.

**The control is an icon and a number, with no chrome.** On a card it stacks under the status pill in `.ec-side` at the outer edge — a second bordered capsule there would compete with the pill for the same glance, and the pill is the *state* a reader scans for while the heart is an action. It is coloured at rest (`--mohr`, which already means «warm, personal» here; status keeps `--st-*` and category keeps `--cat-*`, and this rides on neither), because a heart that only appears once liked reads as disabled — **liked is carried by the filled shape**, with a deeper ink beside it, so the state survives a greyscale screenshot.

### Comments — the same polymorphic trade, plus one level of threading

A comment is a row in **`Comments`** (`app/models/comment.py`): `user_id` + `subject_type` + `subject_id` + `parent_id` + `body` + `sticker`. Polymorphic for the reason `Reactions` is — the next thing worth commenting on is a `CommentSubject` member and a resolver in `app/comments.py`, not a second table, router and piece of page JS.

**The visibility rule is the code's job.** `SUBJECT_RESOLVERS` composes `challenge_visibility_filter` *itself*, never a copy, so an unreachable subject is a **404** on every door: a comment must not become a way to learn a private challenge exists. There is no write path that skips a resolver.

**Threads are one level deep, and that is enforced at the write.** `create_comment` re-points a reply-to-a-reply at its **root**, so no read recurses and no template flattens. Undoing that later is an edit to one function.

**Reading a page is three queries**: the roots (newest-first, `newest_first(Comment)`, `limit + 1` to answer «is there more»), then all their replies in one `parent_id IN (...)` query, oldest-first — a conversation reads forwards, the same exception the group request queue takes. There is **no counter column**: the badge is `count_for`, read live off `ix_comments_subject`, like every other count in this app. Everything past `REPLY_PREVIEW` is *rendered and hidden*, so «پاسخ‌های بیشتر» is a class toggle rather than a paged list inside a paged list.

**Deleting a root takes its thread**, through one explicit `DELETE ... WHERE parent_id = ...` in `delete_comment` — not an ORM cascade and not `ON DELETE CASCADE`, because every environment here is SQLite, which enforces foreign keys only with `PRAGMA foreign_keys` on: a database-level cascade would work on Postgres and silently orphan every reply everywhere else. Somebody else's comment is a **404** on delete, for the reason a check-in is.

**Content is text, a sticker, or both — never neither.** Both rules live in `schemas/comment.py`, the one write boundary. `Comments.sticker` holds an id from `app/stickers.py` (ids in, characters out through `sticker_char`, registered onto a views env like every other filter; an unknown id renders as `FALLBACK_STICKER` rather than raising) — the shape the composer wrote while an emoji was a *thing you attached*. The column, its validator and its rendering all stay, because rows written that way still have to read; nothing new writes one.

**An emoji is now something you type, which is why the catalogue left Python.** The picker inserts the character into the body at the caret, so what reaches the server is text — and once no emoji has to be named, the set can be Unicode's whole keyboard instead of the ~44 ids a stored-id design could afford. `app/static/js/emoji.js` is that catalogue: **generated and committed**, exactly like the avatars and for the same reason (the app must work inside the Docker image with no outbound network), rebuilt with `python -m app.scripts.generate_emoji_catalogue` from Unicode's own `emoji-test.txt` — fully-qualified forms only, skin-tone variants dropped, capped at `MAX_EMOJI_VERSION`. It is loaded **only by a page carrying a composer**; 1800 tiles server-rendered into every page of a conversation is a quarter of a megabyte of markup for a control most readers never open.

`createEmojiPicker()` in `app.js` is the control — a tab strip, the open group named under it, the tiles below, «اخیر» first out of `localStorage["chalesh-emoji-recent"]`. Three things in it are load-bearing:

- **One tab is in the DOM at a time**, its HTML cached after the first paint. Nine groups at once is ~1800 buttons; the open one is at most ~530.
- **The device decides what is on its own keyboard.** `drawableEmoji()` measures each emoji against a glyph no font has and against a known-ancient one: a box is exactly as wide as any other unknown glyph, and a ZWJ sequence a font only half knows falls apart into the two emoji it is made of and comes out nearly twice as wide. Both are dropped. It fails *open* — where the ruler cannot tell the two apart, the list comes back untouched, because a keyboard with a few boxes beats an empty one. This is the thing a version cap alone cannot do: any cap is wrong for somebody's phone in one direction or the other.
- **The caret is remembered, not read at insert time.** Opening the panel blurs the textarea (so a phone's own keyboard gets out of the way), and an unfocused textarea reports its caret at the *end* — which is exactly how every emoji ends up after the text instead of where the writer was.

The control that opens it carries no chrome and the tabs are monochrome stroke icons from the app's own set (`emSmiley`, `emPaw`, …): a coloured row above a grid of coloured emoji gives the eye nothing to land on, and a bordered circle beside the text field reads as a second thing to send with. The accent lands in exactly two places, the open tab and the open button, meaning what it always means here — this is the live one.

**Two notification kinds**, because they are two events reaching two people: `CHALLENGE_COMMENTED` (the challenge's owner hears a thread started) and `COMMENT_REPLIED` (the author hears an answer). `notify()`'s own rule drops the case where that is the actor.

**The screens — one modal, no page.** The conversation is `challenge/_comment_modal.html` plus `static/js/comments.js`: a bottom sheet at `88dvh` that comes up over whatever screen the reader is already on, because reading what people said is not leaving the list they scrolled to find it and a page took their place with it. There is **no `/views/challenges/{id}/comments`** — only its `/fragment`, which now serves page one as well, so the server knows nothing about a modal and `createInfiniteScroller()` pages exactly as it did. The **fragment takes `get_optional_user_id`**, unlike every other `/fragment` in the app: this list is readable signed out, so a 401 would bounce a visitor out of a page they are allowed to be on. Writing is `POST /comments/{subject_type}/{subject_id}` with `get_current_user_id`; sending ends in `scroller.reset()` rather than splicing a card in by hand, because *where* a comment belongs is a server-side answer.

**One modal per page, re-aimed rather than rebuilt.** The partial ships empty and `subjectId` is a variable the scroller's `buildUrl` closes over, so opening a second challenge's conversation is a `reset()` — not a second scroller, observer and composer to keep in step. The opener carries the id, the title and the count, so the header is drawn before a request goes out, and a sent comment moves *both* numbers: the header's and the control's, which is still on the page behind. It closes on ✕, the scrim, Esc and `popstate` — a `pushState` on open is what makes the phone's back gesture close the modal rather than leave the page, the same trade the rail's reader makes. The scrim and the slide are `createSheet`'s own (`.sheet-backdrop`, `sheetSlideUp` at .2s), re-floored to `z-index:0` *inside* the modal: left as it stands, a sheet's scrim paints over the panel it belongs to. The emoji catalogue is fetched on the first tap of the keyboard rather than shipped, because this partial now rides on screens that are not about writing at all.

**Three ways in, and they are the heart's.** A comment control sits wherever a like does — in `.ec-side` on every challenge card (list, rail and the rail's reader, one markup), beside the heart in challenge-detail's hero (`.hero-social`), and as the full-width `.lb-entry` row in «درباره», now a `<button>` with a `chevronUp` because there is no page to navigate to. The card's is a `<span role="button">` for the reason the heart's is: the card is a single `<a>`. Counts come from `social_context` in `views/challenge.py` (the old `like_context`, which now asks `comments.counts_for` alongside the two reaction queries) — one query for a whole page of cards, spread by all four card-rendering routes. Colour stays honest: the bubble takes the neutral ink, because `--mohr` means the heart.

### Uploaded pictures — the column stores an id, the disk stores the bytes

`app/media.py` (the subsystem), `app/routers/media.py` (`POST /media/`), `app/static/js/imagepick.js` + `createSheet`'s `type: "image"` field (the control), `static/vendor/cropper/` (Cropper.js, vendored). Three surfaces have one: `Users.avatar`, `Challenges.image_square`, `Challenges.image_tall`.

**The same contract `app/avatars.py` has.** A column holds a **key** — `<24 hex>.webp`, generated by this app — never a path, a URL or the bytes. `is_media_key` is the only thing that parses one, so a value that reached the column by any route still cannot escape `MEDIA_ROOT`; `media_url` is the one id-to-URL function and answers **`None`** for a non-key, because unlike an avatar "no picture" here means a surface draws something else entirely.

**Nothing a client sends is trusted.** Not the filename (unused — the id is generated), not the content type, not the extension, not the dimensions. Every upload is decoded and re-encoded by Pillow: that strips EXIF (a photograph carries where it was taken), proves the bytes are an image, and caps the file on a disk that is a mounted volume rather than an object store. `SHAPES` is the ceiling per frame and its keys are the *same names* the client's `IMAGE_FRAMES` uses, so the two halves cannot drift.

**Storing a file and choosing what a row points at are two acts.** `POST /media/` answers a key and writes no column; the existing `PATCH /users/{id}` / `PATCH /challenges/{id}` write that key through the validators guarding every other field. That is what lets the create wizard — where there is no row yet — use exactly the same picker as an edit sheet, and it is why there is no `/users/{id}/avatar` route. Signing in is the whole authorisation: an unsaved upload is a 24-random-hex key nobody else can reach, and ownership is checked where it always was, at the PATCH.

**`Users.avatar` holds either kind of id**, catalogue or upload, rather than growing a second column — which is what lets the roster, the leaderboard, a comment and a group's emblem show an uploaded photo through the `| avatar_url` they already had, with no template change and no "which of the two is showing" for each of them to answer.

**There is no `Media` table and no orphan sweep.** A key is a small opaque string in the column that wanted a picture; a row of metadata beside it would have to be kept in step by hand and answers no question the app asks. `discard_replaced` at each write boundary (`update_user`, `update_challenge`) and `discard` on challenge delete cover the common case; an upload nobody ever saved is an orphan, and a sweep is a job this app has no runner for (see `purge_expired_codes`).

**Files are served by a second static mount**, `/media` → `data/media` (beside the database, on the same disk, deliberately not under `app/static/`, which a `COPY . .` rebuilds). `ImmutableStaticFiles` is the opposite trade from `RevalidatedStaticFiles`: a key is generated per upload and never reused, so a URL there names one immutable set of bytes and a roster of forty photographs costs no conditional requests. Keys fan out into a directory per first byte-pair.

**Cropping is client-side, and the crop is the point.** Every surface draws its picture `object-fit:cover`, so an uncropped photo is *silently* cut and the member finds out later; the cropper is that cut made visible and theirs, locked to the frame's own aspect because a shape the layout cannot honour is a promise the app then breaks. Cropper.js is **vendored, not from a CDN** (the avatars' and the emoji catalogue's reason: no outbound network inside the image) and loaded on the *first pick* rather than on page load. The canvas re-encodes to WebP before the request, so ~100KB crosses the network instead of a 6MB phone photo — a courtesy to the connection, never a substitute for the server's own re-encode.

**Two pictures per challenge, because the surfaces are two shapes.** `image_square` is the detail hero's ground *and*, cover-cropped to a band, the rail card's 4:3 cover and the 52px list thumb; `image_tall` is the full-screen reader, which is the shape of the phone it fills. Both ship in **one** card markup and the tall one has no layout box outside the reader, so `loading="lazy"` never fetches it while a list is scrolled. NULL is permanent and legitimate: the picture is painted *inside* `.explore-thumb` rather than replacing it, so the cadence badge, the scrim and the status pill's placement are untouched and a challenge without one keeps exactly the category-coloured cover it always had.

**The hero's picture sits behind the glass**, wearing the same `--hero-fade` mask the pane does, so it dissolves at the edges like everything else in that card instead of arriving as a rectangle; its own scrim (inside the wrapper — an `<img>` has no pseudo-element) is what keeps `--text-*` legible over whatever was photographed, and the pane's backdrop blur drops from 20px to 3px, or the photograph is a smear. Colour gains no channel: status still owns `--st-*` and category `--cat-*`.

**One control, three places.** `window.pickImage(frame)` resolves to `{key, url}` or **null** for every refusal — a dismissed chooser, a cancelled crop, a rejected file — so a caller is one `if` and never a try/catch. It is `createSheet`'s `type: "image"` field in the manage sheet, the same markup inline in the create wizard, and the avatar grid's first tile (`.ap-upload`), where a picture of one's own is ahead of the forty drawn ones because it is the answer most people are looking for.

### Avatars

40 SVGs in `app/static/img/avatars/`, generated once from DiceBear and **committed, not fetched at render time** — the app must work inside the Docker image with no outbound network. Ten styles, four each: `glyphs`, `cameo`, `marbles`, `clay`, `critters`, `bottts-neutral`, `shapes`, `squircles`, `slice`, `stack` (CC0 except `glyphs`, CC BY 4.0 / Matt Houser, and `bottts-neutral`, free for commercial use / Pablo Stanley). Regenerating one means replacing its file, not editing code.

An uploaded photo lives in this same column as a media key and `avatar_url` resolves both (see «Uploaded pictures» above), so every surface here renders one without knowing the difference.

`app/avatars.py` is the whole subsystem: `AVATAR_IDS` (ordered by style, which is the picker grid's order), `is_valid_avatar`, `avatar_url`, `register_avatar_filters(env)`.

- **`Users.avatar` stores the id, never a path.** The stored value is fed straight back out as a static path, so the only gate that matters is the write boundary — the `avatar` field validator in `schemas/user.py`, inherited by both `POST /users/` and `PATCH /users/{id}`.
- **`None` is a permanent state, not a pending one.** `avatar_url(None)` — and any retired id — answers `_default.svg`, which reads as *unset* rather than as a quieter avatar. Migration `a7c31d8be402` backfills nothing. `_default.svg` is the one file not from DiceBear: drawn in the app's own palette, with colours hardcoded for both themes, because an `<img>` cannot see `[data-theme]` and a `prefers-color-scheme` block inside it would contradict an explicit choice.

**Picking happens twice, from one catalogue.** Signup renders the grid server-side into `auth.html` (`.avatar-pick`/`.ap-item`, selected tile toggles off to mean "no pick"); the profile reopens *the same markup* inside `createSheet`'s `type: "avatars"` field (options `{value, url, label}`, `""` is the real "no pick", and the field drops the boxed input shell exactly like `type: "chips"`). The control is `button.profile-avatar`. It saves with `PATCH /users/{id}` carrying **only** `avatar`, and the handler swaps the `<img>` src instead of reloading.

Several styles ship a **transparent** ground, so every surface rendering an avatar paints an *opaque* neutral (`--bg-1`) behind it — `.profile-avatar` traded its accent gradient for exactly this, since the gradient gave those members a halo. A translucent `--fill-*` token would let it back through.

### «راهنمای درجا» — the in-place explainer

`app/explainers.py` + the "راهنمای درجا" section of `styles.css` + `initExplainers()` in `app.js`. A small «؟» beside the thing it explains, opening a popover with two or three sentences and — where the thing *is* a set of named states — one line per state. It exists because this app is dense with **derived** figures (a ring that is a share, a recomputed streak, a status nobody stored, a grid whose columns are weeks). It is the opposite trade from the tour: that introduces a screen once, this answers a question at the moment it is asked.

- **One registry, not a phrase per template.** `EXPLAINERS` is the only place a term becomes Farsi. The same term is explained on several screens, and two copies drift. This is deliberately **not** the per-surface pattern the short Farsi *labels* follow: a label names a thing the reader sees, an explanation makes a promise about how the app behaves.
- **The text ships with the markup.** `explain(key)` renders the whole entry into `data-explain-*` attributes on the button — no fetch, nothing to fail offline, and a dot inside an infinite-scroll fragment works the moment it lands (the listener is delegated on the document). Hence no JSON endpoint.
- **A missing key fails loudly.** `explain()` raises `KeyError`, and `tests/test_explainers.py` walks every template for unknown keys plus every `Jinja2Templates` environment for a missing `register_explainer_filters` call. This is the fifth registration contract (after icons, avatars, groups, notifications) and the only one that is *checked*.
- **Colour means what it already means.** The dot is neutral at rest and takes the accent only while its popover is open.

Mechanics worth keeping. The pane is `position:fixed` and placed from the dot's viewport rect, because a dot can sit inside a scrolling card, a sticky header or a sheet and an absolutely positioned pane would be clipped by the first ancestor with `overflow`; the cost is that it re-places on scroll (rAF-throttled) and **closes rather than follows** once its anchor leaves the screen. Its floor is above the bottom nav. Its open state is a **transition, not a keyframe** — an animation that has not started (a backgrounded tab) leaves the pane at 0% opacity, which is the bug that shipped first. The arrow's two visible borders are **physical** sides, since which two form the tip is a fact about the 45° rotation and must not flip with the writing direction. Esc is handled in the **capture** phase and swallowed, so an explainer opened inside a sheet is what Esc puts away first.

`createSheet` takes an `explainHtml` option carrying the same `explain()` markup.

### The onboarding tour

`app/static/js/tour.js` + the "Tour" section of `styles.css` are **one run walked across the pages it describes**: the bottom nav first (the four doors), then نوار → خانه → نوار → امروز → نوار → چالش‌ها → نوار → پروفایل — the same order the bottom nav itself lists them in — listed in flow order in `TOUR_STEPS`. Bumping `TOUR_VERSION` replays everything for everyone. Within پروفایل the order is «اطلاعات حساب» → «گروه‌ها» → «تنظیمات», matching the sections' actual position on the page. Within چالش‌ها, the first step spotlights the filters button (`data-tour="explore-filters"`, on `#clFilterBtn` itself rather than the whole search bar) — category and "my challenges" scope live behind it, which is worth a dedicated step; the search input needs none.

**State is a set of seen step ids, not a position.** Every step carries a stable `id`; the entry is `localStorage["chalesh-tour:<user id>"]` → `{v, seen: [...]}`. The id comes from `data-user-id` on `layout.html`'s `<body>` — a shared key would silently deny the next member on a shared phone their onboarding. That attribute is the contract, pinned by `tests/test_tour_anchors.py`.

- **Targets are named, not selected.** A step points at `data-tour="<id>"` in the template, never a class chain. Nothing links the two at import time, so the test asserts the contract in *both* directions — the failure mode is silent: a step with a missing target drops out and the counter promises one fewer step than it showed.
- **A step's `path` says where it is answerable**: a concrete page, `ANY_PAGE` (`"*"`) for the four nav steps, since `layout.html` puts the nav on every page, or a prefix ending in `*` for the profile, whose URL carries the member's own id. The test resolves all three to a real page before fetching it.
- **The tour never navigates itself — it asks.** A step carrying `action: "click"` hides «بعدی» and hands the spotlight over: `.tour-ring` takes `is-handover` (`pointer-events:auto`, the only part of the overlay that takes a pointer), and a tap on it calls `el.click()` on the target, so the member's own tap moves both the page and the run. Everything else stays swallowed by the veil, so there is exactly one way forward. The ask is said in **two channels**: a `.tour-cta` line inside the callout (its own `actionHint`, falling back to `HANDOVER_HINT`, with the `tap` icon) and the ring's own faster, wider beat (`tourTap` instead of `tourBreathe`) — the breathing ring alone reads as «look here», which is what every other step's ring already says. Such a step **ends that page's run** — `untilHandover()` cuts there, because what follows it lives on the page it opens.
- **A missing target is deferred, not skipped.** Such a step is never marked seen, so it runs by itself the first time the page renders it — and because a page's run is a *filter over the flow* rather than a cursor into it, a deferred step holds up nothing behind it. Two anchors are data-dependent: `today-item` (only when something is due) and `home-focus` (only once something has been logged); the counter counts only the steps that rendered.
- **Auto-start is the unseen, available steps of wherever the member is**, cut at the first handover — so a member who wanders off the path is picked up where they actually are rather than stalling. **The two endings differ**: «تمام» ends that page's run and leaves the rest of the flow for the pages it belongs to; ✕ or Esc ends the *tour*, marking every remaining step seen — somebody who dismissed it is not asking to be met again on the next screen.
- **Replay is the «؟» button, one per toured page** — an `.icon-btn` with `[data-tour-help]` in `{% block topbar_action %}` beside the notification bell (wrapped in `.topbar-actions`, since the topbar is `space-between`). It runs that page's **own** steps, ignoring `seen` and leaving the nav steps out: somebody asking what *this screen* does is not asking to be walked to another one. The four toured pages are امروز, خانه, چالش‌ها and the member's **own** profile (`is_own_profile` gates both the script and the «؟», so an operator reading someone else's profile gets neither). Settings has no tour and does not load `tour.js`.
- **The spotlight is a hole in the veil, not a raised element.** The veil is clipped with `clip-path: path(evenodd, …)`; the target is never re-parented, re-stacked or cloned — lifting it with `z-index` dies on the first ancestor owning a stacking context, which here is most of them. Where `path()` is unsupported the `@supports` fallback drops the blur and only dims. Blur and tint are kept light so the page behind stays legible as context; the ring does the pointing instead (`tourBreathe` swells and fades its hairline and halo together without the ring ever moving). `--tour-veil` is the one theme token (all three blocks). The callout is opaque like `.sheet-panel` — its arrow overlaps that background by half its width, and a translucent pane shows the seam.
- **`place()` floors the hole's width and height at 0.** A target mid-scroll (or one `scrollIntoView` hasn't committed yet) can hand back a rect whose far edge is on the near side of the clamped start — an unfloored subtraction goes negative and the clip path, the ring radius and the popup's above/below/centre branch all key off that same corrupted number, which is what a fully broken-looking overlay (a solid veil, a popup off-screen) turns out to be upstream of. **The corrective `scrollIntoView` in `render()`'s settle callback is followed by a double `requestAnimationFrame`, not an immediate `place()`** — a non-smooth `scrollIntoView` does not commit its new offset synchronously, so measuring in the same tick can still see the pre-scroll position.

### Logging

`app/logging_config.py` (setup, context, `log_event`) + `app/middleware.py` (the access log). Everything goes to **stdout** — human-readable lines in development, one JSON object per line elsewhere (`LOG_LEVEL`/`LOG_FORMAT` in `.env`).

**A domain event stores a name and fields, never a sentence** — `log_event(logger, "challenge.created", challenge_id=…)`. Same trade as `app/notifications.py`: rewording never breaks a search. `challenge.created` sits inside `create_challenge_record`, the funnel both front doors share, so a create cannot happen without a line.

**Correlation is ambient.** `RequestLogMiddleware` stamps `request_id` (honouring an inbound `X-Request-ID`, echoed on the response) and `user_id` (read from the session cookie — HMAC only, no I/O) into two `ContextVar`s, and a filter puts them on every record. That is why a router logs an event without taking a `request` argument.

**Levels mean urgency, not verbosity.** INFO = it happened as designed; WARNING = a refusal a member can cause (bad password, rate limit, any 4xx, a request slower than `SLOW_REQUEST_MS`); ERROR = 5xx and unhandled exceptions. Nothing routine is ERROR, or alerts get muted. Irreversible acts (`*.deleted`, `user.role_changed`) are WARNING so they stay findable.

**No secrets, no bare PII** — ids are the join key; an email or mobile a human must recognise is masked (`mask_email`, `mask_mobile`). Performance: one line per request, `/static/` unlogged, `uvicorn.access` disabled as a duplicate, `sqlalchemy.engine`/`httpx` pinned to WARNING.

**There is no `ActionLog` table, on purpose.** Every request is already logged, and `AuditBase` (`updated_at` + `last_modifier_user_id`) records who last changed each row. A DB audit trail is worth adding only once the admin panel needs to *show* history — and then it is those ~20 `log_event` sites gaining a row, not a second mechanism.

### Async DB access

`app/database.py` builds the async engine and `AsyncSessionLocal`, defines `Base`, and imports `app.models` at the bottom so every model registers with `Base.metadata`. All routes depend on `get_db`.

Because sessions are async, **any relationship touched after the query must be eagerly loaded** — view routes use `selectinload(...)` (e.g. `selectinload(Challenge.enrollments).selectinload(Enrollment.user)`). A missing `selectinload` shows up as `MissingGreenlet` during template rendering, not as a query error.

`alembic/env.py` imports each model module explicitly for `target_metadata` — add new model modules there too.

### Templates and front-end

There is **no template inheritance from a base document**: every page is a standalone `<!DOCTYPE html>` with `lang="fa" dir="rtl"`, pulling `styles.css` and `app.js` via `url_for('static', …)`. A new page copies the head/shell of an existing one.

**Colour is two themes, and lives only in the token blocks.** `styles.css` opens with three blocks and nothing below them may hardcode a colour: `:root` (light, «شن و مریم‌گلی»), then `@media (prefers-color-scheme:dark) :root:not([data-theme="light"])` and `:root[data-theme="dark"]`. The media copy and the attribute copy are duplicated on purpose — one serves a device preference, the other an explicit choice that must beat it — so a new token goes in both.

Two mechanics make that possible. Tinted fills read `rgba(var(--accent-rgb),0.12)` rather than a second hex, so `--accent-rgb`/`--mohr-rgb`/`--gold-rgb` ship alongside every accent. And **the neutral fills are semantic, not levels**: `--glass` is the card pane (a faint white wash in dark, a high-alpha white sheet in light — one token running in opposite directions), while `--fill-1`/`--fill-2`/`--track`/`--fill-mute` are inset surfaces that sit *below* a card in light and *above* it in dark. `--on-accent`/`--on-gold`/`--on-finished` are the ink on a filled surface and flip with the theme. Glass gets its lit edge from `--glass-sheen`, accent controls their halo from `--glow-*`. The `.app-bg` tile is a data URI and a data URI cannot resolve a var, so the whole `url()` is the `--tile-url` token.

**The theme has three states.** `getTheme()`/`setTheme()`/`cycleTheme()` persist `system` | `light` | `dark` under `localStorage["chalesh-theme"]`; `system` deletes `[data-theme]`. The control is a `[data-theme-choice]` segmented group on `/views/settings/`, wired by `initThemeControls()` (which still supports the compact cycling `[data-theme-toggle]` row shape, unused today). Controls are painted from storage on load and carry no server-rendered selection. The stored value is read a *second* time by a small inline script in the `<head>` of `layout.html`, `auth.html` and `error.html` — blocking and pre-paint on purpose, because deferring it to `app.js` repaints one frame in, which is the flash it exists to prevent. **Four files share that key.**

**Every app-level preference lives on `/views/settings/`.** `views/settings.py` + `templates/settings/index.html`; it takes no `db` today but is still gated by `get_page_user`, so the first per-account preference does not require gating it retroactively. The profile keeps identity (avatar, «اطلاعات حساب») and its own actions plus one row pointing here; a preference added back to the profile is the regression `tests/test_settings_page.py` pins.

The page is `<section class="setting-group">` per question, `.setting-row` per setting, and a row owes the reader three things: a label, a one-line `.sr-hint` saying what changing it *does*, and its current value visible without opening anything (which is why the theme row drops `.sr-value` — the segmented control spells it out). A few mutually exclusive options reuse `.seg`; anything longer opens a `createSheet`. Unbuilt settings are **commented out** rather than rendered with `[data-coming-soon]` — a row that only toasts «به‌زودی» is worse than no row.

Underscore-prefixed templates (`challenge/_challenge_cards.html`, `home/_enrollment_cards.html`, …) are **infinite-scroll fragments**, `{% include %}`-ed for page one and re-served by `/fragment` endpoints. The contract:

- The `/fragment` route returns bare card markup plus `X-Has-More: true|false`.
- `createInfiniteScroller()` observes a sentinel, appends the HTML, re-runs `renderIcons()` **and `renderDates()`** on the new nodes, and redirects to `/views/auth/` on a 401. A `reset()` supersedes in-flight requests via a request token — keep that guard.
- `app.js` also exposes `renderIcons`, `renderDates`, `formatJalali`, `debounce`, `showToast`; `[data-coming-soon]` elements toast instead of doing nothing.

**Every tappable thing shares one press animation.** `initPressFeedback()` is a single delegated `pointerdown` listener putting `.is-pressed` on the nearest `button` / `a[href]` / `summary` / `[role=button|tab|radio|option]` / `label[for]` / `[data-press]` (opt a subtree out with `[data-no-press]`), swapping for `.is-releasing` on `pointerup`. Three deliberate choices: it is **not** `:active` (which iOS Safari never fires without a touch listener, survives a tap that becomes a scroll, and flashes for one frame on a flick — the JS holds the squeeze `PRESS_MIN_MS` and cancels after `PRESS_SLOP` px of travel); it animates the standalone **`scale`** property so it composes with existing transforms; and it runs as a **keyframe animation, not a transition**, because a component's `transition:transform …` on a class outweighs a global rule on a bare element selector. Depth is tuned per component by overriding `--press-scale` in that one section — so nothing else needs a `:active` rule.

**Colour carries two channels, and they never share a surface.** *Status* answers «should I act on this» and owns the `--st-*` hues (card edge stripe, pill, filter button). *Category* answers «what kind of thing is this» and owns the six `--cat-*` hues (the explore card's thumb, the detail hero's filled tile, the word naming the category). Mixing them made every card read the same green.

The six hues sit at one OKLCH lightness and chroma (dark ≈ L .78 / C .10, light ≈ L .58 / C .13) so no category shouts louder; the primary sage is deliberately *not* among them. `--on-cat` is the ink on a filled category surface. A card exposes its hue with `data-cat="<slug>"`, the slug being `ChallengeCategory.name | lower` from the `category_slug` filter in `app/icons.py` (same both-shapes contract as `category_icon`: the ORM hands back the enum, `TodayItem` a raw Farsi string). A template omitting the attribute silently falls back to the accent, so **adding a category means a hue in all three theme blocks plus one `[data-cat=…]` line** — and a new card template means remembering the attribute.

**Icons per category and cadence.** The SVG set in `app.js` carries one icon per `ChallengeCategory` and per `CadenceKind`. The *name* is chosen server-side by `app/icons.py` and reaches templates as `category_icon` / `cadence_icon`, so the Farsi values are not retyped into a JS map that can drift. Because every views router builds its own `Jinja2Templates`, each one rendering a category or cadence must call `register_icon_filters(templates.env)`; forgetting it is a render-time error. `create-challenge.html` is the exception — its pill grids are built client-side, so a new category means editing `CATEGORY_ICONS` *and* that `cats` array.

**Breadcrumbs and back buttons.** `layout.html` renders `[data-crumb-trail]`, filled by `initNavTrail()` from a sessionStorage trail of pages actually visited. A page marked `data-crumb-root` (the four bottom-nav destinations) *clears* the trail; anything reached from one appends; revisiting a page already in the trail truncates back to it, so list → detail → list collapses. A one-entry trail stays hidden, which is why the nav ships `hidden` and un-hides itself. Pages set their label with `{% block breadcrumb %}` (or `{% set crumb_label = … %}` for roots); the create wizard overrides it with nothing and never enters the trail. Icon-only back controls are `[data-back]` and must ship a real fallback `href` for a cold landing — `wireBackButtons()` only retargets them at the previous trail entry; `data-back-optional` hides one when there is nothing behind it.

### Dates and times in the UI

Storage stays Gregorian/UTC; **every date and time the user sees is Jalali**, converted client-side by `renderDates()` (`Intl.DateTimeFormat("fa-IR-u-ca-persian")`). There is deliberately no Python Jalali dependency. Templates render the ISO value as the element's text *and* into the data attribute, so the pre-JS fallback is a real (if Gregorian) date.

- `data-jalali="<ISO>"` + optional `data-jalali-format` (`day-month`, `weekday-day-month`, `day-month-year`, `month`, `numeric`, `time`, `datetime`, `datetime-full`), `data-jalali-tz`, `data-jalali-prefix`, `data-jalali-attr` (write into an attribute instead of `textContent`).
- **A bare `YYYY-MM-DD` is a floating local date and is never shifted between zones; a value containing `T` is an instant.** An instant with no offset is read as UTC — SQLite has no aware datetime type, so dev/test hand back `2026-10-06T11:06:36` where Postgres appends `+00:00`, and `new Date()` would otherwise read those as the *viewer's* local time.
- **Instants are rendered in the timezone they were judged in, not the viewer's.** Enrollment-scoped instants pass `data-jalali-tz="{{ item.timezone }}"`; `APP_TIMEZONE` (`Asia/Tehran`) is only the fallback for challenge-level instants like `due_date`/`created_at`.
- Two CLDR `fa` patterns come out year-first (`۱۴۰۵ شهریور`), which no Iranian writes: `datetime-full` uses `dateStyle`/`timeStyle`, and the `month` label is reassembled from `formatToParts`. Time formats pin `hour12: false`.
- **Date entry** is `app/static/js/jalali-picker.js`, loaded only by `create-challenge.html`. Native `<input type="date">` cannot render a Jalali calendar, so it hides each one (keeping it in the DOM as the value holder, same ISO format, same `min`/`max`, still firing `input`/`change`) and puts a `.jp-trigger` plus a bottom sheet in front. Everything reading `.value` is untouched. Its calendar maths comes from ICU too — 1 Farvardin is found by asking `Intl` which of 19–23 March it is. Rows added after load (the schedule list) must call `enhanceJalaliInputs(row)`.
- Today's cards split the deadline in two: the server renders the fixed window label (only it knows the cadence — `تا پایان امروز` for `recurring_days`, `تا پایان این هفته/ماه` for `recurring_quota`, the clock time for `schedule`, none for `once`), and `[data-deadline]` carries a live remainder that `renderDeadlines()` re-ticks every 60s. `TodayItem` carries `timezone`, `cadence_kind` and `quota_period` for exactly this. `closes_at_utc` is an *exclusive* bound, so `data-deadline-exclusive` makes the tooltip name the last instant inside the window.

**The SSR "form" pages post JSON, not form-encoded data.** `POST /views/challenges/create` takes a Pydantic body, and `create-challenge.html` / `auth.html` submit via `fetch(..., {headers: {"Content-Type": "application/json"}})`. Don't "fix" these handlers to `Form(...)` without changing the templates.
