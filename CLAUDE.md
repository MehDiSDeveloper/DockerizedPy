# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project overview

"Challenge Manager" — a FastAPI app where users create and join fitness/habit "challenges" (one-time or recurring), track enrollments, and see progress. It exposes both a JSON API and server-rendered HTML views (Jinja2 + `challenge_manager` static assets). There is currently no auth: several endpoints hardcode `owner_id=1` / `CURRENT_USER_ID=1` as a placeholder (`app/routers/enrollment.py`).

## Commands

Run everything through the venv at `.venv/` (Windows: `.venv/Scripts/`).

```
# Install deps
pip install -r requirements.txt

# Run the app locally (SQLite by default, see app/config.py)
uvicorn app.main:app --reload

# Run via Docker Compose (Postgres + app, with debugpy on :5678)
docker-compose up

# Lint
ruff check .

# Alembic migrations
alembic revision --autogenerate -m "message"
alembic upgrade head

# Seed mock data (users, challenges, enrollments) against the real ORM/session
python -m app.scripts.generate_mock_data
```

There is no test suite in this repo currently.

Note: `requirements.txt` is UTF-16 encoded — editing it with plain-text tools may corrupt it; prefer `pip freeze`/`pip install X && pip freeze > requirements.txt` or verify encoding after edits.

## Configuration

- `app/config.py` reads settings from a `.env` file via `pydantic-settings`. Key var: `DATABASE_URL` (async SQLAlchemy URL — `sqlite+aiosqlite://` locally, `postgresql+asyncpg://` in Docker).
- Two env files exist: `.env` (local/SQLite dev, gitignored) and `.env.docker` (used by `docker-compose.yml`'s `app` service, Postgres). `.env.example` documents the Postgres shape.
- `docker-compose.yml` runs Postgres 17 (`db`) plus the app with `debugpy` attached on port 5678 (see `.vscode/launch.json` for the "Docker: Attach to FastAPI" config) and live-reload via a bind mount + `uvicorn --reload`.

## Architecture

**Layering**: `models` (SQLAlchemy ORM) → `schemas` (Pydantic, request/response) → `routers` (JSON API) and `routers/views` (HTML pages via Jinja2, under `/views/...` prefixes). All DB access is async (`AsyncSession`, `async_sessionmaker`) via `app/database.py:get_db`.

- **`app/database.py`** defines the async engine/session and the declarative `Base`. It imports `app.models` at module load time so all models register with `Base.metadata` (required for Alembic autogenerate and `create_all`-style usage).
- **`app/models/`**: `AuditBase` (`auditBase.py`) is an abstract mixin adding `created_at`/`updated_at`/`last_modifier_user_id` to models. `Challenge` uses single-table inheritance (`polymorphic_on="challenge_type"`) with two concrete subclasses, `OneTimeChallenge` and `RecurringChallenge` (which adds `recurrence_pattern`/`interval`/`end_date`). `Enrollment` is the join entity between `User` and `Challenge` (unique on `user_id`+`challenge_id`), carrying `status` and `completed_count`. Table names are capitalized (`Users`, `Challenges`, `Enrollments`) — keep this consistent when adding FKs/migrations. Enum values (`ChallengeCategory`, `ChallengeType`, `RecurringType`) are stored as Farsi display strings, not English codes.
- **`app/schemas/`**: mirrors the polymorphic model structure with discriminated unions — `ChallengeCreateUnion`/`ChallengeReadUnion` in `schemas/challenge.py` use Pydantic's `Field(discriminator="challenge_type")` over `OneTimeChallenge{Create,Read}`/`RecurringChallenge{Create,Read}`. Follow this pattern when extending challenge types rather than adding conditional fields to a single schema.
- **`app/routers/`** (JSON CRUD API, mounted with no shared prefix beyond each router's own, e.g. `/challenges`, `/users`, `/enrollments`) vs **`app/routers/views/`** (HTML pages under `/views/challenges`, `/views/users`, rendered with `Jinja2Templates(directory=BASE_DIR / "templates")`). Both layers duplicate similar create/list/detail logic against the same models — when changing challenge/user creation logic, check whether the parallel logic in `routers/views/` also needs updating.
- **Alembic** (`alembic/env.py`) runs migrations async via `async_engine_from_config`, importing all models explicitly for `target_metadata`. When adding a new model module, import it here too so autogenerate picks it up.
- **`app/scripts/generate_mock_data.py`** seeds data through the real ORM/async session (not raw SQL) — run with `python -m app.scripts.generate_mock_data` from the project root after migrations are applied.
- **Templates** (`app/templates/`) are organized by domain (`challenge/`, `user/`, `home/`, `common/error.html`) with a shared `base.html`; static assets live in `app/static/`.
