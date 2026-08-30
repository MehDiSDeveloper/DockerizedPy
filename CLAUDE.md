# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project overview

"چالش" / Challenge Manager — a FastAPI app where users create fitness/habit **challenges** (cadence-based: one-time, scheduled, recurring-days, or recurring-quota) and **enroll** in each other's, then log progress as **check-ins** against a **Today** feed. It serves two parallel front doors over the same models: a JSON CRUD API and server-rendered Jinja2 pages (Farsi, RTL). Auth is hand-rolled cookie sessions. A pytest suite lives under `tests/` — see Testing below.

## Commands

Everything runs through the venv at `.venv/` (`.venv/Scripts/` on Windows).

```powershell
uvicorn app.main:app --reload          # local dev (SQLite via .env)
docker-compose up                      # Postgres 17 + app, debugpy on :5678
ruff check .                           # lint (no config file — ruff defaults)
alembic upgrade head                   # apply migrations
alembic revision --autogenerate -m "…" # new migration
python -m app.scripts.generate_mock_data   # seed 10 users / 30 challenges / 60 enrollments
python -m app.scripts.set_user_role EMAIL admin  # make the first admin (--list to see roles)
python -m pytest tests\ -q             # run the test suite (in-memory SQLite, no Postgres needed)
```

Gotchas around dependencies:

- **`requirements.txt` is UTF-16 encoded.** Writing it with a plain-text tool corrupts it — use `pip freeze > requirements.txt` (PowerShell redirection preserves UTF-16 here) or verify the BOM after editing.
- It pins `starlette<1.0.0`. The installed venv can drift ahead of that pin, which breaks **every** `TemplateResponse` call with a confusing signature error. If all HTML pages suddenly 500, check `pip show starlette` first.
- `app/scripts/generate_mock_data.py` imports `faker`, which is installed in the local venv but **missing from `requirements.txt`** — so the seed script fails inside the Docker image. Add it to the pin file (respecting the UTF-16 caveat above) if seeding needs to work there.

Debugging in Docker: `.vscode/launch.json` has "Docker: Attach to FastAPI" (attach to `localhost:5678`, `${workspaceFolder}` → `/app`). `app/main.py` has a commented-out `debugpy.listen(...)`; the compose `command` already wraps uvicorn in debugpy, so leave it commented.

## Testing

`tests/` covers the occurrence engine (`test_occurrences.py`), the Jalali converter against ICU (`test_jalali.py`), timezone/backfill-window edge cases (`test_timezone_boundaries.py`), check-in idempotency (`test_checkin_idempotency.py`), the challenge lock/visibility/auto-enrollment rules (`test_challenge_lifecycle.py`), which SSR pages require a session (`test_page_authorization.py`), who may see a given row once they have one (`test_object_authorization.py`), the avatar catalogue (`test_avatars.py`), the settings page and the profile/settings split (`test_settings_page.py`), the two role axes and the line between them (`test_user_roles.py`), the admin panel's gate, its roster search/role filter and its lazy-loading fragment (`test_admin_page.py`), the moderation roster's gate, reach and the line between moderating and editing (`test_admin_challenges.py`), the home dashboard's counting and grid frame (`test_home_dashboard.py`), and the onboarding tour's step/anchor contract (`test_tour_anchors.py`). Run with:

```powershell
.venv\Scripts\python -m pytest tests\ -q
```

`tests/conftest.py` spins up an in-memory SQLite engine (`sqlite+aiosqlite:///:memory:`), creates the schema with `Base.metadata.create_all`, overrides `get_db`, and drives the real app through an `httpx.AsyncClient` (`ASGITransport`) — no Postgres or Docker required. This only works because the three columns that used to be native Postgres enums (`challenge_type`, `recurrence_pattern`, `Enrollments.status`) are now plain `VARCHAR`; SQLite can't represent a PG enum type, so a test-suite failure that looks enum-shaped means a native PG enum crept back onto one of those columns. `pytest.ini` at the repo root sets `asyncio_mode = auto`, so async test functions don't need `@pytest.mark.asyncio`.

## Configuration

`app/config.py` (`pydantic-settings`) reads `database_url`, `environment`, and `secret_key` from `.env`. Two env files exist: `.env` (local, gitignored) and `.env.docker` (consumed by `docker-compose.yml`); `.env.example` documents the shape. `secret_key` signs session cookies and defaults to an insecure dev placeholder. `environment != "development"` is what flips the session cookie to `secure=True`.

**Every environment runs on SQLite — local, compose, and the Liara deploy — on purpose.** The two used to differ (Postgres locally, SQLite on the disk Liara mounts), which meant a column added through a Postgres-only migration reached production as `no such column: Users.avatar`. `DEFAULT_SQLITE_URL` in `config.py` is `sqlite+aiosqlite:///./data/challenges.db`; `data/` is the mount point in `liara.json`, and `WORKDIR` is `/app`, so the relative path resolves onto the persistent disk. `settings.is_sqlite` / `settings.sqlite_path` are the accessors (the latter goes through `make_url`, since `sqlite:///rel` vs `sqlite:////abs` differ by one slash), and importing `config` creates the parent directory — a missing one fails at connect time, not import, and reads as a query bug.

**Postgres is still wired up and must stay that way**: the whole `alembic/` history, the `db` service in `docker-compose.yml`, the `asyncpg` pin, and a commented `DATABASE_URL` line in each env file. Switching back is uncommenting that line and running `alembic upgrade head`.

**Schema changes go to the models, and nothing else.** `app/scripts/bootstrap_db.py` (run by `start.sh` before uvicorn) branches on the backend: on Postgres it runs `alembic upgrade head`; on SQLite it runs `Base.metadata.create_all` and then `sync_sqlite_schema()`, which diffs each existing table against `Base.metadata` and issues `ALTER TABLE ADD COLUMN` / `CREATE INDEX` for what the file is missing. `create_all` alone will not do this — it skips a table that already exists, which is exactly how the avatar column shipped without arriving. What the sync deliberately cannot do is *change* an existing column (SQLite has no real `ALTER COLUMN`) or add a NOT NULL column with no default to a non-empty table; the second case prints a loud warning naming the column rather than failing the boot or silently relaxing the constraint. New migrations are only needed for the eventual switch back to Postgres.

Note `alembic.ini`'s `sqlalchemy.url` is a dead value — `alembic/env.py` overwrites it with `settings.database_url` at runtime.

## Architecture

### Two router layers over one model set

`app/routers/` is the JSON API (`/challenges`, `/users`, `/enrollments`, `/auth`, `/checkins`, `/today`). `app/routers/views/` is the SSR layer (`/views/challenges`, `/views/users`, `/views/home`, `/views/auth`, `/views/today`). Both are mounted in `app/main.py` and hit the same ORM models. `/` redirects to `/views/today/`.

They are **not** fully independent: `routers/views/challenge.py` imports `challenge_visibility_filter`, `fetch_challenge_page`, and the page-size constants from `routers/challenge.py`. Query/visibility logic belongs in the API router and gets reused by the view; only presentation lives in `views/`. Some create/auth logic *is* still duplicated across the two layers — when changing it, check whether the parallel handler needs the same edit.

