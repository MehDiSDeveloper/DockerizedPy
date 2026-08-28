"""Prepare the database before the app boots.

The Alembic history is Postgres-only -- it uses ``postgresql_using`` casts,
raw ``CAST(... AS JSONB)`` and bare ``alter_column`` calls that SQLite cannot
execute -- so ``alembic upgrade head`` is only run against Postgres. On SQLite
the schema is built straight from the models instead and the revision table is
stamped, so a later switch to Postgres still starts from a sane baseline.
"""

import asyncio
import subprocess
import sys

from app.config import settings
from app.database import Base, engine


async def _create_all() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


def main() -> None:
    if settings.database_url.startswith("sqlite"):
        asyncio.run(_create_all())
        subprocess.run([sys.executable, "-m", "alembic", "stamp", "head"], check=True)
    else:
        subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], check=True)


if __name__ == "__main__":
    sys.exit(main())
