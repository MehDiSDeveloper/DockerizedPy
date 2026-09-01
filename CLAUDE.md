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

Test files are named after what they pin; read the relevant one before changing a rule below. Nearly every invariant documented here has a test: occurrences, jalali, timezone boundaries, check-in idempotency, challenge lifecycle/status, page & object authorization, roles, admin panel/challenges/participants/profile, home dashboard, list ordering, notifications, anonymity, leaderboard, OTP auth, explainers, tour anchors, and the group subsystem (groups, invites, challenges, visibility, privacy, notifications, requests, leaving).

## Configuration

`app/config.py` (`pydantic-settings`) reads `database_url`, `environment`, `secret_key` from `.env`. Two env files: `.env` (local, gitignored) and `.env.docker` (used by `docker-compose.yml`); `.env.example` documents the shape. `secret_key` signs session cookies. `environment != "development"` flips the session cookie to `secure=True`.

**Every environment runs on SQLite — local, compose, and the Liara deploy — on purpose.** They used to differ (Postgres locally, SQLite on Liara's disk), so a Postgres-only migration reached production as `no such column: Users.avatar`. `DEFAULT_SQLITE_URL` is `sqlite+aiosqlite:///./data/challenges.db`; `data/` is the Liara mount point and `WORKDIR` is `/app`. `settings.is_sqlite` / `settings.sqlite_path` are the accessors (the latter goes through `make_url`, since `sqlite:///rel` vs `sqlite:////abs` differ by one slash); importing `config` creates the parent directory.

**Postgres stays wired up**: the `alembic/` history, the `asyncpg` pin, and a commented `DATABASE_URL` in each env file. Switching back is uncommenting that line and running `alembic upgrade head`. `alembic.ini`'s `sqlalchemy.url` is dead — `alembic/env.py` overwrites it with `settings.database_url`.

**Schema changes go to the models, and nothing else.** `app/scripts/bootstrap_db.py` (run by `start.sh`) branches on backend: Postgres → `alembic upgrade head`; SQLite → `create_all` then `sync_sqlite_schema()`, which diffs each existing table against `Base.metadata` and issues `ALTER TABLE ADD COLUMN` / `CREATE INDEX`. `create_all` alone skips existing tables — that is how the avatar column shipped without arriving. The sync cannot *change* a column (SQLite has no real `ALTER COLUMN`) or add a NOT NULL column without a default to a non-empty table; the second case prints a loud warning naming the column rather than failing the boot.

**The operator account is seeded on every boot.** A deploy's database is a different database and the first admin cannot be made through the app, so `start.sh` runs `app/scripts/seed_admin.py`: creates `SEED_ADMIN_EMAIL`/`SEED_ADMIN_PASSWORD` with `role=admin` if unknown, promotes an existing member, does nothing if already admin, does nothing at all when `SEED_ADMIN_EMAIL` is empty (the default). **It never rewrites an existing account's password** — otherwise the env var is a way to take over any account whose email is put in it. `SEED_ADMIN_RESET_PASSWORD=true` forces it for one boot (the locked-out escape hatch). Both env files are gitignored *and* in `.dockerignore`; the deploy reads these from Liara's environment.

## Architecture

### Two router layers over one model set

`app/routers/` is the JSON API (`/challenges`, `/users`, `/enrollments`, `/groups`, `/invites`, `/auth`, `/checkins`, `/today`). `app/routers/views/` is the SSR layer (`/views/...`). Both mounted in `app/main.py`, both hitting the same models. `/` redirects to `/views/today/`.

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

**Object-level access is a composed SQL predicate, not a post-load `if`.** There are three such filters and no permissions framework: `challenge_visibility_filter` (`routers/challenge.py`) — visible if public, owned, or enrolled; `profile_visibility_filter` (`routers/user.py`); `group_visibility_filter` (`app/routers/group`). Each is `.where()`-ed in so a miss falls out as "no such row", and each is shared by both front doors. Loosening a rule means editing one function.

**Profiles are own-only for members, reachable by an operator.** `profile_visibility_filter(viewer)` answers `true()` for a holder of `Perm.USER_VIEW_ANY` and `User.id == viewer.id` otherwise. Both `GET /views/users/{id}` and `GET /users/{id}` compose it. Nothing links a member to another profile and ids are sequential, so a miss is a **404** on both doors. The admin read still answers `UserPublicRead`; the wider `UserAdminRead` is confined to the `Perm.USER_LIST` roster route.

**403 vs 404.** Where the caller could already see the resource, 403 is honest. Where a 403 would answer the only question an enumerator is asking, it is a 404:

| Route | Not yours | Why |
|---|---|---|
| `/views/users/{id}`, `GET /users/{id}` | **404** | sequential ids, no in-app link to another profile |
| `PATCH`/`DELETE /checkins/{id}` | **404** | a 403 maps every member's logged history. The 403s left on these routes are *backfill-window* refusals about your own row |
| `PATCH`/`DELETE /challenges/{id}` | **404** if invisible, **403** if visible | the mutation selects through `challenge_visibility_filter` too |

Pages fail *authentication* with a 303 and *authorization* with 404/403; `get_page_user` running first keeps a signed-out visitor from getting the forbidden answer.

**`GET /users/` is the admin panel's roster** — the one read that ignores `profile_visibility_filter`, gated on `Perm.USER_LIST`, answering `UserAdminRead`. Paginated and filterable (`q`, `role`, `offset`, `limit`) through `fetch_member_page` / `apply_member_filters`, so the panel and the API cannot disagree about what a search matches.

### Roles — three axes, one `can()`

`app/permissions.py` is the only place a role becomes an answer. Callers ask for a permission, never a role: `can(user, Perm.CHALLENGE_DELETE, challenge=c)`. Policy is the grant maps there, so a new role is a row in a map instead of a sweep for `role == "admin"`. `can()` runs *after* a row is in hand — it does not replace the visibility filters, or the 404-vs-403 split collapses.

Three role columns, **deliberately never merged**:

- **`Users.role`** (`member` | `admin`) — app-wide, "may you run the place". Default `member`.
- **`Enrollments.role`** (`participant` | `owner`) — one challenge. The creator's auto-enrolment is `owner`.
- **`GroupMembership.role`** (`member` | `admin` | `owner`) — one group.

Merging them makes an admin the silent owner of everything. **No app-wide role grants a per-challenge or a `GROUP_*` permission, and no group role grants a `CHALLENGE_*` one**; `tests/test_user_roles.py` and `tests/test_groups.py` parametrize that over both global roles.

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

**Five screens.** `/views/groups/` (your groups; no discovery, so the empty state offers the two things that get somebody into one). `/views/groups/{id}` — «چالش‌ها» / «اعضا» / «درباره» as hash-backed tab panels, with only **one** of the three paged, because two paged lists on one screen would fight over one URL; «اعضا» is a screenful linking to `/views/groups/{id}/members`, the searched paged roster. `/views/groups/{id}/manage` holds the ways in, the invite links, the settings, and the open request queue **rendered directly on the page** — no pagination and no terminal state to filter between, so only `MAX_PENDING_ON_MANAGE` (50) rows show, with a note if the queue runs longer. Pending is a queue, oldest first (`fetch_request_page`). Decided rows are kept in the database but are no longer surfaced in the UI; `GET /groups/{id}/requests` still answers all three states. `/views/groups/{id}/challenges/{cid}/participants` is the only screen that adds or removes somebody from a group challenge. `/views/invites/{code}` is the one screen a non-member sees and shows the group's name, emblem, kind and size and nothing else — a preview listing challenges or people would make an unused invite a way to read the group. A signed-out visitor tapping an invite is handled by `LoginRequired` → 303 with `?next=`.

**The way in is a profile menu row and a settings row**, both always rendered — being in no group is the normal state. Not a fifth bottom-nav tab.

**The create wizard is unchanged for a personal challenge.** `?group=` is absent from every existing entrance, so the two extra questions do not exist on that path; a group challenge is created from the group's own «+». Both create paths go through `create_challenge_record`, which calls `resolve_group_for_create` / `seed_group_participants` internally, so a missing permission check cannot go missing from one half.

**Colour means nothing new**: a group's identity is its emblem and a neutral `.group-chip`. Two things are coloured and both are states — an invite link that still works takes the accent, and «اجباری» takes `--gold`.

**Where it grows.** One challenge in several groups is a join table plus an edit to `group_scope_filter` and `group_ids_for` (every read reaches a group challenge through those two). Sub-teams are a `parent_id` plus a recursive `group_ids_for`. Auto-join by email domain is a column on `Group` and one `apply_standing_audience` call at signup. None is implemented and none needs this design rewritten.

### Avatars

40 SVGs in `app/static/img/avatars/`, generated once from DiceBear and **committed, not fetched at render time** — the app must work inside the Docker image with no outbound network. Ten styles, four each: `glyphs`, `cameo`, `marbles`, `clay`, `critters`, `bottts-neutral`, `shapes`, `squircles`, `slice`, `stack` (CC0 except `glyphs`, CC BY 4.0 / Matt Houser, and `bottts-neutral`, free for commercial use / Pablo Stanley). Regenerating one means replacing its file, not editing code.

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

`app/static/js/tour.js` + the "Tour" section of `styles.css` are **three small per-page tours, not one run across pages**: امروز (2 steps), خانه (3), چالش‌ها (2), listed together in `TOUR_STEPS` and grouped by each step's `path`. The bottom nav's own step opens the خانه run, since خانه is the page a member lands on. **The tour never navigates** — a page's last step says «تمام». Bumping `TOUR_VERSION` replays everything for everyone.

**State is a set of seen step ids, not a position.** Every step carries a stable `id`; the entry is `localStorage["chalesh-tour:<user id>"]` → `{v, seen: [...]}`. The id comes from `data-user-id` on `layout.html`'s `<body>` — a shared key would silently deny the next member on a shared phone their onboarding. That attribute is the contract, pinned by `tests/test_tour_anchors.py`.

- **Targets are named, not selected.** A step points at `data-tour="<id>"` in the template, never a class chain. Nothing links the two at import time, so the test asserts the contract in *both* directions — the failure mode is silent: a step with a missing target drops out and the counter promises one fewer step than it showed.
- **A missing target is deferred, not skipped.** Such a step is never marked seen, so it runs by itself the first time the page renders it. The concrete case: `data-tour="today-item"` exists only when something is due, so a member with an empty امروز sees a 1-step tour and their first day with a card gets the «کارت هر نوبت» step alone. Each page's counter counts only the steps that rendered.
- **Auto-start is per page and independent.** Arriving on a toured page runs that page's *unseen, available* steps. Closing early (✕ or Esc) marks everything it offered seen; steps that never rendered stay pending.
- **Replay is the «؟» button, one per toured page** — an `.icon-btn` with `[data-tour-help]` in `{% block topbar_action %}` beside the notification bell (wrapped in `.topbar-actions`, since the topbar is `space-between`). It runs *all* of that page's available steps, ignoring `seen`. Settings has no tour and does not load `tour.js`.
- **The spotlight is a hole in the veil, not a raised element.** The veil is clipped with `clip-path: path(evenodd, …)`; the target is never re-parented, re-stacked or cloned — lifting it with `z-index` dies on the first ancestor owning a stacking context, which here is most of them. Where `path()` is unsupported the `@supports` fallback drops the blur and only dims. Blur and tint are kept light so the page behind stays legible as context; the ring does the pointing instead (`tourBreathe` swells and fades its hairline and halo together without the ring ever moving). `--tour-veil` is the one theme token (all three blocks). The callout is opaque like `.sheet-panel` — its arrow overlaps that background by half its width, and a translucent pane shows the seam.

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