**The two layers are not meant to mirror each other endpoint-for-endpoint.** Only `create` has a `/views/` twin (`POST /views/challenges/create`, which exists to answer JSON `{id}` to a `fetch()` instead of a redirect the caller would discard). Every other mutation — enrol/unenrol, check in, edit, delete, login, signup, logout — is called from page JS against the JSON API directly, so a missing `/views/` route is the design, not a gap. What *is* a gap is a JSON endpoint no page can reach: `PATCH`/`DELETE /challenges/{id}` and `PATCH`/`DELETE /checkins/{id}` had no caller at all until the manage sheet and the timeline's edit button were added to `challenge-detail.html`. `GET /challenges/{id}/stats`, `GET /enrollments/{id}/history` and `GET /today/` are deliberately API-only: the SSR pages compute the same numbers server-side and inline them, so those exist for API consumers, not for the templates. The `/views/*/fragment` routes are the reverse and equally deliberate — they return card markup for infinite scroll and have no business being JSON.

### Auth (`app/auth.py`)

Everything is written from scratch — no passlib, no JWT, no session store:

- Passwords: `pbkdf2_sha256$<iterations>$<salt_b64>$<digest_b64>`, 260k iterations, verified with `hmac.compare_digest`.
- Sessions: a stateless cookie `base64(user_id:expires_at).HMAC-SHA256`, keyed on `settings.secret_key`, 7-day expiry. Nothing is stored server-side, so **rotating `secret_key` logs everyone out** and there is no revocation.
- Dependencies: `get_current_user_id` (401s), `get_optional_user_id` (returns `None`), `get_current_user` (loads the row), `get_page_user` (loads the row, redirects to login — see the 303/401 split below).

`POST /auth/login` and `POST /users/` (signup) both set the cookie; `POST /auth/logout` clears it. The HTML login/signup page is `/views/auth/`, and protected view routes redirect there with `?next=<path>`. `/views/auth/` itself bounces an already-signed-in visitor straight to `next` — otherwise the bottom nav's profile tab parks them on a login form for the session they already hold.

### Page auth vs API auth — the 303/401 split

A page and a `fetch()` need different failure modes, so there are two dependencies, and picking the wrong one is a real bug:

- **`get_page_user`** (`app/auth.py`) is for full-page SSR routes. It raises `LoginRequired` — deliberately *not* an `HTTPException` — which `app/main.py` turns into a `303` to `/views/auth/?next=<path+query>`, clearing the session cookie on the way. Query strings are preserved so a deep link survives the round trip.
- **`get_current_user_id`** stays on the `/views/*/fragment` routes and the whole JSON API, where a real `401` is load-bearing: `createInfiniteScroller()` in `app.js` reads that status to redirect itself, and a `fetch()` would silently follow a `303` and append the login page's markup as if it were cards.

`get_page_user` returns the **`User` row**, not just the id — pages need it for the template anyway, and loading it is what makes a stateless, unrevocable cookie safe: one signed for a since-deleted account still verifies, so the row lookup is the only thing that fails it closed. Handlers that used to re-`select(User)` by hand should take the dependency instead.

**Which pages are gated** (`tests/test_page_authorization.py` is the list): `/views/today/`, `/views/home/`, `/views/challenges/create` and `/views/users/{id}` require a session. Challenge **discovery stays open** — `/views/challenges/` and `/views/challenges/{id}` run on `challenge_visibility_filter(None)`, which already narrows an anonymous visitor to public challenges, and gating it would make the app unlinkable from outside. Profiles are the opposite call, and gated twice over — see the object-level rule below.

### Ownership and visibility — the rule to preserve

Routers derive the acting user from the session, **never** from a path/query/body param:

- Mutating a challenge or user requires being the owner → 403 otherwise.
- Enrollment routes are keyed on `challenge_id` + the session user; there is no client-supplied `user_id` anywhere in `routers/enrollment.py`.
- Reads go through `challenge_visibility_filter(user_id)` in `app/routers/challenge.py`: visible if `is_public`, owned by the requester, or the requester is enrolled. Anonymous callers see public challenges only.

New challenge/enrollment endpoints must reuse that filter and the same session-derived-user pattern.

**Object-level access is a composed SQL predicate, not a post-load `if`.** There are exactly two such filters and no permissions framework — `challenge_visibility_filter` in `app/routers/challenge.py`, and `profile_visibility_filter(user_id)` in `app/routers/user.py`. Each is `.where()`-ed into the query so a miss falls out as "no such row", and each is shared by both front doors: `routers/views/user.py` imports the profile one exactly as `routers/views/challenge.py` imports the challenge one. Loosening a rule means editing one function, and both the page and the JSON endpoint follow.

**Profiles are own-only.** `profile_visibility_filter` is `User.id == user_id` — nothing anywhere in the app links to another member's profile (`layout.html`'s bottom nav points at the viewer's own id; challenge-detail renders participant *initials*, never a roster), so there is no legitimate way to arrive at someone else's, and the path id is a bare sequential integer. `GET /views/users/{id}` and `GET /users/{id}` both apply it. `is_own_profile` is therefore always true in the template today; it stays because the owner-only affordances branch on it and it is the flag a relationship rule ("visible if we share a challenge") would start flipping. **If a participant list ever ships, that is the one function to loosen** — do not re-open the routes individually.

**403 vs 404 — the split.** Where the caller could already *see* the resource, a 403 leaks nothing and is the more honest answer: PATCH/DELETE on a challenge you can read but do not own is 403. Where a 403 would answer the only question an enumerator is asking, it is a 404 instead, byte-identical to a genuine miss:

| Route | Not yours | Why |
|---|---|---|
| `/views/users/{id}`, `GET /users/{id}` | **404** | sequential ids, no in-app link to another profile — a 403 maps the whole membership |
| `PATCH`/`DELETE /checkins/{id}` | **404** | sequential ids reachable only through your own enrollment; a 403 maps every member's logged history. The 403s left on these routes are *backfill-window* refusals about your own row |
| `PATCH`/`DELETE /challenges/{id}` | **404** if invisible, **403** if visible | the mutation selects through `challenge_visibility_filter` too, so a private challenge `GET` hides cannot be confirmed by writing to it |

Pages fail *authentication* with a 303 to login and *authorization* with a 404/403 — the two are deliberately different, and `get_page_user` running first is what keeps a signed-out visitor from getting the forbidden answer.

**`GET /users/` is the admin panel's roster.** It returns every member and is the one read that ignores `profile_visibility_filter` on purpose, so it is gated on `Perm.USER_LIST` rather than on mere authentication, and answers `UserAdminRead` (a third read shape — see Roles below). It is paginated and filterable (`q`, `role`, `offset`, `limit`) through `fetch_member_page` / `apply_member_filters` in `routers/user.py` — the same "query logic in the API router, presentation in `views/`" split `fetch_challenge_page` follows, so the panel and the API can never disagree about what a search matches.

### Roles — two axes, one `can()`

Access has three layers now and they answer different questions. Reachability
is still the two SQL visibility filters; **who you are** is roles; and the
303/401 split above is authentication. `app/permissions.py` is the only place
a role becomes an answer.

**Two role columns, deliberately never merged:**

- **`Users.role`** (`UserRole`: `member` | `admin`) — app-wide. Answers "may
  you run the place". Default `member`, so every existing row and every
  signup lands on the old behaviour.
