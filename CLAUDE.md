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
python -m app.scripts.seed_admin           # ensure the SEED_ADMIN_* account exists (start.sh runs this)
python -m pytest tests\ -q             # run the test suite (in-memory SQLite, no Postgres needed)
```

Gotchas around dependencies:

- **`requirements.txt` is UTF-16 encoded.** Writing it with a plain-text tool corrupts it — use `pip freeze > requirements.txt` (PowerShell redirection preserves UTF-16 here) or verify the BOM after editing.
- It pins `starlette<1.0.0`. The installed venv can drift ahead of that pin, which breaks **every** `TemplateResponse` call with a confusing signature error. If all HTML pages suddenly 500, check `pip show starlette` first.
- `app/scripts/generate_mock_data.py` imports `faker`, which is installed in the local venv but **missing from `requirements.txt`** — so the seed script fails inside the Docker image. Add it to the pin file (respecting the UTF-16 caveat above) if seeding needs to work there.

Debugging in Docker: `.vscode/launch.json` has "Docker: Attach to FastAPI" (attach to `localhost:5678`, `${workspaceFolder}` → `/app`). `app/main.py` has a commented-out `debugpy.listen(...)`; the compose `command` already wraps uvicorn in debugpy, so leave it commented.

## Testing

`tests/` covers the occurrence engine (`test_occurrences.py`), the Jalali converter against ICU (`test_jalali.py`), timezone/backfill-window edge cases (`test_timezone_boundaries.py`), check-in idempotency (`test_checkin_idempotency.py`), the challenge lock/visibility/auto-enrollment rules (`test_challenge_lifecycle.py`), which SSR pages require a session (`test_page_authorization.py`), who may see a given row once they have one (`test_object_authorization.py`), the avatar catalogue (`test_avatars.py`), the settings page and the profile/settings split (`test_settings_page.py`), the two role axes and the line between them (`test_user_roles.py`), the admin panel's gate, its roster search/role filter and its lazy-loading fragment (`test_admin_page.py`), the moderation roster's gate, reach and the line between moderating and editing (`test_admin_challenges.py`), the per-challenge participant roster's gate and the membership sort behind it (`test_admin_participants.py`), the home dashboard's counting and grid frame (`test_home_dashboard.py`), the newest-first ordering every paginated list shares (`test_list_ordering.py`), the boot-time operator account (`test_seed_admin.py`), the notification feed's invariants, ownership and gate (`test_notifications.py`), anonymous participation's resolution/lock/leak (`test_anonymity.py`), the onboarding tour's step/anchor contract (`test_tour_anchors.py`), the SMS login flow -- its normalization, its six invariants and the account it creates from nothing but a number (`test_otp_auth.py`), the in-place explainer's key/registration contract (`test_explainers.py`), and the group subsystem — its three-way role split, its 404 gate and the owner who cannot vanish (`test_groups.py`), the invite link's capacity under a real concurrent race (`test_group_invites.py`), the audience, the standing «همه» and the mandatory lock (`test_group_challenges.py`), the one conjunct that makes a group's challenges reach every door and no other (`test_group_visibility.py`), the un-asked anonymity default and what administering a group does *not* buy (`test_group_privacy.py`), the seven group notification kinds (`test_group_notifications.py`), the
membership request queue on a screen of its own — its default, its two
orders and its gate (`test_group_requests.py`), the two exits — the bulk unenrol that keeps the obligations, the leave that releases them, and the removal that touches neither (`test_group_leaving.py`), and the third door in with the second approval behind it — adding somebody by their number, the public link that admits nobody, and the «محرمیت» that is what actually lets a member into the group's standing challenges (`test_group_trust.py`). Run with:

```powershell
.venv\Scripts\python -m pytest tests\ -q
```

`tests/conftest.py` spins up an in-memory SQLite engine (`sqlite+aiosqlite:///:memory:`), creates the schema with `Base.metadata.create_all`, overrides `get_db`, and drives the real app through an `httpx.AsyncClient` (`ASGITransport`) — no Postgres or Docker required. This only works because the three columns that used to be native Postgres enums (`challenge_type`, `recurrence_pattern`, `Enrollments.status`) are now plain `VARCHAR`; SQLite can't represent a PG enum type, so a test-suite failure that looks enum-shaped means a native PG enum crept back onto one of those columns. `pytest.ini` at the repo root sets `asyncio_mode = auto`, so async test functions don't need `@pytest.mark.asyncio`.

## Configuration

`app/config.py` (`pydantic-settings`) reads `database_url`, `environment`, and `secret_key` from `.env`. Two env files exist: `.env` (local, gitignored) and `.env.docker` (consumed by `docker-compose.yml`); `.env.example` documents the shape. `secret_key` signs session cookies and defaults to an insecure dev placeholder. `environment != "development"` is what flips the session cookie to `secure=True`.

**Every environment runs on SQLite — local, compose, and the Liara deploy — on purpose.** The two used to differ (Postgres locally, SQLite on the disk Liara mounts), which meant a column added through a Postgres-only migration reached production as `no such column: Users.avatar`. `DEFAULT_SQLITE_URL` in `config.py` is `sqlite+aiosqlite:///./data/challenges.db`; `data/` is the mount point in `liara.json`, and `WORKDIR` is `/app`, so the relative path resolves onto the persistent disk. `settings.is_sqlite` / `settings.sqlite_path` are the accessors (the latter goes through `make_url`, since `sqlite:///rel` vs `sqlite:////abs` differ by one slash), and importing `config` creates the parent directory — a missing one fails at connect time, not import, and reads as a query bug.

**Postgres is still wired up and must stay that way**: the whole `alembic/` history, the `db` service in `docker-compose.yml`, the `asyncpg` pin, and a commented `DATABASE_URL` line in each env file. Switching back is uncommenting that line and running `alembic upgrade head`.

**Schema changes go to the models, and nothing else.** `app/scripts/bootstrap_db.py` (run by `start.sh` before uvicorn) branches on the backend: on Postgres it runs `alembic upgrade head`; on SQLite it runs `Base.metadata.create_all` and then `sync_sqlite_schema()`, which diffs each existing table against `Base.metadata` and issues `ALTER TABLE ADD COLUMN` / `CREATE INDEX` for what the file is missing. `create_all` alone will not do this — it skips a table that already exists, which is exactly how the avatar column shipped without arriving. What the sync deliberately cannot do is *change* an existing column (SQLite has no real `ALTER COLUMN`) or add a NOT NULL column with no default to a non-empty table; the second case prints a loud warning naming the column rather than failing the boot or silently relaxing the constraint. New migrations are only needed for the eventual switch back to Postgres.

**The operator account is seeded on every boot.** A deploy's database is a
*different* database, and the first admin deliberately cannot be made through
the app (`set_user_role` needs a shell), so a fresh Liara disk would boot with
no way in. `start.sh` therefore runs `app/scripts/seed_admin.py` right after
`bootstrap_db`; it is idempotent and declarative — creates the account named by
`SEED_ADMIN_EMAIL`/`SEED_ADMIN_PASSWORD` with `role=admin` if the email is
unknown, promotes it if it exists as a member, does nothing if it is already an
admin, and does nothing at all when `SEED_ADMIN_EMAIL` is empty (the default,
so nothing is seeded by accident).

**It never rewrites an existing account's password.** That is the rule to keep:
resetting on every boot would revert a password its owner changed, and would
make the env var a way to take over any account whose email is put in it.
`SEED_ADMIN_RESET_PASSWORD=true` forces it for one boot and is the locked-out
escape hatch. `.env` and `.env.docker` are both gitignored *and* in
`.dockerignore`, so the deploy reads these from Liara's own environment
variables, not from a file in the image. `tests/test_seed_admin.py` pins the
create/promote/idempotent/no-reset behaviour.

Note `alembic.ini`'s `sqlalchemy.url` is a dead value — `alembic/env.py` overwrites it with `settings.database_url` at runtime.

## Architecture

### Two router layers over one model set

`app/routers/` is the JSON API (`/challenges`, `/users`, `/enrollments`, `/groups`, `/invites`, `/auth`, `/checkins`, `/today`). `app/routers/views/` is the SSR layer (`/views/challenges`, `/views/users`, `/views/groups`, `/views/invites`, `/views/home`, `/views/auth`, `/views/today`). Both are mounted in `app/main.py` and hit the same ORM models. `/` redirects to `/views/today/`.

They are **not** fully independent: `routers/views/challenge.py` imports `challenge_visibility_filter`, `fetch_challenge_page`, and the page-size constants from `routers/challenge.py`. Query/visibility logic belongs in the API router and gets reused by the view; only presentation lives in `views/`. Some create/auth logic *is* still duplicated across the two layers — when changing it, check whether the parallel handler needs the same edit.

