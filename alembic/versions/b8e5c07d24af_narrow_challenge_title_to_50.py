"""narrow Challenges.title to the length the API actually accepts

Revision ID: b8e5c07d24af
Revises: f47b2e9a1c6d
Create Date: 2026-08-26 00:00:00.000000

`Challenges.title` has been `VARCHAR(150)` since the init migration, but
`ChallengeBase.title` validates `min_length=3, max_length=50`, so no title
longer than 50 characters can be written through either front door. The
column is narrowed to match the contract that is actually enforced.

Rows predating the schema limit (seeded/mock data, or anything written
before the validator existed) can still exceed 50 characters, so the
upgrade truncates them first -- Postgres refuses the ALTER otherwise. That
truncation is not reversible; the downgrade only restores the wider column,
not the lost characters.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b8e5c07d24af"
down_revision: str | Sequence[str] | None = "f47b2e9a1c6d"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Table names are capitalized -- they must stay quoted in raw SQL.
    op.execute(
        'UPDATE "Challenges" SET title = substr(title, 1, 50) WHERE length(title) > 50'
    )
    op.alter_column(
        "Challenges",
        "title",
        existing_type=sa.String(length=150),
        type_=sa.String(length=50),
        existing_nullable=False,
    )


def downgrade() -> None:
    op.alter_column(
        "Challenges",
        "title",
        existing_type=sa.String(length=50),
        type_=sa.String(length=150),
        existing_nullable=False,
    )