- **`Enrollments.role`** (`ChallengeRole`: `participant` | `owner`) — scoped
  to one challenge. Answers "what are you *in here*". The creator's D1
  auto-enrolment is written with `owner`; everyone else enrols as
  `participant`.

Merging them is the failure mode worth naming: with one column, an admin
silently becomes the owner of every challenge. **No app-wide role grants any
per-challenge permission today** — an admin gets no edit button on someone
else's challenge, and `tests/test_user_roles.py` parametrizes that assertion
over both global roles so a future grant cannot quietly cross the line.

**`Challenge.owner_id` stays the record of ownership**, and
`challenge_role(user_id, challenge, enrollment)` resolves the two sources:
owner_id wins, the enrollment supplies the role for everyone else. That is
not belt-and-braces — an owner may unenrol from their own challenge, deleting
the only row that could carry `owner`, and without the resolution they would
lose the challenge they still own. The enrollment is also checked to belong
to the asker; a row handed in for someone else grants nothing.

**Moderation is the third thing, and it is not ownership.** An operator has
to be able to *see* every challenge and take a bad one off the floor, so the
admin role grants three app-wide permissions — `CHALLENGE_LIST_ALL`,
`CHALLENGE_MODERATE`, `CHALLENGE_DELETE_ANY` — and deliberately **not**
`CHALLENGE_EDIT`, which stays per-challenge. Changing *what state a challenge
is in* is moderation; changing what it says (title, rules, cadence, goal) is
authorship, and an admin who could do it silently would be indistinguishable
from the owner in the record. That is why moderation has names of its own
rather than admin being added to `CHALLENGE_GRANTS` — the shortcut that would
have made an admin the owner of everything.

Two mechanics enforce it, both in `app/routers/challenge.py`. `reachable_for(user)`
is the clause every challenge *mutation* selects through: the visibility
filter for everyone, `true()` for a holder of `CHALLENGE_LIST_ALL` — so the
404-vs-403 split survives for members while a moderator can reach a private
row. And `MODERATABLE_FIELDS = {lifecycle_status, visibility}` is checked as
a whole-body rule: a non-owner's PATCH passes only when *every* key in it is
moderatable, so one request that renames and archives is refused entire
rather than half-applied. Deletion is the one place moderation buys nothing
extra — the 409 that protects other people's logged history applies to
operator and owner alike, and archiving is the exit.

**Callers ask for a permission, never a role**:
`can(user, Perm.CHALLENGE_DELETE, challenge=c)`. Policy is the two grant maps
in `permissions.py`, so a third role is a row in a map instead of a sweep for
`role == "admin"`. `can()` runs *after* a row is in hand — it does not
replace `challenge_visibility_filter` / `profile_visibility_filter`, which
must stay composed into the query or the 404-vs-403 split above collapses.

**Writing a role.** `Users.role` has exactly one door,
`PATCH /users/{id}/role` (`UserRoleUpdate`, admin-only), kept off `UserUpdate`
so the profile PATCH can never be a side channel; self-demotion is a 400,
since the last admin dropping their own role locks the panel with no console
to undo it from. The *first* admin cannot be made through the app at all —
`python -m app.scripts.set_user_role <email> admin` (also `--list`) is the
operator path, deliberately not a "first signup wins" rule.

**The panel** is `/views/admin/` + `templates/admin/index.html`: the roster,
each row showing the one field it administers, edited through a `createSheet`
against the JSON API like every other edit in the app. It is a page of its
own rather than admin-only branches inside member-facing screens — one route,
one dependency, one wide query. `get_admin_page_user` answers a signed-in
member **404** (a member has no business learning the panel exists);
`get_admin_user` answers a JSON caller **403** (fixed path, nothing to
enumerate).

The roster is **searched, role-filtered and lazily paged** with the challenge
list's own machinery, not a second one: a `.search-bar` (name *and* email —
the two things a support request arrives as) and a three-option `.seg`, both
kept on-screen rather than behind a filter sheet since there is no third cut
to hide; `createInfiniteScroller()` over `GET /views/admin/fragment`, which
`{% include %}`s `admin/_member_rows.html` for page one and re-serves it bare
with `X-Has-More` for the rest; and the state mirrored into the query string,
so a filtered roster is linkable and the server renders the first page
already narrowed. Two details are load-bearing. The fragment takes
**`get_admin_user`**, not the page dependency — a `fetch()` needs a real 401
to redirect on, and the 303 `get_page_user` raises would be followed silently
and the login page appended as member rows. And a row carries its own
`data-member-name` / `data-member-role`, with the sheet opened by one
delegated listener on the list: a page-level JSON array of members cannot
describe rows that arrive on page two, which is exactly the drift the
attributes avoid. A row whose role no longer matches the active filter is
repainted, never dropped — the scroller's `offset` counts rows the *server*
returned, so removing one would skip a member on the next page.

**The moderation roster** is the panel's other half: `/views/admin/challenges`
+ `templates/admin/challenges.html`, with `admin/_challenge_rows.html` as its
fragment. It is the public challenge list's own screen on purpose — the same
`.search-bar`, the same status `.seg` in the same `--st-*` colours, the same
`createInfiniteScroller()`, the same `createSheet` against the JSON API — and
only two things make it a moderation screen: it is served with
`fetch_challenge_page(..., scope_all=True)`, which is the one caller allowed
to skip `listing_visibility_filter` (the challenge-side twin of `GET /users/`
ignoring `profile_visibility_filter`), and each row can be acted on. `scope_all`
is a parameter on the shared query rather than a second query builder, so the
panel and the public list can never disagree about what a search matches.

A row shows two different things about the same challenge and both earn their
place: the **status pill** is the *derived* status a member sees on the public
list, and the hint spells out the two *stored* fields moderation writes —
lifecycle and visibility — because the derived badge alone cannot tell a draft
from a challenge that has not started, or an archived one from one that ended.
The sheet PATCHes only the fields that changed (a moderator's body is refused
if it carries anything else), and every successful action ends in
`scroller.reset()` rather than a hand-patched row: the pill is derived, and
re-deriving it in JS is exactly the drift the one-rule-in-one-place design
avoids. Delete is the sheet's `danger` button and refuses client-side with the
reason when others have joined, matching the server's 409 instead of
discovering it.

It is a page of its own rather than a second tab on `/views/admin/`: two
searched, filtered, lazily-paged lists on one screen would fight over one URL
and neither would stay linkable. A `.setting-row` under «چالش‌ها» on the panel
index is the only way in.

There are two entry points, both rendered only when `viewer_is_admin` and
both pointing at a route that gates itself: a `.setting-row` under «مدیریت»
on `/views/settings/`, and a `.menu-item` at the top of the profile's menu
list (shield icon, «مدیر» pill). The profile one reads `viewer`, never the
profile's own user — on someone else's profile, the day one becomes
reachable, the menu still belongs to the person looking. Neither is a fifth
bottom-nav tab, which would cost every member a slot for a handful of
accounts.

**Migration** is models-only, per the Configuration section: both columns
carry a default so `sync_sqlite_schema` can add them to a populated table,
and `bootstrap_db.backfill_enrollment_roles` marks each creator's own
enrolment `owner` — idempotent, run every boot.

### Derived challenge status — the badge and the filter are one rule