**The two layers are not meant to mirror each other endpoint-for-endpoint.** Only `create` has a `/views/` twin (`POST /views/challenges/create`, which exists to answer JSON `{id}` to a `fetch()` instead of a redirect the caller would discard). Every other mutation — enrol/unenrol, check in, edit, delete, login, signup, logout — is called from page JS against the JSON API directly, so a missing `/views/` route is the design, not a gap. What *is* a gap is a JSON endpoint no page can reach: `PATCH`/`DELETE /challenges/{id}` and `PATCH`/`DELETE /checkins/{id}` had no caller at all until the manage sheet and the timeline's edit button were added to `challenge-detail.html`. `GET /challenges/{id}/stats`, `GET /enrollments/{id}/history` and `GET /today/` are deliberately API-only: the SSR pages compute the same numbers server-side and inline them, so those exist for API consumers, not for the templates. The `/views/*/fragment` routes are the reverse and equally deliberate — they return card markup for infinite scroll and have no business being JSON.

### Auth (`app/auth.py`)

Everything is written from scratch — no passlib, no JWT, no session store:

- Passwords: `pbkdf2_sha256$<iterations>$<salt_b64>$<digest_b64>`, 260k iterations, verified with `hmac.compare_digest`.
- Sessions: a stateless cookie `base64(user_id:expires_at).HMAC-SHA256`, keyed on `settings.secret_key`, 7-day expiry. Nothing is stored server-side, so **rotating `secret_key` logs everyone out** and there is no revocation.
- Dependencies: `get_current_user_id` (401s), `get_optional_user_id` (returns `None`), `get_current_user` (loads the row), `get_page_user` (loads the row, redirects to login — see the 303/401 split below).

`POST /auth/login` and `POST /users/` (signup) both set the cookie; `POST /auth/logout` clears it. The HTML login/signup page is `/views/auth/`, and protected view routes redirect there with `?next=<path>`. `/views/auth/` itself bounces an already-signed-in visitor straight to `next` — otherwise the bottom nav's profile tab parks them on a login form for the session they already hold.

### Signing in by SMS — one flow for signing up and signing in

`app/phone.py` (a typed string becomes an identity), `app/sms.py` (the
Kavenegar gateway, the only place the provider exists), `app/otp.py` (the
whole policy), `app/models/otp.py` (the row), `app/schemas/otp.py` and
`app/routers/otp.py` (`POST /auth/otp/request`, `POST /auth/otp/verify`). The
SSR page is `/views/auth/`, which calls those two endpoints exactly as every
other page in this app calls the JSON API.

**Sign-up and sign-in are one flow, and that is the central decision.** There
is no "do you have an account" branch anywhere: `/request` texts a code to
whatever number it is given, and `/verify` creates the account if the proved
number is new. Three things fall out of it at once — an enumeration attempt
learns nothing because there is no second path to reveal, a member does not
have to know something about themselves before they can act, and **an account
needs no personal information at all**: name, email, password and avatar are
all absent from that path. A new account gets a placeholder «کاربر ۴۵۶۷» from
the last four digits of its own number and lands on its own profile once,
which is where a real name goes if the member wants one.

**The credential is `Users.mobile`, and it is deliberately not `Users.phone`.**
`phone` stays exactly what it always was: an optional free-text contact detail
a member types on their profile, permissive enough for a number abroad,
writable through `PATCH /users/{id}` like any other profile field — which is
precisely why it cannot be the credential, since a column any member may set
to arbitrary text is a column any member could write somebody else's login
into. `mobile` is UNIQUE, canonical `+989…`, absent from every `User*` write
schema, and has **one door**: `POST /auth/otp/verify`, after a code was
proved. `mobile_verified_at` sits beside it and is re-stamped on every
sign-in, because "when did we last see proof of this number" is the question
support actually gets. Both are nullable, permanently and legitimately — an
account that signs in with a password has neither — which is also what lets
`sync_sqlite_schema` add them to a populated table (NULLs do not collide in a
UNIQUE index on either backend). The profile renders the verified number as a
read-only «شماره ورود» row above the editable ones and only when there is one;
the editable field was relabelled «شماره تماس» so the two are not read as one
thing.

**`app/phone.py` is the one place a typed string becomes an identity.**
`09121234567`, `+98 912 123 4567`, `۰۹۱۲۱۲۳۴۵۶۷` and `00989121234567` are one
account, and without a single normalizer they are four — the fourth of which
gets an SMS the first three never see. It is Iran-only on purpose: this gates
a login, the gateway delivers to Iranian carriers, and a number the app cannot
text is a member it cannot let back in. That is a different question from
`schemas/user.py`'s `_plausible_phone`, which stays permissive because it
guards a contact detail rather than a credential. The invisible bidi marks a
number pastes out of an RTL app with are stripped, and are named by code point
rather than written into the pattern — a source line carrying control
characters is a line nobody can review.

**`app/otp.py` is the only door in**, the same shape `app/notifications.py`
has and for the same reason: every invariant here is one a call site would
otherwise have to remember, and the one that forgets is not a visible error
but a login anybody can walk into. Six invariants:

1. **The code is never stored.** `hash_code` is an HMAC keyed on `secret_key`
   and **bound to the mobile**, so a leaked table is not a set of working
   logins and a code seen for one number cannot be replayed against another.
   Rotating `secret_key` invalidates every outstanding code — the same
   consequence it already has for every session cookie.
2. **A code dies on the first success and on the last wrong guess.** Six
   digits is a million possibilities, which is nothing to a script if guessing
   is free, so `attempts` is counted on the row and the row is *burned* at
   `MAX_ATTEMPTS` rather than merely counted — the next guess must find no
   live row at all. Rate-limiting the requests alone would leave the guessing
   unbounded.
3. **Requesting a new code kills the old ones.** Otherwise every resend widens
   the set of currently-valid codes, and a member tapping «ارسال دوباره» three
   times has tripled an attacker's odds rather than helped themselves.
4. **Sending is bounded per number *and* per source**, because either window
   alone is trivially sidestepped — one host walking a list of numbers, or a
   botnet hammering one number. `X-Forwarded-For` is honoured and is known to
   be spoofable: the header only ever *widens* how many buckets exist, so
   forging it lets an attacker escape their own limit but never consume
   somebody else's, and the limit that actually protects a member — and the
   SMS bill for one number — is the per-mobile one, which no header can move.
5. **Nothing says whether an account exists**, per the unified flow above. One
   refusal message covers "never asked", "expired", "already used" and
   "wrong", because distinguishing them tells an attacker which numbers have a
   code outstanding and tells a member nothing they could act on differently.
6. **The code goes out before the row is committed.** `request_code` flushes
   and sends but never commits; the caller commits, exactly as `notify()`
   does. A gateway failure therefore rolls the row back with the request, so
   nobody holds a live code they were never sent and nobody spends a
   rate-limit slot on an SMS that did not happen. `verify_code` is the
   opposite on refusal — the router **commits** the attempt counter, since
   discarding it is what would make `MAX_ATTEMPTS` unenforceable.

**Password accounts still work, and an OTP account has no password.**
`Users.password_hash` is NOT NULL and SQLite cannot relax a column in place
(the schema-change rule above), so an OTP account gets
`unusable_password_hash()` — a random `!`-prefixed marker that is
syntactically not a `pbkdf2_sha256$…` string. `verify_password` refuses it on
the *meaning* rather than on the parse, so the refusal survives a change of
hash format, and the marker is random rather than a shared constant so two
such accounts are not equal to each other either. This is Django's device, for
its reasons.

**The provider is Kavenegar's `verify/lookup`, not `sms/send`.** That is the
route meant for one-time codes in Iran: no sender line to get approved, it
reaches numbers that have opted out of bulk messaging, and it is not subject
to the night-time delivery window. The cost is that the *wording* lives in an
approved template on the Kavenegar panel rather than in this repo — the app
sends a token and the panel decides the sentence around it — which is this
app's own "store the event, derive the sentence" rule with the derivation
happening one system over. `sms/send` remains as the fallback for a deploy
that has a sender line and no approved template yet (`KAVENEGAR_SENDER` +
`OTP_SMS_TEXT`), and is the lesser path on purpose. Kavenegar answers `200 OK`
at the HTTP layer for most failures and carries the real outcome in
`return.status`, so both layers are checked and the provider's own wins.
**Development without a working gateway does not fail**: the code is printed
to the server log and the send counts as successful, so the whole login runs
offline like the rest of the stack. "Working" is the *pair* — a key plus one
of template/sender — not the key alone, because a key pasted in before its
template is approved is the normal state of this integration for a day or
two, and a local login screen that dies during it is worse than one that logs
the code. Outside development that same state is a hard failure: a login
screen silently accepting codes nobody was sent is worse than one that is
plainly down. No new dependency; `httpx` was already pinned.

