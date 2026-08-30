"""Prepare the database before the app boots.

Both local dev and the Liara deploy run on SQLite (see ``app/config.py``).
The Alembic history is Postgres-only -- it uses ``postgresql_using`` casts,
raw ``CAST(... AS JSONB)`` and bare ``alter_column`` calls SQLite cannot
execute -- so ``alembic upgrade head`` is only run against Postgres.

On SQLite the schema is instead reconciled against the models directly:
``create_all`` adds whole missing tables, then :func:`sync_sqlite_schema`
adds columns and indexes the models grew since the file was created. That
second step is the point -- ``create_all`` leaves an *existing* table alone,
which is how a deploy onto a persistent disk ends up serving
``no such column: Users.avatar`` after a new column ships. Adding a column
to a model is therefore the only step needed for it to reach production.

What this cannot do is *change* an existing column (SQLite has no real
``ALTER COLUMN``) or add a NOT NULL column with no default to a table that
already has rows. Those are reported loudly rather than silently skipped.
"""

import asyncio
import subprocess
import sys

from sqlalchemy import Connection, inspect, text
from sqlalchemy.schema import CreateIndex

from app.config import settings
from app.database import Base, engine


def sync_sqlite_schema(conn: Connection) -> list[str]:
    """Add columns/indexes the models have and the SQLite file does not.

    Returns the DDL statements applied, for logging.
    """
    inspector = inspect(conn)
    existing_tables = set(inspector.get_table_names())
    applied: list[str] = []

    for table in Base.metadata.sorted_tables:
        if table.name not in existing_tables:
            continue  # create_all already built it, in full.

        present = {col["name"] for col in inspector.get_columns(table.name)}
        for column in table.columns:
            if column.name in present:
                continue

            spec = _column_spec(conn, table.name, column)
            if spec is None:
                continue
            ddl = f'ALTER TABLE "{table.name}" ADD COLUMN {spec}'
            conn.execute(text(ddl))
            applied.append(ddl)

        existing_indexes = {ix["name"] for ix in inspector.get_indexes(table.name)}
        for index in table.indexes:
            if index.name in existing_indexes:
                continue
            conn.execute(CreateIndex(index))
            applied.append(f"CREATE INDEX {index.name}")

    return applied


def _column_spec(conn: Connection, table_name: str, column) -> str | None:
    """Render one ``ADD COLUMN`` clause, or None if SQLite cannot take it."""
    type_sql = column.type.compile(conn.dialect)
    spec = f'"{column.name}" {type_sql}'

    default = None
    if column.server_default is not None:
        default = str(column.server_default.arg)
    elif column.default is not None and not column.default.is_callable:
        value = column.default.arg
        default = f"'{value}'" if isinstance(value, str) else repr(value)

    if not column.nullable:
        if default is None:
            rows = conn.execute(
                text(f'SELECT COUNT(*) FROM "{table_name}"')
            ).scalar_one()
            if rows:
                print(
                    f"  !! {table_name}.{column.name} is NOT NULL with no default "
                    f"and {table_name} already has {rows} row(s) -- SQLite cannot "
                    "add it. Give the column a default, or make it nullable.",
                    file=sys.stderr,
                )
                return None
            # Empty table: NOT NULL is safe, nothing to backfill.
        spec += " NOT NULL"

    if default is not None:
        spec += f" DEFAULT {default}"
    return spec


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


async def _prepare_sqlite() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        applied = await conn.run_sync(sync_sqlite_schema)
        promoted = await conn.run_sync(backfill_enrollment_roles)
    for statement in applied:
        print(f"  ++ {statement}")
    if promoted:
        print(f"  ++ {promoted} enrollment(s) marked as challenge owner")
    await engine.dispose()


def main() -> None:
    if settings.is_sqlite:
        print(f"bootstrap_db: syncing SQLite schema at {settings.sqlite_path}")
        asyncio.run(_prepare_sqlite())
    else:
        subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], check=True)


if __name__ == "__main__":
    sys.exit(main())