A `Challenge` has no start/end column, so the status the list colours cards by (`شروع نشده` / `در حال اجرا` / `تمام شده`) is **derived** in `app/routers/challenge.py` from `lifecycle_status`, `due_date`, and two keys of the cadence JSON:

- **finished** — archived, or `due_date` / `cadence.end_date` already past
- **upcoming** — not finished, and either still a draft or `cadence.datetimes[0]` is still ahead
- **active** — everything else

It is written twice on purpose: `challenge_status(challenge)` renders a card, `status_filter(status)` is the SQL clause behind `?status=` (the list is paginated server-side, so a Python-side filter could not page). **Both are deliberately restricted to the same four inputs so a card and the filter can never disagree** — which is why a `schedule` challenge whose sessions have all passed still reads as *active* until its `due_date` passes or the owner archives it: only element `[0]` of `cadence.datetimes` is reachable portably from SQL, since SQLite's `json_extract` has no negative index. Adding an input means adding it to both halves; `tests/test_challenge_status_filter.py` asserts the two agree case by case.

`?status=` is accepted by `GET /challenges/`, `/views/challenges/` and `/views/challenges/fragment` — the fragment included, or the other statuses leak back in on scroll. Farsi labels and icons live in `STATUS_META` in `routers/views/challenge.py` (same per-surface labelling pattern as `CADENCE_LABELS`, D7), and reach the card template as the `status_meta` Jinja global plus a `challenge_status` filter. The colours are the `--st-*` tokens in `styles.css`, shared by the card's accent stripe, its pill, and the matching button in the status filter, so one colour always means one thing. The pill always spells the status out — colour alone cannot carry it.

On the list page, status and search stay on-screen; category and the "my challenges" scope live in a `createSheet` filter sheet (`type: "chips"`), with a count badge on the filter button and removable chips below it, so a filter can never be left on invisibly.

### The home dashboard — one member's whole run, derived on every load

`/views/home/` is a dashboard over the member's own activity, and `build_dashboard` in `routers/views/home.py` is the only place in the app that reads a member's *whole* run rather than one challenge's. It answers four questions in order — «نبض امروز» (a ring of today's completions over everything today asked for, plus the streak/week/lifetime tiles), «روند فعالیت» (the twelve-week grid), «تمرکز تو» (which categories the last 30 days went to), then the enrollment list that was the whole page before.

Everything it shows is **read live off `CheckIns`**, for the same reason `build_my_stats` is: the only per-member numbers on a row are the two streaks — recomputed, never incremented — and `legacy_completed_count` is frozen pre-migration data that must never surface as a live figure. The one exception is the remaining-today count, which comes from `get_today_items` rather than a second implementation, so the home ring and the امروز tab can never disagree about how much is left.

Three things about the grid are load-bearing:

- **Columns are Saturday-aligned weeks** (`week_start`), not rolling 7-day chunks, so each *row* is one weekday. That is the whole point — a habit that always breaks on the same day shows up as a pale row. `tests/test_home_dashboard.py` pins the alignment, because losing it leaves the grid rendering perfectly while meaning nothing.
- **Cells are keyed on the stored `occurrence_local_date`**, never re-derived: that column was already bucketed in the enrollment's own zone when the row was written, so a backfilled occurrence lands on the day it was *for*. Only "which day is today" needs a zone, and that is member-level, so it takes `DEFAULT_TIMEZONE` exactly as today/index.html's date line does.
- **Levels are capped, not normalised** to the member's busiest day (`HEATMAP_MAX_LEVEL`), or one heavy day would repaint an unchanged past.

Presentation follows rules that already exist elsewhere: the ring is the same `.mine-card`/`.ring-*` primitive as the challenge-detail «من» panel and its legend carries **only the ring's own two slices** (anything else borrows a swatch that means a share of it); month labels are ISO dates with a `month-only` format hint, converted by `renderDates()` like every other date; and «تمرکز تو» is the taxonomy channel's surface, so each row reads `--cat` from its own `data-cat` — with the accent fallback, so a row missing the attribute still renders. Unenrolling from a card ends in `window.location.reload()` for the same reason a check-in does on challenge-detail: it moves the ring, both streak tiles, the week counter and the tab count at once, and patching derived figures by hand is how they drift apart.

### Deleting a challenge — archive is the normal exit

`DELETE /challenges/{id}` is for a challenge that should never have existed (a typo, a duplicate), not for ending one that has run. It cascades through every enrollment, check-in and the stats row, so it is refused with **409 once any non-owner has enrolled** — the same `count_non_owner_enrollments` threshold that locks `cadence`/`goal_*` edits. Destroying other people's logged history is strictly worse than the cadence change that rule already blocks. The supported way out of a live challenge is `PATCH lifecycle_status="archived"`.

The cascades on `Challenge.enrollments` / `.stats` / `.checkins` are **load-bearing, not tidiness**: every child FK is NOT NULL and `ChallengeStats.challenge_id` is a PK, so without them SQLAlchemy's default de-association raises before reaching the database and *every* delete is a 500 — which is what shipped until the guard above was added. `DELETE /users/{id}` deliberately does the opposite: no cascades, and it catches the resulting `IntegrityError` to answer 409, so an account with any history cannot be deleted at all.

### Models (`app/models/`)