**The page is two steps on one card, not two screens.** Step two has to keep
naming the number the code went to — the commonest failure in this flow is a
typo in that number, and a member who has navigated away from it cannot check
— so `.otp-target` shows the masked number with «تغییر شماره» beside it. Six
`maxlength="1"` boxes, running `dir="ltr"` inside the RTL page because a
number does, and because the first digit of the SMS has to be the box the
cursor starts in or every paste lands reversed. **One `input` handler covers
typing, pasting and platform autofill**: all three arrive as an `input` event,
and autofill in particular drops the whole code into whichever box is focused,
so spreading it from there means there is no second code path that could get
the order wrong. It folds Farsi digits (the boxes are one character wide, so
an unfolded ۵ would occupy a box and read as filled while meaning nothing),
auto-submits on the sixth digit, and steps backwards on Backspace from an
empty box. The resend control **is** the countdown until it reaches zero:
offering a button that answers 429 would be a control that lies. Errors are an
inline `.auth-error`, not a toast — a toast that fades takes the only
explanation of a refusal with it, and this is the one screen a member cannot
navigate past to go and re-read it. Colour means nothing new: status owns the
`--st-*` hues and category the six `--cat-*` ones, a login screen is neither,
and the accent is spent only on the one control meant to be pressed.

Email + password is kept for the accounts that have one, moved behind a
`.auth-alt` link under the card rather than a third tab — SMS is the path
essentially everybody takes, and two equally-weighted options would ask a
question most members have no reason to answer. Both of its forms still post
to the same JSON endpoints they always did.

**Where it grows.** Binding a mobile to an *existing* password account is a
second value on `OtpCodes.purpose` plus a route that requires a session, and
is deliberately not built: it carries a merge question (what if that number
already belongs to another account) the anonymous flow does not have.
`purge_expired_codes` exists and nothing schedules it — there is no job runner
in this app — and its window is days rather than minutes because a burned row
is the only record of a number being hammered. `tests/test_otp_auth.py` pins
the normalization, the six invariants, the account created from nothing but a
number, the credential's single door and the page's anchors.

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

**Profiles are own-only for members, and reachable by an operator.** `profile_visibility_filter(viewer)` takes the `User` row (both callers already hold it) and answers `true()` for a holder of `Perm.USER_VIEW_ANY`, `User.id == viewer.id` for everyone else. `GET /views/users/{id}` and `GET /users/{id}` both compose it — the widening was an edit to *that one function*, which is exactly the door CLAUDE.md left for it, and nothing in either route changed. For a member the rule and its reasoning are untouched: nothing in the app links them to another profile, the path id is a bare sequential integer, so a miss is a **404** on both front doors and a walk of `1..n` learns nothing. The admin read still answers `UserPublicRead`; the wider `UserAdminRead` stays confined to the `Perm.USER_LIST` roster route.

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

**Three role columns, deliberately never merged** (the third arrived with
Groups — see that section):

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

**Support is the fourth thing, and it is the one place an admin may edit
rather than moderate.** `USER_VIEW_ANY` / `USER_EDIT_ANY` let an operator open
any member's profile and correct what it holds. That is deliberately the
opposite call from `CHALLENGE_EDIT`, and the line is authorship: a challenge is
*written* and published under someone's name, so an admin rewriting its title
or cadence would be indistinguishable from its author, while an account's
contact fields are the member's own record held on their behalf and correcting
a mistyped email on request is the whole job. Every such write goes through
`update_user`, which stamps `last_modifier_user_id` with the **acting** user,
so the edit is attributable — that column is what makes the grant defensible.
Three things stay out and each has its own door or none: the **password** (no
route sets someone else's; `app/scripts/seed_admin.py` is the operator-console
reset), the **role** (`PATCH /users/{id}/role`), and **deletion**
(`DELETE /users/{id}` is own-only and 409s once any history exists).
`tests/test_admin_profile.py` pins all of it.

**The profile is the panel's member screen**, not a second one. A roster row
is a plain `<a>` into `/views/users/{id}` — openable in a new tab, and it puts
a role change in front of the evidence for it instead of beside a name in a
list of thirty. The page itself branches on three flags from
`routers/views/user.py`: `is_own_profile` (logout, settings, history, the panel
row — the things that are only ever mine), `can_administer` (the banner naming
whose account this is, and the «مدیریت عضو» role row; false on your own profile
even holding the permission, since the server refuses self-demotion), and
`can_edit`, their union, which is the *only* flag the avatar picker and the
account sheet read — so an operator's edit is the same `PATCH /users/{id}` a
member's is, permitted on the server rather than duplicated in the template.

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
index is the only way in — plus the three «یک نگاه» counts, which are links
into the list each one counts. «عضو» is an in-page anchor (`#adminMembers`),
because the member roster *is* the index's own list; «چالش» opens the
moderation roster; «عضویت» opens it again with `?sort=members`.

**«عضویت» is a sort, not a fourth screen.** A list of enrollments broken down
by challenge *is* the challenge list in a different order, so it is
`fetch_challenge_page(sort=SORT_MEMBERS)` and a two-button `.seg`
(«تازه‌ترین» / «پرعضوترین») on the roster it already has, rather than a second
page serving the same rows under a second URL — which is how one search term
starts meaning two things. The rank is counted live off `Enrollments`
(`_member_count()` in `routers/challenge.py`), deliberately **not** off
`ChallengeStats.participant_count`: that counter is monotonic and never comes
back down, so it would rank a challenge everyone has left above one they are
still in — the same reason Discover computes its velocity live. The roster
does not offer `velocity`: "what is hot" is a discovery question, and its
`?sort=` pattern refuses it rather than ignoring it.

**The participant roster is the drill-down, and it is operator-only.**
`/views/admin/challenges/{id}/members` + `templates/admin/challenge_members.html`
(fragment `admin/_participant_rows.html`, query `fetch_participant_page` in
`routers/enrollment.py`) is the one screen in the app that answers *who is in
this*. That is exactly what `profile_visibility_filter` withholds from a
member — which is why challenge-detail renders participant initials and never
a roster — so it is gated like everything else here (page 404s a member,
fragment 403s a `fetch()`) and has **no JSON endpoint**. Its rows link to each
participant's profile, which works for the same reason the screen exists: an
operator passes that filter and a member does not. A *member-facing*
participant list still means loosening that one function again, not opening
this route. The rows carry no actions either:
removing someone from a challenge is not a power `app/permissions.py` grants
an operator, and an admin who could do it would be indistinguishable from the
owner in the record. Search is `apply_member_filters` reused, so "search means
name and email" stays one rule across both rosters.

The way in is the moderation row's own «N عضو» line — a real `<a>` in a
`.sr-foot` under the row, because it is navigation rather than a moderation
action, and because the count is what an operator is already reading when they
want to know *who*. The row's delegated click handler bails on any `<a>` it
contains, so tapping it does not also open the sheet behind it. That count is
*every* enrollment — the figure `sort=members` ranks by and the «عضویت» card
totals — while `data-challenge-others` stays the non-owner count the delete
guard uses; they are two different numbers on purpose.
`tests/test_admin_participants.py` pins the gate, the sort and the links.

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

### List ordering — one rule, newest first

Every paginated list — the challenge list and its `/fragment`, the home
dashboard's enrolments, `GET /enrollments/`, the admin roster and the
moderation roster (which is `fetch_challenge_page` again) — orders by
`newest_first(Model)` in `app/models/audit_base.py`, which is
`(created_at.desc(), id.desc())`. There is no per-surface ordering to
reconcile: a member and an operator read the same list in the same order.

`created_at` is the key rather than `id` because it is what the ordering
*means*, and it is immutable — routers only ever write `updated_at` — so a
scroller can page through it without a row it has already shown reappearing
further down. **The `id` tie-break is not decoration**: `server_default=
func.now()` has second granularity on SQLite, so a seed run or a burst of
signups writes whole blocks of rows sharing one timestamp, and without it
their order is undefined per query and a page boundary inside such a block
drops or repeats a member. `?sort=velocity` on the challenge list and
`?sort=members` on the moderation roster each keep their own leading key and
fall back to this pair for everything they tie on.
`tests/test_list_ordering.py` pins both the direction and the tie-break.

**One list reads the other way, and it says why.** A group's *pending*
membership requests are a queue rather than a feed — the person who has been
waiting longest is the answer that is owed — so `fetch_request_page` orders
them oldest-first, and only them: the same query's terminal states are
history and read newest-first off `decided_at`. Both still fall through to
the id tie-break, for the reason above.

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

