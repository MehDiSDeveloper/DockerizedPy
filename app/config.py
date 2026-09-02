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
    # The account `app.scripts.seed_admin` guarantees on every boot. It exists
    # because a deploy's database is a *different* database: the first admin
    # cannot be made through the app (see `app.scripts.set_user_role`), so a
    # fresh disk would otherwise boot with no way in at all.
    #
    # Defaults are empty, which means "seed nothing" -- the deploy that wants
    # an account sets these in its environment. The password is only ever used
    # to *create* a missing account; an existing one keeps its own unless
    # `seed_admin_reset_password` is turned on for a boot.
    seed_admin_email: str = ""
    seed_admin_password: str = ""
    seed_admin_name: str = "مدیر"
    seed_admin_reset_password: bool = False

    # --- SMS one-time passwords (Kavenegar) ----------------------------
    # The gateway behind `app/sms.py`. `kavenegar_otp_template` is the name of
    # an *approved template* on the Kavenegar panel and selects the
    # `verify/lookup` route, which is the one meant for one-time codes: no
    # sender line to get approved, delivery to numbers opted out of bulk SMS,
    # and no night-time window. `kavenegar_sender` + `otp_sms_text` are the
    # fallback for a deploy that has a line but no template yet.
    #
    # All three default to empty, which means "no gateway". In development
    # that prints the code to the log instead of failing, so the whole OTP
    # flow runs offline like the rest of the stack; anywhere else it is a
    # hard error, because a login screen that accepts codes nobody was sent
    # is worse than one that is plainly down.
    # --- Logging --------------------------------------------------------
    # `log_format` is "auto" (human-readable lines in development, one JSON
    # object per line everywhere else), or "text"/"json" to force one. See
    # `app/logging_config.py`.
    log_level: str = "INFO"
    log_format: str = "auto"

    kavenegar_api_key: str = ""
    kavenegar_otp_template: str = ""
    kavenegar_sender: str = ""
    otp_sms_text: str = "کد ورود به چالش: {code}"

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
