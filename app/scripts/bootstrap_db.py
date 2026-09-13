"""Prepare the database before the app boots.

The schema is the Alembic history: ``alembic upgrade head``. A schema change
is therefore a model change *plus* a migration
(``alembic revision --autogenerate``, reviewed before committing) --
``alembic check`` fails while the two disagree.

After the upgrade, :func:`backfill_enrollment_roles` runs every boot.
"""

import asyncio
import subprocess
import sys

from sqlalchemy import Connection, text

from app.database import engine


def backfill_enrollment_roles(conn: Connection) -> int:
    """Give the creator's own enrolment ``role = 'owner'``.

    ``Enrollments.role`` arrives with ``DEFAULT 'participant'``, which is the
    right answer for every row except one per challenge: the creator's
    auto-enrolment (D1), written before the column existed. New enrolments
    set the role themselves, so this only ever has work to do on the boot
    that adds the column -- but it is run every boot because it is idempotent
    and cheap, and a guard on "did we just add it" is a guard that goes wrong
    exactly once, silently.

    Ownership itself is *not* moved here: ``Challenge.owner_id`` stays the
    record, and ``app.permissions.challenge_role`` resolves the two. This
    only stops the enrolment row from contradicting it.
    """
    result = conn.execute(
        text(
            "UPDATE \"Enrollments\" SET role = 'owner' "
            "WHERE role <> 'owner' AND EXISTS ("
            '  SELECT 1 FROM "Challenges" c'
            '  WHERE c.id = "Enrollments".challenge_id'
            '    AND c.owner_id = "Enrollments".user_id)'
        )
    )
    return result.rowcount or 0


async def _backfill() -> None:
    async with engine.begin() as conn:
        promoted = await conn.run_sync(backfill_enrollment_roles)
    if promoted:
        print(f"  ++ {promoted} enrollment(s) marked as challenge owner")
    await engine.dispose()


def main() -> None:
    subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], check=True)
    asyncio.run(_backfill())


if __name__ == "__main__":
    sys.exit(main())
