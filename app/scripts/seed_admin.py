"""Make sure the operator's own account exists, on every boot.

The problem this solves is a deploy's database being a *different* database.
The first admin is deliberately not made through the app (see
``app.scripts.set_user_role``), so a fresh Liara disk boots with a schema, no
members, and no shell handy to promote anyone from -- the panel is
unreachable and there is no account to sign in with at all.

``start.sh`` therefore runs this after ``bootstrap_db``. It is idempotent and
declarative: it states which account must exist, and does the least it can to
make that true.

* No account with that email -> create it, hashed password, ``role=admin``.
* Account exists, not an admin -> promote it. This is the case where the
  operator signed up through the app on the deploy and needs the panel.
* Account exists and is an admin -> nothing at all.

**The password is never rewritten for an account that already exists.**
Silently resetting a password on every boot would mean an operator who
changed theirs gets it reverted by the next deploy, and it would make this
script a way to take over any account whose email is put in the env. Set
``SEED_ADMIN_RESET_PASSWORD=true`` for one deploy to force it -- that is the
"locked out" escape hatch, and it is opt-in on purpose.

Configuration is ``SEED_ADMIN_EMAIL`` / ``SEED_ADMIN_PASSWORD`` /
``SEED_ADMIN_NAME`` (``app/config.py``). With no email configured this does
nothing and says so, so a deploy that does not want a seeded account simply
leaves it unset.
"""

import asyncio
import sys

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import hash_password
from app.config import settings
from app.database import AsyncSessionLocal, engine
from app.models.user import User, UserRole


async def ensure_seed_admin(session: AsyncSession) -> str:
    """Create or promote the configured admin. Returns a log line."""
    email = (settings.seed_admin_email or "").strip().lower()
    if not email:
        return "seed_admin: SEED_ADMIN_EMAIL not set -- nothing to seed"
    if not settings.seed_admin_password:
        return f"seed_admin: {email} configured but SEED_ADMIN_PASSWORD is empty -- skipped"

    user = (
        await session.execute(select(User).where(User.email == email))
    ).scalar_one_or_none()

    if user is None:
        session.add(
            User(
                name=settings.seed_admin_name,
                email=email,
                password_hash=hash_password(settings.seed_admin_password),
                role=UserRole.ADMIN.value,
            )
        )
        await session.commit()
        return f"seed_admin: created {email} as admin"

    changes: list[str] = []
    if user.role != UserRole.ADMIN.value:
        user.role = UserRole.ADMIN.value
        changes.append("promoted to admin")
    if settings.seed_admin_reset_password:
        user.password_hash = hash_password(settings.seed_admin_password)
        changes.append("password reset")

    if not changes:
        return f"seed_admin: {email} already an admin -- unchanged"
    await session.commit()
    return f"seed_admin: {email} {', '.join(changes)}"


async def _run() -> None:
    async with AsyncSessionLocal() as session:
        print(await ensure_seed_admin(session))
    await engine.dispose()


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    asyncio.run(_run())
    return 0


if __name__ == "__main__":
    sys.exit(main())
