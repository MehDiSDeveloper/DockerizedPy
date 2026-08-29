"""Add Users.avatar

Nullable and left NULL for every existing row on purpose: accounts created
before the picker had nothing to pick, and NULL is what `avatar_url()` turns
into the shadowed-head placeholder. Backfilling a random avatar would be
inventing a choice the member never made.

Revision ID: a7c31d8be402
Revises: e91c47d2f0a3
Create Date: 2026-08-29

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a7c31d8be402"
down_revision: str | Sequence[str] | None = "e91c47d2f0a3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("Users", sa.Column("avatar", sa.String(length=60), nullable=True))


def downgrade() -> None:
    op.drop_column("Users", "avatar")
