"""add auth, visibility, and enrollment audit columns

Revision ID: c2b7f4a19d3e
Revises: a114a3534821
Create Date: 2026-08-24 00:00:00.000000

"""

import base64
import hashlib
import secrets
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c2b7f4a19d3e"
down_revision: str | Sequence[str] | None = "a114a3534821"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_PBKDF2_ITERATIONS = 260_000


def _hash_password(password: str) -> str:
    """Standalone copy of app.auth.hash_password for this data migration.

    Deliberately duplicated rather than imported: a migration must keep
    producing the same output even if the app's implementation changes later.
    """
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt, _PBKDF2_ITERATIONS, dklen=32
    )
    salt_b64 = base64.urlsafe_b64encode(salt).rstrip(b"=").decode("ascii")
    digest_b64 = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return f"pbkdf2_sha256${_PBKDF2_ITERATIONS}${salt_b64}${digest_b64}"


def upgrade() -> None:
    """Upgrade schema."""
    bind = op.get_bind()

    # --- Users: password -> password_hash, drop redundant member_since ---
    op.add_column("Users", sa.Column("password_hash", sa.String(length=255), nullable=True))

    rows = bind.execute(sa.text('SELECT id, password FROM "Users"')).fetchall()
    for row in rows:
        bind.execute(
            sa.text('UPDATE "Users" SET password_hash = :h WHERE id = :id'),
            {"h": _hash_password(row.password), "id": row.id},
        )

    op.alter_column("Users", "password_hash", nullable=False)
    op.drop_column("Users", "password")
    # Note: "member_since" is declared on the User model but was never actually
    # created by the init migration (schema drift) -- nothing to drop here.

    # --- Challenges: fix OneTimeChallenge nullability bug, add visibility ---
    op.alter_column("Challenges", "recurrence_pattern", nullable=True)
    op.alter_column("Challenges", "interval", nullable=True)
    op.add_column(
        "Challenges",
        sa.Column("is_public", sa.Boolean(), nullable=False, server_default=sa.true()),
    )

    # --- Enrollments: gain AuditBase columns (created_at/updated_at/last_modifier_user_id) ---
    op.add_column(
        "Enrollments",
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.add_column(
        "Enrollments", sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        "Enrollments", sa.Column("last_modifier_user_id", sa.Integer(), nullable=True)
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("Enrollments", "last_modifier_user_id")
    op.drop_column("Enrollments", "updated_at")
    op.drop_column("Enrollments", "created_at")

    op.drop_column("Challenges", "is_public")
    op.alter_column("Challenges", "interval", nullable=False)
    op.alter_column("Challenges", "recurrence_pattern", nullable=False)

    # Password hashes cannot be reversed into plaintext; downgrade restores
    # the column's shape only, not its pre-migration values. "member_since"
    # is intentionally not restored -- it never existed in the DB (see upgrade()).
    op.add_column("Users", sa.Column("password", sa.String(length=150), nullable=True))
    op.drop_column("Users", "password_hash")