### Anonymous participation — the policy decides, the answer is stored

A challenge carries `identity_mode` (`named` | `anonymous` | `member_choice`)
and an enrollment carries `is_anonymous`. `app/identity.py` is the only place
the first becomes the second, the same "one rule, one place it becomes an
answer" shape `app/permissions.py` and `app/notifications.py` have.

**The policy decides; the request is only consulted where the policy defers
to it.** `resolve_anonymity(identity_mode, requested)` runs at the write
boundary in `routers/enrollment.py` — the same single-gate design as the
`avatar` field validator. A body asking to hide inside a `named` challenge is
answered with a *named* enrolment and a 201, not a 422: the request is simply
not the question that challenge asks. An unrecognised mode falls back to the
column default rather than raising — a challenge nobody can join is worse
than one behaving like the default it was created with.

**The answer is stored, not re-derived on every read**, and that is the whole
design. Recomputing from the challenge's *current* mode each time a name is
rendered is what makes a mode change retroactive, silently unmasking people
who joined under a different promise. So `identity_mode` joins `cadence` /
`goal_amount` / `goal_unit` in `locked_fields` (`update_challenge`), frozen
by `count_non_owner_enrollments` once anyone but the owner has joined — for a
different reason than the others, but the same reason as the 409 on hard
delete: it protects what other people already committed to. Before anyone
joins there is nothing to break, so an owner can still fix a mistake; the
only enrolment that exists then is their own.

**The owner is never anonymous.** Anonymity here is about *participation*;
authorship is separate, and a challenge whose content nobody is accountable
for is a moderation problem rather than a privacy feature. The creator's D1
auto-enrolment is written `is_anonymous=False` in every mode and
`resolve_anonymity` is deliberately not consulted for it — challenge-detail
keeps naming them as «سازنده». This is the same line the roles section draws
between moderating and authoring.

**Two surfaces hide something, and one deliberately does not.** The
participant avatars on challenge-detail go through the `participant_avatar`
filter, which answers `None` for an anonymous member so `avatar_url` renders
the same `_default.svg` an unset member gets — a picture from a fixed
catalogue names nobody, but *their own* avatar is recognisable to anyone who
has seen it elsewhere. The owner's join/leave notifications are raised with
`anonymous_actor=True`, which is invariant four in `app/notifications.py`:
the row is stored with **no** `actor_user_id` at all, so the sentence falls
back to `UNKNOWN_ACTOR` («یکی از اعضا»), the same stand-in a deleted account
gets — indistinguishable on purpose, since a wording reserved for anonymity
would announce that someone chose it. That drop happens at the *write*, after
the self-drop rule has run against the real actor, for a reason worth
keeping: `ENROLLMENT_LEFT` outlives the enrollment row that carried the
choice, so there would be nothing left to ask at render time — and a feed
that stores the name and merely declines to print it is one template away
from printing it. The operator's participant roster is the exception: an
admin sees who is really there, because moderating abuse it cannot attribute
is not moderation, and the row carries a «ناشناس برای بقیه» mark so the
operator can see which expectation they are holding.

**`EnrollmentUpdate` deliberately does not carry `is_anonymous`.**
`update_my_enrollment` writes whatever that shape holds straight onto the
row, so adding the field would make the PATCH a way around
`resolve_anonymity`. Changing one's mind is a feature that belongs behind
that same resolution, not behind a blind `setattr`.

The choice is asked once, in the join sheet on challenge-detail, and only
when `asks_the_joiner(identity_mode)` — a `createSheet` with `type: "chips"`
instead of the bare `confirm()` every other join gets, since there is
nothing to decide in the other two modes. That sheet is why `createSheet`
grew `onClose` (a sheet that *asks* a question must hear about a dismissal,
or a caller awaiting the answer waits forever) and `hint` on a field
(`.field-note`, the `.sr-hint` type scale — «ناشناس» is a promise, and a
promise nobody spelled out is a promise nobody trusts). Farsi labels follow
the per-surface pattern (D7): `IDENTITY_LABELS`/`HINTS`/`ICONS` in
`routers/views/challenge.py` for the server-rendered surfaces, and the create
wizard keeps its own client-side copy exactly as it does for cadence — adding
a mode means editing both. `tests/test_anonymity.py` pins the resolution, the
lock and the leak.

### Notifications — the event is stored, the sentence is derived

`/views/notifications/` is one flat feed of what happened to a member, and
the bell in `layout.html`'s header is its only entrance. Four pieces:
`app/models/notification.py` (the row), `app/notifications.py` (the wording
and the *only* way one is created), `app/routers/notification.py` (the
queries and three JSON endpoints), `app/routers/views/notification.py` +
`templates/notification/` (the page and its `/fragment`).

**A notification stores the event, never the text.** There is no `title` or
`body` column — `kind` plus `actor_user_id` plus `challenge_id`, and
`NOTIFICATION_META` in `app/notifications.py` is the single place a kind
becomes Farsi (reaching templates as the `notification_title` /
`notification_text` / `notification_icon` filters, the same
`register_icon_filters` registration contract, so a views router that renders
one must call `register_notification_filters`). Rewording a notification, or
translating the set, is therefore a code change rather than a data migration
over every row ever written — the same "one fact, one place it becomes text"
rule the derived challenge status follows. The JSON `NotificationRead` keeps
that promise outward: it answers `kind`/`actor_name`/`challenge_title`
structured, and lets a consumer word it.

**`notify()` / `notify_many()` are the only door in, and they hold two
invariants** a call site would otherwise have to remember and would forget:

- **You are never told about your own action.** `notify` drops a
  notification whose recipient is its actor. Every emitting site has a case
  where the two coincide — the creator's D1 auto-enrolment in their own
  challenge, an owner archiving it, a moderator acting on one they happen to
  be enrolled in — and a feed that reports your own taps back to you trains
  people to ignore the bell.
- **It lands in the event's own transaction.** They `db.add()` and never
  `commit()`; the caller commits once. A rolled-back enrolment cannot leave
  behind a notification saying it happened.

**Three events raise one today**, and each is a place a member could not
otherwise find out: someone joined or left a challenge you own (both halves —
a feed that only ever announces growth flatters), and a challenge you are
enrolled in changed lifecycle state.

**The membership pair fires only for a challenge that is not public**
(`membership_is_announced` in `routers/enrollment.py`, asked by both halves
so the rule cannot be honoured on the way in and forgotten on the way out).
A public challenge is a noticeboard — anyone who finds it may join, and its
owner invited none of them — so an arrival is a number on the roster rather
than an event, and a feed of them buries the notifications that *are* about
the owner's own doing. Private and unlisted are the opposite: reaching one
took a link the owner handed out. The two `hint`s in `NOTIFICATION_META` say
«خصوصی» for that reason — a switch that promises more than the event
delivers reads as broken. The lifecycle broadcast is deliberately *not*
narrowed this way: an archived challenge stops producing occurrences
whoever can see it. That last is the app's one *broadcast*
(`notify_lifecycle_change` in `routers/challenge.py`) and it is keyed on the
**new state alone, and only on a real transition**: an archived challenge
stops producing occurrences, so a member who is not told simply watches their
streak stop. Every other field a PATCH can carry is already visible on the
page they would be looking at. Moderation is the case it matters most for —
the owner did not do it and has no other way to learn of it.

**Access is one clause, not a filter to compose.** The whole table is
per-member, so there is no third visibility function: every statement in
`routers/notification.py` carries `Notification.user_id == <session user>`,
and there is no client-supplied recipient anywhere, the same rule
`routers/enrollment.py` follows. The page takes `get_page_user` and the
`/fragment` takes `get_current_user_id` — the 303/401 split, for the reason
every other pair has it.

**Marking read is a POST the page sends, not a side effect of the GET that
renders it.** `POST /notifications/read` stamps every unread row of the
caller's (`read_at.is_(None)` in the UPDATE, so re-reading never rewrites
when it was *first* seen), and the feed fires it once its rows are on screen
and repaints nothing — so what was new keeps its «تازه» mark for that view
while the bell goes quiet on the next page. That is also why there is **no**
per-row `PATCH`, no `DELETE`, and no «خوانده‌نشده» tab: everything here is
informational, nothing is ever kept unread as a task, and a control
partitioning a list nobody needs partitioned is a control that has to be
explained. The three endpoints that do exist each have a caller, per the
"no JSON endpoint a page can reach" rule.

**Which kinds reach you is a mute list on the recipient, enforced in
`notify()`.** `Users.muted_notification_kinds` is a JSON list of kind codes
(NULL and `[]` both mean "nothing muted"), and the check lives in the one
door in rather than at each emitting site — a preference honoured only by the
call sites that remembered it stops working the first time a kind is added.
That is why `notify`/`notify_many` are async: they read the recipients'
column, once per call (`_mute_map`), so a broadcast stays one query. It is a
*mute* list and not a subscribe list so a new kind is on for everyone with no
backfill, the same rule `Users.role`'s default follows.

