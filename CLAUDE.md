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
python -m pytest tests\ -q             # run the test suite (in-memory SQLite, no Postgres needed)
```

Gotchas around dependencies:

- **`requirements.txt` is UTF-16 encoded.** Writing it with a plain-text tool corrupts it — use `pip freeze > requirements.txt` (PowerShell redirection preserves UTF-16 here) or verify the BOM after editing.
- It pins `starlette<1.0.0`. The installed venv can drift ahead of that pin, which breaks **every** `TemplateResponse` call with a confusing signature error. If all HTML pages suddenly 500, check `pip show starlette` first.
- `app/scripts/generate_mock_data.py` imports `faker`, which is installed in the local venv but **missing from `requirements.txt`** — so the seed script fails inside the Docker image. Add it to the pin file (respecting the UTF-16 caveat above) if seeding needs to work there.

Debugging in Docker: `.vscode/launch.json` has "Docker: Attach to FastAPI" (attach to `localhost:5678`, `${workspaceFolder}` → `/app`). `app/main.py` has a commented-out `debugpy.listen(...)`; the compose `command` already wraps uvicorn in debugpy, so leave it commented.

## Testing

`tests/` covers the occurrence engine (`test_occurrences.py`), the Jalali converter against ICU (`test_jalali.py`), timezone/backfill-window edge cases (`test_timezone_boundaries.py`), check-in idempotency (`test_checkin_idempotency.py`), and the challenge lock/visibility/auto-enrollment rules (`test_challenge_lifecycle.py`). Run with:

```powershell
.venv\Scripts\python -m pytest tests\ -q
```

`tests/conftest.py` spins up an in-memory SQLite engine (`sqlite+aiosqlite:///:memory:`), creates the schema with `Base.metadata.create_all`, overrides `get_db`, and drives the real app through an `httpx.AsyncClient` (`ASGITransport`) — no Postgres or Docker required. This only works because the three columns that used to be native Postgres enums (`challenge_type`, `recurrence_pattern`, `Enrollments.status`) are now plain `VARCHAR`; SQLite can't represent a PG enum type, so a test-suite failure that looks enum-shaped means a native PG enum crept back onto one of those columns. `pytest.ini` at the repo root sets `asyncio_mode = auto`, so async test functions don't need `@pytest.mark.asyncio`.

## Configuration

`app/config.py` (`pydantic-settings`) reads `database_url`, `environment`, and `secret_key` from `.env`. Two env files exist: `.env` (local SQLite, gitignored) and `.env.docker` (Postgres, consumed by `docker-compose.yml`); `.env.example` documents the Postgres shape. `secret_key` signs session cookies and defaults to an insecure dev placeholder. `environment != "development"` is what flips the session cookie to `secure=True`.

Note `alembic.ini`'s `sqlalchemy.url` is a dead value — `alembic/env.py` overwrites it with `settings.database_url` at runtime.

## Architecture

### Two router layers over one model set

`app/routers/` is the JSON API (`/challenges`, `/users`, `/enrollments`, `/auth`, `/checkins`, `/today`). `app/routers/views/` is the SSR layer (`/views/challenges`, `/views/users`, `/views/home`, `/views/auth`, `/views/today`). Both are mounted in `app/main.py` and hit the same ORM models. `/` redirects to `/views/today/`.

They are **not** fully independent: `routers/views/challenge.py` imports `challenge_visibility_filter`, `fetch_challenge_page`, and the page-size constants from `routers/challenge.py`. Query/visibility logic belongs in the API router and gets reused by the view; only presentation lives in `views/`. Some create/auth logic *is* still duplicated across the two layers — when changing it, check whether the parallel handler needs the same edit.

### Auth (`app/auth.py`)

Everything is written from scratch — no passlib, no JWT, no session store:

- Passwords: `pbkdf2_sha256$<iterations>$<salt_b64>$<digest_b64>`, 260k iterations, verified with `hmac.compare_digest`.
- Sessions: a stateless cookie `base64(user_id:expires_at).HMAC-SHA256`, keyed on `settings.secret_key`, 7-day expiry. Nothing is stored server-side, so **rotating `secret_key` logs everyone out** and there is no revocation.
- Dependencies: `get_current_user_id` (401s), `get_optional_user_id` (returns `None`), `get_current_user` (loads the row).

`POST /auth/login` and `POST /users/` (signup) both set the cookie; `POST /auth/logout` clears it. The HTML login/signup page is `/views/auth/`, and protected view routes redirect there with `?next=<path>`.

### Ownership and visibility — the rule to preserve

Routers derive the acting user from the session, **never** from a path/query/body param:

