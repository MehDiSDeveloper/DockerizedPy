"""Copy every row of an old SQLite database into the Postgres at DATABASE_URL.

One-shot, for the move off SQLite:

    alembic upgrade head
    python -m app.scripts.copy_sqlite_to_postgres data/challenges.db

The target must already be at ``alembic head`` and hold no rows in any model
table (a seeded admin included) -- the ids are copied verbatim, so a row
already there would collide. Refuses otherwise rather than merging.

Tables go in foreign-key order, rows in id order (so a self-referencing
parent is always written before its child), only the columns both sides have
(anything the old file predates takes its default), and each ``id`` sequence
is moved past the copied maximum at the end. One transaction: it lands whole
or not at all.
"""

import argparse
import asyncio
import sys
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import DateTime, func, inspect, select, text
from sqlalchemy.ext.asyncio import create_async_engine

from app.config import settings
from app.database import Base

BATCH = 500


def _aware(value):
    # SQLite has no aware datetime type and hands back naive UTC.
    if isinstance(value, datetime) and value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value


async def copy(source_path: Path) -> None:
    source = create_async_engine(f"sqlite+aiosqlite:///{source_path.as_posix()}")
    target = create_async_engine(settings.database_url)
    tables = Base.metadata.sorted_tables

    async with source.connect() as src, target.begin() as dst:
        present = await src.run_sync(
            lambda c: {
                t: {col["name"] for col in inspect(c).get_columns(t)}
                for t in inspect(c).get_table_names()
            }
        )

        for table in tables:
            count = (await dst.execute(select(func.count()).select_from(table))).scalar()
            if count:
                sys.exit(f"refusing: {table.name} already has {count} row(s) in the target")

        for table in tables:
            if table.name not in present:
                print(f"  -- {table.name}: not in the source, skipped")
                continue
            columns = [c for c in table.columns if c.name in present[table.name]]
            aware = {c.name for c in columns if isinstance(c.type, DateTime)}
            order = [table.c.id] if "id" in table.c else list(table.primary_key)
            result = await src.stream(select(*columns).order_by(*order))

            copied = 0
            async for chunk in result.mappings().partitions(BATCH):
                rows = [
                    {k: (_aware(v) if k in aware else v) for k, v in row.items()}
                    for row in chunk
                ]
                await dst.execute(table.insert(), rows)
                copied += len(rows)
            print(f"  ++ {table.name}: {copied} row(s)")

            if "id" in table.c and table.c.id.autoincrement is not False and copied:
                await dst.execute(
                    text(
                        f"SELECT setval(pg_get_serial_sequence('\"{table.name}\"', 'id'), "
                        f'(SELECT MAX(id) FROM "{table.name}"))'
                    )
                )

    await source.dispose()
    await target.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("sqlite_file", type=Path)
    args = parser.parse_args()
    if not args.sqlite_file.is_file():
        sys.exit(f"no such file: {args.sqlite_file}")
    if settings.database_url.startswith("sqlite"):
        sys.exit("DATABASE_URL must point at the Postgres to copy into")
    asyncio.run(copy(args.sqlite_file))
    print("done")


if __name__ == "__main__":
    main()
