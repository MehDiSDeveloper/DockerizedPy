from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent

# Every environment runs on Postgres; the schema is the Alembic history
# (`alembic upgrade head`, run by `app.scripts.bootstrap_db` on boot). The
# default is the docker-compose `db` service as seen from the host.
DEFAULT_DATABASE_URL = (
    "postgresql+asyncpg://challengeuser:challengepass@localhost:5432/challengemanager"
)

# Uploaded pictures (`app/media.py`) live beside the database, on the same
# mounted disk and for the same reason: they have to survive a redeploy. They
# are deliberately *not* under `app/static/`, which is code the image ships
# with -- a directory the app writes to has no business inside the tree a
# `COPY . .` rebuilds.
MEDIA_ROOT = BASE_DIR.parent / "data" / "media"


class Settings(BaseSettings):
    database_url: str = DEFAULT_DATABASE_URL
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

    # --- Web Push (VAPID) -----------------------------------------------
    # The deploy's identity to every push service, generated once with
    # `python -m app.scripts.generate_vapid_keys`. Both are base64url: the
    # private key is the raw 32-byte scalar and the public key the
    # uncompressed 65-byte point, which is exactly the string the browser
    # hands to `subscribe()` -- so there is nothing to convert between the
    # env file and the page.
    #
    # Empty means "no push", and that is the *default*: with no keys the
    # subscribe route answers "unavailable", the settings row never appears
    # and nothing is ever sent. Same trade as the SMS gateway -- development
    # runs the whole stack offline -- except that push simply does not exist
    # rather than falling back to a log line, because there is no code a
    # member has to read out of one.
    #
    # Rotating these invalidates every existing subscription (a browser binds
    # its subscription to the public key it was created with), so it is the
    # same class of decision as rotating `secret_key`.
    vapid_public_key: str = ""
    vapid_private_key: str = ""
    # RFC 8292 wants a way to reach whoever is sending, for a push service
    # with a problem. A `mailto:` or an https URL; it is never shown to a
    # member.
    vapid_subject: str = "mailto:support@chalesh.ir"

    kavenegar_api_key: str = ""
    kavenegar_otp_template: str = ""
    kavenegar_sender: str = ""
    otp_sms_text: str = "کد ورود به چالش: {code}"

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    @property
    def push_enabled(self) -> bool:
        """Whether this deploy can send a push at all.

        Asked in exactly two places -- the route that hands the browser the
        public key, and the dispatcher -- so a deploy with no keys is one
        `if` away from the whole subsystem being inert, rather than a set of
        failures scattered across a send.
        """
        return bool(self.vapid_public_key and self.vapid_private_key)


settings = Settings()

# `StaticFiles` refuses to mount a directory that is not there yet, which on a
# fresh disk is every first boot.
MEDIA_ROOT.mkdir(parents=True, exist_ok=True)