The switches are `/views/settings/notifications` — a page of its own, with
the index keeping one row that says how many kinds are on. It is a page for
the reason the moderation roster is: this is the one setting whose row count
is not fixed, so held inline it would grow with `NOTIFICATION_META` until it
pushed every other question off an index that is scanned rather than read.
It is the first per-account preference, on a screen that was already gated
for it — one row per kind, built server-side by `notification_settings()`
from `NOTIFICATION_META`
(which gained a `hint` alongside `title`/`text`/`icon`), so a kind cannot ship
without a switch and the switch cannot describe something other than the
notification it governs. The control is a native `<input type=checkbox>`
inside `.switch`, wrapped by the `.sr-head` label — focusable, announced, and
flipped by a tap anywhere on the row — and it reuses the feed's own
`.notif-icon` tile rather than a second one. `initNotificationPrefs()` saves
on the tap through `GET`/`PUT /notifications/prefs` (one kind per body, not
the whole map, so two devices cannot undo each other), moves optimistically
and snaps back with a toast on failure; there is no «ذخیره» button because
there is nothing to review before committing one boolean.

**The bell's count is client-side on purpose.** `initNotificationBell()` in
`app.js` asks `GET /notifications/unread-count` once the page is up. Filling
it in server-side would mean every view router in the app running one more
COUNT and passing one more context key, and a route that forgot would show a
silently wrong badge. It renders only for a signed-in visitor (challenge
discovery is open to anonymous readers), uses plain `fetch` rather than
`apiFetch` — a background count must not bounce someone out of the page they
are reading on a stale cookie — and `data-icon` sits on an inner span,
because `renderIcons()` replaces its element's whole innerHTML and would wipe
the badge beside it.

Colour on this screen means exactly one thing: **unread**. A notification is
neither a status nor a category, so it borrows neither channel's hues; the
accent is spent on the one distinction the list is scanned for, and the
«تازه» pill spells it out, because colour alone may not carry a meaning. The
rows are the settings screen's own primitives (`.setting-row` inside
`.settings-page`), like the admin panel's, and `Challenge.notifications`
cascades — a notification about a challenge that no longer exists is a
sentence about nothing that opens a 404.
`tests/test_notifications.py` pins the invariant, the ownership, the gate,
the transaction, and that a muted kind stops arriving — per member, even
inside a broadcast.

### The leaderboard — membership is the gate, not a wider profile filter

`/views/challenges/{id}/leaderboard` (+ its `/fragment`) ranks one
challenge's participants. Four pieces: `fetch_leaderboard_page` /
`leaderboard_rank` / `assign_ranks` / `leaderboard_standing` in
`routers/enrollment.py` (the queries), `participant_display` in
`app/identity.py` (who a row may look like), the two routes and
`build_leaderboard_rows` in `routers/views/challenge.py`, and
`templates/challenge/leaderboard.html` + `_leaderboard_rows.html`.

**It does not widen `profile_visibility_filter`, and that is the point.** A
board that names people is one step; letting someone open the named person's
profile is a second, and CLAUDE.md already says that second step is an edit
to *that one function* and nothing else. So a row is a `<div>`, not an `<a>`,
and there is nothing here to link to. The admin participant roster stays the
only screen that links a participant to a profile, for exactly the reason it
already gives.

**What it does widen is bounded and has its own name: membership.**
`_leaderboard_challenge` gates in two steps and the order is load-bearing —
`challenge_visibility_filter` composed into the query first (so a challenge
you cannot see is a plain **404**), then `challenge_role(...) is not None`
(so a member who has not joined a challenge they *can* see is a **403**).
403 is the honest answer there: they can already read the challenge, its
size and its collective progress, and a 404 would hide the one thing they
need to be told — joining is what opens the board. Asking through
`challenge_role` rather than comparing ids is what lets an owner who
unenrolled from their own challenge still reach it. The page takes
`get_page_user` and the `/fragment` takes `get_current_user_id`: the 303/401
split, for the reason every other pair has it.

**Anonymity is honoured by `participant_display`, the name-half of
`participant_avatar`** and deliberately in the same module — an anonymity
promise kept only by the surfaces that remembered it is not a promise. An
anonymous participant keeps their score and loses their name and picture
(«عضو ناشناس», the unset avatar, indistinguishable from a member who never
picked one). They are **not** dropped from the list: hiding the name is the
promise, and removing the row would let everyone else derive who is missing.
Your own row is always you, because a board you cannot find yourself on is a
board nobody can read their position off.

**Ranks are counted live off `CheckIns`, and ties share a rank.** Volume is
`COUNT(CheckIns) WHERE state='completed'` as a correlated subquery — never
`ChallengeStats.total_completions` (challenge-wide *and* monotonic) and never
`legacy_completed_count` (frozen pre-migration data), the same rule
`build_my_stats` and the home dashboard follow. Streaks come from
`Enrollments.current_streak`, which is legitimate precisely because
`compute_streaks` recomputes rather than increments it. Ranking reads **one**
leading key — a rank answers "how many are ahead of me", which has no meaning
against a tuple nobody can see — and the other metric is the tie-break, shown
beside it on every row so switching the sort is never needed to read it.
`leaderboard_rank` is `1 + count(score > mine)`, i.e. competition ranking:
two people on eleven check-ins are both 1st and the next is 3rd, because
numbering them 3rd and 4th invents a difference the data does not contain.
`assign_ranks` extends that across a page — the first row's rank is that same
query, and the rest is arithmetic — so a tie is still shared when it straddles
a page boundary, which a bare `offset + i + 1` would silently break. The
«رتبه تو» card is `leaderboard_standing`, which calls the same two functions,
so the card and the row it points at cannot disagree.

**`?sort=` is two options and no more** (`completions` | `streak`), the same
shape the moderation roster's `?sort=members` has: one query with a swapped
leading key, mirrored into the query string so a board is a link someone can
send, and an unrecognised value is **refused** by the pattern rather than
silently falling back. Ordering falls through to `newest_first(Enrollment)`
like every other paginated list — the id tie-break matters here as much as
anywhere, since an offset-paged scroller over a non-total order drops one
member and repeats another.

It is a page of its own rather than a fourth tab on challenge-detail, for the
reason the moderation roster is one: it is ordered and lazily paged, and a
list that pages cannot live in a panel that is only ever a screenful. There
is exactly **one** way in — a `.lb-entry` row in challenge-detail's «من»
panel, where a participant is already reading their own numbers, rendered
only when `participant_count > 1` (a board of one is not a board) — and it is
in «من» rather than «درباره» because a link in a panel a non-participant can
read would lead them to a 403. Colour means *rank* and borrows `--gold`,
which already means achievement on the streak tiles; it borrows neither the
`--st-*` status hues nor the six `--cat-*` category ones. The podium is
`[data-podium]` tinting the top three rank badges rather than a raised trio
above the list — a second layout would re-read the same three people twice,
and a four-way tie at rank 1 simply tints four badges instead of breaking a
fixed three-slot frame. `tests/test_leaderboard.py` pins the gate, the live
count, the shared ranks, the anonymity halves and the entry link.

### Groups — membership is one more conjunct, not a second visibility rule

A **group** is the space an organisation gathers its people in: a company
running a step challenge for its staff, a school running a reading challenge
for a class. `app/models/group.py` holds the four tables, `app/groups.py` is
the domain module (the predicate and the two writes that depend on it),
`app/routers/group.py` is the JSON door plus the queries the pages share, and
`app/routers/views/group.py` + `templates/group/` are the five screens.

**Groups are not public and are not searchable.** There is no listing of all
groups anywhere: `GET /groups/` answers *your* groups, every other route
composes `group_visibility_filter(user_id)` — the third such filter, beside
`challenge_visibility_filter` and `profile_visibility_filter` — and the only
way in from outside is an unguessable invite code. That is why `Group` has no
`visibility` column: there is no second state for it to be in.

**The whole access rule for a group's challenges is one extra conjunct.**
`group_scope_filter` is `AND`-ed onto both challenge visibility filters and
nothing else changes:

```
group_id IS NULL  OR  group_id IN <my groups>  OR  id IN <my enrollments>
```

Outside a group it is a no-op. Inside one, the app's existing three-way rule
(public / mine / enrolled) runs **unchanged**, which is exactly what makes
«عمومی» mean *public to the group* and «خصوصی» mean only the people actually
in the challenge. One rule, scoped — not a second rule that has to be kept in
agreement with the first, and not a per-surface check that the `/fragment`
route would eventually be written without. `?status=`, `?q=`, the JSON list,
the SSR list, its fragment and every detail route inherit it for free, and
`tests/test_group_visibility.py` walks all of them.

