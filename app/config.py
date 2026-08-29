from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import make_url

BASE_DIR = Path(__file__).resolve().parent

# Both local dev and the Liara deploy run on SQLite. The file lives under
# `data/`, which is the directory Liara mounts its persistent disk on
# (`liara.json` -> mountTo: /app/data), so the database survives a redeploy.
# The Postgres wiring (alembic history, docker-compose `db` service,
# asyncpg pin, `.env.example`) is all still here -- switching back is a
# matter of pointing DATABASE_URL at Postgres again.
DEFAULT_SQLITE_URL = "sqlite+aiosqlite:///./data/challenges.db"


class Settings(BaseSettings):
    database_url: str = DEFAULT_SQLITE_URL
    environment: str = "development"
    secret_key: str = "dev-insecure-secret-change-me"

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    @property
    def is_sqlite(self) -> bool:
        return self.database_url.startswith("sqlite")

    @property
    def sqlite_path(self) -> Path | None:
        """Filesystem path behind a SQLite URL, or None for other backends."""
        if not self.is_sqlite:
            return None
        database = make_url(self.database_url).database
        if not database or database == ":memory:":
            return None
        return Path(database)


settings = Settings()

# A SQLite URL pointing into a directory that does not exist yet fails at
# connect time, not at import, which makes it look like a query bug. Create
# it up front instead -- on Liara the mount is there but empty on first boot.
_sqlite_path = settings.sqlite_path
if _sqlite_path is not None:
    _sqlite_path.parent.mkdir(parents=True, exist_ok=True)