- Mutating a challenge or user requires being the owner → 403 otherwise.
- Enrollment routes are keyed on `challenge_id` + the session user; there is no client-supplied `user_id` anywhere in `routers/enrollment.py`.
- Reads go through `challenge_visibility_filter(user_id)` in `app/routers/challenge.py`: visible if `is_public`, owned by the requester, or the requester is enrolled. Anonymous callers see public challenges only.

New challenge/enrollment endpoints must reuse that filter and the same session-derived-user pattern.

### Models (`app/models/`)

- `AuditBase` is an abstract `Base` subclass adding `created_at` / `updated_at` / `last_modifier_user_id`. Routers set `updated_at` and `last_modifier_user_id` by hand on every mutation — there is no ORM event hook doing it.
- `Challenge` is a flat table — the old single-table-inheritance split into `OneTimeChallenge`/`RecurringChallenge` is gone, and with it the `with_polymorphic`/`MissingGreenlet` hazard that used to be documented here. Recurrence now lives in two columns: `cadence_kind` (`once` | `schedule` | `recurring_days` | `recurring_quota`) and `cadence` (JSON, shaped by the `CadenceUnion` discriminated union — see "Cadence, occurrences, and check-ins" below). `visibility` (`private`/`unlisted`/`public`) and `lifecycle_status` (`draft`/`active`/`archived`) are plain `String` columns, not native Postgres enums (only `category` stays a native PG enum — see the enum bullet below). `goal_amount`/`goal_unit` describe an optional collective target; a check-in's `amount` is required iff `goal_unit is not None`. `legacy_cadence` is a verbatim JSON snapshot of the pre-migration recurrence columns for rows the new cadence model can't losslessly re-express — it's preserved user data, never read by application code, and must not be treated as scaffolding to clean up.
- `Enrollment` joins `User`↔`Challenge`, unique on `(user_id, challenge_id)`. `status` is `active`/`completed`/`abandoned`. Per-enrollment `timezone` (IANA name, default `Asia/Tehran`), `start_date`, `current_streak`/`longest_streak`, and `last_checkin_local_date` feed the occurrence engine. `legacy_completed_count` is the old `completed_count`, kept read-only — nothing writes it and it is never converted into `CheckIn` rows.
- `CheckIn` (table `CheckIns`) is one row per recorded occurrence: `enrollment_id` + a denormalized `challenge_id` (so Discover's velocity query needs no join), `occurrence_key`, `state` (`completed`/`skipped` only — `pending`/`missed` are derived at read time, never stored), the local date and UTC instant recorded, a `timezone` snapshot, and optional `amount`/`unit`/`note`/`photo_url`. `UniqueConstraint(enrollment_id, occurrence_key)` is the idempotency guarantee — see "Check-ins" below.
- `ChallengeStats` (table `ChallengeStats`, PK = `challenge_id`) holds **monotonic counters only**: `participant_count`, `total_completions`, `total_amount`, `last_checkin_at`. Nothing here goes stale and there is no refresh job — Discover's 7-day velocity sort is computed live from `CheckIns` via `ix_checkins_challenge_created` instead of a cached `checkins_7d` column.
- **Table names are capitalized** (`Users`, `Challenges`, `Enrollments`, `CheckIns`, `ChallengeStats`) and must be quoted in raw SQL on Postgres. Match this when adding FKs or hand-written migrations.
- **`ChallengeCategory` is the one enum that keeps Farsi values; every enum introduced by the cadence migration uses English codes instead (D7).** `ChallengeCategory.FITNESS == "سلامت جسمانی"` still travels all the way to the browser — JS payloads and template `data-cat` attributes contain the literal Farsi text, and renaming that enum's *value* is a data migration plus a template/JS change, not a rename. `Visibility`, `LifecycleStatus`, `CadenceKind`, and `EnrollmentStatus`, by contrast, are plain English codes (`"public"`, `"active"`, `"recurring_days"`, ...); there is no shared Farsi display-map module for them yet, so each template/script that shows one to a user inlines its own Farsi label (e.g. `challenge-detail.html`'s `visibility != 'public'` check, or `create-challenge.html`'s `cadenceKindLabels`) — follow that existing per-template pattern rather than introducing a new one.

### Schemas (`app/schemas/`)

`schemas/challenge.py` is flat, not a discriminated union — `ChallengeCreate`/`ChallengeRead`/`ChallengeUpdate` each carry a single `cadence: CadenceUnion` field. The discriminated union lives in `app/schemas/cadence.py` instead: `OnceCadence` / `ScheduleCadence` / `RecurringDaysCadence` / `RecurringQuotaCadence`, keyed on `Field(discriminator="kind")`. Adding a cadence kind means adding a member to that union plus the matching branches in `app/occurrences.py` — not conditional fields on one flat schema.

`UserRead` vs `UserPublicRead` matters: the public variant drops `email` and is what `/users/` list/detail return.

### Cadence, occurrences, and check-ins

`app/occurrences.py` is pure logic — no DB, no routes — that turns a `CadenceUnion` plus an enrollment's `start_date`/`timezone` into today's due occurrences, streaks, and the writability of a given check-in key. Read it before touching `app/routers/checkin.py`, `app/routers/today.py`, or the challenge-detail heatmap.

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

Underscore-prefixed templates (`challenge/_challenge_cards.html`, `home/_enrollment_cards.html`) are **infinite-scroll fragments**, `{% include %}`-ed for the first server-rendered page and re-served by `/fragment` endpoints for subsequent pages. The contract:

- The `/fragment` route returns bare card markup plus an `X-Has-More: true|false` response header.
- `createInfiniteScroller()` in `app.js` observes a sentinel, appends the HTML, re-runs `renderIcons()` **and `renderDates()`** on the new nodes, and redirects to `/views/auth/` on a 401. A `reset()` supersedes in-flight requests via a request token — keep that guard when editing.
- `app.js` also exposes `renderIcons` (inline SVG set injected into `[data-icon]`), `renderDates`, `formatJalali`, `debounce`, and `showToast`; `[data-coming-soon]` elements toast instead of doing nothing.

### Dates and times in the UI

Storage stays Gregorian/UTC; **every date and time the user sees is Jalali**, converted client-side by `renderDates()` in `app.js` (`Intl.DateTimeFormat("fa-IR-u-ca-persian")`). There is deliberately no Python Jalali dependency — adding one would have to survive the UTF-16 `requirements.txt` caveat above, and `Intl` needs nothing. Templates render the ISO value as the element's text *and* into the data attribute, so the pre-JS/no-`Intl` fallback is a real (if Gregorian) date.

- `data-jalali="<ISO>"` + optional `data-jalali-format` (`day-month`, `weekday-day-month`, `day-month-year`, `month`, `numeric`, `time`, `datetime`, `datetime-full`), `data-jalali-tz`, `data-jalali-prefix`, `data-jalali-attr` (write into an attribute instead of `textContent`).
- **A bare `YYYY-MM-DD` is a floating local date and is never shifted between zones; a value containing `T` is an instant.** An instant with no offset is read as UTC — SQLite has no aware datetime type, so dev/test hand back `2026-10-06T11:06:36` where Postgres appends `+00:00`, and `new Date()` would otherwise read those as the *viewer's* local time.
- **Instants are rendered in the timezone they were judged in, not the viewer's.** Enrollment-scoped instants (Today's windows) pass `data-jalali-tz="{{ item.timezone }}"`; `APP_TIMEZONE` (`Asia/Tehran`) is only the fallback for challenge-level instants like `due_date`/`created_at`, mirroring `DEFAULT_TIMEZONE` in `routers/challenge.py`.
- Two CLDR `fa` patterns come out year-first (`۱۴۰۵ شهریور`), which no Iranian writes: `datetime-full` therefore uses `dateStyle`/`timeStyle`, and the `month` label is reassembled from `formatToParts`. Time formats pin `hour12: false`.
- **Date entry** is `app/static/js/jalali-picker.js`, loaded only by `create-challenge.html`. Native `<input type="date">` / `type="datetime-local"` cannot render a Jalali calendar, so it hides each one (keeping it in the DOM as the value holder, same ISO format, same `min`/`max`, still firing `input`/`change`) and puts a `.jp-trigger` button plus a bottom sheet in front. Everything reading `.value` is untouched. Its calendar maths comes from ICU too — 1 Farvardin is found by asking `Intl` which of 19–23 March it is, rather than reimplementing the 33-year leap cycle. Rows added after load (the schedule list) must call `enhanceJalaliInputs(row)`.
- Today's cards split the deadline in two: the server renders the fixed window label (only it knows the cadence — `تا پایان امروز` for `recurring_days`, `تا پایان این هفته/ماه` for `recurring_quota`, the clock time for `schedule`, no deadline for `once`), and `[data-deadline]` carries a live remainder that `renderDeadlines()` re-ticks every 60s. `TodayItem` exists to feed this: it carries `timezone`, `cadence_kind`, and `quota_period` for exactly that reason. `closes_at_utc` is an *exclusive* bound (local midnight opening the next day), so `data-deadline-exclusive` makes the tooltip name the last instant inside the window instead.

**The SSR "form" pages post JSON, not form-encoded data.** `POST /views/challenges/create` takes a Pydantic `ChallengeCreateUnion` body, and `create-challenge.html` / `auth.html` submit via `fetch(..., {headers: {"Content-Type": "application/json"}})`. Don't "fix" these handlers to `Form(...)` without changing the templates.
