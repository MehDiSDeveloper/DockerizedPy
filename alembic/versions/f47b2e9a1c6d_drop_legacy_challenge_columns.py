"""revised challenge model: contract (drop legacy STI columns)

Revision ID: f47b2e9a1c6d
Revises: d3f9a21b5c7a
Create Date: 2026-08-25 00:00:00.000000

The expand migration (d3f9a21b5c7a) kept `is_public`, `challenge_type`,
`recurrence_pattern`, `interval`, and `end_date` around, unused, so it stayed
reversible. Every reader has moved to `visibility` / `cadence_kind` /
`cadence` since then, so this migration drops them for good.

`legacy_cadence` and `legacy_completed_count` are NOT touched here -- they
are the preserved user data from the original migration, not scaffolding.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f47b2e9a1c6d"
down_revision: str | Sequence[str] | None = "d3f9a21b5c7a"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_column("Challenges", "is_public")
    op.drop_column("Challenges", "challenge_type")
    op.drop_column("Challenges", "recurrence_pattern")
    op.drop_column("Challenges", "interval")
    op.drop_column("Challenges", "end_date")


def downgrade() -> None:
    """Recreates the columns, empty.

    This cannot restore the original values -- they were destroyed by
    upgrade(). A downgrade past this point leaves every row's `is_public`
    NULL and every recurrence/type column NULL; the app's Step 1 code (which
    still reads `visibility`/`cadence`, not these columns) is unaffected, but
    anything that expects Step-0-era data in these columns will not find it.
    """
    op.add_column("Challenges", sa.Column("is_public", sa.Boolean(), nullable=True))
    op.add_column(
        "Challenges", sa.Column("challenge_type", sa.String(50), nullable=True)
    )
    op.add_column(
        "Challenges", sa.Column("recurrence_pattern", sa.String(50), nullable=True)
    )
    op.add_column("Challenges", sa.Column("interval", sa.Integer(), nullable=True))
    op.add_column(
        "Challenges",
        sa.Column("end_date", sa.DateTime(timezone=True), nullable=True),
    )