- `User.role` is the app-wide role (`member`/`admin`, default `member`) — see Roles above; `Enrollment.role` is the per-challenge one. The two are separate columns on purpose.
- `User.avatar` holds an **id from a fixed catalogue** (`app/avatars.py`), never a path or a URL — see "Avatars" below. `NULL` is permanent and legitimate.
- `AuditBase` is an abstract `Base` subclass adding `created_at` / `updated_at` / `last_modifier_user_id`. Routers set `updated_at` and `last_modifier_user_id` by hand on every mutation — there is no ORM event hook doing it.
- `Challenge` is a flat table — the old single-table-inheritance split into `OneTimeChallenge`/`RecurringChallenge` is gone, and with it the `with_polymorphic`/`MissingGreenlet` hazard that used to be documented here. Recurrence now lives in two columns: `cadence_kind` (`once` | `schedule` | `recurring_days` | `recurring_quota`) and `cadence` (JSON, shaped by the `CadenceUnion` discriminated union — see "Cadence, occurrences, and check-ins" below). `visibility` (`private`/`unlisted`/`public`) and `lifecycle_status` (`draft`/`active`/`archived`) are plain `String` columns, not native Postgres enums (only `category` stays a native PG enum — see the enum bullet below). `goal_amount`/`goal_unit` describe an optional collective target; a check-in's `amount` is required iff `goal_unit is not None`. `legacy_cadence` is a verbatim JSON snapshot of the pre-migration recurrence columns for rows the new cadence model can't losslessly re-express — it's preserved user data, never read by application code, and must not be treated as scaffolding to clean up.
- `Enrollment` joins `User`↔`Challenge`, unique on `(user_id, challenge_id)`. `status` is `active`/`completed`/`abandoned`. Per-enrollment `timezone` (IANA name, default `Asia/Tehran`), `start_date`, `current_streak`/`longest_streak`, and `last_checkin_local_date` feed the occurrence engine. `legacy_completed_count` is the old `completed_count`, kept read-only — nothing writes it and it is never converted into `CheckIn` rows.
- `CheckIn` (table `CheckIns`) is one row per recorded occurrence: `enrollment_id` + a denormalized `challenge_id` (so Discover's velocity query needs no join), `occurrence_key`, `state` (`completed`/`skipped` only — `pending`/`missed` are derived at read time, never stored), the local date and UTC instant recorded, a `timezone` snapshot, and optional `amount`/`unit`/`note`/`photo_url`. `UniqueConstraint(enrollment_id, occurrence_key)` is the idempotency guarantee — see "Check-ins" below.
- `ChallengeStats` (table `ChallengeStats`, PK = `challenge_id`) holds **monotonic counters only**: `participant_count`, `total_completions`, `total_amount`, `last_checkin_at`. Nothing here goes stale and there is no refresh job — Discover's 7-day velocity sort is computed live from `CheckIns` via `ix_checkins_challenge_created` instead of a cached `checkins_7d` column.
- **Table names are capitalized** (`Users`, `Challenges`, `Enrollments`, `CheckIns`, `ChallengeStats`) and must be quoted in raw SQL on Postgres. Match this when adding FKs or hand-written migrations.
- **`ChallengeCategory` is the one enum that keeps Farsi values; every enum introduced by the cadence migration uses English codes instead (D7).** `ChallengeCategory.FITNESS == "سلامت جسمانی"` still travels all the way to the browser — JS payloads and template `data-cat` attributes contain the literal Farsi text, and renaming that enum's *value* is a data migration plus a template/JS change, not a rename. `Visibility`, `LifecycleStatus`, `CadenceKind`, and `EnrollmentStatus`, by contrast, are plain English codes (`"public"`, `"active"`, `"recurring_days"`, ...); there is no shared Farsi display-map module for them yet, so each template/script that shows one to a user inlines its own Farsi label (e.g. `challenge-detail.html`'s `visibility != 'public'` check, or `create-challenge.html`'s `cadenceKindLabels`) — follow that existing per-template pattern rather than introducing a new one.

### The onboarding tour

`app/static/js/tour.js` + the "Tour" section of `styles.css` are **three small
per-page tours, not one run across pages**: امروز (3 steps), خانه (2) and
چالش‌ها (2), listed together in `TOUR_STEPS` and grouped by each step's `path`.
**The tour never navigates** — no redirects, no forward-only cursor, no
handover button; a page's last step says «تمام» and the other pages wait for
the member to walk over there themselves. Bumping `TOUR_VERSION` replays
everything for everyone, which is the intended way to ship a changed run.

**State is a set of seen step ids, not a position.** Every step carries a
stable `id` and the entry is
`localStorage["chalesh-tour:<user id>"]` → `{v, seen: [...]}`. The key is
per-member, and the id comes from `data-user-id` on `layout.html`'s `<body>` —
a phone that already walked one member through the app has to introduce it to
the next member who signs in on it, and a shared key silently denies a whole
account its onboarding. That attribute is the contract, so
`tests/test_tour_anchors.py` pins it alongside the anchors.

Four rules hold it together, and that test pins the anchors and the «؟»:

- **Targets are named, not selected.** A step points at a `data-tour="<id>"`
  attribute in the template, never at a class chain. Nothing links the two at
  import time, so the test asserts the contract in *both* directions — every
  step names an anchor that exists, and every anchor belongs to a step —
  because the failure mode is silent: a step with a missing target drops out
  and the counter quietly promises one fewer step than it showed.
- **A missing target is deferred, not skipped.** A step whose anchor is absent
  is never marked seen, so it runs by itself the first time the page does
  render it — even when the rest of that page's tour was seen long ago. The
  concrete case: `data-tour="today-item"` only exists when the member has
  something due today, so a member with an empty امروز sees a 2-step tour, and
  their first day with a card gets the «کارت هر نوبت» step alone. Each page's
  counter therefore counts only the steps that actually rendered.
- **Auto-start is per page and independent.** Arriving on a toured page runs
  that page's *unseen, available* steps. Closing a run early (the ✕ or Esc)
  marks everything it offered seen; steps that never rendered stay pending.
- **Replay is the «؟» button, one per toured page** — an `.icon-btn` with the
  `help` icon, `[data-tour-help]`, in `{% block topbar_action %}` beside the
  notification bell (wrapped in `.topbar-actions`, since the topbar is
  `space-between` and two loose children land at opposite ends). It runs *all*
  of that page's available steps, ignoring `seen`. It renders only on toured
  pages; settings has no tour, no `[data-tour-restart]` row and no `tour.js`.
- **The spotlight is a hole in the veil, not a raised element.** The veil is
  clipped with `clip-path: path(evenodd, …)` so its blur stops at the target's
  edge, and the target itself is never re-parented, re-stacked or cloned —
  lifting it with `z-index` dies on the first ancestor owning a stacking
  context, which here is most of them. Where `path()` is unsupported the
  `@supports` fallback drops the blur and only dims, because a blurred target
  is worse than no frosting. Both the blur and the tint are kept light on
  purpose — the page behind stays legible as *context*, since a control shown
  without its surroundings is a control nobody can find again — so the ring
  does the pointing instead: `tourBreathe` swells and fades its hairline and
  halo together, which draws the eye back without the ring ever moving.
  `--tour-veil` is the one theme token (all three blocks); everything else is
  `rgba(var(--accent-rgb),…)`, `--bg-1` and the existing radii. The callout is
  opaque like `.sheet-panel`, for the same reason plus one more: its arrow
  overlaps that background by half its width, and a translucent pane shows the
  seam.

### Avatars

Members pick a picture at signup from a fixed catalogue of 40 SVGs in `app/static/img/avatars/`, generated once from [DiceBear](https://www.dicebear.com) and **committed, not fetched at render time** — the app has to work inside the Docker image with no outbound network, and an avatar that 404s on someone else's outage is worse than none. Ten DiceBear styles, four each: `glyphs`, `cameo`, `marbles`, `clay`, `critters`, `bottts-neutral`, `shapes`, `squircles`, `slice`, `stack` (CC0 except `glyphs`, CC BY 4.0 / Matt Houser, and `bottts-neutral`, free for commercial use / Pablo Stanley). Regenerating one means replacing its file, not editing code.

`app/avatars.py` is the whole subsystem: `AVATAR_IDS` (ordered by style, which is the order the picker's grid reads in), `is_valid_avatar`, `avatar_url`, and `register_avatar_filters(env)` — the same per-router registration `register_icon_filters` needs, so a views router rendering a member must call it or the filter is missing at render time.

Two rules hold it together:

- **`Users.avatar` stores the id, never a path.** The stored value is fed straight back out as a static path, so the only gate that matters is the one at the write boundary — the `avatar` field validator in `schemas/user.py`, which both `POST /users/` and `PATCH /users/{id}` inherit from `UserBase`. Nothing else validates, and nothing else needs to.
- **`None` is a permanent state, not a pending one.** Every account predating the column has it, picking is optional, and the profile picker can clear a pick, so `avatar_url(None)` — and `avatar_url` of any id whose file was retired — answers `_default.svg`, a head and shoulders that reads as *unset* rather than as a quieter avatar. Migration `a7c31d8be402` deliberately backfills nothing. `_default.svg` is the one file here **not** from DiceBear: it is drawn in the app's own sand/sage palette so an unset member looks like part of the catalogue, and its colours are hardcoded for both themes on purpose — an `<img>` cannot see `[data-theme]`, so a `prefers-color-scheme` block inside it would contradict an explicit theme choice.

**Picking happens twice, from one catalogue.** Signup renders the grid server-side into `auth.html` (`.avatar-pick` / `.ap-item`, selected tile toggles off to mean "no pick"); the profile reopens *the same markup* inside a sheet via `createSheet`'s `type: "avatars"` field — options are `{value, url, label}`, `""` is the real "no pick" value, and the field drops the boxed input shell exactly like `type: "chips"` does (`.field-avatars`/`.field-chips` share that rule). The control is the profile picture itself: `button.profile-avatar` with the camera badge that was already drawn there. It saves with `PATCH /users/{id}` carrying **only** `avatar` — which is why `UserUpdate` overrides `name` to optional, with a validator rejecting an explicit `{"name": null}` because that column is NOT NULL. Nothing else on the profile renders the avatar, so the handler swaps the `<img>` src instead of reloading.

Several catalogue styles ship a **transparent** ground, so whatever sits behind the `<img>` becomes part of the avatar. Every surface that renders one therefore paints an *opaque* neutral (`--bg-1`) behind it — `.profile-avatar` traded its accent gradient for exactly this reason, since the gradient gave those members a halo that members who picked an opaque style never got from the same picker. A translucent `--fill-*` token would let it back through.

### Schemas (`app/schemas/`)

`schemas/challenge.py` is flat, not a discriminated union — `ChallengeCreate`/`ChallengeRead`/`ChallengeUpdate` each carry a single `cadence: CadenceUnion` field. The discriminated union lives in `app/schemas/cadence.py` instead: `OnceCadence` / `ScheduleCadence` / `RecurringDaysCadence` / `RecurringQuotaCadence`, keyed on `Field(discriminator="kind")`. Adding a cadence kind means adding a member to that union plus the matching branches in `app/occurrences.py` — not conditional fields on one flat schema.

`UserRead` vs `UserPublicRead` matters: the public variant drops `email` and is what `/users/` list/detail return. `UserUpdate` is the one shape that is genuinely partial — see Avatars above for why its `name` is optional and why null is still refused.

### Cadence, occurrences, and check-ins

`app/occurrences.py` is pure logic — no DB, no routes — that turns a `CadenceUnion` plus an enrollment's `start_date`/`timezone` into today's due occurrences, streaks, the writability of a given check-in key, and — looking the other way — the next occurrences ahead (`upcoming_occurrences` / `count_occurrences_between`, which feed challenge-detail's plan card). Read it before touching `app/routers/checkin.py`, `app/routers/today.py`, or the challenge-detail heatmap.

**`occurrence_key` contract** — the letter prefix keeps keys from colliding if a cadence changes mid-life:

| Cadence | Key format | Example |
|---|---|---|
| `once` | `single` | `single` |
| `schedule` | `S` + ISO-8601 UTC instant | `S2026-09-14T06:00:00+00:00` |
| `recurring_days` | `D` + local ISO date | `D2026-09-14` |
| `recurring_quota` (week) | `W` + local Saturday ISO date + `#` + seq | `W2026-09-12#1` |
| `recurring_quota` (month) | `M` + **Jalali** `YYYY-MM` + `#` + seq | `M1405-06#3` |

**Weekdays are Iranian-indexed everywhere in this codebase: `0 = Saturday … 6 = Friday`**, not Python's Monday-0. Convert with `(d.weekday() + 2) % 7` (`to_ir_weekday`). Weeks start Saturday too — `week_key()` returns `W<local Saturday date>`, not an ISO year-week.

**Months are Jalali, not Gregorian.** `month_key()` emits `M1405-06` (Shahrivar), `period_bounds(d, "month")` returns that month's real span (23 Aug – 22 Sep 2026), and `is_occurrence_day`'s `every_n_months` steps Jalali months too. This is not cosmetic — a Gregorian month reset mid-Shahrivar and split a user's quota across two buckets, so someone who met their target inside the month they could actually see was shown as having missed it. Migration `e91c47d2f0a3` rewrote the pre-existing `M2026-08`-style keys, re-deriving each row's period from its own `occurrence_local_date` (a Gregorian month straddles two Jalali months, so the old key alone is ambiguous) and renumbering `#seq` within the new period. `_parse_period_key` rejects Jalali years outside `PLAUSIBLE_JALALI_YEARS`, so an un-migrated key fails closed instead of resolving ~600 years out.

Conversion lives in **`app/jalali.py`** — pure arithmetic, no dependency (a package would have to survive the UTF-16 `requirements.txt` caveat above, and `faker` already shows how that breaks the Docker image). `tests/test_jalali.py` checks all ~44,000 days from 1300 to 1420 against ICU's Persian calendar, which is the same authority the browser formats with — so the server can never bucket a date into a month the UI labels differently.

**The challenge-detail plan card** (`build_cadence_plan` in `app/routers/views/challenge.py`) is the one place that describes a cadence to a human, and it is deliberately rendered for non-participants too — the shape of a challenge is what someone weighs before enrolling. It branches per `CadenceKind`: `schedule` gets sessions-passed progress plus first/last dates, `recurring_days` gets a Saturday-first weekday strip and a "how many in the next 30 days" count, `recurring_quota` gets the current Jalali period's real bounds and length, `once` gets no calendar at all. It reads in the viewer's own enrollment timezone (falling back to `DEFAULT_TIMEZONE`), and emits **ISO dates plus a `format` hint, never formatted text** — `renderDates()` does the Jalali conversion, same as everywhere else. Adding a cadence kind means adding a branch here alongside the ones in `app/occurrences.py`.

**Challenge-detail is three tab panels, not one scroll.** `challenge-detail.html` splits by *question*, not by data source: «من» (personal ring, streak tiles, recent-run strip, timeline/quota history, membership facts), «برنامه» (the plan card above plus upcoming occurrences and `due_date`), «درباره» (description, rules, collective progress with the participant avatars on the same card, and a details grid holding only what the other two don't). A visitor with no enrollment gets no «من» and lands on «برنامه»; a participant lands on «من». The selected panel lives in the URL hash, which is what makes a shared link land where it was shared from *and* survives the reload a check-in triggers. A figure appears in exactly one place — the hero's three stats swap with the viewer (participants / my progress / my streak when enrolled, participants / completions / deadline when not), and nothing it shows is repeated below.

The panel builders in `routers/views/challenge.py` return dicts, not tuples: `build_history_timeline` hands back `items` / `summary` / `has_more` / `strip` / `today_key`, `build_quota_period_rows` hands back `rows` / `summary` / `today_key`. `summary` carries the full four-state tally (`completed`/`skipped`/`missed`/`pending`) because the ring shows a *share*, and `missed` is derived at read time — it cannot be counted off `CheckIns` rows. `today_key` is the occurrence the sticky CTA offers to record: an enrolled participant's primary action on this page is checking in, so the CTA is «ثبت نوبت امروز» whenever something is open and only falls back to «انصراف از چالش» when nothing is, with leaving demoted to the icon beside it.

**Every check-in mutation on this page ends in `window.location.reload()`.** One check-in moves the ring, the legend, both streak tiles, the run strip, the week counter and the CTA at once; patching six derived figures by hand is how they drift apart. The occurrence stays inside its backfill window, so the reloaded page still renders it as editable — undoing a mis-tap is one tap, same as before.

**Backfill window**: a key is writable only within `BACKFILL_DAYS = 2` of its occurrence closing (`once` never closes, so it's always writable). `is_key_writable()` is the security boundary for `POST /checkins` — it re-derives whether a client-supplied key is legal for the cadence and enrollment; never trust the key as given. Outside the window, create/edit/delete on `/checkins` all return 403.

**Idempotency**: `CheckIns` has `UniqueConstraint(enrollment_id, occurrence_key)`. A duplicate `POST /checkins` is a success, not an error — on the resulting `IntegrityError` the router rolls back, re-selects the existing row, and returns it with `200`. For `recurring_quota` keys specifically, a `#seq` collision is retried once with `seq + 1` before falling back to that 200 path; a second collision returns 409.

**Streaks are always recomputed, never incremented.** `compute_streaks()` walks `expected_keys_desc()` backwards from today and is the only place `current_streak`/`longest_streak` get written — there is no `+= 1` anywhere in the codebase. It's bounded by `MAX_STREAK_WALK = 400` occurrences.

See `tests/test_occurrences.py` and `tests/test_timezone_boundaries.py` for the exact behavior at cadence/timezone boundaries. The single most important case: two enrollments on the same challenge in different timezones, fed the identical UTC instant, derive different `occurrence_key`s — anything that computes "today" from a shared clock instead of each enrollment's own `timezone` will break this.

### Async DB access

`app/database.py` builds the async engine and `AsyncSessionLocal`, defines `Base`, and imports `app.models` at the bottom of the module so every model registers with `Base.metadata` (needed for Alembic autogenerate). All routes depend on `get_db`.

Because sessions are async, **any relationship touched after the query must be eagerly loaded** — view routes use `selectinload(...)` (e.g. `selectinload(Challenge.enrollments).selectinload(Enrollment.user)`). A missing `selectinload` shows up as `MissingGreenlet` during template rendering, not as a query error.

`alembic/env.py` runs migrations through `async_engine_from_config` and imports each model module explicitly for `target_metadata` — add new model modules there too.

### Templates and front-end

`app/templates/base.html` is **empty (0 bytes) and unused** — there is no template inheritance. Every page is a standalone `<!DOCTYPE html>` document with `lang="fa" dir="rtl"`, pulling shared `app/static/css/styles.css` and `app/static/js/app.js` via `url_for('static', …)`. If you add a page, copy the head/shell of an existing one.

**Colour is two themes, and lives only in the token blocks.** `styles.css` opens with three blocks and nothing below them may hardcode a colour: `:root` is the light theme «شن و مریم‌گلی» (sand & sage) and is also the base every token is declared in, then `@media (prefers-color-scheme:dark) :root:not([data-theme="light"])` and `:root[data-theme="dark"]` restate the same names for the dark theme «شب روشن» (soft night). The media-query copy and the attribute copy are duplicated on purpose — one serves a device preference, the other an explicit choice that must beat it — so a new token has to be added to both.

Two mechanics make that possible. Tinted fills read `rgba(var(--accent-rgb),0.12)` rather than a second copy of the hex, so `--accent-rgb`/`--mohr-rgb`/`--gold-rgb` ship alongside every accent. And **the neutral fills are semantic, not levels**: `--glass` is the card pane (a faint white wash in dark, a high-alpha white *sheet* in light — the same token running in opposite directions), while `--fill-1`/`--fill-2`/`--track`/`--fill-mute` are the inset surfaces that must sit *below* a card in light and *above* it in dark. `--on-accent`/`--on-gold`/`--on-finished` are the ink on a filled surface and flip with the theme; a filled control must read one of them instead of assuming dark text. Glass gets its lit top edge from `--glass-sheen` and accent controls their halo from `--glow-*` — both are tokens because a bloom that reads as light on the night ground reads as smudge on the sand one. The `.app-bg` tile texture is a data URI, and a data URI cannot resolve a var, so the whole `url()` is the `--tile-url` token and each theme ships its own stroke.

**The theme is chosen in three states, not two.** `getTheme()`/`setTheme()`/`cycleTheme()` in `app.js` persist `system` | `light` | `dark` under `localStorage["chalesh-theme"]`; `system` deletes `[data-theme]` and hands the choice back to the media query. The control lives on the settings page (`/views/settings/`) as a `[data-theme-choice]` segmented group of three `[data-theme-option]` buttons, wired by `initThemeControls()`, which also still supports the compact cycling `[data-theme-toggle]` row shape (no page ships one today). Controls are painted from storage on load and carry no server-rendered selection — the server cannot know a `localStorage` value. The stored value is read a *second* time by a small inline script in the `<head>` of `layout.html`, `auth.html` and `error.html` — that copy is blocking and pre-paint on purpose, because deferring it to `app.js` repaints the whole app one frame in, which is the flash it exists to prevent. Four files share that key; changing it means changing all four.

**Every app-level preference lives on `/views/settings/`, and nowhere else.** `routers/views/settings.py` + `templates/settings/index.html` are the whole page; it takes no `db` today because nothing on it is server state, but it is still gated by `get_page_user` — "my settings" is a personal surface, and the first per-account preference must not require gating it retroactively. The profile keeps identity (avatar, «اطلاعات حساب») and its own actions (history, logout) plus one row pointing here; a preference added back to the profile is the regression `tests/test_settings_page.py` pins.

The page is `<section class="setting-group">` per question, `.setting-row` per setting, and a row owes the reader three things: a label, a one-line `.sr-hint` saying what changing it *does*, and its current value visible without opening anything — which is why the theme row drops `.sr-value`, since the segmented control already spells the answer out. A few mutually exclusive options reuse the challenge list's `.seg` control; anything longer opens a `createSheet`, same as every other edit in the app. Unbuilt settings stay listed with `[data-coming-soon]` rather than appearing later: the shape of the screen is part of what it tells you.

Underscore-prefixed templates (`challenge/_challenge_cards.html`, `home/_enrollment_cards.html`) are **infinite-scroll fragments**, `{% include %}`-ed for the first server-rendered page and re-served by `/fragment` endpoints for subsequent pages. The contract:

- The `/fragment` route returns bare card markup plus an `X-Has-More: true|false` response header.
- `createInfiniteScroller()` in `app.js` observes a sentinel, appends the HTML, re-runs `renderIcons()` **and `renderDates()`** on the new nodes, and redirects to `/views/auth/` on a 401. A `reset()` supersedes in-flight requests via a request token — keep that guard when editing.
- `app.js` also exposes `renderIcons` (inline SVG set injected into `[data-icon]`), `renderDates`, `formatJalali`, `debounce`, and `showToast`; `[data-coming-soon]` elements toast instead of doing nothing.

**Every tappable thing shares one press animation.** `initPressFeedback()` in `app.js` is a single delegated `pointerdown` listener that puts `.is-pressed` on the nearest `button` / `a[href]` / `summary` / `[role=button|tab|radio|option]` / `label[for]` / `[data-press]` (opt a subtree out with `[data-no-press]`), swaps it for `.is-releasing` on `pointerup`, and the "Press feedback" section of `styles.css` does the rest: the control sinks to `--press-scale`, lights up by `--press-lit`, and springs back past 1 on the way out. Three deliberate choices hold it together — it is **not** `:active` (which iOS Safari never fires without a touch listener, which survives a tap that turns into a scroll, and which a flick tap flashes for one frame; the JS holds the squeeze `PRESS_MIN_MS` and cancels it after `PRESS_SLOP` px of travel), it animates the standalone **`scale`** property so it composes with the transforms controls already own for layout and hover, and it runs as a **keyframe animation, not a transition**, because a component's `transition:transform …` on a class outweighs any global rule on a bare element selector. Depth is tuned per component by overriding `--press-scale` in that one section — a 40px icon button and a full-width card cannot share a percentage — so nothing anywhere else needs a `:active` rule; the nine that existed were removed in favour of this.

**Colour carries two channels, and they never share a surface.** *Status* answers «should I act on this» and owns the `--st-*` hues — the card's edge stripe, its pill, the matching filter button. *Category* answers «what kind of thing is this» and owns the six `--cat-*` hues — the explore card's thumb, the detail hero's filled tile, and the word naming the category. Mixing them is what made every card read the same: before this, every thumb was the primary accent, so the only colour on a list of thirty challenges was one green.

The six hues sit at one OKLCH lightness and chroma (dark ≈ L .78 / C .10, light ≈ L .58 / C .13) so no category shouts louder than another, and the primary sage is deliberately *not* among them — it means «primary/live» everywhere else and a category may not borrow it. `--on-cat` is the ink on a filled category surface (the `--on-accent` of that scale) and flips per theme. A card exposes its hue by carrying `data-cat="<slug>"`, where the slug is `ChallengeCategory.name | lower` produced by the `category_slug` filter in `app/icons.py` (same both-shapes contract as `category_icon`: the ORM hands back the enum, `TodayItem` a raw Farsi string); the `[data-cat]` rules in `styles.css` read it. A template that omits the attribute silently falls back to the accent styling, so **adding a category means a hue in all three theme blocks plus one `[data-cat=…]` line** — and a new card template means remembering the attribute.

**Icons per category and cadence.** The SVG set in `app.js` carries one icon for every `ChallengeCategory` (`catFitness`, `catNutrition`, …) and every `CadenceKind` (`cadenceOnce`, `cadenceSchedule`, …). The *name* is chosen server-side by `app/icons.py` and reaches templates as the `category_icon` / `cadence_icon` Jinja filters — deliberately, so the Farsi `ChallengeCategory` values are not retyped into a second JS map that can drift. Because every views router builds its own `Jinja2Templates`, each one rendering a category or cadence must call `register_icon_filters(templates.env)`; forgetting it is a template error at render time, not import time. `create-challenge.html` is the one exception — its pill grids are built client-side and keep their own copy of the mapping, so a new category means editing `CATEGORY_ICONS` *and* that `cats` array.

**Breadcrumbs and back buttons.** `layout.html` renders a `[data-crumb-trail]` element that `initNavTrail()` in `app.js` fills from a sessionStorage trail of the pages actually visited. A page marked `data-crumb-root` (the four bottom-nav destinations) *clears* the trail — the bottom nav is a switch, not a step — while anything reached from one appends; revisiting a page already in the trail truncates back to it, so list → detail → list collapses. A one-entry trail stays hidden, which is why the nav ships `hidden` and un-hides itself. Pages set their own label by overriding `{% block breadcrumb %}` (or, for the roots, `{% set crumb_label = … %}`); the create wizard overrides it with nothing and so never enters the trail at all. Icon-only back controls are `[data-back]` and must ship a real fallback `href` for a cold landing — `wireBackButtons()` only retargets them at the previous trail entry; add `data-back-optional` to one that should hide itself when there is nothing behind it.

### Dates and times in the UI

Storage stays Gregorian/UTC; **every date and time the user sees is Jalali**, converted client-side by `renderDates()` in `app.js` (`Intl.DateTimeFormat("fa-IR-u-ca-persian")`). There is deliberately no Python Jalali dependency — adding one would have to survive the UTF-16 `requirements.txt` caveat above, and `Intl` needs nothing. Templates render the ISO value as the element's text *and* into the data attribute, so the pre-JS/no-`Intl` fallback is a real (if Gregorian) date.

- `data-jalali="<ISO>"` + optional `data-jalali-format` (`day-month`, `weekday-day-month`, `day-month-year`, `month`, `numeric`, `time`, `datetime`, `datetime-full`), `data-jalali-tz`, `data-jalali-prefix`, `data-jalali-attr` (write into an attribute instead of `textContent`).
- **A bare `YYYY-MM-DD` is a floating local date and is never shifted between zones; a value containing `T` is an instant.** An instant with no offset is read as UTC — SQLite has no aware datetime type, so dev/test hand back `2026-10-06T11:06:36` where Postgres appends `+00:00`, and `new Date()` would otherwise read those as the *viewer's* local time.
- **Instants are rendered in the timezone they were judged in, not the viewer's.** Enrollment-scoped instants (Today's windows) pass `data-jalali-tz="{{ item.timezone }}"`; `APP_TIMEZONE` (`Asia/Tehran`) is only the fallback for challenge-level instants like `due_date`/`created_at`, mirroring `DEFAULT_TIMEZONE` in `routers/challenge.py`.
- Two CLDR `fa` patterns come out year-first (`۱۴۰۵ شهریور`), which no Iranian writes: `datetime-full` therefore uses `dateStyle`/`timeStyle`, and the `month` label is reassembled from `formatToParts`. Time formats pin `hour12: false`.
- **Date entry** is `app/static/js/jalali-picker.js`, loaded only by `create-challenge.html`. Native `<input type="date">` / `type="datetime-local"` cannot render a Jalali calendar, so it hides each one (keeping it in the DOM as the value holder, same ISO format, same `min`/`max`, still firing `input`/`change`) and puts a `.jp-trigger` button plus a bottom sheet in front. Everything reading `.value` is untouched. Its calendar maths comes from ICU too — 1 Farvardin is found by asking `Intl` which of 19–23 March it is, rather than reimplementing the 33-year leap cycle. Rows added after load (the schedule list) must call `enhanceJalaliInputs(row)`.
- Today's cards split the deadline in two: the server renders the fixed window label (only it knows the cadence — `تا پایان امروز` for `recurring_days`, `تا پایان این هفته/ماه` for `recurring_quota`, the clock time for `schedule`, no deadline for `once`), and `[data-deadline]` carries a live remainder that `renderDeadlines()` re-ticks every 60s. `TodayItem` exists to feed this: it carries `timezone`, `cadence_kind`, and `quota_period` for exactly that reason. `closes_at_utc` is an *exclusive* bound (local midnight opening the next day), so `data-deadline-exclusive` makes the tooltip name the last instant inside the window instead.

**The SSR "form" pages post JSON, not form-encoded data.** `POST /views/challenges/create` takes a Pydantic `ChallengeCreateUnion` body, and `create-challenge.html` / `auth.html` submit via `fetch(..., {headers: {"Content-Type": "application/json"}})`. Don't "fix" these handlers to `Form(...)` without changing the templates.