The third leg is load-bearing: **an existing enrollment is a key of its
own.** Somebody *removed* from a group keeps the challenges they were already
in, along with everything they logged there (somebody who walks out
themselves gives them up — see «Leaving has two doors» below) — the app never destroys that
(the 409 on hard-deleting a challenge is the same principle) — and a member
who could neither see nor leave a challenge they are still enrolled in would
be stuck. It leaks nothing: they were in the room. Somebody who was *never* in
the group has neither key, so for them a group challenge does not exist in
any listing, at any URL, through either front door.

`fetch_challenge_page(group_id=…)` is how the group page lists its own
challenges — the app-wide query with one more narrowing, applied *on top of*
the visibility clause, so asking for a group you are not in returns nothing
rather than that group's challenges, and «چالش‌های این گروه» can never
disagree with «چالش‌ها» about what a search matches.

**The third role axis, and it does not merge either.** `GroupMembership.role`
(`member` | `admin` | `owner`) joins `Users.role` and `Enrollments.role` in
`app/permissions.py`, and the rule that keeps the first two apart holds twice
over here: no app-wide role grants a single `GROUP_*` permission — an
operator does not become the administrator of a company that happens to use
this app — and no group role grants a single `CHALLENGE_*` one. A group
administrator gets `GROUP_CREATE_CHALLENGE`, checked *at the moment* a
challenge is created under their group; from then on that challenge has an
owner like any other and its edit rights come from the challenge axis alone.
Deciding **who is in** a challenge is running the group; changing **what it
says** is authorship — the same line moderation already draws.
`tests/test_groups.py` parametrizes the assertion over both global roles, as
`test_user_roles.py` does, so a future grant fails the test rather than
quietly crossing the line.

`group_role(user_id, group, membership)` resolves `Group.owner_id` against
the membership row exactly as `challenge_role` does, and for the same reason:
the column is the record of ownership and always present, the row carries the
role for everyone else. `admin` sits strictly below `owner` —
`GROUP_MANAGE_ADMINS`, `GROUP_TRANSFER` and `GROUP_DELETE` are the owner's
alone, because an administrator who could hand out admin is an owner with a
delay. **The owner cannot leave without handing the group over** (409): a
group with no owner has nobody who can invite, approve or delete it, and no
route anywhere could put one back. `POST /groups/{id}/transfer` moves the
column and both membership rows in one transaction, and leaves the outgoing
owner an `admin` — they were running the place a moment ago.

**404 vs 403, inside and out.** The group itself is a **404** to anyone not
in it, through both front doors, so a sequential id cannot be probed; the
`/manage` and participants pages are **404** to a member who is not an
administrator, the same call `get_admin_page_user` makes for the operator
panel. Once you are in, a refusal is an honest **403** and leaks nothing —
everybody in a group can already see who administers it. The one exception is
a member id that is not in this group: **404**, because a 403 there would
confirm the account exists.

**Three doors in, and each is the answer to something the others are not.**

*The invite link.* `GroupInvite` carries `code` (43 unguessable characters),
an optional `label`, `max_uses` (NULL = unlimited), `uses`, `expires_at` and
`revoked_at`. **The capacity is real**: it is consumed by a conditional
`UPDATE … SET uses = uses + 1 WHERE revoked_at IS NULL AND (expires_at IS
NULL OR expires_at > now) AND (max_uses IS NULL OR uses < max_uses)` and a
check of `rowcount` — never by reading `uses`, deciding, and writing back,
which is precisely the race that lets a one-seat link admit two people
(`tests/test_group_invites.py` drives four concurrent accepts at one seat).
Everything is one transaction, so a membership that fails to insert takes the
consumed seat back with it, and re-opening the link you joined with spends
nothing. The `state` an administrator reads is **derived** (`invite_state`),
never stored, for the reason the challenge status is: a stored column would
need a job to rewrite it the minute a link expired, and a state that
disagrees with whether the link works is worse than none. Revoking is a
timestamp rather than a DELETE — a row that vanishes reads as one that never
existed.

*The join request.* `GroupJoinRequest` is a table of its own rather than a
`pending` value on `GroupMemberships`, and that is the important choice: with
one table every query asking "is this person in this group" would have to
*remember* `status = 'active'`, and the one that forgets is a silent
access-control bug rather than a visible error. **A membership row means
membership, full stop.** The request is keyed on the **invite code**, not on
a group id (`POST /invites/{code}/request`): a route taking an id would let
anybody walk the sequential ids, learn which groups exist and spam every one
of them. It is the door for somebody whose link expired or filled up, which
is why the landing page offers it in exactly those states. Terminal rows are
kept, with `decided_by_user_id` — the only place that answers "who let this
person in", since the membership row says nothing about how it came to be.

*The public link.* Not a fourth mechanism: `GroupInvite.requires_approval` is
a flag on the same table, and a link carrying it reaches the same landing
page and **admits nobody** — `accept_invite` refuses it with 409 and the page
draws «درخواست عضویت» instead of «پیوستن». That is what makes it postable: an
address that keeps meaning "this group" wherever it is pasted, where the door
is the request queue rather than the link itself. `POST
/groups/{id}/invites/public` is **get-or-create** (one active unlimited
approval link per group, `PUBLIC_INVITE_LABEL`), because a route that minted
a fresh code per tap would leave every copy already handed out alive and
unaccounted for. Rotating it is revoke-then-ask-again through the routes that
already exist, so there is no rotate endpoint. Everything else about the link
— the code, the derived state, revocation, the landing page, the request
keyed on the code — is unchanged, which is the whole reason it is a flag
rather than a second table.

*Adding somebody directly.* `POST /groups/{id}/members` is the
administrator's door for the case the other two handle badly: somebody
standing in front of you, or somebody who gave you their number and will
never open a link. **Named by credential, never by id and never by a name
search** — `MemberAdd.identifier` goes through `normalize_mobile` (so
`0912…`, `+98912…` and `۰۹۱۲…` are one account) and falls back to an exact
email. A search box here, or a route taking a user id, would hand every group
administrator a way to walk the whole membership, which is exactly what
`profile_visibility_filter` exists to prevent. The refusal for an unknown
number is an honest **404**: an administrator has to be able to tell "no such
account" from "already in" (409), the oracle that costs is one whole number
per guess, and the alternative is a screen that silently does nothing. The
new member is told through `GROUP_MEMBER_ADDED` — its own kind rather than
`GROUP_JOIN_APPROVED`, because nothing was asked for, and the row stores the
event.

**Being in the group and being let into its challenges are two answers,
given at two moments.** `GroupMembership.is_trusted` («محرمیت») is the
second, and it is a column rather than a second membership state for the
reason the join request is a table of its own: a membership row must keep
meaning membership, full stop, so nothing that asks "is this person in this
group" has to remember a second condition. A member who is waiting is a
member — they read the group, they appear on the roster, and an administrator
may put them into a challenge **by name**. What waits is only the *standing*
audience: «همه اعضا» means every *approved* member.

The rule lives in exactly two places and both are the ones that already
existed. `apply_standing_audience` reads the column and returns early — the
check is inside the one door, not at its three call sites (a link, an
approved request, an administrator adding somebody), because the site that
forgot would not be a visible error but somebody quietly enrolled in
obligations nobody approved them for. And `seed_group_participants`
intersects `all` with `trusted_member_ids` at creation time, so a challenge
written while somebody waits and a member who arrives afterwards reach the
same answer. Naming a person explicitly is deliberately **not** filtered by
either: picking somebody by hand *is* the approval this column stands for,
and asking for it twice would be asking an administrator to approve one
thing twice.

`POST`/`DELETE /groups/{id}/members/{user_id}/trust` is the write, on
`GROUP_MANAGE_MEMBERS` rather than a permission of its own — it changes
nothing about what a member may *do*, only what they are put *into*, which
is the same job as deciding who is in the group. Approving is **retroactive
and idempotent**: it calls the same `apply_standing_audience` an arrival
goes through, so somebody approved a week late ends up exactly where
somebody approved on the day is, and `assign_participants` enrols nobody
twice. Taking it back **touches no existing enrollment** — the identical
call `remove_member` makes, for the identical reason: what somebody logged
is theirs. The owner cannot be un-approved (400: nobody could approve them
again) and promoting somebody to `admin` carries the approval with it, since
an administrator waiting to be approved may already put *other* people into
the group's challenges. Existing rows were not demoted by the migration:
`server_default` is `1` while the ORM default is `False`, so
`sync_sqlite_schema` backfills every membership that predates the column as
approved and only new rows start waiting.

Two queues therefore sit behind the manage page — requests to answer and
members to approve — kept apart rather than marked inside one list, because
they are about different moments and different people. The approval queue is
a section on the manage page (it is bounded by the roster and has no
history); the request queue is a row pointing at its own screen (it is
neither). The badge on the group page is their **sum**: two numbers on one
17px icon is two numbers nobody can tell apart. `tests/test_group_trust.py` pins the split in
both directions, the retroactive approval, the public link's refusal to
admit, and the credential lookup.

**A group challenge is handed out, not joined.** `Challenge.group_id` is a
nullable FK (NULL = every challenge that existed before this, so nothing
needed backfilling), plus `group_audience` (`all` | `selected`) and
`participation_mode` (`optional` | `mandatory`). All three are **create-only**
— none is on `ChallengeUpdate`. Moving a challenge between groups would
retroactively change who has been able to see it, which is the same objection
that locks `identity_mode`; the other two are the terms the participants were
enrolled under, and changing the terms of something people are already in is
what the lock rules exist to prevent.

«یک نفر خاص» is not a third audience — it is `selected` with one person
picked, so there is no third branch anywhere for a case that behaves
identically to the second. `all` is the one that carries a *standing*
meaning: `apply_standing_audience` re-reads it whenever somebody joins the
group, through **both** doors, so "for everyone" keeps meaning everyone and a
member who arrived by one door cannot miss what everybody else has (archived
challenges are skipped — enrolling somebody in one gives them a row that can
never be acted on). `assign_participants` is the one place a group challenge
gains a participant, it is idempotent, and the audience is enforced
server-side: `seed_group_participants` intersects `member_ids` with the
group's membership, so a body naming an outsider enrols nobody — dropped
rather than refused, so it is not possible to *test* whether an id is in the
group by watching which bodies fail.

`ParticipationMode` is one refusal, in `leaving_is_allowed`, asked by the
`unenroll` route, the bulk leave below, and the page that decides whether to
draw the button. **The obligation belongs to the membership, not to the
person**: a mandatory challenge cannot be left while you are still in the
group that set it, and the moment you are not — you left, or were removed —
it becomes an ordinary enrollment you may give up, with everything you logged
intact.

**Leaving has two doors and they are one function.**
`leave_group_challenges` in `app/groups.py` gives up every enrollment this
group holds for one member, and both doors are it: `DELETE
/groups/{id}/enrollments` («انصراف از همهٔ چالش‌های گروه»), and the self-leave
half of `DELETE /groups/{id}/members/{me}`, in the *same transaction* as the
membership going. It is deliberately the same act `DELETE /enrollments/{id}`
performs, repeated rather than shortcut: every counter comes down, and every
challenge owner still hears the departure through `membership_is_announced` —
a bulk action that quietly stopped notifying twenty different owners would be
a way to slip out unannounced. That function moved to `app/notifications.py`
when the third caller appeared.

`still_in_group` is the parameter that separates the two, and it is passed
rather than looked up because the callers know the answer *about different
moments*. The bulk route passes `True`: it keeps a mandatory challenge and
**names it back** in the response, so the page can explain why the list is
not empty and point at the door that does release it. Leaving the group
passes `False`, so everything goes including the mandatory ones — which is
exactly what the confirmation on the group page promises, and one function is
what keeps the promise and the write from coming apart.

**Being removed is the opposite call, and the older rule stands.** An
administrator's removal leaves the enrollments alone: destroying what another
person logged is not a power running a group buys — the harm the 409 on
hard-deleting a challenge exists to prevent — and `group_scope_filter` lets
an existing enrollment through so those challenges stay reachable to them
afterwards. What ends either way is the obligation, so somebody who was
removed can still walk away from each one, having been *given* the choice
rather than had it made for them. There is deliberately no administrator
version of the bulk route for the same reason.

The two exits are `.setting-row`s at the foot of the «درباره» panel — the one
panel every member can read, and the one they are on when they ask whether
they still want to be here — and each opens a `createSheet` rather than a
`confirm()`, because both are irreversible in the way that matters (the
check-ins go with the enrollment) and a `confirm()` cannot name which
challenges those are. That is what `createSheet`'s `note` field type is for:
a read-only paragraph, distinct from a field's `hint`, since these sheets
have no input at all. The owner gets **no leave row** and is told why, with a
link to the transfer — drawing a control that would answer 409 is a control
that lies, the same call the login screen's resend countdown makes.
`tests/test_group_leaving.py` pins all of it.

**Anonymity has a second write boundary, and it defaults the other way.**
`resolve_assigned_anonymity` in `app/identity.py` is `resolve_anonymity` for
somebody who was *put into* a challenge rather than joining it, and it differs
in exactly one case: `member_choice` resolves to **anonymous**. Somebody who
was never asked is not named — a corporate health challenge is precisely
where an employee must not find their number beside their name because a
screen they never saw defaulted for them. The way to change one's mind is
`PATCH /enrollments/{challenge_id}/anonymity`, a door of its own for the
reason `EnrollmentUpdate` deliberately does not carry the field: that handler
writes its body onto the row with a blind `setattr`, so a field there would be
a way *around* the resolution. This route runs the answer back through
`resolve_anonymity`, and answers **409** in a challenge that decides for
everyone — unlike a join, where "hide me" is simply not the question that
challenge asks, this request has no other purpose, and 200-with-nothing-changed
would leave the member believing something the row does not say. Every
existing surface (`participant_display`, `participant_avatar`, the
leaderboard, the owner's join/leave notifications) is untouched and applies
inside a group exactly as outside.

**Administering a group buys nothing about anybody's account.**
`profile_visibility_filter` is not widened by this feature and no group
screen links to a profile — the admin participant roster stays the only one
that does, for the reason it already gives. A group roster is not what that
filter withholds (these people are in a room together), but `GroupMemberRead`
carries a name, a picture and a role and nothing else, and the per-challenge
participants screen shows a checkbox rather than a score: deciding who is in
a challenge is running the group; a file on anybody is not part of it. What a
group's administrators get about progress is the collective figures every
member already sees. `tests/test_group_privacy.py` pins all of it.

**Seven notification kinds**, each a place a member could not otherwise find
out — a group happens *around* you. `Notification` gained a nullable
`group_id` (cascaded exactly like `challenge_id`, and for its reasons) and
`NOTIFICATION_META` a `{group}` placeholder. Joining by link raises nothing:
the joiner did it themselves and is looking at the result. The two assignment
kinds are **separate kinds**, not one with an adjective in the text — the row
stores the event and never the sentence, and the split is also what lets a
member silence the optional invitations while still being told about the
obligations. Everything goes through `notify`/`notify_many`, so the four
invariants (no self-notification, one transaction, the mute list, the
anonymising) hold without a single group call site restating them.

**Farsi labels live in `app/groups.py`**, not in one views router, and reach
templates through `register_group_filters(env)` — the same registration
contract `register_icon_filters` and `register_notification_filters` have.
That is `NOTIFICATION_META`'s call rather than the app's usual per-surface one
(D7) because two routers render them: the group screens, and the group chip
on a challenge card, which `views/challenge.py` draws. The create wizard keeps
its own client-side copy, exactly as it does for cadence — adding a
`GroupKind` means editing both.

**A group's emblem is an avatar id.** `Group.emblem` draws from the *same*
fixed catalogue `Users.avatar` does (`app/avatars.py`), validated by the same
`is_valid_avatar` at the same kind of write boundary, rendered by the same
`avatar_url`. A second catalogue would mean a second asset pipeline and a
second way for a stored value to become a path on disk, for no gain — and the
abstract styles in the set read perfectly well as an emblem. Every surface
paints `--bg-1` behind it, for the reason `.profile-avatar` does.

**Six screens, and the split follows the app's own rule about lists.**
`/views/groups/` is the member's own groups (there is no discovery, so the
empty state offers the two things that actually get somebody into one).
`/views/groups/{id}` is «چالش‌ها» / «اعضا» / «درباره» as three tab panels —
challenge-detail's shape, hash-backed so a shared link lands where it was
shared from — and only **one** of the three pages lazily, because two paged
lists on one screen would fight over one URL and neither would stay linkable;
«اعضا» is a screenful with a link to `/views/groups/{id}/members`, which is
the searched, paged roster. `/views/groups/{id}/manage` holds the ways in,
the approval queue, the invite links and the settings, and keeps a **row**
pointing at `/views/groups/{id}/requests` rather than the requests
themselves — that list is unbounded and filtered, and a paged list has no
business inside a settings screen that is scanned rather than read (the same
call that made the moderation roster its own page). What the row carries is
the one number that screen owes the reader: how many are waiting.
`/views/groups/{id}/requests` is the queue itself, `?status=` over
`pending` | `approved` | `rejected` and lazily paged through its own
`/fragment`. **The open queue is the default and the terminal states are one
tap away**: a decided request is history — something to check, not to act on
— and mixing it into the list of people still waiting is how an
administrator loses the queue. The order flips with the state, which is
`fetch_request_page`'s doing: pending is a *queue* (oldest first, the one
list in this app that deliberately does not read newest-first, because the
longest wait is the answer that is owed) and a decided row is history
(newest first off `decided_at`, which is when the thing the reader is
looking for actually happened). The gate is the manage page's — **404** to a
member on the page, a real 401/403 to a `fetch()` on the fragment and on the
JSON twin — and `tests/test_group_requests.py` pins all of it.
`/views/groups/{id}/challenges/{cid}/participants` is the only screen that
`/views/groups/{id}/challenges/{cid}/participants` is the only screen that
adds or removes somebody from a group challenge. `/views/invites/{code}` is
the one screen a non-member ever sees, and it shows the group's name, emblem,
kind and size and nothing else — whoever holds the code is not a member yet,
so a preview listing the challenges or the people would make an unused invite
a way to read the group. A signed-out visitor tapping an invite is handled by
machinery that already existed: `get_page_user` raises `LoginRequired`,
`app/main.py` turns it into a 303 carrying `?next=`, and the link still works
when they land back.

**The way in is a profile menu row and a settings row**, both always
rendered — being in no group is the normal state, and the page's own empty
state is what explains what a group is. Not a fifth bottom-nav tab, for the
reason the operator panel is not one: the nav is the four things every member
does, and a member with no group would pay a tab for something they never
open.

**The create wizard is unchanged for a personal challenge.** `?group=` is
absent from every existing entrance, so the two extra questions (who in the
group this is for, and whether it is optional) do not exist on that path; a
group challenge is created from the group's own «+», which is also what makes
«چالش از همان ابتدا ذیل گروه ساخته شود» true by construction rather than by a
validation rule. Both create paths — `POST /challenges/` and
`POST /views/challenges/create` — call the same `resolve_group_for_create` /
`seed_group_participants`, because the duplicated create logic this file
already warns about is exactly where a permission check goes missing from one
half.

**Colour means nothing new.** Status owns the `--st-*` hues and category the
six `--cat-*` ones; a group is neither, so its identity is carried by its
emblem and by a neutral `.group-chip` on the card and the detail hero. Two
things are coloured and both are states rather than identities: an invite
link that still works takes the accent, and «اجباری» takes `--gold`, which
already means "worth noticing" on the streak tiles — the alternative,
`--mohr`, reads as an error, and a required challenge is not one.

**Joining by link tells the joiner nothing, and an approved request does.**
That falls out of `notify`'s first invariant rather than from a branch: the
link-user is their own actor, so the assignment notifications
`apply_standing_audience` raises are dropped — they tapped "join" a second
ago and are looking at the page that lists those challenges. Somebody an
administrator approved has the *administrator* as their actor, so they are
told what they were signed up to. The asymmetry is right and costs nothing.

**Where it grows, and what stays a local change.** `Challenge.group_id` is a
scalar FK on purpose: one challenge in several groups is a join table plus an
edit to `group_scope_filter` and `group_ids_for`, because every read reaches a
group challenge through those two and nothing compares the column by hand.
Sub-teams are a `parent_id` on `Group` plus a recursive `group_ids_for`.
Auto-join by email domain is a column on `Group` and one call to
`apply_standing_audience` at signup — the door that already exists for "a new
arrival picks up what the group asks of everyone". HR reporting is aggregate
queries over `CheckIns`, which is where every live figure in this app already
comes from. A group plan is a column or a table on `Group` and a check in
`can()`. None of them is implemented, and none of them needs this design
rewritten.

### «راهنمای درجا» — the in-place explainer

`app/explainers.py` + the "راهنمای درجا" section of `styles.css` +
`initExplainers()` in `app.js`. A small «؟» beside the thing it explains,
opening a popover with two or three sentences and — where the thing *is* a set
of named states — one line per state.

It exists because this app is unusually dense with **derived** figures: a ring
that is a share, a streak that is recomputed rather than counted, a status
nobody stored, a grid whose columns are weeks and whose rows are weekdays. None
of that is inferable from the number itself, and the alternative — a line of
hint text under every heading — is a screen nobody reads twice. So the resting
layout pays one 17px dot and the explanation is one tap away, *next to* what it
describes rather than in a help page somebody has to go and find. It is the
opposite trade from the tour (`tour.js`), which introduces a screen once; this
answers a question at the moment it is asked, for as long as the app exists.

Four rules hold it together:

- **One registry, not a phrase per template.** `EXPLAINERS` in
  `app/explainers.py` is the only place a term becomes Farsi. The same term is
  explained on several screens — «رشته» on the home dashboard and on
  challenge-detail, «مهلت ثبت» on the run strip and the quota history — and two
  copies drift the moment one is edited. That is `NOTIFICATION_META`'s call,
  and deliberately **not** the per-surface pattern (D7) the short Farsi
  *labels* follow: a label names a thing the reader is already looking at, an
  explanation makes a promise about how the app behaves, and a promise may not
  have two versions.
- **The text ships with the markup.** `explain(key)` renders the whole entry
  into `data-explain-*` attributes on the button. No fetch, no loading state,
  nothing to fail offline, and a dot inside an infinite-scroll fragment works
  the moment it lands — the listener is delegated on the document, so there is
  no re-initialisation to forget. It is also why there is no JSON endpoint for
  this: nothing would call it.
- **A missing key fails loudly.** `explain()` raises `KeyError`, and
  `tests/test_explainers.py` walks every template for keys that do not exist —
  plus every `Jinja2Templates` environment for the missing
  `register_explainer_filters` call. This is the fifth registration contract
  (after icons, avatars, groups, notifications) and the only one that is
  *checked* rather than left to be discovered at render time, because it is
  chrome that appears on nearly every screen.
- **Colour means what it already means.** The dot is neutral at rest — an
  accent means "act on this", and help is never the primary action on any
  screen — and takes the accent only while its popover is open, which is the
  one moment the colour says something: *this* is the one that is talking.

Mechanics worth keeping. The pane is `position:fixed` and placed from the dot's
viewport rect, because a dot can sit inside a scrolling card, a sticky header
or a sheet and an absolutely positioned pane would be clipped by the first
ancestor with `overflow`; the cost is that it re-places on scroll (rAF-throttled)
and **closes rather than follows** once its anchor leaves the screen. Its floor
is above the bottom nav, not the viewport edge. Its open state is an opacity/
transform **transition, not a keyframe** — an animation that has not started yet
(a backgrounded tab, a paused compositor) leaves the pane sitting at its 0%
opacity, which is exactly the bug that shipped first. The arrow's two visible
borders are **physical** sides, since which two form the tip is a fact about the
45° rotation and must not flip with the writing direction. And Esc is handled in
the **capture** phase and swallowed: an explainer opened from inside a sheet has
to be what Esc puts away first, or one press closes both and the member loses
the form they were only asking a question about.

`createSheet` takes an `explainHtml` option carrying the same `explain()`
markup, so the app's other main surface has the same affordance rather than a
hint line per field saying half of it — the challenge manage sheet uses it to
say why cadence and goal are not in the sheet at all.

Two pieces of standing page text were **removed** in favour of a dot: the
leaderboard's footer paragraph (which said less, at the bottom of a ranked
list) and the anonymity switch's `title=` (invisible on the phone this app is
designed for). That direction is the point — the explainer is a way to say
*more* while the screens hold *less*.

### The onboarding tour

`app/static/js/tour.js` + the "Tour" section of `styles.css` are **three small
per-page tours, not one run across pages**: امروز (2 steps), خانه (3) and
چالش‌ها (2), listed together in `TOUR_STEPS` and grouped by each step's `path`.
The bottom nav's own step («مسیرهای اصلی») opens the خانه run rather than the
امروز one: خانه is the page a member lands on, so introducing the four
destinations anywhere else introduces them after the fact.
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
  something due today, so a member with an empty امروز sees a 1-step tour, and
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

**Colour is two themes, and lives only in the token blocks.** `styles.css` opens with three blocks and nothing below them may hardcode a colour: `:root` is the light theme «شن و مریم‌گلی» (sand & sage) and is also the base every token is declared in, then `@media (prefers-color-scheme:dark) :root:not([data-theme="light"])` and `:root[data-theme="dark"]` restate the same names for the dark theme «شب کرمی» (creamy night). The media-query copy and the attribute copy are duplicated on purpose — one serves a device preference, the other an explicit choice that must beat it — so a new token has to be added to both.

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
